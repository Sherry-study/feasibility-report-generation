"""Shared exceptions for tool cores."""

from __future__ import annotations


class ToolDiagnosticError(Exception):
    """Base exception carrying the public diagnostic fields."""

    def __init__(self, message: str, *, code: str, retryable: bool) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)


class HostStorageError(RuntimeError):
    """HostClient storage/auth/transport failure.

    This is not a business validation failure. MCP adapters must translate it
    to a ToolError instead of returning a ``status=failed`` business envelope.
    """

    code = "HOST_STORAGE_ERROR"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool = False,
    ) -> None:
        self.code = code or self.code
        self.retryable = retryable
        super().__init__(message)


class HostStorageIntegrityError(HostStorageError):
    """Saved host artifact could not be read back with matching bytes."""

    code = "HOST_STORAGE_INTEGRITY_ERROR"


class ToolInternalError(RuntimeError):
    """Internal tool failure with a stable public diagnostic code."""

    code = "TOOL_INTERNAL_ERROR"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool = False,
    ) -> None:
        self.code = code or self.code
        self.retryable = retryable
        super().__init__(message)


class BusinessValidationError(ValueError):
    """User-correctable request, input content, or input schema error."""

    code = "BUSINESS_VALIDATION_ERROR"
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool = True,
    ) -> None:
        self.code = code or self.code
        self.retryable = retryable
        super().__init__(message)
