from django.contrib import admin

from .models import Addon, Category, Item, Variant


class VariantInline(admin.TabularInline):
    model = Variant
    extra = 0


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "food_type", "is_active", "is_sold_out")
    list_filter = ("category", "is_active")
    inlines = [VariantInline]


admin.site.register(Category)
admin.site.register(Addon)
