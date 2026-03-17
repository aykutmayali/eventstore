"""Tests for Celery tasks, outbox pattern, and idempotent consumers."""

from decimal import Decimal

import pytest

from apps.common.models import OutboxEvent, ProcessedEvent
from apps.common.outbox import is_already_processed, mark_processed, publish_outbox_event
from apps.customers.models import Customer
from apps.inventory.models import InventoryItem
from apps.orders.models import Order, OrderLine, OrderStatus
from apps.orders.tasks import release_stock, reserve_stock, send_order_email
from apps.products.models import Product


@pytest.fixture()
def customer():
    return Customer.objects.create(email="task@example.com")


@pytest.fixture()
def product():
    return Product.objects.create(sku="TASK-SKU", name="Task Product", price=Decimal("10.00"))


@pytest.fixture()
def inventory(product):
    return InventoryItem.objects.create(product=product, on_hand=100, reserved=0)


@pytest.fixture()
def placed_order(customer, product):
    order = Order.objects.create(customer=customer, status=OrderStatus.PLACED)
    OrderLine.objects.create(order=order, product=product, qty=5, price=Decimal("10.00"))
    order.total_amount = Decimal("50.00")
    order.save(update_fields=["total_amount"])
    return order


# ---------------------------------------------------------------------------
# Outbox helper tests
# ---------------------------------------------------------------------------
class TestOutboxHelpers:
    def test_publish_outbox_event(self, placed_order):
        event = publish_outbox_event(
            aggregate_type="order",
            aggregate_id=placed_order.pk,
            event_type="OrderPlaced",
            payload={"order_id": str(placed_order.pk)},
        )
        assert event.published is False
        assert event.event_type == "OrderPlaced"
        assert OutboxEvent.objects.count() == 1

    def test_mark_processed_first_time(self, placed_order):
        event = publish_outbox_event(
            aggregate_type="order",
            aggregate_id=placed_order.pk,
            event_type="OrderPlaced",
        )
        assert mark_processed(event.pk, "test_consumer") is True
        assert ProcessedEvent.objects.count() == 1

    def test_mark_processed_duplicate(self, placed_order):
        event = publish_outbox_event(
            aggregate_type="order",
            aggregate_id=placed_order.pk,
            event_type="OrderPlaced",
        )
        mark_processed(event.pk, "test_consumer")
        assert mark_processed(event.pk, "test_consumer") is False
        assert ProcessedEvent.objects.count() == 1

    def test_is_already_processed(self, placed_order):
        event = publish_outbox_event(
            aggregate_type="order",
            aggregate_id=placed_order.pk,
            event_type="OrderPlaced",
        )
        assert is_already_processed(event.pk, "test_consumer") is False
        mark_processed(event.pk, "test_consumer")
        assert is_already_processed(event.pk, "test_consumer") is True


# ---------------------------------------------------------------------------
# reserve_stock task tests
# ---------------------------------------------------------------------------
class TestReserveStockTask:
    def test_reserve_stock_success(self, placed_order, inventory):
        result = reserve_stock(str(placed_order.pk))
        assert result["status"] == "reserved"

        inventory.refresh_from_db()
        assert inventory.reserved == 5

        placed_order.refresh_from_db()
        assert placed_order.status == OrderStatus.RESERVED

    def test_reserve_stock_idempotent_with_event_id(self, placed_order, inventory):
        event = publish_outbox_event(
            aggregate_type="order",
            aggregate_id=placed_order.pk,
            event_type="OrderPlaced",
        )

        result1 = reserve_stock(str(placed_order.pk), event_id=str(event.pk))
        assert result1["status"] == "reserved"

        # Second call with same event_id should skip
        result2 = reserve_stock(str(placed_order.pk), event_id=str(event.pk))
        assert result2["status"] == "skipped"
        assert result2["reason"] == "duplicate"

        # Stock should only be reserved once
        inventory.refresh_from_db()
        assert inventory.reserved == 5

    def test_reserve_stock_partial_when_insufficient(self, placed_order, product):
        # Only 3 available, order needs 5
        InventoryItem.objects.create(product=product, on_hand=3, reserved=0)

        result = reserve_stock(str(placed_order.pk))
        assert result["status"] == "reserved"

        placed_order.refresh_from_db()
        assert placed_order.status == OrderStatus.RESERVED

    def test_reserve_stock_skips_non_placed_order(self, customer, product, inventory):
        order = Order.objects.create(customer=customer, status=OrderStatus.DRAFT)
        OrderLine.objects.create(order=order, product=product, qty=2, price=Decimal("10.00"))

        result = reserve_stock(str(order.pk))
        assert result["status"] == "skipped"

        inventory.refresh_from_db()
        assert inventory.reserved == 0

    def test_reserve_stock_fifo_ordering(self, placed_order, product):
        """Older inventory batches should be reserved first."""
        old_item = InventoryItem.objects.create(
            product=product, on_hand=3, reserved=0, batch_no="OLD",
        )
        new_item = InventoryItem.objects.create(
            product=product, on_hand=10, reserved=0, batch_no="NEW",
        )

        reserve_stock(str(placed_order.pk))

        old_item.refresh_from_db()
        new_item.refresh_from_db()
        # Old batch should be fully reserved first (3), then 2 from new
        assert old_item.reserved == 3
        assert new_item.reserved == 2


# ---------------------------------------------------------------------------
# release_stock task tests
# ---------------------------------------------------------------------------
class TestReleaseStockTask:
    def test_release_stock_success(self, customer, product):
        item = InventoryItem.objects.create(product=product, on_hand=100, reserved=5)
        order = Order.objects.create(customer=customer, status=OrderStatus.CANCELLED)
        OrderLine.objects.create(order=order, product=product, qty=5, price=Decimal("10.00"))

        result = release_stock(str(order.pk))
        assert result["status"] == "released"

        item.refresh_from_db()
        assert item.reserved == 0

    def test_release_stock_idempotent(self, customer, product):
        item = InventoryItem.objects.create(product=product, on_hand=100, reserved=5)
        order = Order.objects.create(customer=customer, status=OrderStatus.CANCELLED)
        OrderLine.objects.create(order=order, product=product, qty=5, price=Decimal("10.00"))

        event = publish_outbox_event(
            aggregate_type="order",
            aggregate_id=order.pk,
            event_type="OrderCancelled",
        )

        result1 = release_stock(str(order.pk), event_id=str(event.pk))
        assert result1["status"] == "released"

        result2 = release_stock(str(order.pk), event_id=str(event.pk))
        assert result2["status"] == "skipped"

        item.refresh_from_db()
        assert item.reserved == 0


# ---------------------------------------------------------------------------
# send_order_email task tests
# ---------------------------------------------------------------------------
class TestSendOrderEmailTask:
    def test_send_email_mock(self, placed_order):
        result = send_order_email(str(placed_order.pk), "OrderPlaced")
        assert result["status"] == "sent"
        assert result["email"] == "task@example.com"
        assert result["event_type"] == "OrderPlaced"

    def test_send_email_idempotent(self, placed_order):
        event = publish_outbox_event(
            aggregate_type="order",
            aggregate_id=placed_order.pk,
            event_type="OrderPlaced",
        )

        result1 = send_order_email(
            str(placed_order.pk), "OrderPlaced", event_id=str(event.pk),
        )
        assert result1["status"] == "sent"

        result2 = send_order_email(
            str(placed_order.pk), "OrderPlaced", event_id=str(event.pk),
        )
        assert result2["status"] == "skipped"


# ---------------------------------------------------------------------------
# Outbox event emission from API tests
# ---------------------------------------------------------------------------
class TestOutboxFromAPI:
    @pytest.fixture()
    def api_client(self):
        from rest_framework.test import APIClient

        return APIClient()

    def test_place_order_creates_outbox_event(self, api_client, customer, product):
        resp = api_client.post(
            "/api/orders/",
            {
                "customer": str(customer.pk),
                "lines": [{"product": str(product.pk), "qty": 1, "price": "10.00"}],
            },
            format="json",
        )
        order_id = resp.data["id"]

        api_client.post(f"/api/orders/{order_id}/place/")

        events = OutboxEvent.objects.filter(event_type="OrderPlaced")
        assert events.count() == 1
        assert str(events.first().aggregate_id) == order_id

    def test_cancel_order_creates_outbox_event(self, api_client, customer, product):
        resp = api_client.post(
            "/api/orders/",
            {
                "customer": str(customer.pk),
                "lines": [{"product": str(product.pk), "qty": 1, "price": "10.00"}],
            },
            format="json",
        )
        order_id = resp.data["id"]

        api_client.post(f"/api/orders/{order_id}/cancel/")

        events = OutboxEvent.objects.filter(event_type="OrderCancelled")
        assert events.count() == 1
