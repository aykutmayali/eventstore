from django.core.validators import MinValueValidator
from django.db import models

from apps.common.models import TimeStampedModel


class InventoryItem(TimeStampedModel):
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.CASCADE,
        related_name="inventory_items",
    )
    warehouse = models.CharField(max_length=64, default="default", db_index=True)
    batch_no = models.CharField(max_length=64, blank=True, default="")
    on_hand = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0)],
    )
    reserved = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0)],
    )

    class Meta(TimeStampedModel.Meta):
        db_table = "inventory_items"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(on_hand__gte=0),
                name="inventory_on_hand_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(reserved__gte=0),
                name="inventory_reserved_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(reserved__lte=models.F("on_hand")),
                name="inventory_reserved_lte_on_hand",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.product_id} @ {self.warehouse} "
            f"(on_hand={self.on_hand}, reserved={self.reserved})"
        )

    @property
    def available(self) -> int:
        return self.on_hand - self.reserved
