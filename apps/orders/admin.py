from django.contrib import admin

from .models import Order, OrderLine


class OrderLineInline(admin.TabularInline):
    model = OrderLine
    extra = 0
    readonly_fields = ("id", "created_at")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "customer", "status", "total_amount", "created_at")
    list_filter = ("status",)
    search_fields = ("customer__email",)
    readonly_fields = ("id", "created_at", "updated_at")
    inlines = [OrderLineInline]


@admin.register(OrderLine)
class OrderLineAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "product", "qty", "price")
    search_fields = ("order__id", "product__sku")
    readonly_fields = ("id", "created_at", "updated_at")
