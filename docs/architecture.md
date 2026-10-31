# Architecture

`models.py` contains frozen `OrderItem` and `Order` values with validation and computed `Decimal` totals. `service.py` owns a per-instance dictionary, creates validated orders with sequential string IDs and looks them up. `__init__.py` exports the public API. Tests depend only on that API and pytest.

## Invariants and boundaries

- Product/order IDs must be nonblank strings. Quantities are positive integers; booleans are rejected. Prices must be finite, nonnegative `Decimal` values; free items are allowed.
- An order contains at least one valid item in an immutable tuple. The service copies the incoming iterable before storing an order. Invalid creation does not store anything or consume an ID.
- Line total is quantity times unit price; order total is the sum of line totals. No taxes, discounts, currency conversion or quantization are implied. Calculations use the caller's Decimal context (default precision 28); extreme magnitudes are outside this small POC's guarantees.
- Domain models do not depend on the service or external systems. The service depends on models and Python's standard library only. There is no persistence, network access or workflow implementation.
- IDs and storage are scoped to one service instance. Data is lost on restart; concurrent access is unsupported. Repeated valid creation calls currently produce distinct orders. Idempotency is a proposed future task only.

## Errors and verification

Use `TypeError` for unsupported input types, `ValueError` for invalid values and `KeyError` for unknown order IDs. Do not suppress errors or mutate storage before validation succeeds.

Deterministic unit tests cover totals, validation (including NaN/infinities and booleans), immutable snapshots, independent storage, missing IDs and failure atomicity. Review must preserve these invariants and dependency boundaries. No integration infrastructure is needed for Phase 1.
