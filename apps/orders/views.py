from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.outbox import publish_outbox_event
from apps.orders.models import InvalidStatusTransition, Order, OrderLine, OrderStatus
from apps.orders.serializers import (
    OrderCreateSerializer,
    OrderSerializer,
    OrderStatusSerializer,
)


class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.prefetch_related("lines").select_related("customer").all()
    serializer_class = OrderSerializer
    filterset_fields = ["status", "customer"]
    search_fields = ["customer__email"]
    ordering_fields = ["created_at", "total_amount"]

    def get_serializer_class(self):
        if self.action == "create":
            return OrderCreateSerializer
        if self.action in ("place", "pay"):
            return OrderStatusSerializer
        return OrderSerializer

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = OrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        order = Order.objects.create(
            customer_id=serializer.validated_data["customer"],
        )

        total = 0
        for line_data in serializer.validated_data["lines"]:
            line = OrderLine.objects.create(
                order=order,
                product_id=line_data["product"],
                qty=line_data["qty"],
                price=line_data["price"],
            )
            total += line.qty * line.price

        order.total_amount = total
        order.save(update_fields=["total_amount"])

        output = OrderSerializer(order)
        return Response(output.data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=OrderSerializer)
    @action(detail=True, methods=["post"], url_path="place")
    def place(self, request, pk=None):
        """Transition order DRAFT -> PLACED. Emits OrderPlaced outbox event."""
        with transaction.atomic():
            order = self.get_object()
            try:
                order.transition_to(OrderStatus.PLACED)
            except InvalidStatusTransition as e:
                return Response({"detail": str(e)}, status=status.HTTP_409_CONFLICT)
            order.save(update_fields=["status"])

            publish_outbox_event(
                aggregate_type="order",
                aggregate_id=order.pk,
                event_type="OrderPlaced",
                payload={
                    "order_id": str(order.pk),
                    "customer_id": str(order.customer_id),
                    "total_amount": str(order.total_amount),
                },
            )

        return Response(OrderSerializer(order).data)

    @extend_schema(request=None, responses=OrderSerializer)
    @action(detail=True, methods=["post"], url_path="pay")
    def pay(self, request, pk=None):
        """Transition order RESERVED -> PAID. Emits PaymentConfirmed outbox event."""
        with transaction.atomic():
            order = self.get_object()
            try:
                order.transition_to(OrderStatus.PAID)
            except InvalidStatusTransition as e:
                return Response({"detail": str(e)}, status=status.HTTP_409_CONFLICT)
            order.save(update_fields=["status"])

            publish_outbox_event(
                aggregate_type="order",
                aggregate_id=order.pk,
                event_type="PaymentConfirmed",
                payload={
                    "order_id": str(order.pk),
                    "customer_id": str(order.customer_id),
                    "total_amount": str(order.total_amount),
                },
            )

        return Response(OrderSerializer(order).data)

    @extend_schema(request=None, responses=OrderSerializer)
    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        """Cancel an order. Emits OrderCancelled outbox event."""
        with transaction.atomic():
            order = self.get_object()
            try:
                order.transition_to(OrderStatus.CANCELLED)
            except InvalidStatusTransition as e:
                return Response({"detail": str(e)}, status=status.HTTP_409_CONFLICT)
            order.save(update_fields=["status"])

            publish_outbox_event(
                aggregate_type="order",
                aggregate_id=order.pk,
                event_type="OrderCancelled",
                payload={
                    "order_id": str(order.pk),
                    "customer_id": str(order.customer_id),
                },
            )

        return Response(OrderSerializer(order).data)
