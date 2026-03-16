from django.core.validators import MinValueValidator
from django.db import models

from apps.common.models import TimeStampedModel


class OrderStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    PLACED = "PLACED", "Placed"
    RESERVED = "RESERVED", "Reserved"
    PAID = "PAID", "Paid"
    SHIPPED = "SHIPPED", "Shipped"
    CANCELLED = "CANCELLED", "Cancelled"


# Valid state transitions for the order status machine
ORDER_STATUS_TRANSITIONS: dict[str, list[str]] = {
    OrderStatus.DRAFT: [OrderStatus.PLACED, OrderStatus.CANCELLED],
    OrderStatus.PLACED: [OrderStatus.RESERVED, OrderStatus.CANCELLED],
    OrderStatus.RESERVED: [OrderStatus.PAID, OrderStatus.CANCELLED],
    OrderStatus.PAID: [OrderStatus.SHIPPED, OrderStatus.CANCELLED],
    OrderStatus.SHIPPED: [],
    OrderStatus.CANCELLED: [],
}


class InvalidStatusTransition(Exception):
    pass


class Order(TimeStampedModel):
    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.PROTECT,
        related_name="orders",
    )
    status = models.CharField(
        max_length=20,
        choices=OrderStatus.choices,
        default=OrderStatus.DRAFT,
        db_index=True,
    )
    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )

    class Meta(TimeStampedModel.Meta):
        db_table = "orders"

    def __str__(self) -> str:
        return f"Order {self.pk} [{self.status}]"

    def transition_to(self, new_status: str) -> None:
        """Transition order to a new status, enforcing the status machine."""
        allowed = ORDER_STATUS_TRANSITIONS.get(self.status, [])
        if new_status not in allowed:
            raise InvalidStatusTransition(
                f"Cannot transition from {self.status} to {new_status}. "
                f"Allowed: {allowed}"
            )
        self.status = new_status


class OrderLine(TimeStampedModel):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="order_lines",
    )
    qty = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )

    class Meta(TimeStampedModel.Meta):
        db_table = "order_lines"

    def __str__(self) -> str:
        return f"OrderLine {self.pk}: {self.qty}x {self.product_id}"

    @property
    def line_total(self) -> float:
        return self.qty * self.price
