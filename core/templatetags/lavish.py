from decimal import Decimal

from django import template

from core.utils import inr as _inr, qty_fmt

register = template.Library()


@register.filter
def inr(value):
    return _inr(value)


@register.filter
def inr2(value):
    return _inr(value, decimals=True)


@register.filter
def qty(value, unit=""):
    return qty_fmt(value, unit)


@register.filter
def neg(value):
    return -Decimal(value or 0)


@register.filter
def get(d, key):
    try:
        return d.get(key)
    except AttributeError:
        return None


@register.filter
def pct(part, whole):
    try:
        whole = Decimal(whole)
        return 0 if not whole else round(Decimal(part) * 100 / whole)
    except Exception:
        return 0


@register.simple_tag(takes_context=True)
def qs(context, **kwargs):
    """Rebuild the current query string with some values replaced."""
    q = context["request"].GET.copy()
    for k, v in kwargs.items():
        if v in (None, ""):
            q.pop(k, None)
        else:
            q[k] = v
    return "?" + q.urlencode() if q else "?"


@register.simple_tag
def icon(name, size=""):
    from django.utils.html import format_html

    return format_html('<svg class="i {}" aria-hidden="true"><use href="#i-{}"></use></svg>', size, name)


@register.simple_tag(takes_context=True)
def active(context, *names):
    match = getattr(context["request"], "resolver_match", None)
    return "active" if match and match.url_name in names else ""
