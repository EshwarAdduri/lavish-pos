from django.contrib import admin

from .models import RecipeLine, StockCount, StockItem, StockMovement

admin.site.register(StockItem)
admin.site.register(RecipeLine)
admin.site.register(StockCount)


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("business_date", "stock_item", "kind", "delta", "order")
    list_filter = ("kind", "stock_item")
