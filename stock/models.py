from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from menu.models import Addon, Variant


class StockItem(models.Model):
    """Raw material you buy and use up: chicken, rotis, fries, mayo, bottles…"""

    class Unit(models.TextChoices):
        KG = "kg", "kg"
        PCS = "pcs", "pieces"
        LITRE = "L", "litre"
        PACKET = "pkt", "packet"

    name = models.CharField(max_length=60, unique=True)
    unit = models.CharField(max_length=5, choices=Unit.choices, default=Unit.PCS)
    cost_per_unit = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0"),
        help_text="Latest purchase price per unit (₹). Used to value losses.",
    )
    low_stock_level = models.DecimalField(
        max_digits=10, decimal_places=3, default=Decimal("0"),
        help_text="Warn when stock falls to this level (0 = no warning).",
    )
    is_key = models.BooleanField(default=False, help_text="Show on the dashboard (e.g. chicken, roti).")
    count_daily = models.BooleanField(default=True, help_text="Ask for a physical count at day close.")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=100)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class RecipeLine(models.Model):
    """How much of a stock item one unit of a menu size (or one add-on) uses up."""

    stock_item = models.ForeignKey(StockItem, on_delete=models.CASCADE, related_name="recipe_lines")
    variant = models.ForeignKey(Variant, null=True, blank=True, on_delete=models.CASCADE, related_name="recipe")
    addon = models.ForeignKey(Addon, null=True, blank=True, on_delete=models.CASCADE, related_name="recipe")
    quantity = models.DecimalField(max_digits=10, decimal_places=3, validators=[MinValueValidator(Decimal("0"))])
    verified = models.BooleanField(default=False, help_text="Owner has confirmed this quantity.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["stock_item__sort_order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(variant__isnull=False, addon__isnull=True) | Q(variant__isnull=True, addon__isnull=False)),
                name="recipe_line_variant_xor_addon",
            ),
            models.UniqueConstraint(fields=["stock_item", "variant"], condition=Q(variant__isnull=False), name="uniq_recipe_variant"),
            models.UniqueConstraint(fields=["stock_item", "addon"], condition=Q(addon__isnull=False), name="uniq_recipe_addon"),
        ]

    def __str__(self):
        target = self.variant or self.addon
        return f"{target}: {self.quantity} {self.stock_item.unit} {self.stock_item.name}"


class StockMovement(models.Model):
    """Every change to stock. `delta` is + for stock coming in and − for stock going out."""

    class Kind(models.TextChoices):
        PURCHASE = "purchase", "Purchase"
        SALE = "sale", "Used in sales"
        WASTAGE = "wastage", "Wastage / spoiled"
        STAFF_MEAL = "staff_meal", "Staff meal"
        ADJUST = "adjust", "Correction"

    stock_item = models.ForeignKey(StockItem, on_delete=models.PROTECT, related_name="movements")
    kind = models.CharField(max_length=12, choices=Kind.choices, db_index=True)
    delta = models.DecimalField(max_digits=12, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    business_date = models.DateField(db_index=True)
    order = models.ForeignKey("orders.Order", null=True, blank=True, on_delete=models.CASCADE, related_name="stock_movements")
    note = models.CharField(max_length=150, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-business_date", "-created_at"]
        indexes = [models.Index(fields=["stock_item", "business_date", "kind"])]

    def __str__(self):
        return f"{self.get_kind_display()} {self.delta} {self.stock_item.unit} {self.stock_item}"

    @property
    def value(self) -> Decimal:
        return (abs(self.delta) * self.unit_cost).quantize(Decimal("0.01"))


class StockCount(models.Model):
    """Physical count at day close. The difference vs. expected = missing / extra stock."""

    business_date = models.DateField(db_index=True)
    stock_item = models.ForeignKey(StockItem, on_delete=models.PROTECT, related_name="counts")
    counted_qty = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(Decimal("0"))])
    expected_qty = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))
    variance = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))
    variance_value = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    counted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-business_date", "stock_item__sort_order"]
        constraints = [models.UniqueConstraint(fields=["business_date", "stock_item"], name="uniq_count_per_day")]

    def __str__(self):
        return f"{self.business_date} {self.stock_item}: {self.counted_qty}"
