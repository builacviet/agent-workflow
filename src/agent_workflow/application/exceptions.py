"""Backend-independent failures exposed to application boundaries."""


class ApplicationError(RuntimeError):
    """A failure at the application boundary."""


class RuntimeAdmissionError(ApplicationError):
    """A runtime request or selection is not admitted."""


class RuntimeContractError(ApplicationError):
    """A runtime plan, result or evidence violates its contract."""


class RuntimeExecutionError(ApplicationError):
    """A runtime could not carry out an admitted execution request."""


class InvalidApplicationRequestError(ApplicationError):
    """Invalid caller input or application configuration."""


class PersistenceError(ApplicationError):
    """Failure to access or interpret persisted workflow state."""


class PersistenceUnavailableError(PersistenceError):
    """A persistence backend is temporarily unavailable or failed."""


class CorruptedPersistenceDataError(PersistenceError):
    """Persisted workflow data does not satisfy its expected contract."""


class DispatchCleanupUnconfirmedError(ApplicationError):
    """Terminal dispatch cleanup could not be confirmed."""
