"""Single-process order creation and lookup; no idempotency support yet."""

from collections.abc import Iterable

from order_service.models import Order, OrderItem


class OrderService:
    def __init__(self) -> None:
        self._orders: dict[str, Order] = {}

    def create_order(self, items: Iterable[OrderItem]) -> Order:
        # Construct and validate before changing stored state or allocating an ID.
        order = Order(order_id=str(len(self._orders) + 1), items=tuple(items))
        self._orders[order.order_id] = order
        return order

    def get_order(self, order_id: str) -> Order:
        """Return a stored order, raising KeyError when the ID is unknown."""
        return self._orders[order_id]
