"""Test fakes for duck protocols."""

from __future__ import annotations

import copy
import json
from typing import Any


class FakeContent:
    def __init__(self) -> None:
        self.events: list[tuple[float, float | None, str | None]] = []

    def report_progress(
        self,
        progress: float,
        total: float | None = None,
        message: str | None = None,
    ) -> None:
        self.events.append((progress, total, message))

    def send_progress_with_data(
        self,
        progress: float,
        total: float | None = None,
        message: str | None = None,
        ui_event: dict[str, Any] | None = None,
    ) -> None:
        self.report_progress(progress, total, message)


class FakeHostClient:
    def __init__(self) -> None:
        self.files: dict[str, dict[str, Any] | bytes | str] = {}
        self.fail_on_save_prefix: str | None = None
        self.fail_on_get_prefix: str | None = None

    def save_file(
        self,
        path: str,
        data: dict[str, Any] | bytes | str,
        *,
        kind: str = "auto",
    ) -> None:
        if self.fail_on_save_prefix and path.startswith(self.fail_on_save_prefix):
            raise RuntimeError(f"simulated save failure: {path}")
        self.files[path] = copy.deepcopy(data) if isinstance(data, dict) else data

    def get_file(
        self,
        path: str,
        *,
        kind: str = "auto",
    ) -> dict[str, Any] | bytes | str:
        if self.fail_on_get_prefix and path.startswith(self.fail_on_get_prefix):
            raise RuntimeError(f"simulated get failure: {path}")
        if path not in self.files:
            raise FileNotFoundError(path)
        data = self.files[path]
        if kind == "bytes":
            if isinstance(data, bytes):
                return data
            if isinstance(data, str):
                return data.encode("utf-8")
            return json.dumps(data, ensure_ascii=False).encode("utf-8")
        if kind == "text":
            if isinstance(data, bytes):
                return data.decode("utf-8")
            if isinstance(data, dict):
                return json.dumps(data, ensure_ascii=False)
            return data
        if kind == "json" and not isinstance(data, dict):
            decoded = data.decode("utf-8") if isinstance(data, bytes) else data
            parsed = json.loads(decoded)
            if not isinstance(parsed, dict):
                raise ValueError(f"{path} must contain a JSON object")
            return parsed
        return copy.deepcopy(data) if isinstance(data, dict) else data

    def save_json_text(self, path: str, payload: dict[str, Any]) -> None:
        self.files[path] = json.dumps(payload, ensure_ascii=False)
