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
