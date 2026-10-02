import threading
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone

_local = threading.local()


# ------------------------------------------------------------- request context
def set_current_request(request):
    _local.request = request


def get_current_request():
    return getattr(_local, "request", None)


def client_ip(request) -> str | None:
    if request is None:
        return None
    fwd = request.META.get("HTTP_X_FORWARDED_FOR")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


# ------------------------------------------------------------- business day
def day_start_hour() -> int:
    from core.models import ShopSettings

    try:
        return ShopSettings.load().day_starts_at_hour
    except Exception:  # during first migrate
        return 4


def business_date_for(dt: datetime | None = None, start_hour: int | None = None) -> date:
    """The shop's trading day for a moment in time (orders at 1 AM count for yesterday)."""
    dt = timezone.localtime(dt or timezone.now())
    hour = day_start_hour() if start_hour is None else start_hour
    if dt.hour < hour:
        return (dt - timedelta(days=1)).date()
    return dt.date()


def today() -> date:
    return business_date_for()


# ------------------------------------------------------------- money
TWO = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(value or 0).quantize(TWO, rounding=ROUND_HALF_UP)


def inr(value, decimals: bool | None = None) -> str:
    """Indian-style grouping: 1,23,456.50 -> '₹1,23,456.50'. Whole rupees shown without .00."""
    v = money(value)
    neg = v < 0
    v = abs(v)
    whole, frac = f"{v:.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    show_dec = decimals if decimals is not None else frac != "00"
    s = f"₹{whole}.{frac}" if show_dec else f"₹{whole}"
    return f"-{s}" if neg else s


def qty_fmt(value, unit: str = "") -> str:
    """Trim trailing zeros: 2.500 kg -> 2.5 kg."""
    d = Decimal(value or 0).normalize()
    s = f"{d:f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return f"{s} {unit}".strip()
