from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models


class FoodType(models.TextChoices):
    VEG = "veg", "Veg"
    NON_VEG = "non_veg", "Non-veg"
    EGG = "egg", "Egg"


class Category(models.Model):
    name = models.CharField(max_length=50, unique=True)
    sort_order = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class Item(models.Model):
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="items")
    name = models.CharField(max_length=80)
    short_name = models.CharField(
        max_length=30, blank=True, help_text="Shorter name for buttons and receipts (optional)."
    )
    food_type = models.CharField(max_length=10, choices=FoodType.choices, default=FoodType.NON_VEG)
    sort_order = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True, help_text="Untick to hide from the menu completely.")
    is_sold_out = models.BooleanField(default=False, help_text="Temporarily sold out (still on the menu).")
    addons = models.ManyToManyField("Addon", blank=True, related_name="items", help_text="Extras allowed on this item.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ["category__sort_order", "sort_order", "name"]
        constraints = [models.UniqueConstraint(fields=["category", "name"], name="uniq_item_name_per_category")]

    def __str__(self):
        return self.name

    @property
    def label(self):
        return self.short_name or self.name


class Variant(models.Model):
    """Size / portion with its own price. Every item has at least one ("Regular")."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="variants")
    name = models.CharField(max_length=30, default="Regular")
    price = models.DecimalField(max_digits=9, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])
    sort_order = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ["item", "sort_order", "price"]
        constraints = [models.UniqueConstraint(fields=["item", "name"], name="uniq_variant_name_per_item")]

    def __str__(self):
        return self.full_name

    @property
    def full_name(self):
        if self.name.lower() == "regular" and self.item.variants.filter(is_active=True).count() <= 1:
            return self.item.name
        return f"{self.item.name} ({self.name})"


class Addon(models.Model):
    name = models.CharField(max_length=50, unique=True)
    price = models.DecimalField(max_digits=9, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])
    food_type = models.CharField(max_length=10, choices=FoodType.choices, default=FoodType.VEG)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=100)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name
