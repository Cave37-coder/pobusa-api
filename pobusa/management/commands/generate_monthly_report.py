# PoBuSA management/commands/generate_monthly_report.py — v1.0.0
# Run on the 1st of each month via Railway's cron/scheduled job feature:
#   python manage.py generate_monthly_report
# Schedule: monthly, 1st at 00:15, covering the previous calendar month.
# This ONLY generates and saves the PDF — sending is a manual step via
# Django admin (see admin.py: "Send selected reports via email" action).

from django.core.management.base import BaseCommand
from django.utils import timezone

from pobusa.models import Store
from pobusa.report_builder import generate_and_save_report


class Command(BaseCommand):
    help = "Generates the monthly buy/sell PDF report for every store. Does not send it."

    def handle(self, *args, **options):
        today = timezone.now().date()

        # Previous calendar month, regardless of what day this runs on
        first_of_this_month = today.replace(day=1)
        last_day_prev_month = first_of_this_month.replace(day=1) - timezone.timedelta(days=1)
        start_date = last_day_prev_month.replace(day=1)
        end_date = last_day_prev_month
        month_label = start_date.strftime("%B %Y")

        for store in Store.objects.all():
            report = generate_and_save_report(
                store, start_date=start_date, end_date=end_date,
                period_type="monthly", period_label=f"Monthly — {month_label}",
            )
            self.stdout.write(self.style.SUCCESS(f"Generated monthly report for {store.name}: {report.pdf_file.name}"))
