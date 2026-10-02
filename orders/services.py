"""
All order / payment rules live here, on the server.

The phone only sends "which sizes, how many, which extras". Prices, totals, bill numbers,
limits and stock usage are all worked out here from the real database values, so nobody
can change a price from their browser.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from core import audit
from core.models import ShopSettings
from core.utils import business_date_for, money
from menu.models import Addon, Variant
from stock.models import RecipeLine, StockMovement

from .models import (
    ApprovalRequest,
    Counter,
    Order,
    OrderItem,
    OrderItemAddon,
    OrderStatus,
    OrderType,
    Payment,
    PaymentMethod,
    Platform,
)

MAX_QTY_PER_LINE = 50
MAX_LINES = 60


class OrderError(Exception):
    """A rule was broken. The message is shown to the user as-is."""


@dataclass
class Line:
    variant: Variant
    quantity: int
    addons: list
    note: str


# ------------------------------------------------------------------ helpers
def _dec(value, field="amount") -> Decimal:
    try:
        d = Decimal(str(value if value not in (None, "") else "0"))
    except (InvalidOperation, ValueError):
        raise OrderError(f"Invalid {field}.")
    if d.is_nan() or d.is_infinite():
        raise OrderError(f"Invalid {field}.")
    return money(d)


def next_number(key: str) -> int:
    """Increase a counter safely, even when two devices save at the same moment."""
    counter, _ = Counter.objects.select_for_update().get_or_create(key=key)
    Counter.objects.filter(pk=counter.pk).update(value=F("value") + 1)
    counter.refresh_from_db()
    return counter.value


def _parse_lines(raw_lines) -> list[Line]:
    if not isinstance(raw_lines, list) or not raw_lines:
        raise OrderError("The order has no items.")
    if len(raw_lines) > MAX_LINES:
        raise OrderError("Too many lines in one order.")

    variant_ids, addon_ids = set(), set()
    for raw in raw_lines:
        try:
            variant_ids.add(int(raw["variant_id"]))
            addon_ids.update(int(a) for a in raw.get("addon_ids", []) or [])
        except (KeyError, TypeError, ValueError):
            raise OrderError("Bad item data. Please refresh the page.")

    variants = {
        v.id: v
        for v in Variant.objects.select_related("item", "item__category")
        .prefetch_related("item__addons")
        .filter(id__in=variant_ids)
    }
    addons = {a.id: a for a in Addon.objects.filter(id__in=addon_ids)}

    lines = []
    for raw in raw_lines:
        v = variants.get(int(raw["variant_id"]))
        if v is None or not v.is_active or not v.item.is_active or not v.item.category.is_active:
            raise OrderError("An item in the cart is no longer on the menu. Please refresh.")
        if v.item.is_sold_out:
            raise OrderError(f"{v.item.name} is sold out.")
        try:
            qty = int(raw.get("qty", 1))
        except (TypeError, ValueError):
            raise OrderError("Bad quantity.")
        if qty < 1 or qty > MAX_QTY_PER_LINE:
            raise OrderError(f"Quantity must be between 1 and {MAX_QTY_PER_LINE}.")
        allowed = {a.id for a in v.item.addons.all()}
        line_addons = []
        for aid in raw.get("addon_ids", []) or []:
            a = addons.get(int(aid))
            if a is None or not a.is_active or a.id not in allowed:
                raise OrderError(f"That extra is not available for {v.item.name}.")
            line_addons.append(a)
        lines.append(Line(v, qty, line_addons, str(raw.get("note", ""))[:100]))
    return lines


def _write_items(order: Order, lines: list[Line]) -> Decimal:
    subtotal = Decimal("0")
    for ln in lines:
        addons_price = sum((a.price for a in ln.addons), Decimal("0"))
        line_total = (ln.variant.price + addons_price) * ln.quantity
        oi = OrderItem.objects.create(
            order=order,
            item=ln.variant.item,
            variant=ln.variant,
            item_name=ln.variant.item.name,
            variant_name=ln.variant.name,
            category_name=ln.variant.item.category.name,
            food_type=ln.variant.item.food_type,
            unit_price=ln.variant.price,
            addons_price=addons_price,
            quantity=ln.quantity,
            line_total=line_total,
            note=ln.note,
        )
        for a in ln.addons:
            OrderItemAddon.objects.create(order_item=oi, addon=a, name=a.name, price=a.price)
        subtotal += line_total
    return money(subtotal)


def record_stock_usage(order: Order, lines: list[Line] | None = None):
    """Deduct chicken, rotis, etc. used by this order (replaces any earlier usage for it)."""
    StockMovement.objects.filter(order=order, kind=StockMovement.Kind.SALE).delete()
    if order.status == OrderStatus.CANCELLED:
        return
    usage: dict = defaultdict(Decimal)
    stock_items = {}
    items = order.items.prefetch_related("addons").all()
    variant_ids = {i.variant_id for i in items if i.variant_id}
    addon_ids = {a.addon_id for i in items for a in i.addons.all() if a.addon_id}
    by_variant, by_addon = defaultdict(list), defaultdict(list)
    for r in RecipeLine.objects.select_related("stock_item").filter(variant_id__in=variant_ids):
        by_variant[r.variant_id].append(r)
    for r in RecipeLine.objects.select_related("stock_item").filter(addon_id__in=addon_ids):
        by_addon[r.addon_id].append(r)
    for i in items:
        for r in by_variant.get(i.variant_id, []):
            usage[r.stock_item_id] += r.quantity * i.quantity
            stock_items[r.stock_item_id] = r.stock_item
        for a in i.addons.all():
            for r in by_addon.get(a.addon_id, []):
                usage[r.stock_item_id] += r.quantity * i.quantity
                stock_items[r.stock_item_id] = r.stock_item
    StockMovement.objects.bulk_create(
        [
            StockMovement(
                stock_item=stock_items[sid],
                kind=StockMovement.Kind.SALE,
                delta=-qty,
                unit_cost=stock_items[sid].cost_per_unit,
                business_date=order.business_date,
                order=order,
                note=order.bill_number,
                created_by=order.created_by,
            )
            for sid, qty in usage.items()
            if qty
        ]
    )


def _check_staff_limits(user, shop: ShopSettings, exclude_order: Order | None = None):
    if user.is_owner:
        return
    now = timezone.now()
    recent = Order.objects.filter(created_by=user, created_at__gte=now - timedelta(seconds=60)).count()
    if exclude_order is None and recent >= shop.max_orders_per_minute:
        raise OrderError("Too many orders in one minute. Please wait a moment.")
    open_qs = Order.objects.filter(
        created_by=user,
        status__in=[OrderStatus.PENDING, OrderStatus.PREPARING, OrderStatus.READY],
        paid_amount__lt=F("total"),
    )
    if exclude_order is not None:
        open_qs = open_qs.exclude(pk=exclude_order.pk)
    if exclude_order is None and open_qs.count() >= shop.max_open_orders_per_staff:
        raise OrderError(
            f"You already have {shop.max_open_orders_per_staff} unpaid orders open. "
            "Collect payment or close some first."
        )


def _check_discount(user, shop: ShopSettings, subtotal: Decimal, discount: Decimal):
    if discount < 0:
        raise OrderError("Discount cannot be negative.")
    if discount > subtotal:
        raise OrderError("Discount cannot be more than the bill.")
    if user.is_owner or discount == 0:
        return
    cap = money(subtotal * Decimal(shop.staff_max_discount_percent) / 100)
    if discount > cap:
        raise OrderError(
            f"Staff can give up to {shop.staff_max_discount_percent}% discount. "
            "Save the order without it and tap “Request discount” for the owner to approve."
        )


def _clean_header(payload: dict) -> dict:
    order_type = payload.get("order_type") or OrderType.TAKEAWAY
    if order_type not in OrderType.values:
        raise OrderError("Unknown order type.")
    platform = payload.get("platform") or ""
    if platform not in Platform.values:
        raise OrderError("Unknown platform.")
    if order_type != OrderType.ONLINE:
        platform = ""
    return {
        "order_type": order_type,
        "platform": platform,
        "customer_name": str(payload.get("customer_name", ""))[:60].strip(),
        "customer_phone": str(payload.get("customer_phone", ""))[:20].strip(),
        "note": str(payload.get("note", ""))[:200].strip(),
        "discount_note": str(payload.get("discount_note", ""))[:100].strip(),
    }


# ------------------------------------------------------------------ public API
@transaction.atomic
def create_order(user, device, payload: dict) -> Order:
    shop = ShopSettings.load()
    _check_staff_limits(user, shop)
    header = _clean_header(payload)
    lines = _parse_lines(payload.get("lines"))
    discount = _dec(payload.get("discount_amount"), "discount")

    now = timezone.now()
    bdate = business_date_for(now, shop.day_starts_at_hour)
    bill_no = next_number("bill")
    order = Order.objects.create(
        bill_no=bill_no,
        bill_number=f"{shop.bill_prefix}-{bill_no:06d}",
        token_no=next_number(f"token:{bdate.isoformat()}"),
        business_date=bdate,
        created_by=user,
        device=device,
        created_at=now,
        **header,
    )
    subtotal = _write_items(order, lines)
    _check_discount(user, shop, subtotal, discount)
    order.subtotal = subtotal
    order.discount_amount = discount
    order.total = subtotal - discount
    order.save()
    record_stock_usage(order)

    pay = payload.get("payment")
    if pay:
        add_payment(order, user, device, pay.get("method"), pay.get("amount") or order.total,
                    cash_tendered=pay.get("cash_tendered"), reference=pay.get("reference", ""))
        order.refresh_from_db()
    return order


@transaction.atomic
def update_order(order: Order, user, device, payload: dict) -> Order:
    order = Order.objects.select_for_update().get(pk=order.pk)
    shop = ShopSettings.load()
    if order.status == OrderStatus.CANCELLED:
        raise OrderError("A cancelled order cannot be edited.")
    if not user.is_owner:
        if order.paid_amount > 0:
            raise OrderError("This order is already paid. Only the owner can change it.")
        if order.status not in (OrderStatus.PENDING, OrderStatus.PREPARING):
            raise OrderError("Only pending or preparing orders can be edited.")
    try:
        expected = int(payload.get("version", order.version))
    except (TypeError, ValueError):
        expected = order.version
    if expected != order.version:
        raise OrderError("Someone else changed this order just now. Please reopen it and try again.")

    header = _clean_header(payload)
    lines = _parse_lines(payload.get("lines"))
    discount = _dec(payload.get("discount_amount"), "discount")

    before = {
        "items": [str(i) for i in order.items.all()],
        "total": str(order.total),
    }
    order.items.all().delete()
    subtotal = _write_items(order, lines)
    # An already-approved discount is kept for staff edits.
    if not user.is_owner and discount != order.discount_amount:
        _check_discount(user, shop, subtotal, discount)
    elif discount > subtotal:
        raise OrderError("Discount cannot be more than the bill.")
    for k, v in header.items():
        setattr(order, k, v)
    order.subtotal = subtotal
    order.discount_amount = discount
    order.total = subtotal - discount
    order.version += 1
    order.save()
    record_stock_usage(order)
    audit.log("edit items", order, {
        "items": [before["items"], [str(i) for i in order.items.all()]],
        "total": [before["total"], str(order.total)],
    })
    return order


def _refresh_paid(order: Order):
    paid = order.payments.aggregate(s=Sum("amount"))["s"] or Decimal("0")
    Order.objects.filter(pk=order.pk).update(paid_amount=paid, updated_at=timezone.now())
    order.paid_amount = paid


@transaction.atomic
def add_payment(order: Order, user, device, method, amount, cash_tendered=None, reference="", note="") -> Payment:
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status == OrderStatus.CANCELLED:
        raise OrderError("This order is cancelled.")
    if method not in PaymentMethod.values:
        raise OrderError("Choose cash, UPI, card or online.")
    amount = _dec(amount)
    if amount <= 0:
        raise OrderError("Amount must be more than zero.")
    if amount > order.balance:
        raise OrderError(f"Only ₹{order.balance} is due on this bill.")
    tendered = None
    if method == PaymentMethod.CASH and cash_tendered not in (None, ""):
        tendered = _dec(cash_tendered, "cash given")
        if tendered < amount:
            raise OrderError("Cash given is less than the amount.")
    p = Payment.objects.create(
        order=order,
        method=method,
        amount=amount,
        cash_tendered=tendered,
        reference=str(reference or "")[:60],
        note=str(note or "")[:120],
        received_by=user,
        device=device,
        business_date=business_date_for(),
    )
    _refresh_paid(order)
    return p


@transaction.atomic
def refund(order: Order, user, device, method, amount, note="") -> Payment:
    if not user.is_owner:
        raise OrderError("Only the owner can give refunds.")
    order = Order.objects.select_for_update().get(pk=order.pk)
    amount = _dec(amount)
    if amount <= 0:
        raise OrderError("Refund must be more than zero.")
    if amount > order.paid_amount:
        raise OrderError("Refund is more than what was paid.")
    if method not in PaymentMethod.values:
        raise OrderError("Choose a refund method.")
    p = Payment.objects.create(
        order=order, method=method, amount=-amount, note=f"Refund. {note}"[:120],
        received_by=user, device=device, business_date=business_date_for(),
    )
    _refresh_paid(order)
    return p


STATUS_FLOW = {
    OrderStatus.PENDING: [OrderStatus.PREPARING, OrderStatus.READY, OrderStatus.COMPLETED],
    OrderStatus.PREPARING: [OrderStatus.READY, OrderStatus.COMPLETED, OrderStatus.PENDING],
    OrderStatus.READY: [OrderStatus.COMPLETED, OrderStatus.PREPARING],
    OrderStatus.COMPLETED: [OrderStatus.READY],
}


@transaction.atomic
def set_status(order: Order, user, status: str) -> Order:
    order = Order.objects.select_for_update().get(pk=order.pk)
    if status not in STATUS_FLOW.get(order.status, []):
        raise OrderError(f"Cannot move from {order.get_status_display()} to that status.")
    if status == OrderStatus.COMPLETED and order.balance > 0:
        raise OrderError(f"₹{order.balance} is still unpaid. Take payment before completing.")
    now = timezone.now()
    order.status = status
    if status == OrderStatus.PREPARING and not order.preparing_at:
        order.preparing_at = now
    if status == OrderStatus.READY and not order.ready_at:
        order.ready_at = now
    if status == OrderStatus.COMPLETED:
        order.completed_at = now
    order.save()
    return order


@transaction.atomic
def cancel_order(order: Order, user, device, reason: str) -> Order:
    """Owner only. Puts stock back and refunds anything paid, by the same method."""
    if not user.is_owner:
        raise OrderError("Only the owner can cancel. Use “Request cancel”.")
    reason = (reason or "").strip()
    if not reason:
        raise OrderError("Please give a reason for cancelling.")
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status == OrderStatus.CANCELLED:
        raise OrderError("Already cancelled.")
    by_method = order.payments.values("method").annotate(s=Sum("amount"))
    for row in by_method:
        if row["s"] and row["s"] > 0:
            Payment.objects.create(
                order=order, method=row["method"], amount=-row["s"], note="Refund on cancel",
                received_by=user, device=device, business_date=business_date_for(),
            )
    _refresh_paid(order)
    order.status = OrderStatus.CANCELLED
    order.cancelled_at = timezone.now()
    order.cancelled_by = user
    order.cancel_reason = reason[:200]
    order.save()
    record_stock_usage(order)  # cancelled -> usage removed, stock goes back
    order.approvals.filter(status=ApprovalRequest.Status.PENDING, kind=ApprovalRequest.Kind.CANCEL).update(
        status=ApprovalRequest.Status.APPROVED, decided_by=user, decided_at=timezone.now(),
        decision_note="Cancelled directly by owner",
    )
    return order


def request_approval(order: Order, user, kind: str, reason: str, discount_amount=None) -> ApprovalRequest:
    reason = (reason or "").strip()
    if not reason:
        raise OrderError("Please write a reason.")
    if order.status == OrderStatus.CANCELLED:
        raise OrderError("This order is already cancelled.")
    if kind not in ApprovalRequest.Kind.values:
        raise OrderError("Unknown request.")
    if order.approvals.filter(kind=kind, status=ApprovalRequest.Status.PENDING).exists():
        raise OrderError("A request for this is already waiting for the owner.")
    disc = None
    if kind == ApprovalRequest.Kind.DISCOUNT:
        disc = _dec(discount_amount, "discount")
        if disc <= 0 or disc > order.subtotal:
            raise OrderError("Enter a discount between ₹1 and the bill amount.")
        if order.paid_amount > order.subtotal - disc:
            raise OrderError("The bill is already paid; ask the owner for a refund instead.")
    return ApprovalRequest.objects.create(
        kind=kind, order=order, requested_by=user, reason=reason[:200], discount_amount=disc
    )


@transaction.atomic
def decide(req: ApprovalRequest, user, device, approve: bool, note: str = "") -> ApprovalRequest:
    if not user.is_owner:
        raise OrderError("Only the owner can approve.")
    req = ApprovalRequest.objects.select_for_update().get(pk=req.pk)
    if req.status != ApprovalRequest.Status.PENDING:
        raise OrderError("This request was already handled.")
    if approve:
        if req.kind == ApprovalRequest.Kind.CANCEL:
            if req.order.status != OrderStatus.CANCELLED:
                cancel_order(req.order, user, device, f"{req.reason} (requested by {req.requested_by})")
        elif req.kind == ApprovalRequest.Kind.DISCOUNT:
            order = Order.objects.select_for_update().get(pk=req.order_id)
            if order.paid_amount > order.subtotal - req.discount_amount:
                raise OrderError("Already paid more than the discounted total; use a refund instead.")
            order.discount_amount = req.discount_amount
            order.discount_note = f"Approved: {req.reason}"[:100]
            order.total = order.subtotal - req.discount_amount
            order.version += 1
            order.save()
    req.status = ApprovalRequest.Status.APPROVED if approve else ApprovalRequest.Status.REJECTED
    req.decided_by = user
    req.decided_at = timezone.now()
    req.decision_note = (note or "")[:200]
    req.save()
    return req
