"""
Read-only diagnostic -- samples image_urls at random from across the
ENTIRE pending list (not just the start) and checks whether each one is
actually reachable, without downloading, resizing, uploading, or writing
anything to the DB. Built to answer one question honestly: what fraction
of CatalogProduct.image_url values are genuinely dead at the source
(TCGCSV/TCGplayer's CDN), versus the ~75% failure rate seen in the first
sequential batches of mirror_images_to_r2 -- which may just be an
unlucky early cluster, not representative of the whole 236,665.

Touches nothing: no R2 client, no CatalogProduct writes, just HEAD
requests and a printed tally.

Usage:
    python manage.py sample_check_images                # 300 random samples (default)
    python manage.py sample_check_images --sample-size 500
    python manage.py sample_check_images --seed 7        # change the random sample
"""
import random

import requests
from django.core.management.base import BaseCommand

from pobusa.models import CatalogProduct

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
REFERER = "https://www.tcgplayer.com/"
DEFAULT_SAMPLE_SIZE = 300


class Command(BaseCommand):
    help = "Read-only: samples pending CatalogProduct image_urls to measure the real dead-image rate."

    def add_arguments(self, parser):
        parser.add_argument('--sample-size', type=int, default=DEFAULT_SAMPLE_SIZE)
        parser.add_argument('--seed', type=int, default=42, help='Random seed, so results are reproducible if you re-run this')

    def handle(self, *args, **options):
        sample_size = options['sample_size']
        seed = options['seed']

        urls = list(
            CatalogProduct.objects
            .exclude(image_url='')
            .filter(thumbnail_url__isnull=True)
            .values_list('image_url', flat=True)
            .distinct()
        )
        self.stdout.write(f"total pending: {len(urls)}")

        random.seed(seed)
        sample = random.sample(urls, min(sample_size, len(urls)))

        ok = denied = other = 0
        for i, url in enumerate(sample, 1):
            try:
                resp = requests.head(url, headers={'User-Agent': UA, 'Referer': REFERER}, timeout=10)
                if resp.status_code == 200:
                    ok += 1
                elif resp.status_code in (403, 404):
                    denied += 1
                else:
                    other += 1
                    self.stdout.write(f"  unexpected status {resp.status_code}: {url}")
            except Exception as e:
                other += 1
                self.stdout.write(f"  request error: {url} -- {e}")

            if i % 50 == 0:
                self.stdout.write(f"  ...{i}/{len(sample)} checked so far")

        total = len(sample)
        self.stdout.write(self.style.SUCCESS(
            f"\nSampled {total} of {len(urls)} pending images:\n"
            f"  ok:     {ok} ({ok/total*100:.1f}%)\n"
            f"  denied: {denied} ({denied/total*100:.1f}%)\n"
            f"  other:  {other} ({other/total*100:.1f}%)"
        ))
