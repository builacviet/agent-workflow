"""Correlation IDs restore correctly and remain isolated by execution context."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from agent_workflow.observability.correlation import (
    correlation_scope,
    get_correlation_id,
    is_valid_correlation_id,
    set_correlation_id,
)


def test_nested_scopes_restore_previous_ids_even_on_exception():
    assert get_correlation_id() is None
    with correlation_scope(correlation_id="outer"):
        with correlation_scope(correlation_id="inner"), pytest.raises(RuntimeError):
            assert get_correlation_id() == "inner"
            set_correlation_id("replacement")
            raise RuntimeError
        assert get_correlation_id() == "outer"
        with correlation_scope(prefix="direct-") as generated:
            assert generated.startswith("direct-")
            assert is_valid_correlation_id(generated)
    assert get_correlation_id() is None


@pytest.mark.parametrize("raw", ["abc*/", " abc", "abc\n", "x" * 129, "é", "", None])
def test_regenerates_ids_outside_the_strict_ascii_allowlist(raw):
    with correlation_scope(correlation_id=raw) as generated:
        assert generated.startswith("cli-")
        assert generated != raw
        assert is_valid_correlation_id(generated)


def test_accepts_maximum_length_and_rejects_invalid_generated_prefix():
    supplied = "_-" + "x" * 126
    with correlation_scope(correlation_id=supplied):
        assert get_correlation_id() == supplied
        with pytest.raises(ValueError, match="invalid correlation ID prefix"):
            with correlation_scope(prefix="*/"):
                pytest.fail("invalid prefix must not enter the scope")
        assert get_correlation_id() == supplied


def test_parallel_threads_have_independent_ids():
    barrier = Barrier(2)

    def observe(correlation_id):
        assert get_correlation_id() is None
        with correlation_scope(correlation_id=correlation_id):
            barrier.wait(timeout=5)
            assert get_correlation_id() == correlation_id
        return get_correlation_id()

    with correlation_scope(correlation_id="parent"), ThreadPoolExecutor(2) as pool:
        first = pool.submit(observe, "one")
        second = pool.submit(observe, "two")
        assert first.result(timeout=5) is None
        assert second.result(timeout=5) is None
        assert get_correlation_id() == "parent"


def test_async_tasks_inherit_parent_and_isolate_nested_ids():
    async def observe(correlation_id):
        assert get_correlation_id() == "parent"
        with correlation_scope(correlation_id=correlation_id):
            await asyncio.sleep(0)
            assert get_correlation_id() == correlation_id
        assert get_correlation_id() == "parent"

    async def run():
        with correlation_scope(correlation_id="parent"):
            await asyncio.gather(observe("one"), observe("two"))
            assert get_correlation_id() == "parent"

    asyncio.run(run())
    assert get_correlation_id() is None
