"""PDF receipt sized for 58 mm or 80 mm thermal paper."""

import io
from pathlib import Path

from django.utils import timezone
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from core.utils import inr

FONT_DIR = Path(__file__).resolve().parent / "fonts"
_registered = False


def _fonts():
    global _registered
    if not _registered:
        pdfmetrics.registerFont(TTFont("Dj", str(FONT_DIR / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DjB", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
        _registered = True


def _wrap(text, font, size, width):
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if pdfmetrics.stringWidth(trial, font, size) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def receipt_pdf(order, shop, width_mm=80) -> bytes:
    _fonts()
    W = width_mm * mm
    margin = 3 * mm
    inner = W - 2 * margin
    fs = 8 if width_mm == 58 else 9
    lh = fs * 1.35

    # Build a list of drawing instructions first so we know the page height.
    ops = []

    def text(t, font="Dj", size=fs, align="left"):
        for line in _wrap(t, font, size, inner):
            ops.append(("text", line, font, size, align))

    def row(left, right, font="Dj", size=fs):
        rw = pdfmetrics.stringWidth(right, font, size)
        for i, line in enumerate(_wrap(left, font, size, inner - rw - 4)):
            ops.append(("row", line, right if i == 0 else "", font, size))

    def rule():
        ops.append(("rule",))

    text(shop.shop_name, "DjB", fs + 4, "center")
    if shop.tagline:
        text(shop.tagline, "Dj", fs - 1, "center")
    if shop.address:
        text(shop.address, "Dj", fs - 1, "center")
    if shop.phone:
        text(f"Ph: {shop.phone}", "Dj", fs - 1, "center")
    rule()
    created = timezone.localtime(order.created_at)
    row(f"Bill: {order.bill_number}", f"Token #{order.token_no}", "DjB")
    row(created.strftime("%d %b %Y  %I:%M %p"), order.get_order_type_display() + (f" · {order.get_platform_display()}" if order.platform else ""))
    if order.table_no:
        text(f"Table {order.table_no}", "DjB")
    if order.customer_name:
        text(f"Customer: {order.customer_name}")
    rule()
    for it in order.items.all():
        row(f"{it.quantity} x {it.display_name}", inr(it.line_total))
        for a in it.addons.all():
            text(f"   + {a.name} ({inr(a.price)})", "Dj", fs - 1)
        if it.note:
            text(f"   note: {it.note}", "Dj", fs - 1)
    rule()
    row("Subtotal", inr(order.subtotal))
    if order.discount_amount:
        row("Discount", "-" + inr(order.discount_amount))
    row("TOTAL", inr(order.total), "DjB", fs + 3)
    paid = [p for p in order.payments.all()]
    if paid:
        rule()
        for p in paid:
            label = p.get_method_display() + (" refund" if p.amount < 0 else "")
            row(label, inr(p.amount))
            if p.cash_tendered:
                row("  Cash given / change", f"{inr(p.cash_tendered)} / {inr(p.cash_tendered - p.amount)}", "Dj", fs - 1)
        if order.balance > 0:
            row("Balance due", inr(order.balance), "DjB")
    if shop.upi_id.strip() and order.balance > 0 and order.status != "cancelled":
        from core.upi import upi_link

        rule()
        text(f"Scan to pay {inr(order.balance)} by UPI", "DjB", fs, "center")
        ops.append(("qr", upi_link(shop, order.balance, order.bill_number), (36 if width_mm == 58 else 46) * mm))
        text(shop.upi_id, "Dj", fs - 1, "center")
    if order.status == "cancelled":
        rule()
        text("*** CANCELLED ***", "DjB", fs + 2, "center")
    rule()
    if shop.receipt_footer:
        text(shop.receipt_footer, "Dj", fs, "center")

    def step_of(o):
        if o[0] == "qr":
            return o[2] + lh * 0.5
        if o[0] == "rule":
            return lh * 0.6
        if o[0] == "text" and o[3] > fs + 2:
            return lh * 1.6
        return lh

    height = 12 * mm + sum(step_of(o) for o in ops)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, height))
    c.setTitle(order.bill_number)
    y = height - 6 * mm
    for o in ops:
        if o[0] == "qr":
            from reportlab.graphics import renderPDF

            from core.upi import qr_drawing

            size = o[2]
            y -= size + lh * 0.5
            renderPDF.draw(qr_drawing(o[1], size), c, (W - size) / 2, y + lh * 0.25)
            continue
        if o[0] == "rule":
            y -= lh * 0.25
            c.setDash(1, 2)
            c.line(margin, y, W - margin, y)
            c.setDash()
            y -= lh * 0.35
            continue
        if o[0] == "text":
            _, t, font, size, align = o
            step = lh * (1.6 if size > fs + 2 else 1)
            y -= step
            c.setFont(font, size)
            if align == "center":
                c.drawCentredString(W / 2, y + (step - size) / 2, t)
            else:
                c.drawString(margin, y + (step - size) / 2, t)
        else:
            _, left, right, font, size = o
            y -= lh
            c.setFont(font, size)
            c.drawString(margin, y + (lh - size) / 2, left)
            if right:
                c.drawRightString(W - margin, y + (lh - size) / 2, right)
    c.showPage()
    c.save()
    return buf.getvalue()
