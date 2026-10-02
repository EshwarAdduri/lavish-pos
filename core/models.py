import uuid

from django.conf import settings
from django.db import models


class ShopSettings(models.Model):
    """One row only. Everything the owner may want to change without touching code."""

    shop_name = models.CharField(max_length=80, default="Lavish Shawarma")
    tagline = models.CharField(max_length=120, blank=True, default="Fresh • Juicy • Loaded")
    address = models.CharField(max_length=200, blank=True, default="Mancherial, Telangana")
    phone = models.CharField(max_length=30, blank=True)
    receipt_footer = models.CharField(max_length=200, blank=True, default="Thank you! Visit again.")
    bill_prefix = models.CharField(max_length=8, default="LS")
    receipt_width_mm = models.PositiveSmallIntegerField(
        default=80, choices=[(58, "58 mm"), (80, "80 mm")]
    )
    # Shop day: orders before this hour count for the previous day (late-night sales).
    day_starts_at_hour = models.PositiveSmallIntegerField(
        default=4, help_text="Orders before this hour (0-23) belong to the previous business day."
    )

    # Abuse limits (staff only; the admin is never limited)
    max_open_orders_per_staff = models.PositiveSmallIntegerField(
        default=15, help_text="Unpaid, not-cancelled orders a staff member may have at once."
    )
    max_orders_per_minute = models.PositiveSmallIntegerField(
        default=6, help_text="Orders a staff member may create per minute."
    )
    staff_max_discount_percent = models.PositiveSmallIntegerField(
        default=0, help_text="Discounts above this % need admin approval."
    )
    staff_can_record = models.BooleanField(
        default=False,
        help_text="Let staff record stock purchases, wastage, expenses and the day close (they still cannot edit or delete).",
    )
    staff_can_mark_sold_out = models.BooleanField(default=True, help_text="Let staff mark items as sold out.")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "shop settings"
        verbose_name_plural = "shop settings"

    def __str__(self):
        return "Shop settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "ShopSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Device(models.Model):
    """A phone / tablet / laptop that has opened the app. Identified by a long-lived cookie."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=60, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now_add=True)
    last_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    is_blocked = models.BooleanField(default=False, help_text="Blocked devices cannot use the app.")

    class Meta:
        ordering = ["-last_seen_at"]

    def __str__(self):
        return self.name or self.guess_name()

    def guess_name(self) -> str:
        ua = (self.user_agent or "").lower()
        if "ipad" in ua or "tablet" in ua:
            kind = "Tablet"
        elif "android" in ua and "mobile" not in ua:
            kind = "Android tablet"
        elif "iphone" in ua or "android" in ua or "mobile" in ua:
            kind = "Phone"
        elif "windows" in ua or "macintosh" in ua or "linux" in ua or "cros" in ua:
            kind = "Laptop/PC"
        else:
            kind = "Device"
        return f"{kind} {str(self.id)[:4].upper()}"


class AuditLog(models.Model):
    """Append-only history of every change. Nobody can edit or delete these rows from the app."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    device = models.ForeignKey(Device, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    ip = models.GenericIPAddressField(null=True, blank=True)
    action = models.CharField(max_length=40, db_index=True)  # create / update / delete / login / cancel ...
    model = models.CharField(max_length=60, db_index=True)
    object_id = models.CharField(max_length=64, blank=True, db_index=True)
    object_repr = models.CharField(max_length=200, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.created_at:%d %b %H:%M} {self.actor} {self.action} {self.model} {self.object_repr}"
