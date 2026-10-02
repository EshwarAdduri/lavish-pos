"""Money maths: daily summary, cash drawer, and period analytics."""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce, ExtractHour, ExtractIsoWeekDay
from django.utils import timezone

from orders.models import Order, OrderItem, OrderStatus, Payment, PaymentMethod

from .models import DayClose, Expense

ZERO = Decimal("0")
DEC = DecimalField(max_digits=12, decimal_places=2)


def _s(qs, field="amount"):
    return qs.aggregate(s=Coalesce(Sum(field), Value(ZERO, output_field=DEC)))["s"]


@dataclass
class DaySummary:
    day: date
    orders_count: int
    cancelled_count: int
    sales: Decimal          # value of all non-cancelled bills
    discounts: Decimal
    collected: Decimal      # payments received (net of refunds)
    by_method: dict         # method -> amount
    unpaid: Decimal
    expenses: Decimal
    expenses_from_drawer: Decimal
    opening_cash: Decimal
    expected_cash: Decimal
    avg_bill: Decimal

    @property
    def profit_estimate(self):
        return self.sales - self.expenses


def previous_closing_cash(day: date) -> Decimal:
    prev = DayClose.objects.filter(business_date__lt=day).order_by("-business_date").first()
    return prev.closing_balance if prev else ZERO


def day_summary(day: date) -> DaySummary:
    orders = Order.objects.filter(business_date=day)
    live = orders.exclude(status=OrderStatus.CANCELLED)
    pays = Payment.objects.filter(business_date=day)
    by_method = OrderedDict((m.value, ZERO) for m in PaymentMethod)
    for r in pays.values("method").annotate(s=Sum("amount")):
        by_method[r["method"]] = r["s"] or ZERO
    exp = Expense.objects.filter(business_date=day)
    drawer_exp = _s(exp.filter(paid_from=Expense.PaidFrom.DRAWER))
    agg = live.aggregate(
        n=Count("id"),
        sales=Coalesce(Sum("total"), Value(ZERO, output_field=DEC)),
        disc=Coalesce(Sum("discount_amount"), Value(ZERO, output_field=DEC)),
        unpaid=Coalesce(Sum(F("total") - F("paid_amount"), filter=Q(paid_amount__lt=F("total"))), Value(ZERO, output_field=DEC)),
    )
    close = DayClose.objects.filter(business_date=day).first()
    opening = close.opening_cash if close else previous_closing_cash(day)
    n = agg["n"]
    return DaySummary(
        day=day,
        orders_count=n,
        cancelled_count=orders.filter(status=OrderStatus.CANCELLED).count(),
        sales=agg["sales"],
        discounts=agg["disc"],
        collected=sum(by_method.values(), ZERO),
        by_method=by_method,
        unpaid=agg["unpaid"],
        expenses=_s(exp),
        expenses_from_drawer=drawer_exp,
        opening_cash=opening,
        expected_cash=opening + by_method[PaymentMethod.CASH] - drawer_exp,
        avg_bill=(agg["sales"] / n).quantize(Decimal("0.01")) if n else ZERO,
    )


# ---------------------------------------------------------------- analytics
def period(start: date, end: date):
    """Everything the dashboard needs for a date range (inclusive)."""
    live = Order.objects.filter(business_date__range=(start, end)).exclude(status=OrderStatus.CANCELLED)
    days = (end - start).days + 1

    daily = {start + timedelta(d): {"sales": ZERO, "orders": 0} for d in range(days)}
    for r in live.values("business_date").annotate(s=Sum("total"), n=Count("id")):
        daily[r["business_date"]] = {"sales": r["s"] or ZERO, "orders": r["n"]}

    tz = timezone.get_current_timezone()
    by_hour = {h: ZERO for h in range(24)}
    for r in live.annotate(h=ExtractHour("created_at", tzinfo=tz)).values("h").annotate(s=Sum("total")):
        by_hour[r["h"]] = r["s"] or ZERO

    by_weekday = {d: {"sales": ZERO, "orders": 0} for d in range(1, 8)}
    for r in live.annotate(w=ExtractIsoWeekDay("business_date")).values("w").annotate(s=Sum("total"), n=Count("id")):
        by_weekday[r["w"]] = {"sales": r["s"] or ZERO, "orders": r["n"]}

    items = OrderItem.objects.filter(order__in=live)
    top_items = list(
        items.values("item_name", "variant_name")
        .annotate(qty=Sum("quantity"), amount=Sum("line_total"))
        .order_by("-qty")[:15]
    )
    by_category = list(items.values("category_name").annotate(qty=Sum("quantity"), amount=Sum("line_total")).order_by("-amount"))
    by_staff = list(
        live.values("created_by__username", "created_by__display_name")
        .annotate(n=Count("id"), s=Sum("total"))
        .order_by("-s")
    )
    by_type = list(live.values("order_type").annotate(n=Count("id"), s=Sum("total")).order_by("-s"))
    pays = Payment.objects.filter(business_date__range=(start, end))
    by_method = {m.value: ZERO for m in PaymentMethod}
    for r in pays.values("method").annotate(s=Sum("amount")):
        by_method[r["method"]] = r["s"] or ZERO
    expenses = Expense.objects.filter(business_date__range=(start, end))
    exp_by_cat = list(expenses.values("category").annotate(s=Sum("amount")).order_by("-s"))

    totals = live.aggregate(
        sales=Coalesce(Sum("total"), Value(ZERO, output_field=DEC)),
        n=Count("id"),
        disc=Coalesce(Sum("discount_amount"), Value(ZERO, output_field=DEC)),
    )
    exp_total = _s(expenses)
    return {
        "start": start,
        "end": end,
        "days": days,
        "sales": totals["sales"],
        "orders": totals["n"],
        "discounts": totals["disc"],
        "avg_bill": (totals["sales"] / totals["n"]).quantize(Decimal("0.01")) if totals["n"] else ZERO,
        "expenses": exp_total,
        "profit": totals["sales"] - exp_total,
        "cancelled": Order.objects.filter(business_date__range=(start, end), status=OrderStatus.CANCELLED).count(),
        "daily": daily,
        "by_hour": by_hour,
        "by_weekday": by_weekday,
        "top_items": top_items,
        "by_category": by_category,
        "by_staff": by_staff,
        "by_type": by_type,
        "by_method": by_method,
        "exp_by_cat": exp_by_cat,
    }
