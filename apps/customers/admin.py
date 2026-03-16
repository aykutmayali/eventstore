from django.contrib import admin

from .models import Customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("email", "first_name", "last_name", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("email", "first_name", "last_name")
    readonly_fields = ("id", "created_at", "updated_at")
