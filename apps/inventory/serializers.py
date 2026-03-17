from rest_framework import serializers

from apps.inventory.models import InventoryItem


class InventoryItemSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source="product.sku", read_only=True)
    available = serializers.IntegerField(read_only=True)

    class Meta:
        model = InventoryItem
        fields = [
            "id", "product", "sku", "warehouse", "batch_no",
            "on_hand", "reserved", "available", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "available", "created_at", "updated_at"]
