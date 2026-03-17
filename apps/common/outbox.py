"""Helpers for writing outbox events and ensuring idempotent consumption."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from django.db import IntegrityError

from apps.common.models import OutboxEvent, ProcessedEvent

logger = logging.getLogger(__name__)


def publish_outbox_event(
    *,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> OutboxEvent:
    """Write an event to the outbox table (call inside the same transaction as the domain change)."""
    return OutboxEvent.objects.create(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload=payload or {},
    )


def is_already_processed(event_id: uuid.UUID, consumer: str) -> bool:
    """Check whether this event was already processed by this consumer."""
    return ProcessedEvent.objects.filter(event_id=event_id, consumer=consumer).exists()


def mark_processed(event_id: uuid.UUID, consumer: str) -> bool:
    """Mark event as processed. Returns False if it was already marked (duplicate)."""
    try:
        ProcessedEvent.objects.create(event_id=event_id, consumer=consumer)
        return True
    except IntegrityError:
        logger.info("Duplicate event %s for consumer %s -- skipping", event_id, consumer)
        return False
