"""Shared helpers for private HostClient artifact paths."""

from __future__ import annotations

import datetime as _dt
import uuid


def new_logical_prefix(tool_name: str) -> str:
    """Build a unique logical path prefix for one Tool invocation."""
    stamp = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"runs/{tool_name}/{stamp}-{uuid.uuid4().hex}"
