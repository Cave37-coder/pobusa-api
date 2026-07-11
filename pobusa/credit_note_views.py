# PoBuSA credit_note_views.py — v1.0.0

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import CreditNote
from .credit_note_serializers import CreditNoteCreateSerializer


@api_view(["POST"])
def create_credit_note(request):
    """POST /api/pobusa/credit-notes/
    Issues a refund against a completed sale. References the original sale
    number and requires a real reason (SARS compliance — see Section 5a of
    the spec). Restocks the sold items by default unless restock: false is
    passed (e.g. for a damaged/unsellable return)."""
    serializer = CreditNoteCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    credit_note = serializer.save()

    return Response(
        {
            "credit_note_number": credit_note.credit_note_number,
            "original_sale": credit_note.original_sale.sale_number,
            "date": credit_note.date,
            "reason": credit_note.reason,
            "amount": credit_note.amount,
            "vat_amount": credit_note.vat_amount,
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
def credit_note_detail(request, credit_note_number):
    """GET /api/pobusa/credit-notes/<credit_note_number>/"""
    try:
        credit_note = CreditNote.objects.get(credit_note_number=credit_note_number)
    except CreditNote.DoesNotExist:
        return Response({"error": "Credit note not found"}, status=status.HTTP_404_NOT_FOUND)

    return Response(
        {
            "credit_note_number": credit_note.credit_note_number,
            "original_sale": credit_note.original_sale.sale_number,
            "date": credit_note.date,
            "reason": credit_note.reason,
            "amount": credit_note.amount,
            "vat_amount": credit_note.vat_amount,
        }
    )
