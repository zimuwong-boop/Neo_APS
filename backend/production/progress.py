"""Read-only demo overlays; actual report and allocation quantities remain authoritative."""
from decimal import ROUND_DOWN, Decimal

PERCENT_STEP = Decimal("0.01")
QUANTITY_STEP = Decimal("0.000001")


def percentage(amount, quantity):
    if amount >= quantity:
        return Decimal("100.00")
    return (amount / quantity * 100).quantize(PERCENT_STEP, rounding=ROUND_DOWN)


def display_percentage(order):
    actual = percentage(order.completed_qty, order.quantity)
    if order.demo_progress_percent is None:
        return actual
    return max(actual, order.demo_progress_percent)


def preview_allocations(order):
    """Preview additional demo quantity using the frozen report distribution order."""
    target = order.completed_qty
    if order.demo_progress_percent is not None:
        target = max(target, (order.quantity * order.demo_progress_percent / 100).quantize(
            QUANTITY_STEP, rounding=ROUND_DOWN
        ))
    remaining = target - order.completed_qty
    result = {}
    for allocation in order.allocations.order_by("rank"):
        take = min(remaining, allocation.quantity - allocation.fulfilled_qty)
        result[allocation.pk] = allocation.fulfilled_qty + take
        remaining -= take
    return result
