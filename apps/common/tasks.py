"""Outbox dispatcher: reads unpublished outbox events and dispatches Celery tasks + Kafka."""

from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings

from apps.common.kafka import build_event_message, publish_to_kafka
from apps.common.models import OutboxEvent

logger = logging.getLogger(__name__)

# Map event_type -> list of Celery task paths
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

# Map event_type -> Kafka topic
EVENT_TOPICS: dict[str, str] = {
    "OrderPlaced": settings.KAFKA_TOPIC_ORDER_EVENTS,
    "OrderCancelled": settings.KAFKA_TOPIC_ORDER_EVENTS,
    "PaymentConfirmed": settings.KAFKA_TOPIC_ORDER_EVENTS,
    "StockReserved": settings.KAFKA_TOPIC_INVENTORY_EVENTS,
    "StockReleased": settings.KAFKA_TOPIC_INVENTORY_EVENTS,
}


@shared_task
def dispatch_outbox_events(batch_size: int = 50) -> dict:
    """Poll unpublished outbox events and dispatch to Celery tasks + Kafka topics.

    This task should be called periodically (e.g. via celery beat every few seconds).
    """
    from celery import current_app

    events = OutboxEvent.objects.filter(published=False).order_by("created_at")[:batch_size]
    dispatched = 0
    streamed = 0

    for event in events:
        # 1) Dispatch Celery tasks
        handlers = EVENT_HANDLERS.get(event.event_type, [])
        for task_path in handlers:
            kwargs = {
                "order_id": str(event.aggregate_id),
                "event_id": str(event.pk),
            }
            if "send_order_email" in task_path:
                kwargs["event_type"] = event.event_type

            current_app.send_task(task_path, kwargs=kwargs)
            logger.info(
                "Dispatched %s for event %s (order=%s)",
                task_path, event.pk, event.aggregate_id,
            )

        # 2) Publish to Kafka topic
        topic = EVENT_TOPICS.get(event.event_type)
        if topic:
            message = build_event_message(
                event_id=event.pk,
                event_type=event.event_type,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                payload=event.payload,
            )
            if publish_to_kafka(
                topic=topic,
                key=str(event.aggregate_id),
                value=message,
                headers={"event_type": event.event_type},
            ):
                streamed += 1

        event.published = True
        event.save(update_fields=["published"])
        dispatched += 1

    return {"dispatched": dispatched, "streamed": streamed}
