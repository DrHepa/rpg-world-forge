from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

import isoworld.content.publication_journal as publication_module
import worldforge.game_boundary_policy as policy_module
from isoworld.content.publication_journal import (
    MAX_JOURNAL_RECORD_BYTES,
    audit_publication_journals,
    canonical_journal_record,
    journal_frame,
    recover_last_complete_payload,
)
from worldforge.game_boundary_policy import (
    DEFAULT_IGNORED_TOP_LEVEL,
    JSONPolicyError,
    load_strict_json_object,
    scan_python_capabilities,
    terminal_publication_journal_paths,
    validate_dependency_provenance,
    validate_json_objects,
    validate_publication_journals,
    validate_regular_tree,
)
from worldforge.integrity import canonical_payload_hash


def _bundle_journal_record(
    state: str,
    *,
    operation_id: str = "1" * 32,
) -> dict[str, object]:
    return {
        "format": "isoworld.bundle_import_journal",
        "format_version": 1,
        "operation_id": operation_id,
        "state": state,
        "world_id": "test_world",
        "release_id": "1.0.0",
        "temporary": f"game_data/worlds/test_world/.1.0.0.import-{operation_id}",
        "destination": "game_data/worlds/test_world/1.0.0",
        "bundle_hash": "2" * 64,
        "catalog_before_hash": "3" * 64,
        "catalog_after_hash": "4" * 64,
        "directory_identity": (None if state == "intent" else {"device": 1, "inode": 2}),
        "created_directories": [],
    }


def _catalog_journal_record(
    state: str,
    *,
    operation_id: str = "5" * 32,
    journal_version: int = 1,
    previous_hash: object = None,
    entries: object = None,
    document: dict[str, object] | None = None,
) -> dict[str, object]:
    if document is None:
        document = {
            "format": "isoworld.composed_runtime_catalog_generation",
            "format_version": 1,
            "previous_hash": previous_hash,
            "entries": [] if entries is None else entries,
        }
    else:
        document = dict(document)
    generation_hash = canonical_payload_hash(document)
    document["content_hash"] = generation_hash
    return {
        "format": "isoworld.composed_catalog_publication",
        "format_version": journal_version,
        "operation_id": operation_id,
        "state": state,
        "generation_hash": generation_hash,
        "directory_identity": (None if state == "intent" else {"device": 3, "inode": 4}),
        "document": document,
    }


def _catalog_entry() -> dict[str, object]:
    return {
        "world_id": "test_world",
        "world_content_hash": "1" * 64,
        "release_id": "1.0.0",
        "profile_id": "profile_2_5d",
        "profile_hash": "2" * 64,
        "adapter_id": "isoworld_raylib_2_5d",
        "adapter_version": "0.1.0",
        "adapter_hash": "3" * 64,
        "composition_hash": "4" * 64,
        "bundle_id": "test_bundle",
        "bundle_version": "1.0.0",
        "bundle_hash": "5" * 64,
        "path": (
            "game_data/compositions/test_world/1.0.0/profile_2_5d/"
            "isoworld_raylib_2_5d/0.1.0/test_bundle/1.0.0"
        ),
    }


def _journal_chain(*records: dict[str, object]) -> bytes:
    first, *appended = records
    return canonical_journal_record(first) + b"".join(
        journal_frame(canonical_journal_record(record)) for record in appended
    )


def _published_catalog_phase(
    intent: dict[str, object],
) -> tuple[dict[str, object], ...]:
    identity = {"device": 3, "inode": 4}
    copying = {
        **intent,
        "state": "copying",
        "directory_identity": identity,
    }
    ready = {**copying, "state": "ready"}
    committed = {**ready, "state": "committed"}
    return intent, copying, ready, committed


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

    def test_regular_tree_accepts_an_ordinary_single_link_file(self) -> None:
        regular = self.root / "regular.txt"
        regular.write_text("safe", encoding="utf-8")

        self.assertEqual((), validate_regular_tree(self.root))

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

    def test_regular_tree_rejects_reparse_entries_without_traversing_targets(self) -> None:
        reparse_directory = self.root / "reparse-directory"
        reparse_directory.mkdir()
        nested = reparse_directory / "must-not-be-inspected.txt"
        nested.write_text("unsafe", encoding="utf-8")
        reparse_file = self.root / "reparse-file.txt"
        reparse_file.write_text("unsafe", encoding="utf-8")
        real_stat = policy_module._non_following_stat

        def reparse_entries(candidate: Path) -> object:
            if candidate == nested:
                raise AssertionError("reparse directory target was traversed")
            info = real_stat(candidate)
            if candidate in {reparse_directory, reparse_file}:
                return SimpleNamespace(
                    st_mode=info.st_mode,
                    st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
                )
            return info

        with patch.object(
            policy_module,
            "_non_following_stat",
            side_effect=reparse_entries,
        ):
            issues = validate_regular_tree(self.root)

        self.assertIn("FS_SYMLINK:reparse-directory", issues)
        self.assertIn("FS_SYMLINK:reparse-file.txt", issues)
        self.assertFalse(any("must-not-be-inspected" in issue for issue in issues), issues)

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

    def test_terminal_publication_journals_are_validated_at_exact_paths(self) -> None:
        game_data = self.root / "game_data"
        game_data.mkdir()
        bundle_path = game_data / "bundle-import.journal.json"
        catalog_path = game_data / ".composed-catalog-publication.json"
        bundle_path.write_bytes(canonical_journal_record(_bundle_journal_record("committed")))
        catalog_path.write_bytes(canonical_journal_record(_catalog_journal_record("committed")))

        audit = audit_publication_journals(self.root)

        self.assertEqual((), audit.issues)
        self.assertEqual(
            (
                PurePosixPath("game_data/.composed-catalog-publication.json"),
                PurePosixPath("game_data/bundle-import.journal.json"),
            ),
            audit.terminal_paths,
        )
        self.assertEqual((), validate_publication_journals(self.root))
        self.assertEqual(
            (catalog_path, bundle_path),
            terminal_publication_journal_paths(self.root),
        )

    def test_publication_journal_active_partial_and_foreign_chains_fail_closed(
        self,
    ) -> None:
        game_data = self.root / "game_data"
        game_data.mkdir()
        cases = (
            (
                "bundle-import.journal.json",
                _bundle_journal_record,
            ),
            (
                ".composed-catalog-publication.json",
                _catalog_journal_record,
            ),
        )
        for name, record_factory in cases:
            path = game_data / name
            terminal = canonical_journal_record(record_factory("committed"))
            next_intent = canonical_journal_record(record_factory("intent", operation_id="f" * 32))
            payloads = (
                ("JOURNAL_ACTIVE", next_intent),
                ("JOURNAL_PARTIAL", terminal + journal_frame(next_intent)[:31]),
                ("JOURNAL_INVALID", b'{"foreign":true}\n'),
            )
            for code, payload in payloads:
                with self.subTest(path=name, code=code):
                    path.write_bytes(payload)
                    issues = validate_publication_journals(self.root)
                    self.assertEqual(1, len(issues), issues)
                    self.assertTrue(issues[0].startswith(f"{code}:game_data/{name}:"))
                    with self.assertRaisesRegex(JSONPolicyError, code):
                        terminal_publication_journal_paths(self.root)
                    path.unlink()

    def test_publication_journal_never_resumes_after_a_partial_frame(self) -> None:
        game_data = self.root / "game_data"
        game_data.mkdir()
        path = game_data / "bundle-import.journal.json"
        terminal = canonical_journal_record(_bundle_journal_record("committed"))
        next_intent = canonical_journal_record(
            _bundle_journal_record("intent", operation_id="f" * 32)
        )
        next_committed = canonical_journal_record(
            _bundle_journal_record("committed", operation_id="f" * 32)
        )
        partial = journal_frame(next_intent)[:31]
        payload = terminal + partial + journal_frame(next_intent) + journal_frame(next_committed)
        path.write_bytes(payload)

        self.assertEqual(
            terminal,
            recover_last_complete_payload(
                payload,
                max_record_bytes=MAX_JOURNAL_RECORD_BYTES,
            ),
        )
        issues = validate_publication_journals(self.root)
        self.assertEqual(1, len(issues), issues)
        self.assertTrue(
            issues[0].startswith("JOURNAL_PARTIAL:game_data/bundle-import.journal.json:"),
            issues,
        )

    def test_publication_journal_preserves_base_before_torn_utf8_frame(self) -> None:
        game_data = self.root / "game_data"
        game_data.mkdir()
        path = game_data / "bundle-import.journal.json"
        terminal = canonical_journal_record(_bundle_journal_record("committed"))
        framed = journal_frame(canonical_journal_record({"text": "café"}))
        split = framed.index(b"\xc3\xa9") + 1
        payload = terminal + framed[:split]
        path.write_bytes(payload)

        self.assertEqual(
            terminal,
            recover_last_complete_payload(
                payload,
                max_record_bytes=MAX_JOURNAL_RECORD_BYTES,
            ),
        )
        issues = validate_publication_journals(self.root)
        self.assertEqual(1, len(issues), issues)
        self.assertTrue(
            issues[0].startswith("JOURNAL_PARTIAL:game_data/bundle-import.journal.json:"),
            issues,
        )

    def test_publication_journal_rejects_uppercase_frame_length_hex(self) -> None:
        game_data = self.root / "game_data"
        game_data.mkdir()
        path = game_data / "bundle-import.journal.json"
        terminal = canonical_journal_record(_bundle_journal_record("committed"))
        next_record = canonical_journal_record(
            _bundle_journal_record("intent", operation_id="f" * 32)
        )
        frame = journal_frame(next_record)
        length_start = len(publication_module.JOURNAL_FRAME_MAGIC)
        length_end = length_start + 16
        uppercase = (
            frame[:length_start] + frame[length_start:length_end].upper() + frame[length_end:]
        )
        self.assertNotEqual(
            frame[length_start:length_end],
            uppercase[length_start:length_end],
        )
        path.write_bytes(terminal + uppercase)

        issues = validate_publication_journals(self.root)
        self.assertEqual(1, len(issues), issues)
        self.assertTrue(
            issues[0].startswith("JOURNAL_INVALID:game_data/bundle-import.journal.json:"),
            issues,
        )

    def test_publication_journal_rejects_boolean_versions(self) -> None:
        game_data = self.root / "game_data"
        game_data.mkdir()
        cases: list[tuple[str, dict[str, object]]] = []
        bundle = _bundle_journal_record("committed")
        bundle["format_version"] = True
        cases.append(("bundle-import.journal.json", bundle))
        catalog = _catalog_journal_record("committed")
        document = catalog["document"]
        assert isinstance(document, dict)
        document["format_version"] = True
        generation_hash = canonical_payload_hash(document)
        document["content_hash"] = generation_hash
        catalog["generation_hash"] = generation_hash
        cases.append((".composed-catalog-publication.json", catalog))

        for name, record in cases:
            with self.subTest(name=name):
                path = game_data / name
                path.write_bytes(canonical_journal_record(record))
                issues = validate_publication_journals(self.root)
                self.assertEqual(1, len(issues), issues)
                self.assertTrue(
                    issues[0].startswith(f"JOURNAL_INVALID:game_data/{name}:"),
                    issues,
                )
                path.unlink()

    def test_catalog_journal_document_schema_is_exact_and_typed(self) -> None:
        game_data = self.root / "game_data"
        game_data.mkdir()
        path = game_data / ".composed-catalog-publication.json"
        valid_entry = _catalog_entry()
        malformed_documents: list[dict[str, object]] = [
            {
                "format": "isoworld.composed_runtime_catalog_generation",
                "format_version": 1,
                "previous_hash": None,
                "entries": [],
                "unexpected": True,
            },
            {
                "format": "isoworld.composed_runtime_catalog_generation",
                "format_version": 1,
                "previous_hash": 7,
                "entries": [],
            },
            {
                "format": "isoworld.composed_runtime_catalog_generation",
                "format_version": 1,
                "previous_hash": None,
                "entries": {},
            },
            {
                "format": "isoworld.composed_runtime_catalog_generation",
                "format_version": 1,
                "previous_hash": None,
                "entries": [{**valid_entry, "bundle_version": True}],
            },
            {
                "format": "isoworld.composed_runtime_catalog_generation",
                "format_version": 1,
                "previous_hash": None,
                "entries": [{**valid_entry, "unexpected": "field"}],
            },
        ]

        for document in malformed_documents:
            with self.subTest(document=document):
                path.write_bytes(
                    canonical_journal_record(
                        _catalog_journal_record("committed", document=document)
                    )
                )
                audit = audit_publication_journals(self.root)
                self.assertEqual((), audit.terminal_paths)
                self.assertEqual(1, len(audit.issues), audit)
                self.assertTrue(
                    audit.issues[0].startswith(
                        "JOURNAL_INVALID:game_data/.composed-catalog-publication.json:"
                    ),
                    audit,
                )

    def test_bundle_journal_operations_bind_the_catalog_hash_chain(self) -> None:
        game_data = self.root / "game_data"
        game_data.mkdir()
        path = game_data / "bundle-import.journal.json"
        first = _bundle_journal_record("committed")
        disconnected_intent = _bundle_journal_record("intent", operation_id="f" * 32)
        disconnected_committed = {
            **disconnected_intent,
            "state": "committed",
        }
        path.write_bytes(
            _journal_chain(
                first,
                disconnected_intent,
                disconnected_committed,
            )
        )

        audit = audit_publication_journals(self.root)

        self.assertEqual((), audit.terminal_paths)
        self.assertEqual(1, len(audit.issues), audit)
        self.assertTrue(
            audit.issues[0].startswith("JOURNAL_INVALID:game_data/bundle-import.journal.json:"),
            audit,
        )

        connected_intent = {
            **disconnected_intent,
            "catalog_before_hash": first["catalog_after_hash"],
        }
        connected_committed = {
            **disconnected_committed,
            "catalog_before_hash": first["catalog_after_hash"],
        }
        path.write_bytes(
            _journal_chain(
                first,
                connected_intent,
                connected_committed,
            )
        )
        audit = audit_publication_journals(self.root)
        self.assertEqual((), audit.issues)
        self.assertEqual(
            (PurePosixPath("game_data/bundle-import.journal.json"),),
            audit.terminal_paths,
        )

    def test_bundle_intent_binds_created_ancestor_identities_at_copying(self) -> None:
        game_data = self.root / "game_data"
        game_data.mkdir()
        path = game_data / "bundle-import.journal.json"
        intent = _bundle_journal_record("intent")
        created = [
            {"path": "game_data/worlds", "device": 7, "inode": 11},
            {"path": "game_data/worlds/test_world", "device": 7, "inode": 12},
        ]
        copying = {
            **intent,
            "state": "copying",
            "directory_identity": {"device": 7, "inode": 13},
            "created_directories": created,
        }
        ready = {**copying, "state": "ready"}
        committed = {**ready, "state": "committed"}
        path.write_bytes(_journal_chain(intent, copying, ready, committed))

        audit = audit_publication_journals(self.root)

        self.assertEqual((), audit.issues)
        self.assertEqual(
            (PurePosixPath("game_data/bundle-import.journal.json"),),
            audit.terminal_paths,
        )

        changed_ready = {
            **ready,
            "created_directories": [
                created[0],
                {**created[1], "inode": 99},
            ],
        }
        path.write_bytes(_journal_chain(intent, copying, changed_ready))
        audit = audit_publication_journals(self.root)
        self.assertEqual((), audit.terminal_paths)
        self.assertEqual(1, len(audit.issues), audit)
        self.assertTrue(audit.issues[0].startswith("JOURNAL_INVALID:"), audit)

    def test_aborted_bundle_intent_chains_from_unchanged_catalog_hash(self) -> None:
        game_data = self.root / "game_data"
        game_data.mkdir()
        path = game_data / "bundle-import.journal.json"
        aborted = _bundle_journal_record("committed")
        aborted["directory_identity"] = None
        next_intent = _bundle_journal_record("intent", operation_id="f" * 32)
        next_intent["catalog_before_hash"] = aborted["catalog_before_hash"]
        next_committed = {
            **next_intent,
            "state": "committed",
        }
        path.write_bytes(_journal_chain(aborted, next_intent, next_committed))

        audit = audit_publication_journals(self.root)

        self.assertEqual((), audit.issues)
        self.assertEqual(
            (PurePosixPath("game_data/bundle-import.journal.json"),),
            audit.terminal_paths,
        )

        disconnected = {
            **next_intent,
            "catalog_before_hash": aborted["catalog_after_hash"],
        }
        path.write_bytes(_journal_chain(aborted, disconnected))
        audit = audit_publication_journals(self.root)
        self.assertEqual((), audit.terminal_paths)
        self.assertEqual(1, len(audit.issues), audit)
        self.assertTrue(audit.issues[0].startswith("JOURNAL_INVALID:"), audit)

    def test_aborted_catalog_generation_retry_preserves_the_effective_head(self) -> None:
        game_data = self.root / "game_data"
        game_data.mkdir()
        path = game_data / ".composed-catalog-publication.json"
        previous_hash = "0" * 64
        aborted_intent = _catalog_journal_record(
            "intent",
            previous_hash=previous_hash,
        )
        aborted_committed = {
            **aborted_intent,
            "state": "committed",
        }
        retry_intent = _catalog_journal_record(
            "intent",
            operation_id="e" * 32,
            previous_hash=previous_hash,
        )
        path.write_bytes(
            _journal_chain(
                aborted_intent,
                aborted_committed,
                *_published_catalog_phase(retry_intent),
            )
        )

        audit = audit_publication_journals(self.root)

        self.assertEqual((), audit.issues)
        self.assertEqual(
            (PurePosixPath("game_data/.composed-catalog-publication.json"),),
            audit.terminal_paths,
        )

    def test_published_catalog_generation_advances_the_effective_head(self) -> None:
        game_data = self.root / "game_data"
        game_data.mkdir()
        path = game_data / ".composed-catalog-publication.json"
        published = _catalog_journal_record("committed")
        next_intent = _catalog_journal_record(
            "intent",
            operation_id="e" * 32,
            previous_hash=published["generation_hash"],
        )
        path.write_bytes(
            _journal_chain(
                published,
                *_published_catalog_phase(next_intent),
            )
        )

        audit = audit_publication_journals(self.root)

        self.assertEqual((), audit.issues)
        self.assertEqual(
            (PurePosixPath("game_data/.composed-catalog-publication.json"),),
            audit.terminal_paths,
        )

    def test_catalog_retry_rejects_a_disconnected_effective_head(self) -> None:
        game_data = self.root / "game_data"
        game_data.mkdir()
        path = game_data / ".composed-catalog-publication.json"
        aborted_intent = _catalog_journal_record(
            "intent",
            previous_hash="0" * 64,
        )
        aborted_committed = {
            **aborted_intent,
            "state": "committed",
        }
        disconnected = _catalog_journal_record(
            "intent",
            operation_id="e" * 32,
            previous_hash=aborted_intent["generation_hash"],
        )
        path.write_bytes(
            _journal_chain(
                aborted_intent,
                aborted_committed,
                disconnected,
            )
        )

        audit = audit_publication_journals(self.root)

        self.assertEqual((), audit.terminal_paths)
        self.assertEqual(1, len(audit.issues), audit)
        self.assertTrue(audit.issues[0].startswith("JOURNAL_INVALID:"), audit)

    def test_catalog_v2_commit_requires_a_bound_v1_publication_phase(self) -> None:
        game_data = self.root / "game_data"
        game_data.mkdir()
        path = game_data / ".composed-catalog-publication.json"
        composed_commit = _catalog_journal_record(
            "committed",
            journal_version=2,
            previous_hash="0" * 64,
            entries=[_catalog_entry()],
        )
        path.write_bytes(_journal_chain(composed_commit))

        audit = audit_publication_journals(self.root)

        self.assertEqual((), audit.terminal_paths)
        self.assertEqual(1, len(audit.issues), audit)
        self.assertTrue(
            audit.issues[0].startswith(
                "JOURNAL_ACTIVE:game_data/.composed-catalog-publication.json:"
            ),
            audit,
        )
        with self.assertRaisesRegex(JSONPolicyError, "JOURNAL_ACTIVE"):
            terminal_publication_journal_paths(self.root)

        unbound_intent = _catalog_journal_record(
            "intent",
            operation_id="f" * 32,
            journal_version=1,
            previous_hash="0" * 64,
            entries=[],
        )
        path.write_bytes(
            _journal_chain(
                composed_commit,
                unbound_intent,
            )
        )
        audit = audit_publication_journals(self.root)
        self.assertEqual((), audit.terminal_paths)
        self.assertEqual(1, len(audit.issues), audit)
        self.assertTrue(audit.issues[0].startswith("JOURNAL_INVALID:"), audit)

        composed_document = composed_commit["document"]
        assert isinstance(composed_document, dict)
        bound_intent = _catalog_journal_record(
            "intent",
            operation_id="f" * 32,
            journal_version=1,
            document=composed_document,
        )
        path.write_bytes(
            _journal_chain(
                composed_commit,
                *_published_catalog_phase(bound_intent),
            )
        )
        audit = audit_publication_journals(self.root)
        self.assertEqual((), audit.issues)
        self.assertEqual(
            (PurePosixPath("game_data/.composed-catalog-publication.json"),),
            audit.terminal_paths,
        )

    def test_publication_journal_never_resumes_after_invalid_appended_bytes(
        self,
    ) -> None:
        game_data = self.root / "game_data"
        game_data.mkdir()
        path = game_data / "bundle-import.journal.json"
        terminal = canonical_journal_record(_bundle_journal_record("committed"))
        next_intent = canonical_journal_record(
            _bundle_journal_record("intent", operation_id="f" * 32)
        )
        next_committed = canonical_journal_record(
            _bundle_journal_record("committed", operation_id="f" * 32)
        )
        payload = (
            terminal
            + b"invalid-appended-bytes"
            + journal_frame(next_intent)
            + journal_frame(next_committed)
        )
        path.write_bytes(payload)

        self.assertEqual(
            terminal,
            recover_last_complete_payload(
                payload,
                max_record_bytes=MAX_JOURNAL_RECORD_BYTES,
            ),
        )
        issues = validate_publication_journals(self.root)
        self.assertEqual(1, len(issues), issues)
        self.assertTrue(
            issues[0].startswith("JOURNAL_INVALID:game_data/bundle-import.journal.json:"),
            issues,
        )

    def test_publication_journal_normalizes_non_hashable_json_shapes(self) -> None:
        game_data = self.root / "game_data"
        game_data.mkdir()
        path = game_data / "bundle-import.journal.json"
        malformed_records: list[dict[str, object]] = []

        state_record = _bundle_journal_record("intent")
        state_record["state"] = {}
        malformed_records.append(state_record)

        created_path_record = _bundle_journal_record("intent")
        created_path_record["created_directories"] = [{"path": [], "device": 1, "inode": 1}]
        malformed_records.append(created_path_record)

        for record in malformed_records:
            with self.subTest(field=record):
                path.write_bytes(canonical_journal_record(record))
                issues = validate_publication_journals(self.root)
                self.assertEqual(1, len(issues), issues)
                self.assertTrue(
                    issues[0].startswith("JOURNAL_INVALID:game_data/bundle-import.journal.json:"),
                    issues,
                )

        path.unlink()
        catalog_path = game_data / ".composed-catalog-publication.json"
        catalog_record = _catalog_journal_record("intent")
        catalog_record["state"] = []
        catalog_path.write_bytes(canonical_journal_record(catalog_record))
        issues = validate_publication_journals(self.root)
        self.assertEqual(1, len(issues), issues)
        self.assertTrue(
            issues[0].startswith("JOURNAL_INVALID:game_data/.composed-catalog-publication.json:"),
            issues,
        )

    def test_publication_journal_normalizes_extreme_json_depth(self) -> None:
        game_data = self.root / "game_data"
        game_data.mkdir()
        path = game_data / "bundle-import.journal.json"
        depth = sys.getrecursionlimit() + 200
        path.write_bytes(b'{"state":' + (b"[" * depth) + b"0" + (b"]" * depth) + b"}\n")

        issues = validate_publication_journals(self.root)

        self.assertEqual(1, len(issues), issues)
        self.assertTrue(
            issues[0].startswith("JOURNAL_INVALID:game_data/bundle-import.journal.json:"),
            issues,
        )

    def test_publication_journal_normalizes_invalid_unicode_scalar(self) -> None:
        game_data = self.root / "game_data"
        game_data.mkdir()
        path = game_data / "bundle-import.journal.json"
        path.write_bytes(b'{"state":"\\ud800"}\n')

        issues = validate_publication_journals(self.root)

        self.assertEqual(1, len(issues), issues)
        self.assertTrue(
            issues[0].startswith("JOURNAL_INVALID:game_data/bundle-import.journal.json:"),
            issues,
        )

    def test_publication_journal_revalidates_file_state_after_read(self) -> None:
        game_data = self.root / "game_data"
        game_data.mkdir()
        path = game_data / "bundle-import.journal.json"
        path.write_bytes(canonical_journal_record(_bundle_journal_record("committed")))
        real_descriptor_file_stat = publication_module.descriptor_file_stat

        def changed_after_read(field: str):
            calls = 0

            def inspect(descriptor: int) -> object:
                nonlocal calls
                calls += 1
                info = real_descriptor_file_stat(descriptor)
                if calls == 1:
                    return info
                values = {
                    "st_mode": info.st_mode,
                    "st_dev": info.st_dev,
                    "st_ino": info.st_ino,
                    "st_nlink": info.st_nlink,
                    "st_size": info.st_size,
                    "st_mtime_ns": info.st_mtime_ns,
                    "st_ctime_ns": info.st_ctime_ns,
                    "st_file_attributes": getattr(info, "st_file_attributes", 0),
                }
                if field == "st_mode":
                    values[field] = stat.S_IFIFO | stat.S_IMODE(info.st_mode)
                elif field == "st_file_attributes":
                    values[field] |= getattr(
                        stat,
                        "FILE_ATTRIBUTE_REPARSE_POINT",
                        0x00000400,
                    )
                else:
                    values[field] += 1
                return SimpleNamespace(**values)

            return inspect

        for field in ("st_nlink", "st_ctime_ns", "st_mode", "st_file_attributes"):
            with (
                self.subTest(field=field),
                patch.object(
                    publication_module,
                    "descriptor_file_stat",
                    side_effect=changed_after_read(field),
                ),
            ):
                issues = validate_publication_journals(self.root)
                self.assertEqual(1, len(issues), issues)
                self.assertTrue(
                    issues[0].startswith("JOURNAL_INVALID:game_data/bundle-import.journal.json:"),
                    issues,
                )

    def test_publication_journal_rejects_links(self) -> None:
        game_data = self.root / "game_data"
        game_data.mkdir()
        path = game_data / "bundle-import.journal.json"
        target = game_data / "journal-target"
        target.write_bytes(canonical_journal_record(_bundle_journal_record("committed")))
        try:
            os.link(target, path)
        except OSError as exc:
            self.skipTest(f"hardlinks unavailable: {exc}")
        self.assertTrue(validate_publication_journals(self.root)[0].startswith("JOURNAL_INVALID:"))
        path.unlink()
        try:
            path.symlink_to(target.name)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        self.assertTrue(validate_publication_journals(self.root)[0].startswith("JOURNAL_INVALID:"))

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
