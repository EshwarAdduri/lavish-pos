"""UPI payment QR: any UPI app (GPay, PhonePe, Paytm…) scans it with the amount already filled in."""

from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET
from reportlab.graphics import renderSVG
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing

from .models import ShopSettings


def upi_link(shop: ShopSettings, amount: Decimal | None = None, note: str = "") -> str:
    parts = [f"pa={quote(shop.upi_id.strip(), safe='@.')}", f"pn={quote(shop.upi_name or shop.shop_name)}", "cu=INR"]
    if amount and amount > 0:
        parts.append(f"am={amount:.2f}")
    if note:
        parts.append(f"tn={quote(note[:50])}")
    return "upi://pay?" + "&".join(parts)


def qr_drawing(text: str, size: float) -> Drawing:
    widget = QrCodeWidget(text, barLevel="M")
    x1, y1, x2, y2 = widget.getBounds()
    w, h = x2 - x1, y2 - y1
    d = Drawing(size, size, transform=[size / w, 0, 0, size / h, 0, 0])
    d.add(widget)
    return d


def qr_svg(text: str, size: int = 220) -> str:
    return renderSVG.drawToString(qr_drawing(text, size))


@require_GET
def upi_qr(request):
    """/upi/qr.svg?amount=390&note=LS-000123"""
    shop = ShopSettings.load()
    if not shop.upi_id.strip():
        return HttpResponseBadRequest("Add your UPI ID in Settings first.")
    try:
        amount = Decimal(request.GET.get("amount") or "0").quantize(Decimal("0.01"))
    except InvalidOperation:
        return HttpResponseBadRequest("Bad amount")
    if amount < 0 or amount > Decimal("1000000"):
        return HttpResponseBadRequest("Bad amount")
    svg = qr_svg(upi_link(shop, amount, request.GET.get("note", "")[:40]))
    resp = HttpResponse(svg, content_type="image/svg+xml")
    resp["Cache-Control"] = "private, max-age=300"
    return resp
