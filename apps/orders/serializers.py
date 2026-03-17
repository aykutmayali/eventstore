from decimal import Decimal

from rest_framework import serializers

from apps.orders.models import Order, OrderLine


class OrderLineCreateSerializer(serializers.Serializer):
    product = serializers.UUIDField()
    qty = serializers.IntegerField(min_value=1)
    price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0"))


class OrderCreateSerializer(serializers.Serializer):
    customer = serializers.UUIDField()
    lines = OrderLineCreateSerializer(many=True, min_length=1)


class OrderLineSerializer(serializers.ModelSerializer):
    line_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = OrderLine
        fields = ["id", "product", "qty", "price", "line_total", "created_at"]
        read_only_fields = ["id", "line_total", "created_at"]


class OrderSerializer(serializers.ModelSerializer):
    lines = OrderLineSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "id", "customer", "status", "total_amount",
            "lines", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "status", "total_amount", "created_at", "updated_at"]


class OrderStatusSerializer(serializers.Serializer):
    """Empty serializer for place/pay actions -- no request body needed."""

    pass
