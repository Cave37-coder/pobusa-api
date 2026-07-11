# PoBuSA report_models.py — v1.0.0
# Separate file so it can be merged into models.py without a big diff.
# Add "from .report_models import *" to models.py, or copy this class in directly.

from django.db import models
from .models import Store


class DailyReportFile(models.Model):
    """A PDF report generated automatically overnight, held here until someone
    clicks 'Send' in Django admin. Generation and sending are deliberately
    decoupled — automatic SMTP sends from background jobs have failed
    consistently on Railway, but manual sends via the admin button work."""
    PERIOD_CHOICES = [
        ("daily", "Daily"),
        ("monthly", "Monthly"),
    ]

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="report_files")
    period_type = models.CharField(max_length=10, choices=PERIOD_CHOICES)
    period_start = models.DateField()
    period_end = models.DateField()

    pdf_file = models.FileField(upload_to="pobusa_reports/")
    generated_at = models.DateTimeField(auto_now_add=True)

    sent = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_to = models.EmailField(blank=True)

    def __str__(self):
        return f"{self.store.name} — {self.get_period_type_display()} — {self.period_start}"
