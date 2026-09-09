"""Shared internal exceptions for tool cores."""

from __future__ import annotations


class HostStorageError(RuntimeError):
    """HostClient storage/auth/transport failure.

    This is not a business validation failure. MCP adapters must translate it
    to a ToolError instead of returning a ``status=failed`` business envelope.
    """


class HostStorageIntegrityError(HostStorageError):
    """Saved host artifact could not be read back with matching bytes."""


class BusinessValidationError(ValueError):
    """User-correctable request, input content, or input schema error."""
