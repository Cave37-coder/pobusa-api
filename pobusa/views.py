# PoBuSA views.py — v1.5.0
# v1.5.0: card_search now returns total_matches alongside results, so the
# buy-in screen can tell staff when a search is being truncated.
# v1.4.0: emails the invoice to the seller immediately after saving, if
# seller_email was provided. Failure to send never blocks the invoice —
# it's already saved by the time the email attempt happens.

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db import transaction

from .models import Invoice, CardStockLine, SealedStockItem
from .serializers import InvoiceCreateSerializer, CardStockLineReadSerializer, SealedLineReadSerializer
from . import services
from .email_service import send_invoice_email


@api_view(["GET"])
def card_search(request):
    """GET /api/pobusa/card-search/?q=gastly+sf
    Returns matching cards for the buy-in screen's live search, matched
    against name, set name, set code, and card number together. Response
    includes total_matches so the frontend can prompt staff to narrow the
    search when results are being truncated. Never exposes anything
    beyond structural info + market_ref."""
    query = request.query_params.get("q", "")
    data = services.search_cards(query)
    return Response(data)


@api_view(["GET"])
def card_lookup(request):
    """GET /api/pobusa/card-lookup/?card_id=sv09-074-rev
    Powers the buy-in screen's card search box — returns name/set/condition
    options plus the current market_ref (pulled from pokemart-api's already
    TCGCSV-synced price), so staff see the %-adjuster live before finalising
    the line. Read-only, no stock is created here."""
    card_id = request.query_params.get("card_id")
    if not card_id:
        return Response({"error": "card_id is required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        card_data = services.fetch_card_data(card_id)
    except ValueError as e:
        return Response({"error": str(e)}, status=status.HTTP_404_NOT_FOUND)

    return Response(card_data)


@api_view(["POST"])
@transaction.atomic
def create_buy_in_invoice(request):
    """POST /api/pobusa/invoices/
    Creates a buy-in ticket: one Invoice + many CardStockLine/SealedStockItem
    rows, with buy/sell prices calculated and snapshotted server-side."""
    serializer = InvoiceCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    invoice = serializer.save()

    card_lines = CardStockLine.objects.filter(invoice=invoice)
    sealed_lines = SealedStockItem.objects.filter(invoice=invoice)

    email_sent = False
    email_error = None
    if invoice.seller_email:
        try:
            send_invoice_email(invoice, card_lines, sealed_lines)
            email_sent = True
        except Exception as e:
            email_error = str(e)  # invoice already saved — a failed email never blocks this

    return Response(
        {
            "invoice_number": invoice.invoice_number,
            "total_paid": invoice.total_paid,
            "card_lines": CardStockLineReadSerializer(card_lines, many=True).data,
            "sealed_lines": SealedLineReadSerializer(sealed_lines, many=True).data,
            "email_sent": email_sent,
            "email_error": email_error,
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
def invoice_detail(request, invoice_number):
    """GET /api/pobusa/invoices/<invoice_number>/
    Retrieve a buy-in ticket for review/print — staff-facing, shows full pricing detail."""
    try:
        invoice = Invoice.objects.get(invoice_number=invoice_number)
    except Invoice.DoesNotExist:
        return Response({"error": "Invoice not found"}, status=status.HTTP_404_NOT_FOUND)

    card_lines = CardStockLine.objects.filter(invoice=invoice)
    sealed_lines = SealedStockItem.objects.filter(invoice=invoice)
    return Response(
        {
            "invoice_number": invoice.invoice_number,
            "date": invoice.date,
            "seller_name": invoice.seller_name,
            "total_paid": invoice.total_paid,
            "voided": invoice.voided,
            "card_lines": CardStockLineReadSerializer(card_lines, many=True).data,
            "sealed_lines": SealedLineReadSerializer(sealed_lines, many=True).data,
        }
    )
