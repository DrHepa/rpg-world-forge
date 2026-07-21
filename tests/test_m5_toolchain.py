from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GAME_TEMPLATE = ROOT / "src/worldforge/templates/pyray_game"

AUDITED_TOOLCHAIN = {
    "Pillow": "12.3.0",
    "build": "1.5.0",
    "pip-audit": "2.10.1",
    "raylib": "6.0.1.0",
    "ruff": "0.15.22",
    "setuptools": "83.0.0",
    "wheel": "0.47.0",
}
GAME_LOCK = [
    "cffi==1.17.1",
    "pycparser==2.23",
    "raylib==6.0.1.0",
    "ruff==0.15.22",
    "setuptools==83.0.0",
    "wheel==0.47.0",
]


def _toml(path: Path) -> dict[str, object]:
    with path.open("rb") as source:
        return tomllib.load(source)


def _lock_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


class M5ToolchainTests(unittest.TestCase):
    def test_forge_metadata_pins_the_audited_python_toolchain(self) -> None:
        document = _toml(ROOT / "pyproject.toml")
        project = document["project"]
        self.assertEqual(["setuptools==83.0.0"], document["build-system"]["requires"])
        self.assertEqual(">=3.11,<3.13", project["requires-python"])
        self.assertEqual("MIT", project["license"])
        self.assertEqual(["LICENSE"], project["license-files"])
        self.assertNotIn("License :: OSI Approved :: MIT License", project["classifiers"])
        optional = project["optional-dependencies"]
        self.assertEqual(["raylib==6.0.1.0"], optional["game"])
        self.assertEqual(["Pillow==12.3.0"], optional["asset-production"])
        self.assertEqual(
            [
                "Pillow==12.3.0",
                "build==1.5.0",
                "pip-audit==2.10.1",
                "ruff==0.15.22",
                "wheel==0.47.0",
            ],
            optional["dev"],
        )

    def test_root_lock_and_notices_cover_every_audited_direct_pin(self) -> None:
        expected = [
            f"{name}=={version}"
            for name, version in sorted(
                AUDITED_TOOLCHAIN.items(), key=lambda item: item[0].casefold()
            )
        ]
        self.assertEqual(expected, _lock_lines(ROOT / "requirements-m5.lock"))
        notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        for name, version in AUDITED_TOOLCHAIN.items():
            self.assertIn(f"{name} {version}".casefold(), notices.casefold())

    def test_generated_game_metadata_lock_and_notices_are_synchronized(self) -> None:
        document = _toml(GAME_TEMPLATE / "pyproject.toml.tmpl")
        project = document["project"]
        self.assertEqual(["setuptools==83.0.0"], document["build-system"]["requires"])
        self.assertEqual(">=3.11,<3.13", project["requires-python"])
        self.assertEqual("MIT", project["license"])
        self.assertEqual(["LICENSE"], project["license-files"])
        self.assertNotIn("License :: OSI Approved :: MIT License", project["classifiers"])
        self.assertEqual(["raylib==6.0.1.0"], project["dependencies"])

        requirements = _lock_lines(GAME_TEMPLATE / "requirements.lock.tmpl")
        platform = json.loads(
            (GAME_TEMPLATE / "platform.lock.json.tmpl").read_text(encoding="utf-8")
        )
        self.assertEqual(GAME_LOCK, requirements)
        self.assertEqual(GAME_LOCK, platform["locked_requirements"])
        notices = (GAME_TEMPLATE / "THIRD_PARTY_NOTICES.md.tmpl").read_text(encoding="utf-8")
        for requirement in GAME_LOCK:
            self.assertIn(requirement, notices)

    def test_generated_verifier_requires_pep639_and_exact_build_pin(self) -> None:
        verifier = (GAME_TEMPLATE / "verify_game.py.tmpl").read_text(encoding="utf-8")
        self.assertIn('project.get("license") != "MIT"', verifier)
        self.assertIn('project.get("license-files") != ["LICENSE"]', verifier)
        self.assertIn('"license-files",', verifier)
        self.assertNotIn("License :: OSI Approved :: MIT License", verifier)


if __name__ == "__main__":
    unittest.main()
