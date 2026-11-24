"""Layer: Domain
Responsibility: Define persisted review event action names.
Dependencies: Python standard library.
"""

from enum import StrEnum


class ReviewEventAction(StrEnum):
    LEASE_EXPIRED = "LEASE_EXPIRED"
    CLAIM = "CLAIM"
    REVIEW_SUBMITTED = "REVIEW_SUBMITTED"
