from rest_framework import viewsets

from apps.inventory.models import InventoryItem
from apps.inventory.serializers import InventoryItemSerializer


class InventoryItemViewSet(viewsets.ModelViewSet):
    queryset = InventoryItem.objects.select_related("product").all()
    serializer_class = InventoryItemSerializer
    filterset_fields = ["product__sku", "warehouse"]
    search_fields = ["product__sku", "product__name", "warehouse"]
    ordering_fields = ["on_hand", "reserved", "created_at"]
