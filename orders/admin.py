from django.contrib import admin

from .models import ApprovalRequest, Order, OrderItem, Payment


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = [f.name for f in OrderItem._meta.fields]


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    readonly_fields = [f.name for f in Payment._meta.fields]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("bill_number", "business_date", "status", "total", "paid_amount", "created_by")
    list_filter = ("status", "order_type", "business_date")
    search_fields = ("bill_number", "customer_name")
    inlines = [OrderItemInline, PaymentInline]
    readonly_fields = ("bill_no", "bill_number", "token_no", "subtotal", "total", "paid_amount")

    def has_delete_permission(self, request, obj=None):
        return False  # cancel instead of deleting


admin.site.register(ApprovalRequest)
