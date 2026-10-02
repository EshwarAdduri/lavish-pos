from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Device
from menu.models import Addon, FoodType, Item, Variant


class OrderType(models.TextChoices):
    DINE_IN = "dine_in", "Dine-in"
    TAKEAWAY = "takeaway", "Takeaway"
    PARCEL = "parcel", "Parcel"
    ONLINE = "online", "Online"


class Platform(models.TextChoices):
    NONE = "", "—"
    ZOMATO = "zomato", "Zomato"
    SWIGGY = "swiggy", "Swiggy"
    OTHER = "other", "Other"


class OrderStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PREPARING = "preparing", "Preparing"
    READY = "ready", "Ready"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class PaymentMethod(models.TextChoices):
    CASH = "cash", "Cash"
    UPI = "upi", "UPI"
    CARD = "card", "Card"
    ONLINE = "online", "Online platform"


class Counter(models.Model):
    """Gap-free running numbers (bill numbers, daily tokens). Locked row while incrementing."""

    key = models.CharField(max_length=40, unique=True)
    value = models.PositiveBigIntegerField(default=0)

    def __str__(self):
        return f"{self.key}={self.value}"


class Order(models.Model):
    bill_no = models.PositiveBigIntegerField(unique=True)
    bill_number = models.CharField(max_length=24, unique=True)
    token_no = models.PositiveIntegerField(help_text="Daily token number called out to customers.")
    business_date = models.DateField(db_index=True)

    order_type = models.CharField(max_length=10, choices=OrderType.choices, default=OrderType.TAKEAWAY)
    platform = models.CharField(max_length=10, choices=Platform.choices, blank=True, default="")
    status = models.CharField(max_length=10, choices=OrderStatus.choices, default=OrderStatus.PENDING, db_index=True)

    customer_name = models.CharField(max_length=60, blank=True)
    customer_phone = models.CharField(max_length=20, blank=True)
    note = models.CharField(max_length=200, blank=True)

    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    discount_note = models.CharField(max_length=100, blank=True)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders")
    device = models.ForeignKey(Device, null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    preparing_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    cancel_reason = models.CharField(max_length=200, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["business_date", "status"])]

    def __str__(self):
        return self.bill_number

    @property
    def balance(self) -> Decimal:
        return self.total - self.paid_amount

    @property
    def is_paid(self) -> bool:
        return self.status != OrderStatus.CANCELLED and self.paid_amount >= self.total

    @property
    def is_open(self) -> bool:
        return self.status in (OrderStatus.PENDING, OrderStatus.PREPARING, OrderStatus.READY)

    @property
    def age_minutes(self) -> int:
        return int((timezone.now() - self.created_at).total_seconds() // 60)

    @property
    def item_count(self) -> int:
        return sum(i.quantity for i in self.items.all())


class OrderItem(models.Model):
    """One line of a bill. Names and prices are COPIED at sale time so history never changes."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    variant = models.ForeignKey(Variant, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    item_name = models.CharField(max_length=80)
    variant_name = models.CharField(max_length=30, blank=True)
    category_name = models.CharField(max_length=50, blank=True)
    food_type = models.CharField(max_length=10, choices=FoodType.choices, default=FoodType.NON_VEG)
    unit_price = models.DecimalField(max_digits=9, decimal_places=2)
    addons_price = models.DecimalField(max_digits=9, decimal_places=2, default=Decimal("0"))
    quantity = models.PositiveIntegerField()
    line_total = models.DecimalField(max_digits=10, decimal_places=2)
    note = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.quantity} x {self.display_name}"

    @property
    def each_price(self):
        return self.unit_price + self.addons_price

    @property
    def display_name(self):
        if self.variant_name and self.variant_name.lower() != "regular":
            return f"{self.item_name} ({self.variant_name})"
        return self.item_name


class OrderItemAddon(models.Model):
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name="addons")
    addon = models.ForeignKey(Addon, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    name = models.CharField(max_length=50)
    price = models.DecimalField(max_digits=9, decimal_places=2)

    def __str__(self):
        return self.name


class Payment(models.Model):
    """Money received for an order. Negative amount = refund."""

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="payments")
    method = models.CharField(max_length=10, choices=PaymentMethod.choices)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    cash_tendered = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    reference = models.CharField(max_length=60, blank=True, help_text="UPI / card reference (optional)")
    note = models.CharField(max_length=120, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments")
    device = models.ForeignKey(Device, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    business_date = models.DateField(db_index=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["business_date", "method"])]

    def __str__(self):
        return f"{self.get_method_display()} {self.amount} for {self.order}"

    @property
    def change(self):
        return (self.cash_tendered - self.amount) if self.cash_tendered else None


class ApprovalRequest(models.Model):
    class Kind(models.TextChoices):
        CANCEL = "cancel", "Cancel order"
        DISCOUNT = "discount", "Discount"

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="approvals")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=200)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} — {self.order}"
