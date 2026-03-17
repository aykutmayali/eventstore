"""Tests for the stock allocation service.

Covers FIFO strategy, fewest-splits greedy heuristic, partial fulfillment,
zero stock, multi-product, and the reserve_stock Celery task integration.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.customers.models import Customer
from apps.inventory.allocation import (
    Strategy,
    allocate,
)
from apps.inventory.models import InventoryItem
from apps.orders.models import Order, OrderLine, OrderStatus
from apps.orders.tasks import reserve_stock
from apps.products.models import Product


@pytest.fixture()
def customer():
    return Customer.objects.create(email="alloc@example.com")


@pytest.fixture()
def product_a():
    return Product.objects.create(
        sku="ALLOC-A", name="Product A", price=Decimal("10.00"),
    )


@pytest.fixture()
def product_b():
    return Product.objects.create(
        sku="ALLOC-B", name="Product B", price=Decimal("20.00"),
    )


# ---------------------------------------------------------------------------
# FIFO strategy tests
# ---------------------------------------------------------------------------
class TestFIFOAllocation:
    def test_single_item_full_allocation(self, product_a):
        item = InventoryItem.objects.create(
            product=product_a, on_hand=50, reserved=0, batch_no="B1",
        )
        demand = [(product_a.pk, 10)]
        result = allocate(demand, InventoryItem.objects.all(), strategy=Strategy.FIFO)

        assert result.fully_fulfilled is True
        assert result.total_allocated == 10
        assert result.total_backordered == 0
        assert result.split_count == 1

        item.refresh_from_db()
        assert item.reserved == 10

    def test_fifo_uses_oldest_batch_first(self, product_a):
        old = InventoryItem.objects.create(
            product=product_a, on_hand=5, reserved=0, batch_no="OLD",
        )
        new = InventoryItem.objects.create(
            product=product_a, on_hand=20, reserved=0, batch_no="NEW",
        )

        demand = [(product_a.pk, 8)]
        result = allocate(demand, InventoryItem.objects.all(), strategy=Strategy.FIFO)

        assert result.fully_fulfilled is True
        assert result.split_count == 2

        old.refresh_from_db()
        new.refresh_from_db()
        assert old.reserved == 5  # fully used
        assert new.reserved == 3  # remaining from newer

    def test_fifo_skips_fully_reserved_items(self, product_a):
        full = InventoryItem.objects.create(
            product=product_a, on_hand=10, reserved=10, batch_no="FULL",
        )
        avail = InventoryItem.objects.create(
            product=product_a, on_hand=10, reserved=0, batch_no="AVAIL",
        )

        demand = [(product_a.pk, 5)]
        result = allocate(demand, InventoryItem.objects.all(), strategy=Strategy.FIFO)

        assert result.fully_fulfilled is True
        full.refresh_from_db()
        avail.refresh_from_db()
        assert full.reserved == 10  # unchanged
        assert avail.reserved == 5

    def test_partial_fulfillment_allowed(self, product_a):
        InventoryItem.objects.create(
            product=product_a, on_hand=3, reserved=0,
        )
        demand = [(product_a.pk, 10)]
        result = allocate(
            demand, InventoryItem.objects.all(),
            strategy=Strategy.FIFO, allow_partial=True,
        )

        assert result.fully_fulfilled is False
        assert result.total_allocated == 3
        assert result.total_backordered == 7

    def test_partial_not_allowed_rejects_line(self, product_a):
        InventoryItem.objects.create(
            product=product_a, on_hand=3, reserved=0,
        )
        demand = [(product_a.pk, 10)]
        result = allocate(
            demand, InventoryItem.objects.all(),
            strategy=Strategy.FIFO, allow_partial=False,
        )

        assert result.fully_fulfilled is False
        assert result.total_allocated == 0
        assert result.total_backordered == 10
        # Item should NOT have been touched
        item = InventoryItem.objects.first()
        assert item.reserved == 0

    def test_zero_stock_produces_backorder(self, product_a):
        demand = [(product_a.pk, 5)]
        result = allocate(demand, InventoryItem.objects.all(), strategy=Strategy.FIFO)

        assert result.fully_fulfilled is False
        assert result.total_allocated == 0
        assert result.total_backordered == 5
        assert len(result.backorders) == 1

    def test_multi_product_allocation(self, product_a, product_b):
        InventoryItem.objects.create(
            product=product_a, on_hand=10, reserved=0,
        )
        InventoryItem.objects.create(
            product=product_b, on_hand=20, reserved=0,
        )

        demand = [(product_a.pk, 3), (product_b.pk, 7)]
        result = allocate(demand, InventoryItem.objects.all(), strategy=Strategy.FIFO)

        assert result.fully_fulfilled is True
        assert result.total_allocated == 10
        assert result.split_count == 2

    def test_multiple_splits_across_batches(self, product_a):
        """Three small batches fulfilling one large demand."""
        InventoryItem.objects.create(
            product=product_a, on_hand=3, reserved=0, batch_no="B1",
        )
        InventoryItem.objects.create(
            product=product_a, on_hand=4, reserved=0, batch_no="B2",
        )
        InventoryItem.objects.create(
            product=product_a, on_hand=5, reserved=0, batch_no="B3",
        )

        demand = [(product_a.pk, 10)]
        result = allocate(demand, InventoryItem.objects.all(), strategy=Strategy.FIFO)

        assert result.fully_fulfilled is True
        assert result.total_allocated == 10
        assert result.split_count == 3


# ---------------------------------------------------------------------------
# Fewest-splits greedy strategy tests
# ---------------------------------------------------------------------------
class TestFewestSplitsAllocation:
    def test_single_item_satisfies(self, product_a):
        """If one item can satisfy the entire demand, use it."""
        small = InventoryItem.objects.create(
            product=product_a, on_hand=5, reserved=0, batch_no="SMALL",
        )
        big = InventoryItem.objects.create(
            product=product_a, on_hand=20, reserved=0, batch_no="BIG",
        )

        demand = [(product_a.pk, 10)]
        result = allocate(
            demand, InventoryItem.objects.all(),
            strategy=Strategy.FEWEST_SPLITS,
        )

        assert result.fully_fulfilled is True
        assert result.split_count == 1  # Only one item used!
        assert result.allocations[0].qty == 10

        big.refresh_from_db()
        small.refresh_from_db()
        assert big.reserved == 10
        assert small.reserved == 0

    def test_best_fit_picks_smallest_sufficient(self, product_a):
        """Among items that can satisfy fully, pick the smallest."""
        InventoryItem.objects.create(
            product=product_a, on_hand=100, reserved=0, batch_no="HUGE",
        )
        medium = InventoryItem.objects.create(
            product=product_a, on_hand=12, reserved=0, batch_no="MEDIUM",
        )
        InventoryItem.objects.create(
            product=product_a, on_hand=5, reserved=0, batch_no="SMALL",
        )

        demand = [(product_a.pk, 10)]
        result = allocate(
            demand, InventoryItem.objects.all(),
            strategy=Strategy.FEWEST_SPLITS,
        )

        assert result.split_count == 1
        # Should pick MEDIUM (12 >= 10, smallest sufficient)
        assert result.allocations[0].inventory_item_id == medium.pk

    def test_largest_first_when_no_single_item(self, product_a):
        """When no single item can satisfy, use largest-first."""
        InventoryItem.objects.create(
            product=product_a, on_hand=4, reserved=0, batch_no="B1",
        )
        b2 = InventoryItem.objects.create(
            product=product_a, on_hand=7, reserved=0, batch_no="B2",
        )
        InventoryItem.objects.create(
            product=product_a, on_hand=3, reserved=0, batch_no="B3",
        )

        demand = [(product_a.pk, 10)]
        result = allocate(
            demand, InventoryItem.objects.all(),
            strategy=Strategy.FEWEST_SPLITS,
        )

        assert result.fully_fulfilled is True
        # Should use B2(7) + B1(3 of 4) = 10, only 2 splits
        assert result.split_count == 2

        b2.refresh_from_db()
        assert b2.reserved == 7  # largest first

    def test_fewest_splits_partial(self, product_a):
        """Partial fulfillment with greedy strategy."""
        InventoryItem.objects.create(
            product=product_a, on_hand=3, reserved=0,
        )
        demand = [(product_a.pk, 10)]
        result = allocate(
            demand, InventoryItem.objects.all(),
            strategy=Strategy.FEWEST_SPLITS, allow_partial=True,
        )

        assert result.fully_fulfilled is False
        assert result.total_allocated == 3
        assert result.total_backordered == 7

    def test_fewest_splits_no_stock(self, product_a):
        demand = [(product_a.pk, 5)]
        result = allocate(
            demand, InventoryItem.objects.all(),
            strategy=Strategy.FEWEST_SPLITS,
        )

        assert result.fully_fulfilled is False
        assert result.total_allocated == 0
        assert result.total_backordered == 5


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
class TestAllocationEdgeCases:
    def test_zero_quantity_demand(self, product_a):
        InventoryItem.objects.create(
            product=product_a, on_hand=10, reserved=0,
        )
        demand = [(product_a.pk, 0)]
        result = allocate(demand, InventoryItem.objects.all())

        assert result.fully_fulfilled is True
        assert result.total_allocated == 0

    def test_empty_demand_list(self):
        result = allocate([], InventoryItem.objects.all())

        assert result.fully_fulfilled is True
        assert result.total_allocated == 0
        assert result.total_backordered == 0

    def test_demand_for_nonexistent_product(self):
        """Demand for a product with no inventory items."""
        import uuid

        fake_id = uuid.uuid4()
        demand = [(fake_id, 5)]
        result = allocate(demand, InventoryItem.objects.all())

        assert result.fully_fulfilled is False
        assert result.total_backordered == 5

    def test_partially_reserved_items(self, product_a):
        """Items with some stock already reserved."""
        InventoryItem.objects.create(
            product=product_a, on_hand=10, reserved=7,
        )
        demand = [(product_a.pk, 5)]
        result = allocate(demand, InventoryItem.objects.all())

        assert result.fully_fulfilled is False
        assert result.total_allocated == 3
        assert result.total_backordered == 2

    def test_exact_match(self, product_a):
        """Demand exactly equals available stock."""
        InventoryItem.objects.create(
            product=product_a, on_hand=10, reserved=0,
        )
        demand = [(product_a.pk, 10)]
        result = allocate(demand, InventoryItem.objects.all())

        assert result.fully_fulfilled is True
        assert result.total_allocated == 10
        assert result.total_backordered == 0


# ---------------------------------------------------------------------------
# Integration with reserve_stock Celery task
# ---------------------------------------------------------------------------
class TestReserveStockWithAllocationService:
    def test_reserve_uses_fifo_by_default(self, customer, product_a):
        old = InventoryItem.objects.create(
            product=product_a, on_hand=3, reserved=0, batch_no="OLD",
        )
        new = InventoryItem.objects.create(
            product=product_a, on_hand=10, reserved=0, batch_no="NEW",
        )
        order = Order.objects.create(
            customer=customer, status=OrderStatus.PLACED,
        )
        OrderLine.objects.create(
            order=order, product=product_a, qty=5, price=Decimal("10.00"),
        )
        order.total_amount = Decimal("50.00")
        order.save(update_fields=["total_amount"])

        result = reserve_stock(str(order.pk))

        assert result["status"] == "reserved"
        assert result["fully_fulfilled"] is True

        old.refresh_from_db()
        new.refresh_from_db()
        assert old.reserved == 3
        assert new.reserved == 2

    def test_reserve_with_fewest_splits(self, customer, product_a):
        InventoryItem.objects.create(
            product=product_a, on_hand=3, reserved=0, batch_no="SMALL",
        )
        big = InventoryItem.objects.create(
            product=product_a, on_hand=20, reserved=0, batch_no="BIG",
        )

        order = Order.objects.create(
            customer=customer, status=OrderStatus.PLACED,
        )
        OrderLine.objects.create(
            order=order, product=product_a, qty=10, price=Decimal("10.00"),
        )
        order.total_amount = Decimal("100.00")
        order.save(update_fields=["total_amount"])

        result = reserve_stock(
            str(order.pk), strategy="fewest_splits",
        )

        assert result["status"] == "reserved"
        assert result["splits"] == 1  # only BIG used

        big.refresh_from_db()
        assert big.reserved == 10

    def test_reserve_reports_backorder(self, customer, product_a):
        InventoryItem.objects.create(
            product=product_a, on_hand=3, reserved=0,
        )
        order = Order.objects.create(
            customer=customer, status=OrderStatus.PLACED,
        )
        OrderLine.objects.create(
            order=order, product=product_a, qty=10, price=Decimal("10.00"),
        )
        order.total_amount = Decimal("100.00")
        order.save(update_fields=["total_amount"])

        result = reserve_stock(str(order.pk))

        assert result["status"] == "reserved"
        assert result["fully_fulfilled"] is False
        assert result["backordered"] == 7
