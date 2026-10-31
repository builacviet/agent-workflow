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


def test_keyed_retry_returns_original_without_consuming_id():
    service = OrderService()
    items = [OrderItem("book", 2, Decimal("1.0"))]
    original = service.create_order(iter(items), idempotency_key="retry")
    equivalent = [OrderItem("book", 2, Decimal("1.00"))]
    assert service.create_order(iter(equivalent), "retry") is original
    assert original.total == Decimal("2.0")
    assert service.get_order("1") is original
    with pytest.raises(KeyError):
        service.get_order("2")
    assert service.create_order(items).order_id == "2"


@pytest.mark.parametrize("changed", [
    [OrderItem("other", 1, Decimal("1")), OrderItem("pen", 1, Decimal("2"))],
    [OrderItem("book", 2, Decimal("1")), OrderItem("pen", 1, Decimal("2"))],
    [OrderItem("book", 1, Decimal("3")), OrderItem("pen", 1, Decimal("2"))],
    [OrderItem("pen", 1, Decimal("2")), OrderItem("book", 1, Decimal("1"))],
    [OrderItem("book ", 1, Decimal("1")), OrderItem("pen", 1, Decimal("2"))],
    [OrderItem("book", 1, Decimal("1"))],
])
def test_conflict_preserves_order_key_and_next_id(changed):
    service = OrderService()
    items = [OrderItem("book", 1, Decimal("1")), OrderItem("pen", 1, Decimal("2"))]
    original = service.create_order(items, "key")
    with pytest.raises(ValueError):
        service.create_order(changed, "key")
    assert service.get_order("1") is original
    assert service.create_order(items, "key") is original
    assert service.create_order(changed, "new").order_id == "2"


def test_duplicate_lines_are_preserved_and_not_aggregated():
    service = OrderService()
    item = OrderItem("book", 1, Decimal("1"))
    original = service.create_order([item, item], "key")
    assert service.create_order([item, item], "key") is original
    with pytest.raises(ValueError):
        service.create_order([OrderItem("book", 2, Decimal("1"))], "key")
    assert original.items == (item, item)
    assert service.create_order([item]).order_id == "2"


@pytest.mark.parametrize("key, error", [
    ("", ValueError), (" \t\n", ValueError), (1, TypeError),
    (True, TypeError), ([], TypeError), ({}, TypeError),
])
def test_invalid_key_changes_no_state(key, error):
    service = OrderService()
    items = [OrderItem("book", 1, Decimal("1"))]
    original = service.create_order(items, "existing")
    with pytest.raises(error):
        service.create_order(items, key)
    assert service.create_order(items, "existing") is original
    assert service.create_order(items, "corrected").order_id == "2"


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("invalid, error", [([], ValueError), ([object()], TypeError)])
def test_invalid_items_are_rejected_before_retry_lookup(existing, invalid, error):
    service = OrderService()
    items = [OrderItem("book", 1, Decimal("1"))]
    original = service.create_order(items, "key") if existing else None
    with pytest.raises(error):
        service.create_order(invalid, "key")
    recovered = service.create_order(items, "key")
    assert recovered.order_id == "1"
    if existing:
        assert recovered is original
    assert service.create_order(items).order_id == "2"


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("field, value, error", [
    ("quantity", 0, ValueError), ("quantity", True, TypeError),
    ("unit_price", Decimal("-1"), ValueError),
    ("unit_price", Decimal("NaN"), ValueError),
    ("unit_price", Decimal("Infinity"), ValueError),
    ("unit_price", 1.0, TypeError), ("product_id", "", ValueError),
])
def test_invalid_item_values_cannot_bypass_request_validation(existing, field, value, error):
    service = OrderService()
    items = [OrderItem("book", 1, Decimal("1"))]
    original = service.create_order(items, "key") if existing else None
    # Simulate an instance supplied without normal constructor validation.
    invalid = object.__new__(OrderItem)
    for name, valid in [("product_id", "book"), ("quantity", 1), ("unit_price", Decimal("1"))]:
        object.__setattr__(invalid, name, value if name == field else valid)
    with pytest.raises(error):
        service.create_order([invalid], "key")
    recovered = service.create_order(items, "key")
    assert recovered.order_id == "1"
    if existing:
        assert recovered is original
    assert service.create_order(items).order_id == "2"


def test_keys_are_exact_and_instance_local_and_none_keeps_baseline_behavior():
    service = OrderService()
    items = [OrderItem("book", 1, Decimal("1"))]
    keys = ["Key", "key", " key ", None, None]
    orders = [service.create_order(items, key) for key in keys]
    assert [order.order_id for order in orders] == ["1", "2", "3", "4", "5"]
    for key, original in zip(keys[:3], orders[:3]):
        assert service.create_order(items, key) is original
    other = OrderService().create_order(items, "Key")
    assert other.order_id == "1"
    assert other is not orders[0]
