"""Backend-independent domain errors."""


class DomainError(ValueError):
    """A domain invariant or business rule was violated."""


class BusinessRuleError(DomainError):
    """A business invariant was violated by a domain operation."""
