import uuid
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.customers.models import Customer
from apps.inventory.models import InventoryItem
from apps.orders.models import Order
from apps.products.models import Product


@pytest.fixture()
def api_client():
    return APIClient()


@pytest.fixture()
def product():
    return Product.objects.create(sku="API-SKU-001", name="API Widget", price=Decimal("19.99"))


@pytest.fixture()
def customer():
    return Customer.objects.create(email="api@example.com", first_name="Api", last_name="User")


# ---------------------------------------------------------------------------
# Product API tests
# ---------------------------------------------------------------------------
class TestProductAPI:
    def test_list_products(self, api_client, product):
        resp = api_client.get("/api/products/")
        assert resp.status_code == 200
        assert resp.data["count"] == 1
        assert resp.data["results"][0]["sku"] == "API-SKU-001"

    def test_create_product(self, api_client):
        resp = api_client.post("/api/products/", {
            "sku": "NEW-SKU",
            "name": "New Product",
            "price": "29.99",
        })
        assert resp.status_code == 201
        assert resp.data["sku"] == "NEW-SKU"

    def test_retrieve_product(self, api_client, product):
        resp = api_client.get(f"/api/products/{product.pk}/")
        assert resp.status_code == 200
        assert resp.data["name"] == "API Widget"

    def test_filter_by_sku(self, api_client, product):
        Product.objects.create(sku="OTHER-SKU", name="Other", price=Decimal("5.00"))
        resp = api_client.get("/api/products/?sku=API-SKU-001")
        assert resp.status_code == 200
        assert resp.data["count"] == 1


# ---------------------------------------------------------------------------
# Inventory API tests
# ---------------------------------------------------------------------------
class TestInventoryAPI:
    def test_list_inventory(self, api_client, product):
        InventoryItem.objects.create(product=product, on_hand=100, reserved=10)
        resp = api_client.get("/api/inventory/")
        assert resp.status_code == 200
        assert resp.data["count"] == 1
        assert resp.data["results"][0]["available"] == 90

    def test_filter_by_sku(self, api_client, product):
        InventoryItem.objects.create(product=product, on_hand=50)
        resp = api_client.get(f"/api/inventory/?product__sku={product.sku}")
        assert resp.status_code == 200
        assert resp.data["count"] == 1


# ---------------------------------------------------------------------------
# Order API tests
# ---------------------------------------------------------------------------
class TestOrderAPI:
    def test_create_order(self, api_client, customer, product):
        resp = api_client.post(
            "/api/orders/",
            {
                "customer": str(customer.pk),
                "lines": [
                    {"product": str(product.pk), "qty": 2, "price": "19.99"},
                ],
            },
            format="json",
        )
        assert resp.status_code == 201
        assert resp.data["status"] == "DRAFT"
        assert Decimal(resp.data["total_amount"]) == Decimal("39.98")
        assert len(resp.data["lines"]) == 1

    def test_retrieve_order(self, api_client, customer, product):
        # Create order first
        resp = api_client.post(
            "/api/orders/",
            {
                "customer": str(customer.pk),
                "lines": [{"product": str(product.pk), "qty": 1, "price": "19.99"}],
            },
            format="json",
        )
        order_id = resp.data["id"]

        resp = api_client.get(f"/api/orders/{order_id}/")
        assert resp.status_code == 200
        assert resp.data["id"] == order_id

    def test_place_order(self, api_client, customer, product):
        resp = api_client.post(
            "/api/orders/",
            {
                "customer": str(customer.pk),
                "lines": [{"product": str(product.pk), "qty": 1, "price": "19.99"}],
            },
            format="json",
        )
        order_id = resp.data["id"]

        resp = api_client.post(f"/api/orders/{order_id}/place/")
        assert resp.status_code == 200
        assert resp.data["status"] == "PLACED"

    def test_place_already_placed_order_returns_conflict(self, api_client, customer, product):
        resp = api_client.post(
            "/api/orders/",
            {
                "customer": str(customer.pk),
                "lines": [{"product": str(product.pk), "qty": 1, "price": "19.99"}],
            },
            format="json",
        )
        order_id = resp.data["id"]

        api_client.post(f"/api/orders/{order_id}/place/")
        resp = api_client.post(f"/api/orders/{order_id}/place/")
        assert resp.status_code == 409

    def test_pay_requires_reserved_status(self, api_client, customer, product):
        resp = api_client.post(
            "/api/orders/",
            {
                "customer": str(customer.pk),
                "lines": [{"product": str(product.pk), "qty": 1, "price": "19.99"}],
            },
            format="json",
        )
        order_id = resp.data["id"]

        # Try to pay a DRAFT order -- should fail
        resp = api_client.post(f"/api/orders/{order_id}/pay/")
        assert resp.status_code == 409

    def test_list_orders_filter_by_status(self, api_client, customer, product):
        api_client.post(
            "/api/orders/",
            {
                "customer": str(customer.pk),
                "lines": [{"product": str(product.pk), "qty": 1, "price": "10.00"}],
            },
            format="json",
        )

        resp = api_client.get("/api/orders/?status=DRAFT")
        assert resp.status_code == 200
        assert resp.data["count"] == 1

        resp = api_client.get("/api/orders/?status=PLACED")
        assert resp.status_code == 200
        assert resp.data["count"] == 0


# ---------------------------------------------------------------------------
# Idempotency key tests
# ---------------------------------------------------------------------------
class TestIdempotencyKey:
    def test_idempotent_post_returns_same_response(self, api_client, customer, product):
        idem_key = str(uuid.uuid4())
        payload = {
            "customer": str(customer.pk),
            "lines": [{"product": str(product.pk), "qty": 1, "price": "19.99"}],
        }

        resp1 = api_client.post(
            "/api/orders/", payload, format="json",
            HTTP_IDEMPOTENCY_KEY=idem_key,
        )
        assert resp1.status_code == 201

        resp2 = api_client.post(
            "/api/orders/", payload, format="json",
            HTTP_IDEMPOTENCY_KEY=idem_key,
        )
        # Should return cached response, same order id
        assert resp2.status_code == 201
        resp2_data = resp2.json() if not hasattr(resp2, "data") else resp2.data
        assert resp2_data["id"] == resp1.data["id"]

        # Only one order should exist
        assert Order.objects.count() == 1

    def test_different_keys_create_different_orders(self, api_client, customer, product):
        payload = {
            "customer": str(customer.pk),
            "lines": [{"product": str(product.pk), "qty": 1, "price": "19.99"}],
        }

        resp1 = api_client.post(
            "/api/orders/", payload, format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        resp2 = api_client.post(
            "/api/orders/", payload, format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )

        assert resp1.data["id"] != resp2.data["id"]
        assert Order.objects.count() == 2

    def test_no_idempotency_key_creates_new_order_each_time(self, api_client, customer, product):
        payload = {
            "customer": str(customer.pk),
            "lines": [{"product": str(product.pk), "qty": 1, "price": "19.99"}],
        }

        api_client.post("/api/orders/", payload, format="json")
        api_client.post("/api/orders/", payload, format="json")

        assert Order.objects.count() == 2


# ---------------------------------------------------------------------------
# OpenAPI schema tests
# ---------------------------------------------------------------------------
class TestOpenAPISchema:
    def test_schema_includes_order_endpoints(self, api_client):
        resp = api_client.get("/api/schema/?format=json")
        assert resp.status_code == 200
        paths = resp.data.get("paths", {}) if isinstance(resp.data, dict) else {}
        assert "/api/orders/" in paths
        assert "/api/orders/{id}/place/" in paths
        assert "/api/orders/{id}/pay/" in paths
        assert "/api/products/" in paths
        assert "/api/inventory/" in paths
