# PoBuSA management/commands/generate_daily_report.py — v1.0.0
# Run nightly via Railway's cron/scheduled job feature:
#   python manage.py generate_daily_report
# Schedule: once daily, e.g. 23:55, so it captures the full day's trading.
# This ONLY generates and saves the PDF — sending is a manual step via
# Django admin (see admin.py: "Send selected reports via email" action),
# since automated SMTP sends from background jobs have failed consistently.

from django.core.management.base import BaseCommand
from django.utils import timezone

from pobusa.models import Store
from pobusa.report_builder import update_daily_summary, generate_and_save_report


class Command(BaseCommand):
    help = "Generates the daily buy/sell PDF report for every store. Does not send it."

    def handle(self, *args, **options):
        today = timezone.now().date()

        for store in Store.objects.all():
            update_daily_summary(store, today)  # keeps DailySalesSummary current
            report = generate_and_save_report(
                store, start_date=today, end_date=today,
                period_type="daily", period_label="Daily",
            )
            self.stdout.write(self.style.SUCCESS(f"Generated daily report for {store.name}: {report.pdf_file.name}"))
