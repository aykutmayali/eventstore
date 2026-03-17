"""Tests for the Redis-backed token-bucket rate limiter.

Uses unittest.mock to replace the real Redis connection so that
tests run without a Redis server.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

from apps.common.throttles import (
    TokenBucketThrottle,
    _script_sha_reset,
    consume_token,
)
from apps.customers.models import Customer
from apps.orders.models import Order, OrderLine, OrderStatus
from apps.products.models import Product


@pytest.fixture()
def customer():
    return Customer.objects.create(email="ratelimit@example.com")


@pytest.fixture()
def product():
    return Product.objects.create(
        sku="RL-SKU", name="Rate Limit Product", price=Decimal("10.00"),
    )


@pytest.fixture()
def draft_order(customer, product):
    order = Order.objects.create(customer=customer, status=OrderStatus.DRAFT)
    OrderLine.objects.create(
        order=order, product=product, qty=1, price=Decimal("10.00"),
    )
    order.total_amount = Decimal("10.00")
    order.save(update_fields=["total_amount"])
    return order


@pytest.fixture(autouse=True)
def _reset_script_sha():
    """Reset the cached Lua script SHA before each test."""
    _script_sha_reset()
    yield
    _script_sha_reset()


# ---------------------------------------------------------------------------
# consume_token unit tests (mocked Redis)
# ---------------------------------------------------------------------------
class TestConsumeToken:
    def test_allowed_when_tokens_available(self):
        mock_conn = MagicMock()
        mock_conn.script_load.return_value = "fake_sha"
        mock_conn.evalsha.return_value = [1, "9.0", "0"]

        with patch("apps.common.throttles._get_redis", return_value=mock_conn):
            allowed, remaining, retry = consume_token(
                "test_key", capacity=10, refill_rate=2.0,
            )

        assert allowed is True
        assert remaining == 9.0
        assert retry == 0.0

    def test_denied_when_no_tokens(self):
        mock_conn = MagicMock()
        mock_conn.script_load.return_value = "fake_sha"
        mock_conn.evalsha.return_value = [0, "0.0", "0.5"]

        with patch("apps.common.throttles._get_redis", return_value=mock_conn):
            allowed, remaining, retry = consume_token(
                "test_key", capacity=10, refill_rate=2.0,
            )

        assert allowed is False
        assert remaining == 0.0
        assert retry == 0.5

    def test_passes_correct_args_to_lua(self):
        mock_conn = MagicMock()
        mock_conn.script_load.return_value = "fake_sha"
        mock_conn.evalsha.return_value = [1, "4.0", "0"]

        with (
            patch("apps.common.throttles._get_redis", return_value=mock_conn),
            patch("apps.common.throttles.time") as mock_time,
        ):
            mock_time.time.return_value = 1000.0
            consume_token("my_key", capacity=5, refill_rate=1.0, tokens=1)

        mock_conn.evalsha.assert_called_once_with(
            "fake_sha", 1, "my_key", "5", "1.0", "1000.0", "1",
        )


# ---------------------------------------------------------------------------
# TokenBucketThrottle DRF integration tests
# ---------------------------------------------------------------------------
class TestTokenBucketThrottle:
    def test_get_ident_anonymous(self):
        throttle = TokenBucketThrottle()
        request = MagicMock()
        request.user = MagicMock()
        request.user.is_authenticated = False
        request.META = {"REMOTE_ADDR": "192.168.1.1"}

        ident = throttle.get_ident(request)
        assert ident == "ip:192.168.1.1"

    def test_get_ident_with_xff(self):
        throttle = TokenBucketThrottle()
        request = MagicMock()
        request.user = MagicMock()
        request.user.is_authenticated = False
        request.META = {
            "HTTP_X_FORWARDED_FOR": "10.0.0.1, 10.0.0.2",
            "REMOTE_ADDR": "192.168.1.1",
        }

        ident = throttle.get_ident(request)
        assert ident == "ip:10.0.0.1"

    def test_get_ident_authenticated(self):
        throttle = TokenBucketThrottle()
        request = MagicMock()
        request.user = MagicMock()
        request.user.is_authenticated = True
        request.user.pk = 42

        ident = throttle.get_ident(request)
        assert ident == "user:42"

    def test_allow_request_when_tokens_available(self):
        throttle = TokenBucketThrottle()
        request = MagicMock()
        request.user = MagicMock()
        request.user.is_authenticated = False
        request.META = {"REMOTE_ADDR": "1.2.3.4"}

        with patch(
            "apps.common.throttles.consume_token",
            return_value=(True, 9.0, 0.0),
        ):
            assert throttle.allow_request(request, None) is True

    def test_deny_request_when_exhausted(self):
        throttle = TokenBucketThrottle()
        request = MagicMock()
        request.user = MagicMock()
        request.user.is_authenticated = False
        request.META = {"REMOTE_ADDR": "1.2.3.4"}

        with patch(
            "apps.common.throttles.consume_token",
            return_value=(False, 0.0, 1.5),
        ):
            assert throttle.allow_request(request, None) is False
            assert throttle.wait() == 1.5

    def test_fail_open_on_redis_error(self):
        throttle = TokenBucketThrottle()
        request = MagicMock()
        request.user = MagicMock()
        request.user.is_authenticated = False
        request.META = {"REMOTE_ADDR": "1.2.3.4"}

        with patch(
            "apps.common.throttles.consume_token",
            side_effect=ConnectionError("Redis down"),
        ):
            # Should allow request when Redis is down (fail-open)
            assert throttle.allow_request(request, None) is True


# ---------------------------------------------------------------------------
# API-level rate limiting tests (place endpoint)
# ---------------------------------------------------------------------------
class TestPlaceEndpointRateLimiting:
    @pytest.fixture()
    def api_client(self):
        return APIClient()

    def test_place_succeeds_when_not_throttled(
        self, api_client, draft_order,
    ):
        with patch(
            "apps.common.throttles.consume_token",
            return_value=(True, 9.0, 0.0),
        ):
            resp = api_client.post(
                f"/api/orders/{draft_order.pk}/place/",
            )

        assert resp.status_code == 200
        draft_order.refresh_from_db()
        assert draft_order.status == OrderStatus.PLACED

    def test_place_returns_429_when_throttled(
        self, api_client, draft_order,
    ):
        with patch(
            "apps.common.throttles.consume_token",
            return_value=(False, 0.0, 2.0),
        ):
            resp = api_client.post(
                f"/api/orders/{draft_order.pk}/place/",
            )

        assert resp.status_code == 429
        # Order should remain DRAFT
        draft_order.refresh_from_db()
        assert draft_order.status == OrderStatus.DRAFT
