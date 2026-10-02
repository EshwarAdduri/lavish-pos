from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class LavishUserAdmin(UserAdmin):
    list_display = ("username", "display_name", "role", "is_active", "last_login")
    fieldsets = UserAdmin.fieldsets + (("Shop", {"fields": ("role", "display_name")}),)
