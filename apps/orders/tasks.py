"""Celery tasks for order processing."""

from __future__ import annotations

import logging

from celery import shared_task
from django.db import transaction

from apps.common.outbox import is_already_processed, mark_processed

logger = logging.getLogger(__name__)

CONSUMER_RESERVE = "reserve_stock"
CONSUMER_RELEASE = "release_stock"
CONSUMER_EMAIL = "send_email"


@shared_task(bind=True, max_retries=3, default_retry_delay=5)
def reserve_stock(self, order_id: str, event_id: str | None = None) -> dict:
    """Reserve inventory for all lines in an order.

    Idempotent: if event_id was already processed, skip silently.
    Uses select_for_update to prevent race conditions.
    """
    from apps.inventory.models import InventoryItem
    from apps.orders.models import Order, OrderStatus

    if event_id and is_already_processed(event_id, CONSUMER_RESERVE):
        logger.info("reserve_stock: event %s already processed, skipping", event_id)
        return {"status": "skipped", "reason": "duplicate"}

    try:
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order_id)

            if order.status != OrderStatus.PLACED:
                logger.warning(
                    "reserve_stock: order %s status is %s, expected PLACED",
                    order_id, order.status,
                )
                return {"status": "skipped", "reason": f"status={order.status}"}

            lines = order.lines.select_related("product").all()
            reserved_items = []

            for line in lines:
                # FIFO: oldest inventory first
                items = (
                    InventoryItem.objects
                    .filter(product=line.product)
                    .select_for_update()
                    .order_by("created_at")
                )

                remaining_qty = line.qty
                for item in items:
                    if remaining_qty <= 0:
                        break
                    available = item.on_hand - item.reserved
                    if available <= 0:
                        continue
                    to_reserve = min(remaining_qty, available)
                    item.reserved += to_reserve
                    item.save(update_fields=["reserved", "updated_at"])
                    remaining_qty -= to_reserve
                    reserved_items.append({
                        "inventory_id": str(item.pk),
                        "product_id": str(line.product_id),
                        "reserved_qty": to_reserve,
                    })

                if remaining_qty > 0:
                    logger.warning(
                        "reserve_stock: insufficient stock for product %s "
                        "(needed=%d, short=%d)",
                        line.product_id, line.qty, remaining_qty,
                    )

            order.transition_to(OrderStatus.RESERVED)
            order.save(update_fields=["status", "updated_at"])

            if event_id:
                mark_processed(event_id, CONSUMER_RESERVE)

        return {"status": "reserved", "order_id": order_id, "items": reserved_items}

    except Exception as exc:
        logger.exception("reserve_stock failed for order %s", order_id)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=5)
def release_stock(self, order_id: str, event_id: str | None = None) -> dict:
    """Release reserved inventory when an order is cancelled.

    Idempotent: if event_id was already processed, skip silently.
    """
    from apps.inventory.models import InventoryItem
    from apps.orders.models import Order, OrderStatus

    if event_id and is_already_processed(event_id, CONSUMER_RELEASE):
        logger.info("release_stock: event %s already processed, skipping", event_id)
        return {"status": "skipped", "reason": "duplicate"}

    try:
        with transaction.atomic():
            order = Order.objects.get(pk=order_id)

            if order.status != OrderStatus.CANCELLED:
                return {"status": "skipped", "reason": f"status={order.status}"}

            lines = order.lines.select_related("product").all()
            released_items = []

            for line in lines:
                items = (
                    InventoryItem.objects
                    .filter(product=line.product, reserved__gt=0)
                    .select_for_update()
                    .order_by("-created_at")  # reverse FIFO for release
                )

                remaining_qty = line.qty
                for item in items:
                    if remaining_qty <= 0:
                        break
                    to_release = min(remaining_qty, item.reserved)
                    item.reserved -= to_release
                    item.save(update_fields=["reserved", "updated_at"])
                    remaining_qty -= to_release
                    released_items.append({
                        "inventory_id": str(item.pk),
                        "product_id": str(line.product_id),
                        "released_qty": to_release,
                    })

            if event_id:
                mark_processed(event_id, CONSUMER_RELEASE)

        return {"status": "released", "order_id": order_id, "items": released_items}

    except Exception as exc:
        logger.exception("release_stock failed for order %s", order_id)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=5)
def send_order_email(self, order_id: str, event_type: str, event_id: str | None = None) -> dict:
    """Mock email sender -- logs instead of actually sending.

    Idempotent: if event_id was already processed, skip silently.
    """
    from apps.orders.models import Order

    if event_id and is_already_processed(event_id, CONSUMER_EMAIL):
        logger.info("send_order_email: event %s already processed, skipping", event_id)
        return {"status": "skipped", "reason": "duplicate"}

    try:
        order = Order.objects.select_related("customer").get(pk=order_id)
        logger.info(
            "MOCK EMAIL: [%s] Order %s -> %s (total=%s)",
            event_type, order.pk, order.customer.email, order.total_amount,
        )

        if event_id:
            mark_processed(event_id, CONSUMER_EMAIL)

        return {
            "status": "sent",
            "order_id": order_id,
            "email": order.customer.email,
            "event_type": event_type,
        }

    except Exception as exc:
        logger.exception("send_order_email failed for order %s", order_id)
        raise self.retry(exc=exc)
