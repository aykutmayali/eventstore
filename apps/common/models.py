import uuid

from django.db import models


class TimeStampedModel(models.Model):
    """Abstract base model with UUID pk and created/updated timestamps."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]


class IdempotencyKey(models.Model):
    """Stores idempotency keys to prevent duplicate POST processing."""

    key = models.CharField(max_length=255, unique=True, db_index=True)
    response_status = models.PositiveSmallIntegerField()
    response_body = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "idempotency_keys"

    def __str__(self) -> str:
        return self.key


class OutboxEvent(models.Model):
    """Transactional outbox: events written in the same DB transaction as domain changes."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    aggregate_type = models.CharField(max_length=64, db_index=True)  # e.g. "order"
    aggregate_id = models.UUIDField(db_index=True)
    event_type = models.CharField(max_length=64, db_index=True)  # e.g. "OrderPlaced"
    payload = models.JSONField(default=dict)
    published = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "outbox_events"
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.event_type} [{self.aggregate_id}] published={self.published}"


class ProcessedEvent(models.Model):
    """Tracks already-processed event IDs to guarantee idempotent consumption."""

    event_id = models.UUIDField(unique=True, db_index=True)
    consumer = models.CharField(max_length=64, db_index=True)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "processed_events"
        constraints = [
            models.UniqueConstraint(
                fields=["event_id", "consumer"],
                name="unique_event_per_consumer",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.consumer}:{self.event_id}"
