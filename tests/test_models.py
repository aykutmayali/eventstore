from decimal import Decimal

import pytest
from django.db import IntegrityError

from apps.customers.models import Customer
from apps.inventory.models import InventoryItem
from apps.orders.models import (
    InvalidStatusTransition,
    Order,
    OrderLine,
    OrderStatus,
)
from apps.products.models import Product


# ---------------------------------------------------------------------------
# Product tests
# ---------------------------------------------------------------------------
class TestProduct:
    def test_create_product(self):
        p = Product.objects.create(sku="SKU-001", name="Widget", price=Decimal("9.99"))
        assert p.sku == "SKU-001"
        assert p.name == "Widget"
        assert p.price == Decimal("9.99")
        assert p.is_active is True

    def test_unique_sku_constraint(self):
        Product.objects.create(sku="SKU-DUP", name="First", price=Decimal("1.00"))
        with pytest.raises(IntegrityError):
            Product.objects.create(sku="SKU-DUP", name="Second", price=Decimal("2.00"))

    def test_str(self):
        p = Product.objects.create(sku="SKU-STR", name="Gadget", price=Decimal("5.00"))
        assert str(p) == "SKU-STR - Gadget"


# ---------------------------------------------------------------------------
# Customer tests
# ---------------------------------------------------------------------------
class TestCustomer:
    def test_create_customer(self):
        c = Customer.objects.create(email="test@example.com", first_name="John")
        assert c.email == "test@example.com"
        assert c.is_active is True

    def test_unique_email_constraint(self):
        Customer.objects.create(email="dup@example.com")
        with pytest.raises(IntegrityError):
            Customer.objects.create(email="dup@example.com")

    def test_str(self):
        c = Customer.objects.create(email="str@example.com")
        assert str(c) == "str@example.com"


# ---------------------------------------------------------------------------
# InventoryItem tests
# ---------------------------------------------------------------------------
class TestInventoryItem:
    @pytest.fixture()
    def product(self):
        return Product.objects.create(sku="INV-SKU", name="Inv Product", price=Decimal("10.00"))

    def test_create_inventory_item(self, product):
        item = InventoryItem.objects.create(product=product, on_hand=100, reserved=10)
        assert item.on_hand == 100
        assert item.reserved == 10

    def test_available_property(self, product):
        item = InventoryItem.objects.create(product=product, on_hand=50, reserved=20)
        assert item.available == 30

    def test_default_warehouse(self, product):
        item = InventoryItem.objects.create(product=product, on_hand=10)
        assert item.warehouse == "default"


# ---------------------------------------------------------------------------
# Order status machine tests
# ---------------------------------------------------------------------------
class TestOrderStatusMachine:
    @pytest.fixture()
    def customer(self):
        return Customer.objects.create(email="order@example.com")

    @pytest.fixture()
    def order(self, customer):
        return Order.objects.create(customer=customer)

    def test_default_status_is_draft(self, order):
        assert order.status == OrderStatus.DRAFT

    def test_valid_transition_draft_to_placed(self, order):
        order.transition_to(OrderStatus.PLACED)
        assert order.status == OrderStatus.PLACED

    def test_valid_transition_placed_to_reserved(self, order):
        order.transition_to(OrderStatus.PLACED)
        order.transition_to(OrderStatus.RESERVED)
        assert order.status == OrderStatus.RESERVED

    def test_valid_transition_reserved_to_paid(self, order):
        order.transition_to(OrderStatus.PLACED)
        order.transition_to(OrderStatus.RESERVED)
        order.transition_to(OrderStatus.PAID)
        assert order.status == OrderStatus.PAID

    def test_valid_transition_paid_to_shipped(self, order):
        order.transition_to(OrderStatus.PLACED)
        order.transition_to(OrderStatus.RESERVED)
        order.transition_to(OrderStatus.PAID)
        order.transition_to(OrderStatus.SHIPPED)
        assert order.status == OrderStatus.SHIPPED

    def test_cancel_from_any_active_state(self, order):
        order.transition_to(OrderStatus.PLACED)
        order.transition_to(OrderStatus.CANCELLED)
        assert order.status == OrderStatus.CANCELLED

    def test_invalid_transition_draft_to_paid(self, order):
        with pytest.raises(InvalidStatusTransition):
            order.transition_to(OrderStatus.PAID)

    def test_invalid_transition_shipped_to_anything(self, order):
        order.transition_to(OrderStatus.PLACED)
        order.transition_to(OrderStatus.RESERVED)
        order.transition_to(OrderStatus.PAID)
        order.transition_to(OrderStatus.SHIPPED)
        with pytest.raises(InvalidStatusTransition):
            order.transition_to(OrderStatus.CANCELLED)

    def test_invalid_transition_cancelled_to_anything(self, order):
        order.transition_to(OrderStatus.CANCELLED)
        with pytest.raises(InvalidStatusTransition):
            order.transition_to(OrderStatus.DRAFT)


# ---------------------------------------------------------------------------
# OrderLine tests
# ---------------------------------------------------------------------------
class TestOrderLine:
    @pytest.fixture()
    def product(self):
        return Product.objects.create(sku="OL-SKU", name="Line Product", price=Decimal("25.00"))

    @pytest.fixture()
    def customer(self):
        return Customer.objects.create(email="line@example.com")

    @pytest.fixture()
    def order(self, customer):
        return Order.objects.create(customer=customer)

    def test_create_order_line(self, order, product):
        line = OrderLine.objects.create(
            order=order, product=product, qty=3, price=Decimal("25.00")
        )
        assert line.qty == 3
        assert line.price == Decimal("25.00")

    def test_line_total_property(self, order, product):
        line = OrderLine.objects.create(
            order=order, product=product, qty=4, price=Decimal("10.50")
        )
        assert line.line_total == 4 * Decimal("10.50")
