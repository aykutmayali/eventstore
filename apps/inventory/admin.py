from django.contrib import admin

from .models import InventoryItem


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = (
        "product", "warehouse", "batch_no", "on_hand", "reserved", "available", "created_at",
    )
    list_filter = ("warehouse",)
    search_fields = ("product__sku", "product__name", "warehouse")
    readonly_fields = ("id", "created_at", "updated_at")

    @admin.display(description="Available")
    def available(self, obj):
        return obj.available
