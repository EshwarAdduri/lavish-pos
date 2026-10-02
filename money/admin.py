from django.contrib import admin

from .models import DayClose, Expense

admin.site.register(Expense)
admin.site.register(DayClose)
