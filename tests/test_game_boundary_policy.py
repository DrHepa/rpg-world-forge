from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from worldforge.game_boundary_policy import (
    DEFAULT_IGNORED_TOP_LEVEL,
    JSONPolicyError,
    load_strict_json_object,
    scan_python_capabilities,
    validate_dependency_provenance,
    validate_json_objects,
    validate_regular_tree,
)


class GameBoundaryPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def test_regular_tree_rejects_links_hardlinks_and_nonregular_entries(self) -> None:
        regular = self.root / "regular.txt"
        regular.write_text("safe", encoding="utf-8")
        alias = self.root / "alias.txt"
        try:
            os.link(regular, alias)
        except OSError as exc:
            self.skipTest(f"hardlinks unavailable: {exc}")
        issues = validate_regular_tree(self.root)
        self.assertIn("FS_HARDLINK:regular.txt", issues)
        self.assertIn("FS_HARDLINK:alias.txt", issues)

    def test_regular_tree_skips_only_shared_operational_roots(self) -> None:
        ignored = self.root / ".venv"
        ignored.mkdir()
        (ignored / "unsafe").symlink_to("missing")
        selected = self.root / "selected"
        selected.mkdir()
        (selected / "regular.txt").write_text("safe", encoding="utf-8")

        self.assertEqual(
            (),
            validate_regular_tree(
                self.root,
                ignored_top_level=DEFAULT_IGNORED_TOP_LEVEL,
            ),
        )
        self.assertIn("FS_SYMLINK:.venv/unsafe", validate_regular_tree(self.root))

    def test_regular_tree_rejects_symlink_without_following_it(self) -> None:
        target = self.root / "target.txt"
        target.write_text("safe", encoding="utf-8")
        link = self.root / "link.txt"
        try:
            link.symlink_to(target.name)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        self.assertIn("FS_SYMLINK:link.txt", validate_regular_tree(self.root))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO support required")
    def test_regular_tree_rejects_fifo(self) -> None:
        fifo = self.root / "events.pipe"
        os.mkfifo(fifo)
        self.assertEqual(
            ("FS_NON_REGULAR:events.pipe:fifo",),
            validate_regular_tree(self.root),
        )

    def test_strict_json_rejects_ambiguous_and_invalid_inputs(self) -> None:
        cases = {
            b'{"name": 1, "name": 2}': "JSON_DUPLICATE_KEY",
            b'{"value": NaN}': "JSON_NONFINITE",
            b'{"value": Infinity}': "JSON_NONFINITE",
            b'{"value": 1e400}': "JSON_NUMBER_OVERFLOW",
            b"[]": "JSON_NOT_OBJECT",
            b'{"broken":': "JSON_INVALID",
            b'{"text": "\xff"}': "JSON_NOT_UTF8",
        }
        path = self.root / "selected.json"
        for payload, code in cases.items():
            with self.subTest(code=code):
                path.write_bytes(payload)
                with self.assertRaises(JSONPolicyError) as raised:
                    load_strict_json_object(path)
                self.assertEqual(code, raised.exception.code)

    def test_strict_json_is_bounded_and_rejects_hardlinks(self) -> None:
        path = self.root / "selected.json"
        path.write_text('{"value": 1}', encoding="utf-8")
        with self.assertRaisesRegex(JSONPolicyError, "JSON_TOO_LARGE"):
            load_strict_json_object(path, limit=4)
        alias = self.root / "alias.json"
        try:
            os.link(path, alias)
        except OSError as exc:
            self.skipTest(f"hardlinks unavailable: {exc}")
        with self.assertRaisesRegex(JSONPolicyError, "JSON_HARDLINK"):
            load_strict_json_object(path)

    def test_selected_json_issue_order_is_deterministic(self) -> None:
        first = self.root / "a.json"
        second = self.root / "b.json"
        first.write_bytes(b'{"value": NaN}')
        second.write_bytes(b'{"duplicate": 1, "duplicate": 2}')
        forward = validate_json_objects([second, first], base=self.root)
        reverse = validate_json_objects([first, second], base=self.root)
        self.assertEqual(forward, reverse)
        self.assertEqual(tuple(sorted(forward)), forward)
        self.assertEqual(
            {"JSON_DUPLICATE_KEY:b.json", "JSON_NONFINITE:a.json"},
            set(forward),
        )

    def test_dependency_provenance_and_pins_are_checked_offline(self) -> None:
        issues = validate_dependency_provenance(
            "alpha==1.0\nbeta==2.0\neditable>=3\n",
            "alpha==9.0\n",
            expected_requirements="alpha==1.0\nbeta==2.0\n",
            expected_lock="alpha==1.0\nbeta==2.0\n",
        )
        self.assertEqual(tuple(sorted(issues)), issues)
        self.assertEqual(
            {
                "DEPENDENCY_LOCK_PROVENANCE_MISMATCH",
                "DEPENDENCY_MISSING_FROM_LOCK:beta",
                "DEPENDENCY_PIN_MISMATCH:alpha:required=1.0:locked=9.0",
                "DEPENDENCY_REQUIREMENTS_PROVENANCE_MISMATCH",
                "DEPENDENCY_UNPINNED:requirements:3",
            },
            set(issues),
        )

    def test_matching_dependency_snapshot_has_no_issues(self) -> None:
        requirements = b"alpha==1.0\n"
        lock = b"alpha==1.0\ntransitive==4.2\n"
        self.assertEqual(
            (),
            validate_dependency_provenance(
                requirements,
                lock,
                expected_requirements=requirements,
                expected_lock=lock,
            ),
        )

    def test_ast_scan_detects_aliases_process_network_and_dynamic_escapes(self) -> None:
        game = self.root / "src" / "game"
        game.mkdir(parents=True)
        (game / "unsafe.py").write_text(
            """
import asyncio
import importlib as loader
import os as operating
import socket as net
import _socket
from requests import get as fetch
from subprocess import run as launch

runner = launch
net.socket()
fetch("https://example.invalid")
runner(["tool"])
getattr(operating, "popen")("tool")
operating.fork()
asyncio.create_subprocess_exec("tool")
loader.import_module("math")
__import__("http.client")
compile("1 + 1", "<test>", "eval")
eval("1 + 1")
exec("answer = 1")
""",
            encoding="utf-8",
        )
        issues = scan_python_capabilities(game, base=self.root)
        codes = {issue.split(":", 1)[0] for issue in issues}
        targets = {issue.rsplit(":", 1)[-1] for issue in issues}
        self.assertTrue({"PY_FORBIDDEN_IMPORT", "PY_FORBIDDEN_CALL", "PY_DYNAMIC_ESCAPE"} <= codes)
        self.assertTrue(
            {
                "socket",
                "_socket",
                "requests.get",
                "subprocess.run",
                "socket.socket",
                "os.popen",
                "os.fork",
                "asyncio.create_subprocess_exec",
                "importlib.import_module",
                "builtins.__import__",
                "builtins.compile",
                "builtins.eval",
                "builtins.exec",
            }
            <= targets
        )

    def test_ast_scan_ignores_narrative_text_and_unselected_runtime(self) -> None:
        game = self.root / "src" / "game"
        runtime = self.root / "src" / "isoworld"
        game.mkdir(parents=True)
        runtime.mkdir(parents=True)
        (game / "story.py").write_text(
            '''
"""The hero mentions HTTP, requests, subprocess, eval, server, spawn, and socket."""
DIALOGUE = "The wizard says os.system and urllib are forbidden."
# import socket

def describe() -> str:
    return "exec('narrative only')"
''',
            encoding="utf-8",
        )
        (runtime / "immutable.py").write_text("import socket\nsocket.socket()\n", encoding="utf-8")
        self.assertEqual((), scan_python_capabilities(game, base=self.root))

    def test_ast_scan_order_is_deterministic_and_selected(self) -> None:
        first = self.root / "src" / "game" / "a"
        second = self.root / "src" / "game" / "b"
        outside = self.root / "vendor"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        outside.mkdir()
        (first / "one.py").write_text("import urllib.request\n", encoding="utf-8")
        (second / "two.py").write_text("import ftplib\n", encoding="utf-8")
        (outside / "ignored.py").write_text("import subprocess\n", encoding="utf-8")
        forward = scan_python_capabilities([second, first], base=self.root)
        reverse = scan_python_capabilities([first, second], base=self.root)
        self.assertEqual(forward, reverse)
        self.assertEqual(tuple(sorted(forward)), forward)
        self.assertFalse(any("vendor/ignored.py" in issue for issue in forward))


if __name__ == "__main__":
    unittest.main()
