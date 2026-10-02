from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Expense(models.Model):
    """Money going OUT of the shop."""

    class Category(models.TextChoices):
        STOCK = "stock", "Stock purchase (chicken, roti…)"
        GROCERY = "grocery", "Vegetables & groceries"
        GAS = "gas", "Gas / fuel"
        PACKAGING = "packaging", "Packaging"
        SALARY = "salary", "Salary / wages"
        RENT = "rent", "Rent"
        ELECTRICITY = "electricity", "Electricity & water"
        MAINTENANCE = "maintenance", "Repairs & maintenance"
        TRANSPORT = "transport", "Transport"
        OTHER = "other", "Other"

    class PaidFrom(models.TextChoices):
        DRAWER = "drawer", "Cash drawer (counter)"
        UPI = "upi", "UPI / bank"
        OWNER = "owner", "Owner's pocket"

    business_date = models.DateField(db_index=True)
    category = models.CharField(max_length=12, choices=Category.choices, default=Category.OTHER)
    description = models.CharField(max_length=150)
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    paid_from = models.CharField(max_length=8, choices=PaidFrom.choices, default=PaidFrom.DRAWER)
    stock_movement = models.OneToOneField(
        "stock.StockMovement", null=True, blank=True, on_delete=models.SET_NULL, related_name="expense"
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-business_date", "-created_at"]

    def __str__(self):
        return f"{self.business_date} {self.description} ₹{self.amount}"


class DayClose(models.Model):
    """End-of-day closing: counted cash vs. what the system expects."""

    business_date = models.DateField(unique=True)
    opening_cash = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    cash_sales = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    cash_expenses = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    expected_cash = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    counted_cash = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    cash_difference = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    cash_removed = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0"),
        help_text="Cash taken out of the drawer (to owner / bank). The rest stays as tomorrow's opening cash.",
    )
    upi_sales = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    card_sales = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    online_sales = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    total_sales = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    unpaid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    orders_count = models.PositiveIntegerField(default=0)
    cancelled_count = models.PositiveIntegerField(default=0)
    total_expenses = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    stock_loss_value = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    notes = models.CharField(max_length=300, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    closed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-business_date"]

    def __str__(self):
        return f"Day close {self.business_date}"

    @property
    def closing_balance(self):
        return self.counted_cash - self.cash_removed
