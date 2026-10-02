from django.contrib import admin

from .models import AuditLog, Device, ShopSettings


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "model", "object_repr", "device")
    list_filter = ("action", "model")
    search_fields = ("object_repr", "note")

    # Audit history is read-only, even here.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(Device)
admin.site.register(ShopSettings)
