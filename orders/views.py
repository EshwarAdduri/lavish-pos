import json
from datetime import date, timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import F, Prefetch, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from core.models import AuditLog, ShopSettings
from core.permissions import owner_required
from core.utils import today
from menu.models import Category, Item, Variant

from . import services
from .models import ApprovalRequest, Order, OrderStatus, OrderType, PaymentMethod, Platform
from .receipt import receipt_pdf
from .services import OrderError


# ------------------------------------------------------------------ menu data for the POS
def menu_payload():
    cats = (
        Category.objects.filter(is_active=True)
        .prefetch_related(
            Prefetch(
                "items",
                queryset=Item.objects.filter(is_active=True)
                .prefetch_related(
                    Prefetch("variants", queryset=Variant.objects.filter(is_active=True).order_by("sort_order", "price")),
                    "addons",
                )
                .order_by("sort_order", "name"),
            )
        )
        .order_by("sort_order", "name")
    )
    out = {"categories": [], "items": []}
    for c in cats:
        out["categories"].append({"id": c.id, "name": c.name})
        for it in c.items.all():
            variants = [{"id": v.id, "name": v.name, "price": float(v.price)} for v in it.variants.all()]
            if not variants:
                continue
            out["items"].append(
                {
                    "id": it.id,
                    "cat": c.id,
                    "name": it.name,
                    "label": it.label,
                    "food": it.food_type,
                    "soldOut": it.is_sold_out,
                    "variants": variants,
                    "addons": [
                        {"id": a.id, "name": a.name, "price": float(a.price)} for a in it.addons.all() if a.is_active
                    ],
                }
            )
    return out


def _order_for_edit(order: Order):
    return {
        "id": order.id,
        "bill": order.bill_number,
        "version": order.version,
        "order_type": order.order_type,
        "platform": order.platform,
        "customer_name": order.customer_name,
        "customer_phone": order.customer_phone,
        "note": order.note,
        "discount_amount": float(order.discount_amount),
        "discount_note": order.discount_note,
        "lines": [
            {
                "variant_id": i.variant_id,
                "item_id": i.item_id,
                "qty": i.quantity,
                "addon_ids": [a.addon_id for a in i.addons.all() if a.addon_id],
                "note": i.note,
            }
            for i in order.items.prefetch_related("addons")
            if i.variant_id
        ],
    }


def pos(request):
    shop = ShopSettings.load()
    edit = None
    if request.GET.get("edit"):
        order = get_object_or_404(Order, pk=request.GET["edit"])
        try:
            if order.status == OrderStatus.CANCELLED:
                raise OrderError("Cancelled orders cannot be edited.")
            if not request.user.is_owner and (order.paid_amount > 0 or order.status not in (OrderStatus.PENDING, OrderStatus.PREPARING)):
                raise OrderError("Only the owner can edit a paid or finished order.")
            edit = _order_for_edit(order)
        except OrderError as e:
            messages.error(request, str(e))
            return redirect("order_detail", pk=order.pk)
    staff_cap = None if request.user.is_owner else shop.staff_max_discount_percent
    return render(
        request,
        "orders/pos.html",
        {
            "menu": menu_payload(),
            "edit": edit,
            "pos_config": {
                "orderTypes": OrderType.choices,
                "platforms": [p for p in Platform.choices if p[0]],
                "methods": PaymentMethod.choices,
                "staffDiscountCap": staff_cap,
                "urls": {
                    "create": reverse("order_create"),
                    "menu": reverse("pos_menu"),
                    "receipt": "/orders/0/receipt/",
                    "detail": "/orders/0/",
                },
            },
        },
    )


@require_GET
def pos_menu(request):
    return JsonResponse(menu_payload())


def _json_body(request):
    try:
        return json.loads(request.body.decode() or "{}")
    except (ValueError, UnicodeDecodeError):
        raise OrderError("Bad request.")


def _order_json(order: Order):
    return {
        "ok": True,
        "id": order.id,
        "bill": order.bill_number,
        "token": order.token_no,
        "total": float(order.total),
        "paid": float(order.paid_amount),
        "balance": float(order.balance),
        "version": order.version,
    }


@require_POST
def order_create(request):
    try:
        data = _json_body(request)
        order = services.create_order(request.user, request.device, data)
    except OrderError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    return JsonResponse(_order_json(order))


@require_POST
def order_update(request, pk):
    order = get_object_or_404(Order, pk=pk)
    try:
        data = _json_body(request)
        order = services.update_order(order, request.user, request.device, data)
        pay = data.get("payment")
        if pay and order.balance > 0:
            services.add_payment(order, request.user, request.device, pay.get("method"), pay.get("amount") or order.balance,
                                 cash_tendered=pay.get("cash_tendered"), reference=pay.get("reference", ""))
            order.refresh_from_db()
    except OrderError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    return JsonResponse(_order_json(order))


# ------------------------------------------------------------------ order lists
def _parse_date(value, default):
    try:
        return date.fromisoformat(value) if value else default
    except ValueError:
        return default


def orders_list(request):
    d = _parse_date(request.GET.get("date"), today())
    status = request.GET.get("status", "")
    q = request.GET.get("q", "").strip()
    paid = request.GET.get("paid", "")
    qs = Order.objects.select_related("created_by").prefetch_related("items").filter(business_date=d)
    if status:
        qs = qs.filter(status=status)
    if paid == "unpaid":
        qs = qs.exclude(status=OrderStatus.CANCELLED).filter(paid_amount__lt=F("total"))
    if q:
        qs = Order.objects.select_related("created_by").prefetch_related("items").filter(
            Q(bill_number__icontains=q) | Q(customer_name__icontains=q) | Q(customer_phone__icontains=q)
            | (Q(token_no=int(q), business_date=d) if q.isdigit() else Q(pk__in=[]))
        )
    page = Paginator(qs.order_by("-created_at"), 40).get_page(request.GET.get("page"))
    totals = qs.exclude(status=OrderStatus.CANCELLED).aggregate(s=Sum("total"), p=Sum("paid_amount"))
    totals["unpaid"] = (totals["s"] or 0) - (totals["p"] or 0)
    ctx = {
        "page": page,
        "day": d,
        "prev_day": d - timedelta(days=1),
        "next_day": d + timedelta(days=1),
        "is_today": d == today(),
        "status": status,
        "paid": paid,
        "q": q,
        "statuses": OrderStatus.choices,
        "totals": totals,
    }
    template = "orders/_list_rows.html" if request.headers.get("HX-Request") else "orders/list.html"
    return render(request, template, ctx)


def board(request):
    open_orders = (
        Order.objects.filter(status__in=[OrderStatus.PENDING, OrderStatus.PREPARING, OrderStatus.READY])
        .filter(business_date__gte=today() - timedelta(days=1))
        .prefetch_related("items__addons")
        .order_by("created_at")
    )
    cols = {s: [] for s in (OrderStatus.PENDING, OrderStatus.PREPARING, OrderStatus.READY)}
    for o in open_orders:
        cols[o.status].append(o)
    ctx = {"cols": [(s, OrderStatus(s).label, cols[s]) for s in cols]}
    template = "orders/_board_cols.html" if request.headers.get("HX-Request") else "orders/board.html"
    return render(request, template, ctx)


# ------------------------------------------------------------------ one order
def order_detail(request, pk):
    order = get_object_or_404(
        Order.objects.select_related("created_by", "device", "cancelled_by").prefetch_related("items__addons", "payments__received_by"),
        pk=pk,
    )
    history = []
    if request.user.is_owner:
        history = AuditLog.objects.filter(model="Order", object_id=str(order.pk)).select_related("actor")[:30]
    ctx = {
        "order": order,
        "methods": PaymentMethod.choices,
        "next_statuses": [
            (s, OrderStatus(s).label) for s in services.STATUS_FLOW.get(order.status, [])
            if not (s == OrderStatus.COMPLETED and order.balance > 0)
        ],
        "approvals": order.approvals.select_related("requested_by", "decided_by"),
        "history": history,
        "can_edit": order.status != OrderStatus.CANCELLED
        and (request.user.is_owner or (order.paid_amount == 0 and order.status in (OrderStatus.PENDING, OrderStatus.PREPARING))),
    }
    template = "orders/_detail_body.html" if request.headers.get("HX-Request") else "orders/detail.html"
    return render(request, template, ctx)


def _back(request, order, msg=None, error=None):
    if error:
        messages.error(request, error)
    elif msg:
        messages.success(request, msg)
    nxt = request.POST.get("next")
    if nxt and nxt.startswith("/"):
        return redirect(nxt)
    return redirect("order_detail", pk=order.pk)


@require_POST
def order_pay(request, pk):
    order = get_object_or_404(Order, pk=pk)
    try:
        p = services.add_payment(order, request.user, request.device, request.POST.get("method"),
                                 request.POST.get("amount"), cash_tendered=request.POST.get("cash_tendered"),
                                 reference=request.POST.get("reference", ""))
    except OrderError as e:
        return _back(request, order, error=str(e))
    change = ""
    if p.cash_tendered:
        change = f" Give back ₹{p.cash_tendered - p.amount}."
    return _back(request, order, f"Payment of ₹{p.amount} recorded.{change}")


@require_POST
def order_status(request, pk):
    order = get_object_or_404(Order, pk=pk)
    try:
        services.set_status(order, request.user, request.POST.get("status"))
    except OrderError as e:
        if request.headers.get("HX-Request"):
            return HttpResponse(f'<div class="toast toast-bad" data-autohide>{e}</div>', status=200,
                                headers={"HX-Retarget": "#toasts", "HX-Reswap": "beforeend"})
        return _back(request, order, error=str(e))
    if request.headers.get("HX-Request"):
        return board(request)
    return _back(request, order, "Status updated.")


@require_POST
def order_cancel(request, pk):
    order = get_object_or_404(Order, pk=pk)
    try:
        services.cancel_order(order, request.user, request.device, request.POST.get("reason", ""))
    except OrderError as e:
        return _back(request, order, error=str(e))
    return _back(request, order, "Order cancelled. Stock returned and any payment refunded.")


@require_POST
def order_refund(request, pk):
    order = get_object_or_404(Order, pk=pk)
    try:
        services.refund(order, request.user, request.device, request.POST.get("method"),
                        request.POST.get("amount"), request.POST.get("note", ""))
    except OrderError as e:
        return _back(request, order, error=str(e))
    return _back(request, order, "Refund recorded.")


@require_POST
def order_request(request, pk):
    order = get_object_or_404(Order, pk=pk)
    try:
        services.request_approval(order, request.user, request.POST.get("kind"), request.POST.get("reason", ""),
                                  request.POST.get("discount_amount"))
    except OrderError as e:
        return _back(request, order, error=str(e))
    return _back(request, order, "Request sent to the owner.")


# ------------------------------------------------------------------ receipts
def receipt(request, pk):
    order = get_object_or_404(Order.objects.prefetch_related("items__addons", "payments"), pk=pk)
    shop = ShopSettings.load()
    width = request.GET.get("w") or shop.receipt_width_mm
    width = 58 if str(width) == "58" else 80
    return render(request, "orders/receipt.html", {"order": order, "width": width, "autoprint": request.GET.get("print") == "1"})


def receipt_pdf_view(request, pk):
    order = get_object_or_404(Order.objects.prefetch_related("items__addons", "payments"), pk=pk)
    shop = ShopSettings.load()
    width = 58 if str(request.GET.get("w") or shop.receipt_width_mm) == "58" else 80
    pdf = receipt_pdf(order, shop, width)
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{order.bill_number}.pdf"'
    return resp


# ------------------------------------------------------------------ approvals (owner)
@owner_required
def approvals(request):
    pending = ApprovalRequest.objects.filter(status=ApprovalRequest.Status.PENDING).select_related("order", "requested_by")
    done = ApprovalRequest.objects.exclude(status=ApprovalRequest.Status.PENDING).select_related(
        "order", "requested_by", "decided_by"
    )[:50]
    template = "orders/_approvals_body.html" if request.headers.get("HX-Request") else "orders/approvals.html"
    return render(request, template, {"pending": pending, "done": done})


@owner_required
@require_POST
def approval_decide(request, pk):
    req = get_object_or_404(ApprovalRequest, pk=pk)
    approve = request.POST.get("decision") == "approve"
    try:
        services.decide(req, request.user, request.device, approve, request.POST.get("note", ""))
        messages.success(request, "Approved." if approve else "Rejected.")
    except OrderError as e:
        messages.error(request, str(e))
    return redirect(request.POST.get("next") or "approvals")
