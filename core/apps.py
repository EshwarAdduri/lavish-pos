from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # Turn on the audit trail for everything that matters.
        from accounts.models import User
        from menu.models import Addon, Category, Item, Variant
        from money.models import DayClose, Expense
        from orders.models import ApprovalRequest, Order, Payment
        from stock.models import RecipeLine, StockCount, StockItem, StockMovement

        from .audit import track
        from .models import ShopSettings

        for model in (Category, Item, Variant, Addon, StockItem, RecipeLine, StockCount,
                      Expense, DayClose, Payment, ApprovalRequest, ShopSettings):
            track(model)
        track(Order, exclude=("updated_at", "version"))
        track(User, exclude=("updated_at", "last_login", "password"))
        track(StockMovement, skip=lambda m: m.kind == "sale")  # automatic sale usage is not logged row-by-row
