"""
Mirrors CatalogProduct.image_url (TCGCSV/TCGplayer's hosted images) into
Cloudflare R2 as small POS-grid thumbnails (PoBuSA Checklist Phase 1,
item 5).

Deliberately a separate field from image_url, which is left untouched as
the source/fallback -- see CatalogProduct.thumbnail_url's field comment
in models.py.

Dedupes by image_url before downloading: ~299,728 CatalogProduct rows
share only ~236,665 distinct images (variant printings reusing the same
base art), so this only ever downloads/uploads each unique image once,
then bulk-updates every row sharing that URL in a single query.

Safely re-runnable/resumable -- only ever selects rows where
thumbnail_url is still null, and skips the actual R2 upload (but still
updates the DB) if the object's already there from a prior partial run,
so an interrupted run just picks back up where it left off.

Concurrent: downloads+uploads happen on a thread pool (network I/O bound,
parallelizes well) in fixed-size chunks; every DB write happens on the
main thread only, in between chunks, so there's no concurrent-write
contention against the local SQLite dev DB (production is Postgres,
which wouldn't care either way, but this keeps both safe).

A 403/404 from the source CDN is treated as PERMANENT (see
PermanentImageError below) -- confirmed in practice that a handful of
old promo/judge/gold-stamped items are consistently 403 regardless of
headers, retries, or backoff. These fail instantly rather than eating
retry time, and don't count toward the transient-failure cooldown.

Required environment variables (same names as pokemart-api's own R2
image script, download_images_serebii_limitless.py, for consistency):
    R2_ACCOUNT_ID
    R2_ACCESS_KEY_ID
    R2_SECRET_ACCESS_KEY
    R2_BUCKET        (defaults to "pobusa-catalog-images")
    R2_PUBLIC_URL    (no default -- e.g. "https://images.pobusa.co.za" --
                       must be a custom domain, or r2.dev URL with public
                       access enabled, that you've already set up in the
                       Cloudflare dashboard pointing at R2_BUCKET)

Usage:
    python manage.py mirror_images_to_r2                 # full run, all pending
    python manage.py mirror_images_to_r2 --limit 50       # test on a handful first
    python manage.py mirror_images_to_r2 --dry-run        # log what would happen, no network/writes at all
    python manage.py mirror_images_to_r2 --workers 20      # tune concurrency (default 10)
"""
import hashlib
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO

import requests
from django.core.management.base import BaseCommand

from pobusa.models import CatalogProduct

THUMB_MAX_PX = 300  # longest edge -- a POS grid tile never needs more than this
JPEG_QUALITY = 82
CHUNK_SIZE = 200  # how many images get submitted to the thread pool between DB-write passes
DEFAULT_WORKERS = 10
# TCGplayer's CDN 403s a custom UA with no Referer -- looks exactly like
# scraping to their edge bot-protection. A browser UA + a Referer of the
# actual site the image would be embedded on fixes it, same fix already
# proven in download_images_serebii_limitless.py for Limitless/Serebii.
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
REFERER = "https://www.tcgplayer.com/"
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 4  # 4s, 8s, 12s...
# If more than this fraction of a chunk fails with a TRANSIENT error
# (never counts permanent 403/404s), assume something's actually wrong
# (real rate-limiting, network trouble) and pause before the next chunk.
CHUNK_TRANSIENT_FAILURE_COOLDOWN_FRACTION = 0.5
COOLDOWN_SECONDS = 30


class PermanentImageError(Exception):
    """403/404 from the source CDN. In practice this is a small set of old
    promo/judge/gold-stamped/Secret-Lair-style items that TCGplayer never
    uploaded a real photo for -- confirmed these 403 identically every
    time regardless of headers, retries, or backoff, so retrying or
    cooling down doesn't fix it. Raised as its own type to fail fast and
    stay out of the transient-failure cooldown accounting."""
    pass


def r2_key_for_url(image_url):
    """Deterministic key from the source URL -- the same source image
    always maps to the same R2 object, which is what makes both the
    variant-sharing dedup and safe re-runs work without tracking
    anything extra in a separate table."""
    digest = hashlib.sha1(image_url.encode("utf-8")).hexdigest()[:24]
    return f"catalog/{digest}.jpg"


def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


class Command(BaseCommand):
    help = "Mirrors CatalogProduct images to Cloudflare R2 as small thumbnails."

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, help='Only process this many distinct images (testing)')
        parser.add_argument('--dry-run', action='store_true', help="Log what would happen, don't touch network/DB at all")
        parser.add_argument('--workers', type=int, default=DEFAULT_WORKERS, help=f'Concurrent download threads (default {DEFAULT_WORKERS})')

    def handle(self, *args, **options):
        try:
            from PIL import Image
        except ImportError:
            self.stderr.write(self.style.ERROR("Pillow is required -- pip install -r requirements.txt"))
            return

        dry_run = options.get('dry_run')
        limit = options.get('limit')
        workers = options.get('workers') or DEFAULT_WORKERS

        bucket = os.environ.get('R2_BUCKET', 'pobusa-catalog-images')
        public_url = os.environ.get('R2_PUBLIC_URL', '').rstrip('/')
        if not dry_run and not public_url:
            self.stderr.write(self.style.ERROR(
                "R2_PUBLIC_URL is not set -- set it to your bucket's custom domain "
                "(e.g. https://images.pobusa.co.za) before running for real. "
                "Use --dry-run to preview without it."
            ))
            return

        s3 = None if dry_run else self.get_r2_client()

        urls = list(
            CatalogProduct.objects
            .exclude(image_url='')
            .filter(thumbnail_url__isnull=True)
            .values_list('image_url', flat=True)
            .distinct()
        )
        if limit:
            urls = urls[:limit]

        self.stdout.write(f"{len(urls)} distinct image(s) pending mirror, {workers} concurrent workers...")

        if dry_run:
            for i, image_url in enumerate(urls[:10], 1):
                self.stdout.write(f"  [{i}/{len(urls)}] would mirror {image_url} -> {r2_key_for_url(image_url)}")
            self.stdout.write(self.style.SUCCESS(f"\nDry run: {len(urls)} would be processed."))
            return

        mirrored = skipped_existing = failed_permanent = failed_transient = rows_updated = 0
        processed = 0
        start_time = time.time()

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for chunk in chunked(urls, CHUNK_SIZE):
                future_to_url = {pool.submit(self.process_one, s3, bucket, public_url, url): url for url in chunk}

                chunk_transient_failures = 0
                results = []  # (image_url, public_thumb_url or None, outcome)
                for future in as_completed(future_to_url):
                    image_url = future_to_url[future]
                    outcome, public_thumb_url, error = future.result()
                    results.append((image_url, public_thumb_url, outcome))
                    if outcome == 'mirrored':
                        mirrored += 1
                    elif outcome == 'already_in_r2':
                        skipped_existing += 1
                    elif outcome == 'permanent':
                        failed_permanent += 1
                        self.stderr.write(f"  FAILED (permanent, no retry) {image_url}: {error}")
                    else:  # transient
                        failed_transient += 1
                        chunk_transient_failures += 1
                        self.stderr.write(f"  FAILED {image_url}: {error}")

                # All DB writes for this chunk happen here, on the main
                # thread only -- no concurrent writers against SQLite.
                for image_url, public_thumb_url, outcome in results:
                    if public_thumb_url:
                        rows_updated += CatalogProduct.objects.filter(image_url=image_url).update(thumbnail_url=public_thumb_url)

                processed += len(chunk)
                pct = processed / len(urls) * 100
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                remaining = (len(urls) - processed) / rate if rate > 0 else 0
                self.stdout.write(
                    f"  [{processed}/{len(urls)} = {pct:.1f}%] mirrored={mirrored} already-in-r2={skipped_existing} "
                    f"failed(permanent)={failed_permanent} failed(transient)={failed_transient} "
                    f"-- ~{remaining/60:.0f} min remaining at current rate"
                )

                if len(chunk) > 0 and chunk_transient_failures / len(chunk) >= CHUNK_TRANSIENT_FAILURE_COOLDOWN_FRACTION:
                    self.stdout.write(self.style.WARNING(
                        f"  {chunk_transient_failures}/{len(chunk)} transient failures in this chunk -- "
                        f"pausing {COOLDOWN_SECONDS}s in case we're being rate-limited."
                    ))
                    time.sleep(COOLDOWN_SECONDS)

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. {mirrored} uploaded, {skipped_existing} already in R2, "
            f"{failed_permanent} permanently unavailable, {failed_transient} transient failures (retry these later), "
            f"{rows_updated} CatalogProduct row(s) updated."
        ))

    def process_one(self, s3, bucket, public_url, image_url):
        """Runs on a worker thread -- all network I/O (R2 head/put,
        source download), zero DB access. Always returns a result tuple
        instead of raising, so the main thread never has to catch
        exceptions across the thread boundary.
        Returns: (outcome, public_thumb_url_or_None, error_or_None)
        outcome is one of: 'mirrored', 'already_in_r2', 'permanent', 'transient'
        """
        try:
            from PIL import Image
        except ImportError:
            return 'transient', None, 'Pillow not importable in worker thread'

        key = r2_key_for_url(image_url)
        public_thumb_url = f"{public_url}/{key}"

        try:
            if self.object_exists(s3, bucket, key):
                return 'already_in_r2', public_thumb_url, None

            thumb_bytes = self.download_with_retry(image_url, Image)
            s3.put_object(Bucket=bucket, Key=key, Body=thumb_bytes, ContentType='image/jpeg')
            return 'mirrored', public_thumb_url, None
        except PermanentImageError as e:
            return 'permanent', None, str(e)
        except Exception as e:
            return 'transient', None, str(e)

    def get_r2_client(self):
        import boto3
        from botocore.config import Config
        account_id = os.environ.get('R2_ACCOUNT_ID')
        access_key = os.environ.get('R2_ACCESS_KEY_ID')
        secret_key = os.environ.get('R2_SECRET_ACCESS_KEY')
        missing = [n for n, v in [('R2_ACCOUNT_ID', account_id), ('R2_ACCESS_KEY_ID', access_key),
                                   ('R2_SECRET_ACCESS_KEY', secret_key)] if not v]
        if missing:
            self.stderr.write(self.style.ERROR('Missing required environment variable(s): ' + ', '.join(missing)))
            sys.exit(1)
        endpoint_url = f'https://{account_id}.r2.cloudflarestorage.com'
        # boto3 clients are documented as thread-safe for issuing concurrent
        # calls from multiple threads, which is exactly how this is used.
        return boto3.client(
            's3', endpoint_url=endpoint_url,
            aws_access_key_id=access_key, aws_secret_access_key=secret_key,
            config=Config(signature_version='s3v4'), region_name='auto',
        )

    def object_exists(self, s3, bucket, key):
        try:
            s3.head_object(Bucket=bucket, Key=key)
            return True
        except Exception:
            return False

    def download_with_retry(self, image_url, Image):
        """A few retries with backoff before giving up on this image --
        but only for genuinely transient failures (network blips, 5xx).
        A 403/404 raises PermanentImageError immediately (see its
        docstring) and is never retried here -- confirmed in practice
        that TCGplayer's CDN 403s the same handful of old promo items
        every single time, retry or not, so retrying just burns
        MAX_RETRIES * RETRY_BACKOFF_SECONDS for nothing."""
        last_exception = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return self.download_and_resize(image_url, Image)
            except PermanentImageError:
                raise
            except Exception as e:
                last_exception = e
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
        raise last_exception

    def download_and_resize(self, image_url, Image):
        resp = requests.get(
            image_url,
            headers={'User-Agent': UA, 'Referer': REFERER},
            timeout=20,
        )
        if resp.status_code in (403, 404):
            raise PermanentImageError(f"{resp.status_code} {resp.reason}")
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert('RGB')
        img.thumbnail((THUMB_MAX_PX, THUMB_MAX_PX), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format='JPEG', quality=JPEG_QUALITY)
        return buf.getvalue()
