"""Typed outcomes for durable webhook receipt."""

from enum import Enum, auto


class InboxResult(Enum):
    INSERTED = auto()
    DUPLICATE = auto()
    CONFLICT = auto()
