from __future__ import annotations

import unittest
from pathlib import Path

from worldforge.runtime_audit import audit_runtime


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "src/isoworld"


class ArchitectureTests(unittest.TestCase):
    def test_runtime_has_no_ai_sdk_imports(self) -> None:
        self.assertEqual([], audit_runtime(RUNTIME))

    def test_runtime_does_not_import_authoring_tools(self) -> None:
        offenders: list[str] = []
        for path in RUNTIME.rglob("*.py"):
            if "worldforge" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual([], offenders)

    def test_runtime_does_not_access_project_source_directories(self) -> None:
        runtime_text = "\n".join(
            path.read_text(encoding="utf-8") for path in RUNTIME.rglob("*.py")
        )
        self.assertNotIn("projects/", runtime_text)
        self.assertNotIn("examples/", runtime_text)


if __name__ == "__main__":
    unittest.main()
