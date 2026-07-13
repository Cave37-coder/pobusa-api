"""
One-time seed for the confirmed game shortlist. Safe to re-run -- uses
get_or_create, won't duplicate.

Usage: python manage.py seed_games

Category IDs confirmed live from https://tcgcsv.com/tcgplayer/categories
as of July 2026.
"""
from django.core.management.base import BaseCommand
from pobusa.models import Game

GAMES = [
    # (code, name, tcgcsv_category_id, sort_order)
    ("magic", "Magic: The Gathering", 1, 10),
    ("yugioh", "Yu-Gi-Oh!", 2, 20),
    ("one-piece", "One Piece Card Game", 68, 30),
    ("dragon-ball-super", "Dragon Ball Super: Masters", 27, 40),
    ("dragon-ball-fusion", "Dragon Ball Super: Fusion World", 80, 50),
    ("digimon", "Digimon Card Game", 63, 60),
    ("star-wars-unlimited", "Star Wars: Unlimited", 79, 70),
    ("gundam", "Gundam Card Game", 86, 80),
    ("riftbound", "Riftbound: League of Legends Trading Card Game", 89, 90),
    ("lorcana", "Disney Lorcana", 71, 100),
]


class Command(BaseCommand):
    help = "Seeds the confirmed Game shortlist (idempotent, safe to re-run)."

    def handle(self, *args, **options):
        for code, name, category_id, sort_order in GAMES:
            game, created = Game.objects.get_or_create(
                code=code,
                defaults={
                    "name": name,
                    "tcgcsv_category_id": category_id,
                    "sort_order": sort_order,
                    "is_active": True,
                },
            )
            status = "created" if created else "already exists"
            self.stdout.write(f"{name}: {status}")
