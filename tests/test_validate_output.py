"""Regression tests for local report delivery validation."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.validate_output import validate_output_dir


class ValidateOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.markdown = b"# report\n"
        self.docx = b"PK\x03\x04test-docx"
        (self.root / "可行性研究报告_初稿.md").write_bytes(self.markdown)
        (self.root / "可行性研究报告_初稿.docx").write_bytes(self.docx)
        self.manifest = {
            "schema_version": "1.0",
            "created_at": "2026-09-09T00:00:00Z",
            "markdown": self._artifact("可行性研究报告_初稿.md", self.markdown, "text/markdown"),
            "docx": self._artifact("可行性研究报告_初稿.docx", self.docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            "summary": {"section_count": 66, "fallback_section_count": 0},
        }
        self.write_manifest()

    @staticmethod
    def _artifact(name: str, payload: bytes, media_type: str) -> dict:
        return {
            "path": f"runs/report_finalize/invocation/{name}",
            "media_type": media_type,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }

    def write_manifest(self) -> None:
        (self.root / "report_manifest.json").write_text(
            json.dumps(self.manifest, ensure_ascii=False), encoding="utf-8"
        )

    def test_valid_manifest_and_artifacts_pass(self) -> None:
        result, code = validate_output_dir(self.root)
        self.assertEqual(code, 0, result)
        self.assertTrue(result["valid"])

    def test_tampered_artifact_fails_hash_and_size(self) -> None:
        (self.root / "可行性研究报告_初稿.md").write_bytes(b"tampered")
        result, code = validate_output_dir(self.root)
        self.assertEqual(code, 1)
        self.assertFalse(result["valid"])
        self.assertTrue(any("markdown.sha256" in issue for issue in result["issues"]))
        self.assertTrue(any("markdown.size_bytes" in issue for issue in result["issues"]))

    def test_manifest_path_association_error_fails(self) -> None:
        self.manifest["docx"]["path"] = "runs/report_finalize/other/wrong.docx"
        self.write_manifest()
        result, code = validate_output_dir(self.root)
        self.assertEqual(code, 1)
        self.assertTrue(any("同一逻辑路径前缀" in issue for issue in result["issues"]))
        self.assertTrue(any("docx.path" in issue for issue in result["issues"]))

    def test_manifest_schema_error_fails(self) -> None:
        del self.manifest["markdown"]["sha256"]
        self.write_manifest()
        result, code = validate_output_dir(self.root)
        self.assertEqual(code, 1)
        self.assertTrue(any("Schema" in issue for issue in result["issues"]))


if __name__ == "__main__":
    unittest.main()
