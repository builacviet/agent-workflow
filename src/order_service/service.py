"""Single-process order creation and lookup with optional in-memory idempotency."""

from collections.abc import Iterable

from order_service.models import Order, OrderItem


class OrderService:
    def __init__(self) -> None:
        self._orders: dict[str, Order] = {}
        self._idempotency: dict[str, Order] = {}

    def create_order(
        self, items: Iterable[OrderItem], idempotency_key: str | None = None
    ) -> Order:
        # Validate the full incoming request before consulting key state.
        order = Order(order_id=str(len(self._orders) + 1), items=tuple(items))
        for item in order.items:
            # Recheck values even for retries and externally supplied instances.
            item.__post_init__()
        if idempotency_key is not None:
            if not isinstance(idempotency_key, str):
                raise TypeError("idempotency_key must be a string or None")
            if not idempotency_key.strip():
                raise ValueError("idempotency_key must not be blank")
            existing = self._idempotency.get(idempotency_key)
            if existing is not None:
                if existing.items != order.items:
                    raise ValueError("idempotency_key conflicts with existing order")
                return existing
        self._orders[order.order_id] = order
        if idempotency_key is not None:
            self._idempotency[idempotency_key] = order
        return order

    def get_order(self, order_id: str) -> Order:
        """Return a stored order, raising KeyError when the ID is unknown."""
        return self._orders[order_id]
