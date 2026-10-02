from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING

from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Prefetch, Sum
from django.shortcuts import get_object_or_404, redirect, render

from core.permissions import owner_required, recorder_required
from core.utils import money, today
from menu.models import Addon, Category, Item, Variant
from money.models import Expense

from . import services
from .forecast import forecast
from .models import RecipeLine, StockCount, StockItem, StockMovement


def _day(request, key="date"):
    try:
        return date.fromisoformat(request.GET.get(key, "")) if request.GET.get(key) else today()
    except ValueError:
        return today()


def overview(request):
    d = _day(request)
    rows = services.day_stock(d)
    loss = sum((r.variance_value for r in rows if r.variance_value is not None and r.variance_value < 0), Decimal("0"))
    unverified = RecipeLine.objects.filter(verified=False).count()
    return render(
        request,
        "stock/overview.html",
        {
            "day": d,
            "is_today": d == today(),
            "prev_day": d - timedelta(days=1),
            "next_day": d + timedelta(days=1),
            "rows": rows,
            "loss": loss,
            "unverified": unverified,
            "not_started": [r.item.name for r in rows if not r.tracked],
            "recent": StockMovement.objects.exclude(kind=StockMovement.Kind.SALE)
            .select_related("stock_item", "created_by")[:12],
        },
    )


# ------------------------------------------------------------------ record movements
class MovementForm(forms.Form):
    stock_item = forms.ModelChoiceField(queryset=StockItem.objects.filter(is_active=True))
    quantity = forms.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal("0.001"),
                                  widget=forms.NumberInput(attrs={"step": "0.001", "inputmode": "decimal"}))
    total_cost = forms.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0"), required=False,
                                    label="Total paid (₹)", widget=forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal"}))
    paid_from = forms.ChoiceField(choices=Expense.PaidFrom.choices, initial=Expense.PaidFrom.DRAWER, required=False)
    direction = forms.ChoiceField(choices=[("in", "Add (found more)"), ("out", "Remove (less than system)")], required=False)
    business_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    note = forms.CharField(max_length=150, required=False)


KIND_TITLES = {
    "purchase": ("Stock purchase", "Chicken, rotis or anything you bought. The cost is also saved as an expense."),
    "wastage": ("Wastage / spoiled", "Burnt, spoiled, dropped or thrown away."),
    "staff_meal": ("Staff meal", "Food made for staff (not sold)."),
    "adjust": ("Correction", "Fix a mistake in stock. Prefer the daily count for normal differences."),
}


@recorder_required
def record(request, kind):
    if kind not in KIND_TITLES:
        return redirect("stock")
    if kind == "adjust" and not request.user.is_owner:
        messages.error(request, "Only the owner can make corrections.")
        return redirect("stock")
    initial = {"business_date": today(), "stock_item": request.GET.get("item")}
    form = MovementForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        it = cd["stock_item"]
        qty = cd["quantity"]
        with transaction.atomic():
            unit_cost = it.cost_per_unit
            if kind == "purchase":
                delta = qty
                total = money(cd.get("total_cost") or 0)
                if total > 0:
                    unit_cost = money(total / qty)
                    it.cost_per_unit = unit_cost
                    it.save()
            elif kind == "adjust":
                delta = qty if cd.get("direction") == "in" else -qty
            else:
                delta = -qty
            mv = StockMovement.objects.create(
                stock_item=it, kind=kind, delta=delta, unit_cost=unit_cost, business_date=cd["business_date"],
                note=cd.get("note", ""), created_by=request.user,
            )
            if kind == "purchase" and (cd.get("total_cost") or 0) > 0:
                Expense.objects.create(
                    business_date=cd["business_date"], category=Expense.Category.STOCK,
                    description=f"{it.name} {qty.normalize():f} {it.unit}" + (f" — {cd['note']}" if cd.get("note") else ""),
                    amount=money(cd["total_cost"]), paid_from=cd.get("paid_from") or Expense.PaidFrom.DRAWER,
                    stock_movement=mv, created_by=request.user,
                )
        messages.success(request, f"Saved: {mv.get_kind_display()} — {qty.normalize():f} {it.unit} {it.name}.")
        return redirect(f"/stock/?date={cd['business_date'].isoformat()}")
    title, help_text = KIND_TITLES[kind]
    return render(request, "stock/record.html", {"form": form, "kind": kind, "title": title, "help_text": help_text})


@owner_required
def movement_delete(request, pk):
    mv = get_object_or_404(StockMovement, pk=pk)
    if mv.kind == StockMovement.Kind.SALE:
        messages.error(request, "Sale usage is automatic — edit or cancel the order instead.")
        return redirect("stock_history")
    if request.method == "POST":
        with transaction.atomic():
            exp = getattr(mv, "expense", None)
            if exp:
                exp.delete()
            mv.delete()
        messages.success(request, "Entry deleted (kept in the audit log).")
        return redirect("stock_history")
    return render(request, "confirm.html", {"title": "Delete stock entry?", "text": str(mv), "back": "/stock/history/"})


def history(request):
    qs = StockMovement.objects.select_related("stock_item", "created_by", "order")
    kind = request.GET.get("kind", "")
    item = request.GET.get("item", "")
    if kind:
        qs = qs.filter(kind=kind)
    else:
        qs = qs.exclude(kind=StockMovement.Kind.SALE)
    if item:
        qs = qs.filter(stock_item_id=item)
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "stock/history.html", {
        "page": page, "kind": kind, "item": item, "kinds": StockMovement.Kind.choices, "items": StockItem.objects.all(),
    })


# ------------------------------------------------------------------ stock items & recipes (owner)
class StockItemForm(forms.ModelForm):
    class Meta:
        model = StockItem
        fields = ["name", "unit", "cost_per_unit", "low_stock_level", "is_key", "count_daily", "sort_order", "is_active"]


@owner_required
def items(request):
    return render(request, "stock/items.html", {"items": StockItem.objects.all()})


@owner_required
def item_edit(request, pk=None):
    obj = get_object_or_404(StockItem, pk=pk) if pk else None
    form = StockItemForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Stock item saved.")
        return redirect("stock_items")
    return render(request, "menu/simple_form.html", {"form": form, "obj": obj, "title": "Stock item", "back": "/stock/items/"})


@owner_required
def recipes(request):
    """One big grid: rows = menu sizes & extras, columns = stock items. Type how much each one uses."""
    stock_items = list(StockItem.objects.filter(is_active=True))
    variants = list(
        Variant.objects.filter(is_active=True, item__is_active=True)
        .select_related("item", "item__category")
        .order_by("item__category__sort_order", "item__sort_order", "item__name", "sort_order")
    )
    addons = list(Addon.objects.filter(is_active=True))
    lines = list(RecipeLine.objects.all())
    by_v = {(r.variant_id, r.stock_item_id): r for r in lines if r.variant_id}
    by_a = {(r.addon_id, r.stock_item_id): r for r in lines if r.addon_id}

    if request.method == "POST":
        changed = 0
        with transaction.atomic():
            for kind, objs, existing, fk in (("v", variants, by_v, "variant"), ("a", addons, by_a, "addon")):
                for o in objs:
                    for s in stock_items:
                        raw = request.POST.get(f"{kind}-{o.id}-{s.id}", "").strip()
                        cur = existing.get((o.id, s.id))
                        try:
                            val = Decimal(raw) if raw else Decimal("0")
                        except Exception:
                            continue
                        if val < 0:
                            continue
                        if val == 0:
                            if cur:
                                cur.delete()
                                changed += 1
                            continue
                        if cur:
                            if cur.quantity != val or not cur.verified:
                                cur.quantity = val
                                cur.verified = True
                                cur.save()
                                changed += 1
                        else:
                            RecipeLine.objects.create(stock_item=s, quantity=val, verified=True, **{fk: o})
                            changed += 1
        messages.success(request, f"Recipes saved ({changed} changes). New sales use these amounts.")
        return redirect("stock_recipes")

    rows = []
    for v in variants:
        rows.append({"key": f"v-{v.id}", "group": v.item.category.name, "name": v.item.name,
                     "size": v.name, "cells": [(f"v-{v.id}-{s.id}", by_v.get((v.id, s.id))) for s in stock_items]})
    for a in addons:
        rows.append({"key": f"a-{a.id}", "group": "Extras", "name": a.name, "size": "",
                     "cells": [(f"a-{a.id}-{s.id}", by_a.get((a.id, s.id))) for s in stock_items]})
    return render(request, "stock/recipes.html", {"stock_items": stock_items, "rows": rows})


# ------------------------------------------------------------------ reports
@owner_required
def variance_report(request):
    end = _day(request, "end")
    try:
        start = date.fromisoformat(request.GET.get("start", "")) if request.GET.get("start") else end - timedelta(days=29)
    except ValueError:
        start = end - timedelta(days=29)
    counts = StockCount.objects.filter(business_date__range=(start, end)).select_related("stock_item")
    per_item = (
        counts.values("stock_item__name", "stock_item__unit")
        .annotate(var=Sum("variance"), value=Sum("variance_value"))
        .order_by("value")
    )
    usage = services.usage_between(start, end)
    return render(request, "stock/variance.html", {
        "start": start, "end": end, "per_item": per_item,
        "rows": counts.exclude(variance=0).order_by("-business_date", "stock_item__sort_order")[:200],
        "total_loss": sum((r["value"] for r in per_item if r["value"] and r["value"] < 0), Decimal("0")),
        "usage": usage,
    })


def prep_plan(request):
    try:
        d = date.fromisoformat(request.GET["date"]) if request.GET.get("date") else today() + timedelta(days=1)
    except ValueError:
        d = today() + timedelta(days=1)
    try:
        buffer_pct = max(0, min(100, int(request.GET.get("buffer", 10))))
    except ValueError:
        buffer_pct = 10
    fc = forecast(d)
    now_rows = {r.item.id: r for r in services.day_stock(today())}
    plan = []
    for s in fc.stock:
        it, need = s["item"], s["need"]
        if need <= 0 and not it.is_key:
            continue
        with_buffer = need * (Decimal(100 + buffer_pct) / 100)
        if it.unit == "pcs":
            with_buffer = with_buffer.to_integral_value(rounding=ROUND_CEILING)
        else:
            with_buffer = with_buffer.quantize(Decimal("0.1"), rounding=ROUND_CEILING)
        row = now_rows.get(it.id)
        tracked = bool(row and row.tracked)
        have = row.closing if tracked else None
        # Negative "have" means stock wasn't fully recorded, so don't count it.
        usable = max(Decimal("0"), have) if have is not None else Decimal("0")
        need_shown = need if it.unit != "pcs" else need.quantize(Decimal("0.1"))
        plan.append({"item": it, "need": need_shown, "prepare": with_buffer, "have": have, "tracked": tracked,
                     "buy": max(Decimal("0"), with_buffer - usable) if tracked else None})
    plan.sort(key=lambda r: (not r["item"].is_key, r["item"].sort_order))
    return render(request, "stock/prep.html", {"fc": fc, "plan": plan, "buffer": buffer_pct, "day": d})
