"""Backend-independent domain errors."""


class DomainError(ValueError):
    """A domain invariant or business rule was violated."""
