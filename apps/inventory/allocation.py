"""Stock allocation service.

Provides two allocation strategies:
  - FIFO:           oldest inventory batches first (by created_at).
  - FEWEST_SPLITS:  greedy heuristic that picks the fewest inventory items
                    needed to fill each order line, preferring larger batches.

Both strategies support partial fulfillment (configurable).
Returns (allocations, backorders) for downstream processing.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from django.db.models import QuerySet

    from apps.inventory.models import InventoryItem

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class Allocation:
    """One allocation unit: qty taken from a specific inventory item."""

    inventory_item_id: UUID
    product_id: UUID
    warehouse: str
    batch_no: str
    qty: int


@dataclass
class BackorderLine:
    """Remaining qty that could not be fulfilled."""

    product_id: UUID
    qty_short: int


@dataclass
class AllocationResult:
    """Complete result from an allocation run."""

    allocations: list[Allocation] = field(default_factory=list)
    backorders: list[BackorderLine] = field(default_factory=list)
    fully_fulfilled: bool = True

    @property
    def total_allocated(self) -> int:
        return sum(a.qty for a in self.allocations)

    @property
    def total_backordered(self) -> int:
        return sum(b.qty_short for b in self.backorders)

    @property
    def split_count(self) -> int:
        """Number of distinct inventory items used."""
        return len(self.allocations)


class Strategy(enum.StrEnum):
    FIFO = "fifo"
    FEWEST_SPLITS = "fewest_splits"


# ---------------------------------------------------------------------------
# Core allocation function
# ---------------------------------------------------------------------------
def allocate(
    demand: list[tuple[UUID, int]],
    inventory_qs: QuerySet[InventoryItem],
    *,
    strategy: Strategy = Strategy.FIFO,
    allow_partial: bool = True,
) -> AllocationResult:
    """Allocate inventory to a list of (product_id, qty) demands.

    Parameters
    ----------
    demand:
        List of (product_id, qty_requested) tuples.
    inventory_qs:
        Base QuerySet of InventoryItem (caller should apply select_for_update).
    strategy:
        FIFO or FEWEST_SPLITS.
    allow_partial:
        If False, an order line either gets fully allocated or nothing.

    Returns
    -------
    AllocationResult with allocations and backorders.
    """
    result = AllocationResult()

    for product_id, qty_requested in demand:
        items = list(
            inventory_qs.filter(product_id=product_id)
        )

        # Apply sorting strategy
        if strategy == Strategy.FEWEST_SPLITS:
            items = _sort_fewest_splits(items, qty_requested)
        else:
            # FIFO: already ordered by created_at from QuerySet default ordering
            items = sorted(items, key=lambda it: it.created_at)

        if not allow_partial:
            # Dry-run: check if total available can satisfy demand
            total_available = sum(
                max(0, it.on_hand - it.reserved) for it in items
            )
            if total_available < qty_requested:
                result.backorders.append(BackorderLine(
                    product_id=product_id,
                    qty_short=qty_requested,
                ))
                result.fully_fulfilled = False
                continue

        line_allocations = _allocate_line(items, qty_requested)
        allocated_qty = sum(a.qty for a in line_allocations)
        short = qty_requested - allocated_qty

        result.allocations.extend(line_allocations)

        if short > 0:
            result.backorders.append(BackorderLine(
                product_id=product_id,
                qty_short=short,
            ))
            result.fully_fulfilled = False

    return result


# ---------------------------------------------------------------------------
# Strategy helpers
# ---------------------------------------------------------------------------
def _sort_fewest_splits(
    items: list[InventoryItem],
    qty_needed: int,
) -> list[InventoryItem]:
    """Greedy heuristic: order items to minimize the number of splits.

    Algorithm:
    1. Filter to items with available > 0.
    2. Check if any single item can fully satisfy qty_needed -- if so,
       pick the smallest such item (best-fit) to waste the least capacity,
       then append remaining items sorted largest-first.
    3. Otherwise, sort items by available descending (largest-first) so that
       fewer items are needed.
    """
    available_items = [it for it in items if (it.on_hand - it.reserved) > 0]
    if not available_items:
        return available_items

    # Partition: items that can fully satisfy vs those that cannot
    full_candidates = [
        it for it in available_items
        if (it.on_hand - it.reserved) >= qty_needed
    ]

    if full_candidates:
        # Best-fit: pick the one whose available is closest to qty_needed
        full_candidates.sort(key=lambda it: it.on_hand - it.reserved)
        best = full_candidates[0]
        rest = [it for it in available_items if it.pk != best.pk]
        # Sort rest largest-first for any overflow allocation
        rest.sort(key=lambda it: it.on_hand - it.reserved, reverse=True)
        return [best] + rest

    # No single item can satisfy -- sort largest-first
    available_items.sort(key=lambda it: it.on_hand - it.reserved, reverse=True)
    return available_items


def _allocate_line(
    items: list[InventoryItem],
    qty_needed: int,
) -> list[Allocation]:
    """Walk sorted items and greedily allocate up to qty_needed.

    Mutates item.reserved in-place (caller must be inside transaction).
    """
    allocations: list[Allocation] = []
    remaining = qty_needed

    for item in items:
        if remaining <= 0:
            break
        available = item.on_hand - item.reserved
        if available <= 0:
            continue
        to_reserve = min(remaining, available)
        item.reserved += to_reserve
        item.save(update_fields=["reserved", "updated_at"])
        remaining -= to_reserve
        allocations.append(Allocation(
            inventory_item_id=item.pk,
            product_id=item.product_id,
            warehouse=item.warehouse,
            batch_no=item.batch_no,
            qty=to_reserve,
        ))

    return allocations
