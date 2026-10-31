"""Immutable order values; prices use Decimal without currency rounding."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class OrderItem:
    product_id: str
    quantity: int
    unit_price: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.product_id, str):
            raise TypeError("product_id must be a string")
        if not self.product_id.strip():
            raise ValueError("product_id must not be blank")
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int):
            raise TypeError("quantity must be an integer")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if not isinstance(self.unit_price, Decimal):
            raise TypeError("unit_price must be a Decimal")
        if not self.unit_price.is_finite() or self.unit_price < 0:
            raise ValueError("unit_price must be finite and nonnegative")

    @property
    def total(self) -> Decimal:
        return self.unit_price * self.quantity


@dataclass(frozen=True)
class Order:
    order_id: str
    items: tuple[OrderItem, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.order_id, str):
            raise TypeError("order_id must be a string")
        if not self.order_id.strip():
            raise ValueError("order_id must not be blank")
        if not isinstance(self.items, tuple):
            raise TypeError("items must be a tuple")
        if not self.items:
            raise ValueError("an order must contain at least one item")
        if any(not isinstance(item, OrderItem) for item in self.items):
            raise TypeError("items must contain only OrderItem values")

    @property
    def total(self) -> Decimal:
        return sum((item.total for item in self.items), start=Decimal("0"))
