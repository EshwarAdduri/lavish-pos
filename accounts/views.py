from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core import audit
from core.models import Device
from core.permissions import owner_required
from core.utils import client_ip

from .models import User


def _lock_key(username, ip):
    return f"login-fail:{(username or '').lower()}:{ip}"


def login_view(request):
    if request.user.is_authenticated:
        return redirect("home")
    error = ""
    username = ""
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        key = _lock_key(username, client_ip(request))
        fails = cache.get(key, 0)
        if fails >= settings.LOGIN_MAX_FAILURES:
            error = f"Too many wrong attempts. Try again in {settings.LOGIN_LOCK_MINUTES} minutes."
        else:
            user = authenticate(request, username=username, password=password)
            if user is not None and user.is_active:
                cache.delete(key)
                login(request, user)
                audit.log("login", user, model="User")
                nxt = request.POST.get("next") or request.GET.get("next")
                if nxt and url_has_allowed_host_and_scheme(nxt, {request.get_host()}, request.is_secure()):
                    return redirect(nxt)
                return redirect("home")
            cache.set(key, fails + 1, settings.LOGIN_LOCK_MINUTES * 60)
            audit.log("login failed", None, model="User", note=f"username={username[:40]}")
            error = "Wrong username or password."
    return render(request, "accounts/login.html", {"error": error, "username": username})


@require_POST
def logout_view(request):
    if getattr(request, "demo", None):
        return redirect("/demo/end/")
    if request.user.is_authenticated:
        audit.log("logout", request.user, model="User")
    logout(request)
    return redirect("login")


def change_password(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        audit.log("password changed", user, model="User")
        messages.success(request, "Password changed.")
        return redirect("home")
    return render(request, "accounts/change_password.html", {"form": form})


# ------------------------------------------------------------------ team (owner)
class StaffForm(forms.ModelForm):
    password = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False),
        help_text="At least 8 characters. Leave empty to keep the current password.",
    )

    class Meta:
        model = User
        fields = ["username", "display_name", "role", "is_active"]
        help_texts = {"username": "Used to log in. Letters, numbers and @/./+/-/_ only."}

    def __init__(self, *args, editing_self=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.editing_self = editing_self
        if not self.instance.pk:
            self.fields["password"].required = True
            self.fields["password"].help_text = "At least 8 characters."

    def clean(self):
        data = super().clean()
        if self.editing_self:
            if data.get("role") != User.Role.ADMIN and not self.instance.is_superuser:
                self.add_error("role", "You cannot remove your own owner access.")
            if not data.get("is_active"):
                self.add_error("is_active", "You cannot deactivate yourself.")
        pw = data.get("password")
        if pw:
            try:
                validate_password(pw, self.instance)
            except forms.ValidationError as e:
                self.add_error("password", e)
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data.get("password"):
            user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user


@owner_required
def team(request):
    return render(
        request,
        "accounts/team.html",
        {"users": User.objects.all().order_by("-is_active", "role", "username"), "devices": Device.objects.select_related("last_user")[:50]},
    )


@owner_required
def user_edit(request, pk=None):
    obj = get_object_or_404(User, pk=pk) if pk else None
    form = StaffForm(request.POST or None, instance=obj, editing_self=obj is not None and obj.pk == request.user.pk)
    if request.method == "POST" and form.is_valid():
        u = form.save()
        if form.cleaned_data.get("password") and obj is not None:
            audit.log("password reset", u, model="User")
        messages.success(request, f"Saved {u.username}.")
        return redirect("team")
    return render(request, "accounts/user_form.html", {"form": form, "obj": obj})


class DeviceForm(forms.ModelForm):
    class Meta:
        model = Device
        fields = ["name", "is_blocked"]


@owner_required
def device_edit(request, pk):
    obj = get_object_or_404(Device, pk=pk)
    form = DeviceForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        before = {"name": obj.name, "blocked": obj.is_blocked}
        d = form.save()
        audit.log("device updated", d, {"name": [before["name"], d.name], "blocked": [before["blocked"], d.is_blocked]}, model="Device")
        if request.device and d.pk == request.device.pk and d.is_blocked:
            messages.warning(request, "You blocked the device you are using.")
        messages.success(request, "Device saved.")
        return redirect("team")
    return render(request, "accounts/device_form.html", {"form": form, "obj": obj})
