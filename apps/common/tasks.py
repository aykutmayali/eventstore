"""Outbox dispatcher: reads unpublished outbox events and dispatches Celery tasks."""

from __future__ import annotations

import logging

from celery import shared_task

from apps.common.models import OutboxEvent

logger = logging.getLogger(__name__)

# Map event_type -> list of (task_path, extra_kwargs)
EVENT_HANDLERS: dict[str, list[str]] = {
    "OrderPlaced": [
        "apps.orders.tasks.reserve_stock",
        "apps.orders.tasks.send_order_email",
    ],
    "OrderCancelled": [
        "apps.orders.tasks.release_stock",
        "apps.orders.tasks.send_order_email",
    ],
    "PaymentConfirmed": [
        "apps.orders.tasks.send_order_email",
    ],
}


@shared_task
def dispatch_outbox_events(batch_size: int = 50) -> dict:
    """Poll unpublished outbox events and dispatch them to the appropriate Celery tasks.

    This task should be called periodically (e.g. via celery beat every few seconds).
    """
    from celery import current_app

    events = OutboxEvent.objects.filter(published=False).order_by("created_at")[:batch_size]
    dispatched = 0

    for event in events:
        handlers = EVENT_HANDLERS.get(event.event_type, [])
        if not handlers:
            logger.warning("No handlers for event type %s", event.event_type)
            event.published = True
            event.save(update_fields=["published"])
            continue

        for task_path in handlers:
            kwargs = {
                "order_id": str(event.aggregate_id),
                "event_id": str(event.pk),
            }
            # send_order_email needs event_type kwarg
            if "send_order_email" in task_path:
                kwargs["event_type"] = event.event_type

            current_app.send_task(task_path, kwargs=kwargs)
            logger.info(
                "Dispatched %s for event %s (order=%s)",
                task_path, event.pk, event.aggregate_id,
            )

        event.published = True
        event.save(update_fields=["published"])
        dispatched += 1

    return {"dispatched": dispatched}
