"""
Stock maths — the "nothing goes missing" check.

For each stock item and day:

    opening   = last physical count before today (+ any movements since that count)
    + bought  = purchases today
    − used    = what today's bills should have used (from the recipes)
    − wasted  = spoiled / thrown away (recorded by staff)
    − staff   = staff meals
    ± fixes   = corrections
    = expected closing

    counted closing − expected closing = variance
        negative  -> stock is MISSING (value = qty × cost)
        positive  -> more stock than expected (recipe quantity may be too high)
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import Q, Sum

from .models import StockCount, StockItem, StockMovement

ZERO = Decimal("0")


@dataclass
class DayStock:
    item: StockItem
    opening: Decimal = ZERO
    purchased: Decimal = ZERO
    used: Decimal = ZERO
    wasted: Decimal = ZERO
    staff_meals: Decimal = ZERO
    adjusted: Decimal = ZERO
    counted: Decimal | None = None
    count_obj: StockCount | None = field(default=None, repr=False)
    tracked: bool = True  # False until someone records a purchase / count for this item

    @property
    def expected(self) -> Decimal:
        return self.opening + self.purchased - self.used - self.wasted - self.staff_meals + self.adjusted

    @property
    def wasted_total(self) -> Decimal:
        return self.wasted + self.staff_meals

    @property
    def variance(self) -> Decimal | None:
        return None if self.counted is None else self.counted - self.expected

    @property
    def variance_value(self) -> Decimal | None:
        v = self.variance
        return None if v is None else (v * self.item.cost_per_unit).quantize(Decimal("0.01"))

    @property
    def closing(self) -> Decimal:
        return self.counted if self.counted is not None else self.expected

    @property
    def is_low(self) -> bool:
        return self.tracked and self.item.low_stock_level > 0 and self.closing <= self.item.low_stock_level


def _sum(qs) -> Decimal:
    return qs.aggregate(s=Sum("delta"))["s"] or ZERO


def opening_balance(item: StockItem, day: date) -> Decimal:
    last = StockCount.objects.filter(stock_item=item, business_date__lt=day).order_by("-business_date").first()
    if last:
        return last.counted_qty + _sum(
            StockMovement.objects.filter(stock_item=item, business_date__gt=last.business_date, business_date__lt=day)
        )
    return _sum(StockMovement.objects.filter(stock_item=item, business_date__lt=day))


def day_stock(day: date, items=None) -> list[DayStock]:
    items = list(items if items is not None else StockItem.objects.filter(is_active=True))
    if not items:
        return []
    ids = [i.id for i in items]
    sums = {
        (r["stock_item_id"], r["kind"]): r["s"]
        for r in StockMovement.objects.filter(stock_item_id__in=ids, business_date=day)
        .values("stock_item_id", "kind")
        .annotate(s=Sum("delta"))
    }
    counts = {c.stock_item_id: c for c in StockCount.objects.filter(stock_item_id__in=ids, business_date=day)}
    tracked = set(
        StockMovement.objects.filter(stock_item_id__in=ids, business_date__lte=day)
        .exclude(kind=StockMovement.Kind.SALE).values_list("stock_item_id", flat=True).distinct()
    ) | set(StockCount.objects.filter(stock_item_id__in=ids, business_date__lte=day).values_list("stock_item_id", flat=True).distinct())
    K = StockMovement.Kind
    out = []
    for it in items:
        g = lambda k: sums.get((it.id, k), ZERO) or ZERO  # noqa: E731
        c = counts.get(it.id)
        out.append(
            DayStock(
                item=it,
                opening=opening_balance(it, day),
                purchased=g(K.PURCHASE),
                used=-g(K.SALE),
                wasted=-g(K.WASTAGE),
                staff_meals=-g(K.STAFF_MEAL),
                adjusted=g(K.ADJUST),
                counted=c.counted_qty if c else None,
                count_obj=c,
                tracked=it.id in tracked,
            )
        )
    return out


def current_balance(item: StockItem, day: date) -> Decimal:
    """Expected stock right now (end of `day` so far)."""
    ds = day_stock(day, [item])[0]
    return ds.closing


def usage_between(start: date, end: date):
    """Total used by sales per stock item between two dates (inclusive)."""
    return (
        StockMovement.objects.filter(kind=StockMovement.Kind.SALE, business_date__range=(start, end))
        .values("stock_item__name", "stock_item__unit")
        .annotate(qty=Sum("delta"))
        .order_by("stock_item__sort_order")
    )


def variance_history(start: date, end: date):
    return (
        StockCount.objects.filter(business_date__range=(start, end))
        .exclude(Q(variance=0))
        .select_related("stock_item")
        .order_by("-business_date", "stock_item__sort_order")
    )
