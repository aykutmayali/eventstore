"""Tests for Kafka/streaming utilities and metrics endpoint."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

from apps.common.kafka import build_event_message, publish_to_kafka
from apps.common.models import OutboxEvent
from apps.common.outbox import publish_outbox_event
from apps.common.tasks import dispatch_outbox_events
from apps.customers.models import Customer
from apps.orders.models import Order, OrderLine, OrderStatus
from apps.products.models import Product


@pytest.fixture()
def api_client():
    return APIClient()


@pytest.fixture()
def customer():
    return Customer.objects.create(email="kafka@example.com")


@pytest.fixture()
def product():
    return Product.objects.create(
        sku="KAFKA-SKU", name="Kafka Product", price=Decimal("10.00"),
    )


# ---------------------------------------------------------------------------
# Kafka utility tests
# ---------------------------------------------------------------------------
class TestKafkaUtilities:
    def test_build_event_message(self):
        import uuid

        event_id = uuid.uuid4()
        agg_id = uuid.uuid4()
        msg = build_event_message(
            event_id=event_id,
            event_type="OrderPlaced",
            aggregate_type="order",
            aggregate_id=agg_id,
            payload={"order_id": str(agg_id)},
        )
        assert msg["event_type"] == "OrderPlaced"
        assert msg["aggregate_type"] == "order"
        assert msg["event_id"] == str(event_id)
        assert msg["aggregate_id"] == str(agg_id)
        assert msg["payload"]["order_id"] == str(agg_id)

    @patch("apps.common.kafka._get_producer")
    def test_publish_to_kafka_success(self, mock_get_producer):
        mock_producer = MagicMock()
        mock_get_producer.return_value = mock_producer

        result = publish_to_kafka(
            topic="test.topic",
            key="test-key",
            value={"test": "data"},
        )
        assert result is True
        mock_producer.produce.assert_called_once()
        mock_producer.flush.assert_called_once()

    @patch("apps.common.kafka._get_producer")
    def test_publish_to_kafka_unavailable(self, mock_get_producer):
        mock_get_producer.return_value = None

        result = publish_to_kafka(
            topic="test.topic",
            key="test-key",
            value={"test": "data"},
        )
        assert result is False


# ---------------------------------------------------------------------------
# Outbox dispatcher with Kafka integration
# ---------------------------------------------------------------------------
class TestOutboxDispatcherKafka:
    @patch("apps.common.tasks.publish_to_kafka")
    @patch("celery.current_app")
    def test_dispatcher_publishes_to_kafka(
        self, mock_celery_app, mock_kafka, customer, product,
    ):
        order = Order.objects.create(customer=customer, status=OrderStatus.PLACED)
        OrderLine.objects.create(
            order=order, product=product, qty=1, price=Decimal("10.00"),
        )

        publish_outbox_event(
            aggregate_type="order",
            aggregate_id=order.pk,
            event_type="OrderPlaced",
            payload={"order_id": str(order.pk)},
        )

        mock_kafka.return_value = True
        result = dispatch_outbox_events()

        assert result["dispatched"] == 1
        assert result["streamed"] == 1
        mock_kafka.assert_called_once()

        # Verify event was marked published
        event = OutboxEvent.objects.first()
        assert event.published is True

    @patch("apps.common.tasks.publish_to_kafka")
    @patch("celery.current_app")
    def test_dispatcher_handles_kafka_failure_gracefully(
        self, mock_celery_app, mock_kafka, customer, product,
    ):
        order = Order.objects.create(customer=customer, status=OrderStatus.PLACED)
        publish_outbox_event(
            aggregate_type="order",
            aggregate_id=order.pk,
            event_type="OrderPlaced",
            payload={"order_id": str(order.pk)},
        )

        mock_kafka.return_value = False  # Kafka unavailable
        result = dispatch_outbox_events()

        # Event is still dispatched to Celery, just not streamed
        assert result["dispatched"] == 1
        assert result["streamed"] == 0

        # Event should still be marked published (Celery dispatch succeeded)
        event = OutboxEvent.objects.first()
        assert event.published is True


# ---------------------------------------------------------------------------
# /metrics endpoint
# ---------------------------------------------------------------------------
class TestMetricsEndpoint:
    def test_metrics_returns_prometheus_format(self, api_client):
        resp = api_client.get("/metrics/")
        assert resp.status_code == 200
        assert "text/plain" in resp["Content-Type"]
        content = resp.content.decode()
        # Should contain default Python process metrics at minimum
        assert "python_info" in content or "process_" in content or "eventshop_" in content


# ---------------------------------------------------------------------------
# Analytics consumer event processing (unit test)
# ---------------------------------------------------------------------------
class TestAnalyticsConsumerProcessing:
    def test_process_order_placed_event(self):
        from apps.common.management.commands.run_analytics_consumer import (
            ORDERS_PLACED_TOTAL,
            Command,
        )

        cmd = Command()
        before = ORDERS_PLACED_TOTAL._value.get()
        cmd._process_event(
            {"event_type": "OrderPlaced", "payload": {"order_id": "test-123"}},
            "order.events",
        )
        after = ORDERS_PLACED_TOTAL._value.get()
        assert after == before + 1

    def test_process_stock_reserved_event(self):
        from apps.common.management.commands.run_analytics_consumer import (
            STOCK_RESERVED_TOTAL,
            Command,
        )

        cmd = Command()
        cmd._process_event(
            {
                "event_type": "StockReserved",
                "payload": {"sku": "TEST-SKU", "qty": 5},
            },
            "inventory.events",
        )
        val = STOCK_RESERVED_TOTAL.labels(sku="TEST-SKU")._value.get()
        assert val >= 5

    def test_process_payment_confirmed_event(self):
        from apps.common.management.commands.run_analytics_consumer import (
            PAYMENTS_CONFIRMED_TOTAL,
            Command,
        )

        cmd = Command()
        before = PAYMENTS_CONFIRMED_TOTAL._value.get()
        cmd._process_event(
            {"event_type": "PaymentConfirmed", "payload": {"order_id": "pay-123"}},
            "order.events",
        )
        after = PAYMENTS_CONFIRMED_TOTAL._value.get()
        assert after == before + 1
