from django.contrib import admin

from .models import Product


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("sku", "name", "price", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("sku", "name")
    readonly_fields = ("id", "created_at", "updated_at")
