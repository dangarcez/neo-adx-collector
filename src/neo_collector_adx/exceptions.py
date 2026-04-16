class ConfigurationError(ValueError):
    """Raised when the runtime configuration is invalid."""


class ProcessingError(RuntimeError):
    """Raised when a job row cannot be processed safely."""
