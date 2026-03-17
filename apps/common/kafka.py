"""Kafka/Redpanda producer and consumer utilities."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


def _get_producer():
    """Lazy-init a confluent_kafka Producer. Returns None if Kafka is unavailable."""
    try:
        from confluent_kafka import Producer

        conf = {"bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS}
        return Producer(conf)
    except Exception:
        logger.warning("Kafka producer unavailable -- events will not be streamed")
        return None


def publish_to_kafka(
    topic: str,
    key: str,
    value: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> bool:
    """Publish a JSON message to a Kafka topic.

    Returns True if message was queued, False if Kafka is unavailable.
    """
    producer = _get_producer()
    if producer is None:
        return False

    kafka_headers = [(k, v.encode()) for k, v in (headers or {}).items()]

    try:
        producer.produce(
            topic=topic,
            key=key.encode(),
            value=json.dumps(value, default=str).encode(),
            headers=kafka_headers,
            callback=_delivery_report,
        )
        producer.flush(timeout=5)
        return True
    except Exception:
        logger.exception("Failed to publish to Kafka topic %s", topic)
        return False


def _delivery_report(err, msg):
    """Callback for Kafka produce delivery reports."""
    if err is not None:
        logger.error("Kafka delivery failed: %s", err)
    else:
        logger.debug(
            "Kafka message delivered to %s [%d] @ %d",
            msg.topic(), msg.partition(), msg.offset(),
        )


def build_event_message(
    event_id: uuid.UUID,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Build a standardized event message envelope."""
    return {
        "event_id": str(event_id),
        "event_type": event_type,
        "aggregate_type": aggregate_type,
        "aggregate_id": str(aggregate_id),
        "payload": payload,
    }
