from rest_framework import viewsets

from apps.products.models import Product
from apps.products.serializers import ProductSerializer


class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.filter(is_active=True)
    serializer_class = ProductSerializer
    filterset_fields = ["sku", "is_active"]
    search_fields = ["sku", "name"]
    ordering_fields = ["name", "price", "created_at"]
