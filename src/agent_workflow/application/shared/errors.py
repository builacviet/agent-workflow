"""Backend-independent persistence failures exposed to application boundaries."""


class PersistenceError(RuntimeError):
    """Failure to access or interpret persisted workflow state."""


class PersistenceUnavailableError(PersistenceError):
    """A persistence backend is temporarily unavailable or failed."""


class CorruptedPersistenceDataError(PersistenceError):
    """Persisted workflow data does not satisfy its expected contract."""
