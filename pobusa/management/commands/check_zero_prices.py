"""
Checks how many CatalogProduct rows have a missing or zero market_price --
the real-world risk being that fetch_card_data() would return market_ref=0
for a genuinely valuable item, and buy_price = 0 x buy_percent = 0, meaning
staff could be offered "R0" for something worth real money without any
warning.

Usage: python manage.py check_zero_prices
"""
from django.core.management.base import BaseCommand
from django.db.models import Q, Count
from pobusa.models import CatalogProduct


class Command(BaseCommand):
    help = "Reports how many synced products have a zero or missing market_price."

    def handle(self, *args, **options):
        total = CatalogProduct.objects.filter(is_active=True).count()
        zero_or_missing = CatalogProduct.objects.filter(is_active=True).filter(
            Q(market_price__isnull=True) | Q(market_price=0)
        )
        zero_count = zero_or_missing.count()

        self.stdout.write(f"Total active products: {total:,}")
        self.stdout.write(f"Zero/missing market_price: {zero_count:,} ({zero_count / total * 100:.1f}%)\n")

        self.stdout.write("=== By game ===")
        by_game = (
            zero_or_missing.values("game__name")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        for row in by_game:
            game_name = row["game__name"] or "(accessories / no game)"
            self.stdout.write(f"  {game_name:<45} {row['count']:>8,}")

        self.stdout.write("\n=== By product type ===")
        by_type = (
            zero_or_missing.values("product_type")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        for row in by_type:
            self.stdout.write(f"  {row['product_type']:<15} {row['count']:>8,}")

        self.stdout.write("\n=== By game + type (sealed only -- the highest-risk combination) ===")
        sealed_zero = (
            zero_or_missing.filter(product_type="sealed")
            .values("game__name")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        for row in sealed_zero:
            game_name = row["game__name"] or "(unknown)"
            self.stdout.write(f"  {game_name:<45} {row['count']:>8,}")

        self.stdout.write("\n=== Sample of 15 zero-priced sealed products ===")
        sample = zero_or_missing.filter(product_type="sealed").select_related("game")[:15]
        for p in sample:
            game_name = p.game.name if p.game else "?"
            self.stdout.write(f"  [{game_name}] {p.set_name} -- {p.name} (sku: {p.sku})")
