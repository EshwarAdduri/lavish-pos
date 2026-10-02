from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import login
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.utils import client_ip

from . import demo


@require_POST
def start(request):
    """'Try the demo' button. Makes a private 15-minute copy of the sample shop."""
    if not demo.enabled():
        raise Http404
    if not demo.allow_ip(client_ip(request)):
        return render(request, "demo_busy.html", {"reason": "Too many demos from your network in the last hour. Please try again later."}, status=429)
    old = request.demo
    created, sid = demo.create_sandbox()
    if old:  # restarting: the old copy is removed after this request finishes
        request._demo_delete.append(demo.sandbox_file(old["created"], old["sid"]))
    resp = redirect("/demo/enter/")
    demo.set_cookie(resp, created, sid)
    return resp


def enter(request):
    """Runs INSIDE the visitor's sandbox: log in as the demo owner and add a few live orders."""
    if not request.demo:
        return redirect("/login/")
    from accounts.models import User
    from menu.models import Variant
    from orders import services
    from orders.models import Order

    user = User.objects.get(username="demo")
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    if not Order.objects.filter(created_at__gte=timezone.now() - timedelta(hours=1)).exists():
        v = {f"{x.item.name}|{x.name}": x for x in Variant.objects.select_related("item")}
        def vid(name, size="Regular"):
            return v[f"{name}|{size}"].id
        samples = [
            {"order_type": "dine_in", "table_no": "3", "lines": [{"variant_id": vid("Chicken Peri-Peri Shawarma"), "qty": 2},
                                                               {"variant_id": vid("French Fries"), "qty": 1}]},
            {"order_type": "takeaway", "lines": [{"variant_id": vid("Lavish Special Shawarma"), "qty": 1},
                                                  {"variant_id": vid("Thumbs Up", "Large"), "qty": 1}],
             "payment": {"method": "upi", "amount": None}},
            {"order_type": "dine_in", "table_no": "5", "lines": [{"variant_id": vid("Chicken Cheese Shawarma"), "qty": 2}],
             "payment": {"method": "cash", "amount": None}},
        ]
        for p in samples:
            services.create_order(user, request.device, p)
    messages.info(request, f"Welcome to the demo! It's your own private copy and resets in {demo.minutes()} minutes.")
    return redirect("/pos/")


def end(request):
    if request.demo:
        request._demo_delete.append(demo.sandbox_file(request.demo["created"], request.demo["sid"]))
    resp = redirect("/login/?demo=bye")
    for name in (demo.COOKIE, "ls_demo_session", "ls_demo_device"):
        resp.delete_cookie(name)
    return resp
