# PokeBulk SA - Download Images To R2 (Serebii/Limitless source)
# v1.0
#
# Same architecture as download_images_to_r2_v1_0.py (bible CSV drives which
# rows get images, same R2 key naming cards/{set_code}/{product_id}_{variant}.jpg,
# same env-var credentials) -- but sources images from Limitless first, falling
# back to Serebii, instead of Bulbapedia. Built for PBL specifically because
# Bulbapedia's own rollout is still partial 4 days before release.
#
# For each unique card_number in the bible (grouped, since N/H/RH share one
# base photo): fetch ONE base image, then:
#   - N and H variant rows get the clean image, no watermark
#   - RH variant rows get a synthesized reverse-holo watermark applied
#     (matching me03_download_images.py's exact watermark style/constants)
#
# Per 2026-07-13 confirmation: every N and H print gets a synthesized RH,
# including secret-rare/H-tier cards -- not just N-tier cards.
#
# Credentials from environment variables, same as download_images_to_r2_v1_0.py:
#   $env:R2_ACCOUNT_ID = "your-cloudflare-account-id"
#   $env:R2_ACCESS_KEY_ID = "your-r2-access-key-id"
#   $env:R2_SECRET_ACCESS_KEY = "your-r2-secret-access-key"
#
# Requirements: pip install requests boto3 pillow --break-system-packages
#
# Usage:
#   python download_images_serebii_limitless.py --in pokebulk_bible_v7.csv --set-code PBL --dry-run
#   python download_images_serebii_limitless.py --in pokebulk_bible_v7.csv --set-code PBL --limit 10
#   python download_images_serebii_limitless.py --in pokebulk_bible_v7.csv --set-code PBL

import csv
import os
import re
import sys
import time
import math
import argparse
from io import BytesIO

import requests
import boto3
from botocore.config import Config
from PIL import Image, ImageDraw, ImageFont

R2_BUCKET = 'pokebulkcards'
PUBLIC_URL = 'https://images.pokebulk.co.za'
DELAY_SECS = 0.3

UA = 'PokeBulkSA-ImageSync/1.0 (https://pokebulk.co.za; contact: enquiries@pokebulk.co.za)'

SEREBII_SET_SLUG = 'pitchblack'  # www.serebii.net/card/{slug}/{number}.jpg
LIMITLESS_SET_CODE = 'PBL'       # limitlesstcg.com/cards/{code}/{number}

# ── Watermark settings -- identical to me03_download_images.py ──────────────
WM_TEXT_REV     = "Poke Bulk SA - Reverse Holo"
WM_TEXT_ALPHA   = 110
WM_STROKE       = 3
WM_STROKE_ALPHA = 170
WM_ROTATION     = 45
WM_DIAG_FILL    = 0.92
WM_SAFETY       = 0.96


def load_font(size):
    for p in [r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\calibri.ttf", r"C:\Windows\Fonts\verdana.ttf"]:
        if os.path.exists(p):
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()


def measure(draw, text, font):
    b = draw.textbbox((0, 0), text, font=font, stroke_width=WM_STROKE)
    return b[2] - b[0], b[3] - b[1]


def rotated_bbox(w, h, deg):
    t = math.radians(deg)
    return abs(w * math.cos(t)) + abs(h * math.sin(t)), abs(w * math.sin(t)) + abs(h * math.cos(t))


def best_font_size(iw, ih, text):
    diag = math.hypot(iw, ih) * WM_DIAG_FILL
    lo, hi, best = 10, 420, 24
    dummy = Image.new("RGBA", (10, 10))
    draw = ImageDraw.Draw(dummy)
    while lo <= hi:
        mid = (lo + hi) // 2
        font = load_font(mid)
        tw, th = measure(draw, text, font)
        rw, rh = rotated_bbox(tw, th, WM_ROTATION)
        if (rw <= iw * WM_SAFETY) and (rh <= ih * WM_SAFETY) and (tw <= diag):
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return max(best, 18)


def apply_watermark(img_bytes, text):
    base = Image.open(BytesIO(img_bytes)).convert("RGBA")
    w, h = base.size
    font = load_font(best_font_size(w, h, text))
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    tw, th = measure(draw, text, font)
    pad = WM_STROKE + 8
    txt_img = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt_img)
    d.text((pad, pad), text, font=font, fill=(255, 255, 255, WM_TEXT_ALPHA),
           stroke_width=WM_STROKE, stroke_fill=(0, 0, 0, WM_STROKE_ALPHA))
    rot = txt_img.rotate(WM_ROTATION, resample=Image.BICUBIC, expand=True)
    overlay.alpha_composite(rot, ((w - rot.size[0]) // 2, (h - rot.size[1]) // 2))
    final = Image.alpha_composite(base, overlay).convert("RGB")
    buf = BytesIO()
    final.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def clean_jpeg_bytes(img_bytes):
    # Re-encode through PIL so both watermarked and clean paths produce
    # consistent, valid JPEG bytes regardless of source format (PNG from
    # Limitless, JPEG from Serebii).
    img = Image.open(BytesIO(img_bytes)).convert("RGB")
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


# ── R2 ────────────────────────────────────────────────────────────────────
def get_r2_client():
    account_id = os.environ.get('R2_ACCOUNT_ID')
    access_key = os.environ.get('R2_ACCESS_KEY_ID')
    secret_key = os.environ.get('R2_SECRET_ACCESS_KEY')
    missing = [n for n, v in [('R2_ACCOUNT_ID', account_id),
                               ('R2_ACCESS_KEY_ID', access_key),
                               ('R2_SECRET_ACCESS_KEY', secret_key)] if not v]
    if missing:
        print('Missing required environment variable(s): ' + ', '.join(missing))
        sys.exit(1)
    endpoint_url = 'https://' + account_id + '.r2.cloudflarestorage.com'
    return boto3.client(
        's3', endpoint_url=endpoint_url,
        aws_access_key_id=access_key, aws_secret_access_key=secret_key,
        config=Config(signature_version='s3v4'), region_name='auto',
    )


# ── Image source lookup: Limitless first, Serebii fallback ─────────────────
def get_limitless_image_url(card_number):
    url = f"https://limitlesstcg.com/cards/{LIMITLESS_SET_CODE}/{card_number}"
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Referer": "https://limitlesstcg.com/"}, timeout=20)
        if r.status_code != 200:
            return None
        m = re.search(
            r'(https://limitlesstcg\.nyc3\.cdn\.digitaloceanspaces\.com/tpci/'
            + re.escape(LIMITLESS_SET_CODE) + r'/[^"\'>\s]+\.png)',
            r.text
        )
        return m.group(1) if m else None
    except Exception:
        return None


def get_serebii_image_url(card_number):
    # Confirmed working pattern (2026-07-13): unpadded number, .jpg
    return f"https://www.serebii.net/card/{SEREBII_SET_SLUG}/{card_number}.jpg"


def fetch_base_image(card_number):
    """Returns (image_bytes, source_label) or (None, reason_string)."""
    limitless_url = get_limitless_image_url(card_number)
    if limitless_url:
        try:
            r = requests.get(limitless_url, headers={"User-Agent": UA, "Referer": "https://limitlesstcg.com/"}, timeout=20)
            if r.status_code == 200 and r.content:
                return r.content, 'limitless'
        except Exception:
            pass

    serebii_url = get_serebii_image_url(card_number)
    try:
        r = requests.get(serebii_url, headers={"User-Agent": UA, "Referer": "https://www.serebii.net/"}, timeout=20)
        if r.status_code == 200 and r.content and len(r.content) > 1000:  # guard against tiny placeholder/error images
            return r.content, 'serebii'
    except Exception:
        pass

    return None, 'not found on Limitless or Serebii'


def extract_card_number(card_number_field):
    """Bible's card_number is like '048/084' or '116/084' -- pull the leading int."""
    m = re.match(r'0*(\d+)', card_number_field.strip())
    return int(m.group(1)) if m else None


def r2_key(set_code, product_id, variant_code):
    return f"cards/{set_code}/{product_id}_{variant_code or 'N'}.jpg"


def main():
    parser = argparse.ArgumentParser(description='PokeBulk SA - Download Images (Serebii/Limitless) To R2')
    parser.add_argument('--in', dest='in_path', required=True, help='Bible CSV')
    parser.add_argument('--set-code', required=True, help='Set code (e.g. PBL)')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--limit', type=int, default=None, help='Only process first N unique card numbers')
    args = parser.parse_args()

    if not os.path.isfile(args.in_path):
        print('ERROR: input file not found: ' + args.in_path)
        return

    with open(args.in_path, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    card_rows = [
        r for r in rows
        if r.get('is_card', '').strip().lower() == 'true'
        and r.get('set_code', '').strip() == args.set_code
    ]
    print(f"Card rows matching set_code={args.set_code}: {len(card_rows)}")

    # Group rows by their source card number (N/H/RH of the same print share one base photo)
    groups = {}
    for r in card_rows:
        num = extract_card_number(r.get('card_number', ''))
        if num is None:
            continue
        groups.setdefault(num, []).append(r)

    group_numbers = sorted(groups.keys())
    if args.limit:
        group_numbers = group_numbers[:args.limit]
    print(f"Unique card numbers to process: {len(group_numbers)}  |  Rows covered: {sum(len(groups[n]) for n in group_numbers)}")

    if not args.dry_run:
        s3 = get_r2_client()

    updated = failed = no_source = 0
    failures = []

    for i, num in enumerate(group_numbers, 1):
        group = groups[num]
        name = group[0].get('name', '')

        if i % 25 == 0 or i == len(group_numbers):
            print(f"  [{i}/{len(group_numbers)}] #{num} {name}")

        if args.dry_run:
            variants = [r.get('variant_code', 'N') or 'N' for r in group]
            print(f"  [DRY] #{num} {name} -- variants in bible: {variants}")
            updated += len(group)
            continue

        base_bytes, source = fetch_base_image(num)
        if base_bytes is None:
            no_source += 1
            failures.append((num, name, source))
            continue

        try:
            clean_bytes = clean_jpeg_bytes(base_bytes)
        except Exception as e:
            failed += 1
            failures.append((num, name, f'image decode failed: {e}'))
            continue

        rh_bytes = None  # computed lazily, only if a row needs it

        for r in group:
            variant = (r.get('variant_code', '') or 'N').strip()
            product_id = r.get('product_id', '').strip()
            key = r2_key(args.set_code, product_id, variant)

            try:
                if variant == 'RH':
                    if rh_bytes is None:
                        rh_bytes = apply_watermark(base_bytes, WM_TEXT_REV)
                    payload = rh_bytes
                else:
                    payload = clean_bytes

                s3.put_object(Bucket=R2_BUCKET, Key=key, Body=payload, ContentType='image/jpeg')
                updated += 1
            except Exception as e:
                failed += 1
                failures.append((num, name, f'{variant} R2 upload failed: {e}'))

        time.sleep(DELAY_SECS)

    print('\n' + '=' * 60)
    print('Done!')
    print(f"  {'Would update' if args.dry_run else 'Updated'}: {updated}")
    print(f"  No source image found: {no_source}")
    print(f"  Failed: {failed}")
    if failures:
        print('\n  Failures:')
        for num, name, reason in failures:
            print(f"    #{num} {name} -- {reason}")
    print('=' * 60)


if __name__ == '__main__':
    main()
