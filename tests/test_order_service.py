from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from order_service import Order, OrderItem, OrderService


def test_create_and_retrieve_order_with_decimal_totals():
    service = OrderService()
    order = service.create_order([
        OrderItem("book", 3, Decimal("0.10")),
        OrderItem("pen", 2, Decimal("1.25")),
    ])
    assert order.order_id == "1"
    assert order.items[0].total == Decimal("0.30")
    assert order.total == Decimal("2.80")
    assert isinstance(order.total, Decimal)
    assert service.get_order(order.order_id) is order


def test_zero_price_is_valid():
    assert OrderItem("gift", 2, Decimal("0")).total == Decimal("0")


@pytest.mark.parametrize("quantity", [0, -1, -100])
def test_reject_nonpositive_quantity(quantity):
    with pytest.raises(ValueError):
        OrderItem("book", quantity, Decimal("1"))


@pytest.mark.parametrize("quantity", [True, False, 1.5, "2", None, Decimal("2")])
def test_reject_noninteger_quantity(quantity):
    with pytest.raises(TypeError):
        OrderItem("book", quantity, Decimal("1"))


@pytest.mark.parametrize("price", ["-0.01", "NaN", "sNaN", "Infinity", "-Infinity"])
def test_reject_invalid_decimal_price(price):
    with pytest.raises(ValueError):
        OrderItem("book", 1, Decimal(price))


@pytest.mark.parametrize("price", [0.1, 1, "1.00", None, True])
def test_reject_non_decimal_price(price):
    with pytest.raises(TypeError):
        OrderItem("book", 1, price)


@pytest.mark.parametrize("product_id", ["", "  "])
def test_reject_blank_product_id(product_id):
    with pytest.raises(ValueError):
        OrderItem(product_id, 1, Decimal("1"))


def test_reject_nonstring_product_id():
    with pytest.raises(TypeError):
        OrderItem(None, 1, Decimal("1"))


@pytest.mark.parametrize("items, error", [([], ValueError), ([object()], TypeError)])
def test_invalid_order_does_not_consume_id_or_store_order(items, error):
    service = OrderService()
    with pytest.raises(error):
        service.create_order(items)
    with pytest.raises(KeyError):
        service.get_order("1")
    order = service.create_order([OrderItem("book", 1, Decimal("1"))])
    assert order.order_id == "1"


def test_repeated_creations_are_distinct_in_baseline():
    service = OrderService()
    items = [OrderItem("book", 1, Decimal("1"))]
    first = service.create_order(items)
    second = service.create_order(items)
    assert (first.order_id, second.order_id) == ("1", "2")
    assert service.get_order("1") is first
    assert service.get_order("2") is second


def test_order_snapshots_input_and_values_are_immutable():
    item = OrderItem("book", 1, Decimal("1"))
    items = [item]
    order = OrderService().create_order(iter(items))
    items.clear()
    assert order.items == (item,)
    with pytest.raises(FrozenInstanceError):
        item.quantity = 2
    with pytest.raises(FrozenInstanceError):
        order.order_id = "changed"


def test_services_have_independent_storage():
    first = OrderService()
    second = OrderService()
    first.create_order([OrderItem("book", 1, Decimal("1"))])
    with pytest.raises(KeyError):
        second.get_order("1")


@pytest.mark.parametrize("order_id", ["", "  "])
def test_reject_blank_order_id(order_id):
    with pytest.raises(ValueError):
        Order(order_id, (OrderItem("book", 1, Decimal("1")),))


def test_order_requires_immutable_items_tuple():
    with pytest.raises(TypeError):
        Order("1", [OrderItem("book", 1, Decimal("1"))])
