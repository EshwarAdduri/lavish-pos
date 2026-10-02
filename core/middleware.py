import uuid
from datetime import timedelta

from django.conf import settings
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.utils import timezone

from .models import Device
from .utils import set_current_request

DEVICE_COOKIE = "ls_device"
PUBLIC_PREFIXES = ("/login", "/static/", "/healthz", "/manifest.webmanifest", "/sw.js", "/offline", "/favicon")


class DeviceMiddleware:
    """Gives every browser a permanent device id so each order records which device made it."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_current_request(request)
        request.device = None
        new_cookie = None
        if not request.path.startswith(("/static/", "/healthz", "/sw.js")):
            raw = request.COOKIES.get(DEVICE_COOKIE)
            device = None
            try:
                if raw:
                    device = Device.objects.filter(pk=uuid.UUID(raw)).first()
            except ValueError:
                device = None
            user_ok = getattr(request, "user", None) is not None and request.user.is_authenticated
            if device is None and (user_ok or (request.path.startswith("/login") and request.method == "POST")):
                device = Device.objects.create(user_agent=request.META.get("HTTP_USER_AGENT", "")[:300])
                new_cookie = str(device.id)
            elif device is not None:
                now = timezone.now()
                user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
                if now - device.last_seen_at > timedelta(minutes=5) or (user and device.last_user_id != user.pk):
                    Device.objects.filter(pk=device.pk).update(last_seen_at=now, last_user=user)
            request.device = device
            if device is not None and device.is_blocked and not request.path.startswith("/login"):
                return HttpResponseForbidden("This device has been blocked by the owner.")

        response = self.get_response(request)
        if new_cookie:
            response.set_cookie(
                DEVICE_COOKIE,
                new_cookie,
                max_age=60 * 60 * 24 * 365 * 5,
                httponly=True,
                samesite="Lax",
                secure=not settings.DEBUG,
            )
        set_current_request(None)
        return response


class LoginRequiredMiddleware:
    """Every page needs a login except the login page itself and public files."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if not request.user.is_authenticated and not path.startswith(PUBLIC_PREFIXES) and not path.startswith("/admin/"):
            if request.headers.get("HX-Request"):
                resp = HttpResponseForbidden("login required")
                resp["HX-Redirect"] = "/login/"
                return resp
            return redirect(f"/login/?next={path}")
        if request.user.is_authenticated and not request.user.is_active:
            return redirect("/login/")
        return self.get_response(request)
