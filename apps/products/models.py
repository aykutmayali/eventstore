from django.db import models

from apps.common.models import TimeStampedModel


class Product(TimeStampedModel):
    sku = models.CharField(max_length=64, unique=True, db_index=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta(TimeStampedModel.Meta):
        db_table = "products"

    def __str__(self) -> str:
        return f"{self.sku} - {self.name}"
