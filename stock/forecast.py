"""
Prep plan — "how much chicken / how many rotis should we get ready for tomorrow?"

Version 1 is deliberately simple and explainable:
    expected = average of the same weekday over the last 4 weeks
               (falls back to the average of the last 14 trading days when there is too little history)

It is isolated behind `forecast(day)` so a proper ML model (e.g. LightGBM on the data from
`python manage.py export_ml_data`) can replace it later without touching the pages.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum

from orders.models import Order, OrderItem, OrderStatus

from .models import StockItem, StockMovement

ZERO = Decimal("0")


@dataclass
class Forecast:
    day: date
    method: str
    reference_days: list
    items: list           # [{"name", "qty"}] expected menu items sold
    stock: list           # [{"item": StockItem, "need": Decimal}]
    expected_sales: Decimal
    expected_orders: Decimal


def _trading_days(before: date, lookback: int = 70) -> list[date]:
    return list(
        Order.objects.filter(business_date__lt=before, business_date__gte=before - timedelta(days=lookback))
        .exclude(status=OrderStatus.CANCELLED)
        .values_list("business_date", flat=True)
        .distinct()
        .order_by("-business_date")
    )


def reference_days(day: date) -> tuple[list[date], str]:
    trading = set(_trading_days(day))
    same_weekday = [day - timedelta(weeks=w) for w in range(1, 5) if day - timedelta(weeks=w) in trading]
    if len(same_weekday) >= 2:
        return same_weekday, f"Average of the last {len(same_weekday)} {day.strftime('%A')}s"
    recent = sorted(trading, reverse=True)[:14]
    return recent, f"Average of the last {len(recent)} trading days (not enough {day.strftime('%A')}s yet)"


def forecast(day: date) -> Forecast:
    refs, method = reference_days(day)
    n = Decimal(len(refs)) if refs else Decimal("1")
    items, stock = [], []
    sales = orders = ZERO
    if refs:
        live = Order.objects.filter(business_date__in=refs).exclude(status=OrderStatus.CANCELLED)
        agg = live.aggregate(s=Sum("total"))
        sales = (agg["s"] or ZERO) / n
        orders = Decimal(live.count()) / n
        rows = (
            OrderItem.objects.filter(order__in=live)
            .values("item_name", "variant_name")
            .annotate(q=Sum("quantity"))
            .order_by("-q")
        )
        for r in rows:
            name = r["item_name"] if (r["variant_name"] or "").lower() == "regular" else f"{r['item_name']} ({r['variant_name']})"
            items.append({"name": name, "qty": (Decimal(r["q"]) / n).quantize(Decimal("0.1"))})

        used = defaultdict(lambda: ZERO)
        for r in (
            StockMovement.objects.filter(kind=StockMovement.Kind.SALE, business_date__in=refs)
            .values("stock_item_id")
            .annotate(s=Sum("delta"))
        ):
            used[r["stock_item_id"]] = -(r["s"] or ZERO)
        for it in StockItem.objects.filter(is_active=True):
            need = (used.get(it.id, ZERO) / n).quantize(Decimal("0.001"))
            stock.append({"item": it, "need": need})
    return Forecast(
        day=day,
        method=method if refs else "No sales history yet — start billing and this fills in automatically.",
        reference_days=sorted(refs),
        items=items,
        stock=stock,
        expected_sales=sales.quantize(Decimal("0.01")),
        expected_orders=orders.quantize(Decimal("0.1")),
    )
