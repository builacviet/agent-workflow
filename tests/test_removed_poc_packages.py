"""Guard against accidental reintroduction of retired source packages."""
from pathlib import Path
import importlib


ROOT = Path(__file__).resolve().parents[1]


def test_retired_poc_source_directories_are_absent() -> None:
    assert not (ROOT / "src" / "my_wfc").exists()
    assert not (ROOT / "src" / "order_service").exists()


def test_active_packages_remain_importable() -> None:
    assert importlib.import_module("agent_workflow")
    assert importlib.import_module("coder_worker")
