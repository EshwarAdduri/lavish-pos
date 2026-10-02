import hashlib

from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Count, Max
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from accounts.models import User
from menu.models import Addon, Category, Item, Variant
from orders.models import ApprovalRequest, Order, Payment

from .models import AuditLog, ShopSettings
from .permissions import owner_required


def home(request):
    return redirect("pos")


@never_cache
@require_GET
def healthz(request):
    """Used by the keep-alive pinger. Touches the database so Supabase never sleeps."""
    with connection.cursor() as c:
        c.execute("SELECT 1")
    return HttpResponse("ok", content_type="text/plain")


def _stamp(*values) -> str:
    return hashlib.md5("|".join(str(v) for v in values).encode()).hexdigest()[:12]


@never_cache
@require_GET
def pulse(request):
    """
    Live sync. Every open screen asks this tiny endpoint every few seconds:
    "has anything changed?". If the answer changes, that screen refreshes its data.
    """
    o = Order.objects.aggregate(m=Max("updated_at"), n=Count("id"))
    p = Payment.objects.aggregate(m=Max("created_at"), n=Count("id"))
    menu = [M.objects.aggregate(m=Max("updated_at"))["m"] for M in (Category, Item, Variant, Addon)]
    data = {
        "orders": _stamp(o["m"], o["n"], p["m"], p["n"]),
        "menu": _stamp(*menu, Item.objects.count()),
    }
    if request.user.is_owner:
        a = ApprovalRequest.objects.aggregate(m=Max("updated_at"))
        data["approvals"] = ApprovalRequest.objects.filter(status=ApprovalRequest.Status.PENDING).count()
        data["approvals_stamp"] = _stamp(a["m"])
    return JsonResponse(data)


# ------------------------------------------------------------------ PWA
@require_GET
def manifest(request):
    shop = ShopSettings.load()
    return JsonResponse(
        {
            "name": f"{shop.shop_name} POS",
            "short_name": shop.shop_name[:12],
            "start_url": "/pos/",
            "scope": "/",
            "display": "standalone",
            "orientation": "any",
            "background_color": "#14110F",
            "theme_color": "#E8590C",
            "icons": [
                {"src": static("icons/icon-192.png"), "sizes": "192x192", "type": "image/png"},
                {"src": static("icons/icon-512.png"), "sizes": "512x512", "type": "image/png"},
                {"src": static("icons/icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            ],
        },
        content_type="application/manifest+json",
    )


@require_GET
def service_worker(request):
    resp = render(request, "sw.js", {"assets": [
        static("css/app.css"), static("js/app.js"), static("js/pos.js"),
        static("vendor/htmx.min.js"), static("vendor/alpine.min.js"), static("vendor/chart.min.js"),
        static("icons/icon-192.png"),
    ]}, content_type="application/javascript")
    resp["Cache-Control"] = "no-cache"
    resp["Service-Worker-Allowed"] = "/"
    return resp


def offline(request):
    return render(request, "offline.html")


# ------------------------------------------------------------------ settings
class ShopSettingsForm(forms.ModelForm):
    class Meta:
        model = ShopSettings
        exclude = ["updated_at"]
        widgets = {"day_starts_at_hour": forms.NumberInput(attrs={"min": 0, "max": 23})}

    def clean_day_starts_at_hour(self):
        h = self.cleaned_data["day_starts_at_hour"]
        if h > 23:
            raise forms.ValidationError("Use an hour from 0 to 23.")
        return h


@owner_required
def shop_settings(request):
    obj = ShopSettings.load()
    form = ShopSettingsForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Settings saved.")
        return redirect("settings")
    return render(request, "core/settings.html", {"form": form})


# ------------------------------------------------------------------ audit log
@owner_required
def audit_log(request):
    qs = AuditLog.objects.select_related("actor", "device")
    f = {
        "model": request.GET.get("model", ""),
        "action": request.GET.get("action", ""),
        "user": request.GET.get("user", ""),
        "q": request.GET.get("q", "").strip(),
        "date": request.GET.get("date", ""),
    }
    if f["model"]:
        qs = qs.filter(model=f["model"])
    if f["action"]:
        qs = qs.filter(action=f["action"])
    if f["user"]:
        qs = qs.filter(actor_id=f["user"])
    if f["q"]:
        qs = qs.filter(object_repr__icontains=f["q"])
    if f["date"]:
        qs = qs.filter(created_at__date=f["date"])
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "core/audit.html",
        {
            "page": page,
            "f": f,
            "models": AuditLog.objects.order_by().values_list("model", flat=True).distinct(),
            "actions": AuditLog.objects.order_by().values_list("action", flat=True).distinct(),
            "users": User.objects.all(),
        },
    )


def forbidden(request, exception=None):
    return render(request, "error.html", {"code": 403, "title": "Not allowed",
                                          "text": "Your account doesn't have access to this page."}, status=403)


def not_found(request, exception=None):
    return render(request, "error.html", {"code": 404, "title": "Page not found",
                                          "text": "That page doesn't exist."}, status=404)
