"""Local adapters for CLI and ad-hoc development runs.

These adapters are intentionally outside ``src`` so production business cores
only depend on duck protocols, not local filesystem implementations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ConsoleContent:
    """Progress adapter that prints concise CLI progress lines."""

    def report_progress(
        self,
        progress: float,
        total: float | None = None,
        message: str | None = None,
    ) -> None:
        if message:
            suffix = f"/{total:g}" if total is not None else ""
            print(f"[progress] {progress:g}{suffix} {message}")

    def send_progress_with_data(
        self,
        progress: float,
        total: float | None = None,
        message: str | None = None,
        ui_event: dict[str, Any] | None = None,
    ) -> None:
        self.report_progress(progress, total, message)


class LocalHostClient:
    """HostClient-compatible adapter rooted at a local directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save_file(
        self,
        path: str,
        data: dict[str, Any] | bytes | str,
        *,
        kind: str = "auto",
    ) -> None:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, dict):
            target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        elif isinstance(data, str):
            target.write_text(data, encoding="utf-8")
        elif isinstance(data, (bytes, bytearray)):
            target.write_bytes(bytes(data))
        else:
            raise TypeError(f"unsupported data type: {type(data).__name__}")

    def get_file(
        self,
        path: str,
        *,
        kind: str = "auto",
    ) -> dict[str, Any] | bytes | str:
        target = self._resolve(path)
        if not target.is_file():
            raise FileNotFoundError(path)
        if kind == "bytes":
            return target.read_bytes()
        if kind == "text":
            return target.read_text(encoding="utf-8")
        if kind == "json" or (kind == "auto" and target.suffix.lower() == ".json"):
            payload = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(f"{path} must contain a JSON object")
            return payload
        if kind == "auto" and target.suffix.lower() in {".md", ".txt"}:
            return target.read_text(encoding="utf-8")
        return target.read_bytes()

    def _resolve(self, path: str) -> Path:
        normalized = path.replace("\\", "/").lstrip("/")
        target = (self.root / normalized).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"path escapes local host root: {path}") from exc
        return target
