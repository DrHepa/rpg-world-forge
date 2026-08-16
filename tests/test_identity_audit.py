from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from worldforge.__main__ import main
from worldforge.identity_audit import (
    IdentityAuditError,
    ReviewedIdentityPolicy,
    _category_accepts_path,
    audit_identities,
    refresh_identity_allowlist_evidence,
)
from worldforge.integrity import canonical_json_bytes


def _write_allowlist(root: Path, entries: list[dict[str, object]]) -> Path:
    bound_entries: list[dict[str, object]] = []
    for source_entry in entries:
        entry = dict(source_entry)
        requested_count = entry.pop("count", None)
        relative = entry.get("path")
        pattern = entry.get("pattern")
        payload = b""
        offsets: list[int] = [0]
        if isinstance(relative, str) and isinstance(pattern, str):
            target = root / relative
            try:
                payload = target.read_bytes()
            except OSError:
                payload = b""
            needle = pattern.encode("ascii")
            offsets = []
            position = 0
            while True:
                found = payload.find(needle, position)
                if found < 0:
                    break
                offsets.append(found)
                position = found + len(needle)
        if (
            isinstance(requested_count, int)
            and not isinstance(requested_count, bool)
            and requested_count > len(offsets)
        ):
            next_offset = (offsets[-1] + len(str(pattern)) + 1) if offsets else 0
            while len(offsets) < requested_count:
                offsets.append(next_offset)
                next_offset += len(str(pattern)) + 1
        entry.setdefault("file_sha256", hashlib.sha256(payload).hexdigest())
        entry.setdefault("offsets", offsets)
        bound_entries.append(entry)
    path = root / "contracts/legacy-identity-allowlist.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        canonical_json_bytes(
            {
                "format": "world-forge.legacy_identity_allowlist",
                "format_version": 1,
                "entries": bound_entries,
            }
        )
    )
    return path


class IdentityAuditTests(unittest.TestCase):
    def test_category_path_policy_is_boundary_safe_and_fail_closed(self) -> None:
        exact_rules = (
            ("compatibility_reader", "README.md"),
            ("compatibility_reader", "pyproject.toml"),
            ("legacy_contract", "contracts/catalog.json"),
            ("license_third_party_notice", "LICENSE"),
            ("migration", "MANIFEST.in"),
            ("migration", "README.md"),
            ("migration", "pyproject.toml"),
        )
        for category, exact_path in exact_rules:
            with self.subTest(category=category, exact_path=exact_path):
                self.assertTrue(_category_accepts_path(category, exact_path))
                self.assertFalse(_category_accepts_path(category, f"{exact_path}.unreviewed"))
                self.assertFalse(_category_accepts_path(category, f"{exact_path}/child"))
                self.assertFalse(_category_accepts_path(category, f".{exact_path}"))

        directory_rules = (
            ("compatibility_reader", "apps"),
            ("compatibility_reader", "docs"),
            ("compatibility_reader", "scripts"),
            ("compatibility_reader", "src"),
            ("historical_provenance", "docs/decisions"),
            ("historical_provenance", "docs/audits"),
            ("legacy_contract", "authoring"),
            ("legacy_contract", "schemas"),
            ("license_third_party_notice", "apps/studio/packaging/notices"),
            ("license_third_party_notice", "docs/licenses"),
            ("migration", ".github"),
            ("regression_fixture", "examples"),
            ("regression_fixture", "tests"),
            ("regression_fixture", "apps/studio/tests"),
        )
        for category, directory in directory_rules:
            with self.subTest(category=category, directory=directory):
                self.assertTrue(_category_accepts_path(category, directory))
                self.assertTrue(_category_accepts_path(category, f"{directory}/reviewed.txt"))
                self.assertFalse(
                    _category_accepts_path(category, f"{directory}-sibling/reviewed.txt")
                )

        segment_prefix_rules = (
            ("historical_provenance", "docs/M5_"),
            ("historical_provenance", "docs/M6_"),
            ("license_third_party_notice", "THIRD_PARTY"),
        )
        for category, segment_prefix in segment_prefix_rules:
            with self.subTest(category=category, segment_prefix=segment_prefix):
                self.assertTrue(_category_accepts_path(category, f"{segment_prefix}REVIEWED.md"))
                self.assertFalse(
                    _category_accepts_path(category, f"{segment_prefix}SIBLING/reviewed.txt")
                )

        for unsafe_path in (
            "docs/../README.md",
            "docs\\README.md",
            "/README.md",
            "./README.md",
        ):
            with self.subTest(unsafe_path=unsafe_path):
                self.assertFalse(_category_accepts_path("migration", unsafe_path))

        self.assertFalse(_category_accepts_path("tampered_category", "README.md"))

    def test_exact_allowlist_accepts_legacy_contracts_and_regression_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "schemas").mkdir()
            (root / "tests").mkdir()
            (root / ".git").mkdir()
            (root / "build").mkdir()
            (root / "schemas/legacy.schema.json").write_text(
                '{"const":"rpg-world-forge.project"}\n',
                encoding="utf-8",
            )
            (root / "tests/test_legacy.py").write_text(
                'LEGACY = "RPG World Forge"\n',
                encoding="utf-8",
            )
            (root / ".git/config").write_text("rpg-world-forge\n", encoding="utf-8")
            (root / "build/output.txt").write_text("RPG World Forge\n", encoding="utf-8")
            _write_allowlist(
                root,
                [
                    {
                        "category": "legacy_contract",
                        "count": 1,
                        "justification": "Published project discriminator remains immutable.",
                        "path": "schemas/legacy.schema.json",
                        "pattern": "rpg-world-forge",
                    },
                    {
                        "category": "regression_fixture",
                        "count": 1,
                        "justification": (
                            "Regression verifies legacy product input remains readable."
                        ),
                        "path": "tests/test_legacy.py",
                        "pattern": "RPG World Forge",
                    },
                ],
            )

            result = audit_identities(root)

            self.assertEqual(2, result.occurrences)
            self.assertEqual(2, result.entries)

    def test_scans_binary_invalid_utf8_and_chunk_boundary_occurrences(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "tests/raw.bin"
            target.parent.mkdir()
            pattern = b"rpg-world-forge"
            chunk = 64 * 1024
            target.write_bytes(
                b"\0\xff" + b"x" * (chunk - 2 - 7) + pattern[:7] + pattern[7:] + b"\0" + pattern
            )
            _write_allowlist(
                root,
                [
                    {
                        "category": "regression_fixture",
                        "count": 2,
                        "justification": "Raw fixture proves boundary-safe binary scanning.",
                        "path": "tests/raw.bin",
                        "pattern": "rpg-world-forge",
                    }
                ],
            )

            result = audit_identities(root)

            self.assertEqual(2, result.occurrences)

    def test_nested_build_names_cannot_hide_new_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hidden = root / "src/build/hidden.py"
            hidden.parent.mkdir(parents=True)
            hidden.write_text('LEGACY = "rpg-world-forge"\n', encoding="utf-8")
            _write_allowlist(root, [])

            with self.assertRaisesRegex(IdentityAuditError, "unallowlisted legacy identity"):
                audit_identities(root)

    def test_nested_python_cache_and_bytecode_are_excluded_generically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "plugins/custom/deep/__pycache__"
            cache.mkdir(parents=True)
            (cache / "module.cpython-312.pyc").write_bytes(b"rpg-world-forge\n")
            loose_bytecode = root / "plugins/custom/deep/compiled.pyo"
            loose_bytecode.write_bytes(b"RPG World Forge\n")
            _write_allowlist(root, [])

            result = audit_identities(root)

            self.assertEqual(0, result.entries)
            self.assertEqual(0, result.occurrences)

    def test_rejects_casefold_collisions_hardlinks_and_symlinks(self) -> None:
        with self.subTest("casefold collision"), tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tests").mkdir()
            (root / "tests/Legacy.txt").write_bytes(b"safe")
            (root / "tests/legacy.txt").write_bytes(b"safe")
            _write_allowlist(root, [])
            with self.assertRaisesRegex(IdentityAuditError, "portable path collision"):
                audit_identities(root)

        with self.subTest("hardlink"), tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tests").mkdir()
            source = root / "tests/source.txt"
            source.write_bytes(b"safe")
            try:
                os.link(source, root / "tests/alias.txt")
            except (OSError, NotImplementedError):
                self.skipTest("hardlinks are unavailable on this filesystem")
            _write_allowlist(root, [])
            with self.assertRaisesRegex(IdentityAuditError, "hard-linked"):
                audit_identities(root)

        with self.subTest("symlink"), tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tests").mkdir()
            source = root / "tests/source.txt"
            source.write_bytes(b"safe")
            try:
                os.symlink(source.name, root / "tests/alias.txt")
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable on this filesystem")
            _write_allowlist(root, [])
            with self.assertRaisesRegex(IdentityAuditError, "unsafe source entry"):
                audit_identities(root)

    def test_allowlist_requires_exact_noncolliding_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_allowlist(
                root,
                [
                    {
                        "category": "regression_fixture",
                        "count": 1,
                        "justification": "Aggregate path must be rejected.",
                        "path": "tests/**",
                        "pattern": "rpg-world-forge",
                    }
                ],
            )
            with self.assertRaisesRegex(IdentityAuditError, "must not contain wildcards"):
                audit_identities(root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_allowlist(
                root,
                [
                    {
                        "category": "regression_fixture",
                        "count": 1,
                        "justification": "First portable path.",
                        "path": "tests/Legacy.py",
                        "pattern": "rpg-world-forge",
                    },
                    {
                        "category": "regression_fixture",
                        "count": 1,
                        "justification": "Colliding portable path.",
                        "path": "tests/legacy.py",
                        "pattern": "RPG World Forge",
                    },
                ],
            )
            with self.assertRaisesRegex(IdentityAuditError, "allowlist path collision"):
                audit_identities(root)

    def test_same_count_moved_to_another_file_is_not_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tests").mkdir()
            original = root / "tests/original.py"
            moved = root / "tests/moved.py"
            original.write_text('LEGACY = "rpg-world-forge"\n', encoding="utf-8")
            _write_allowlist(
                root,
                [
                    {
                        "category": "regression_fixture",
                        "count": 1,
                        "justification": "Only the reviewed file is allowed.",
                        "path": "tests/original.py",
                        "pattern": "rpg-world-forge",
                    }
                ],
            )
            original.write_text("SAFE = True\n", encoding="utf-8")
            moved.write_text('LEGACY = "rpg-world-forge"\n', encoding="utf-8")

            with self.assertRaisesRegex(
                IdentityAuditError,
                "unallowlisted legacy identity.*tests/moved.py",
            ):
                audit_identities(root)

    def test_same_file_move_or_reseal_invalidates_bound_evidence(self) -> None:
        with self.subTest("same-file move"), tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "tests/reviewed.py"
            target.parent.mkdir()
            target.write_text('LEGACY = "rpg-world-forge"\n', encoding="utf-8")
            _write_allowlist(
                root,
                [
                    {
                        "category": "regression_fixture",
                        "count": 1,
                        "justification": "Exact reviewed bytes and location are immutable.",
                        "path": "tests/reviewed.py",
                        "pattern": "rpg-world-forge",
                    }
                ],
            )
            target.write_text('PREFIX = True\nLEGACY = "rpg-world-forge"\n', encoding="utf-8")

            with self.assertRaisesRegex(IdentityAuditError, "file hash|offsets"):
                audit_identities(root)

        with self.subTest("same-offset reseal"), tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "tests/reviewed.py"
            target.parent.mkdir()
            target.write_bytes(b"rpg-world-forge:alpha\n")
            _write_allowlist(
                root,
                [
                    {
                        "category": "regression_fixture",
                        "count": 1,
                        "justification": "Unmatched surrounding bytes remain reviewed.",
                        "path": "tests/reviewed.py",
                        "pattern": "rpg-world-forge",
                    }
                ],
            )
            target.write_bytes(b"rpg-world-forge:omega\n")

            with self.assertRaisesRegex(IdentityAuditError, "file hash"):
                audit_identities(root)

    def test_allowlist_offsets_are_closed_nonoverlapping_and_count_is_derived(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "tests/reviewed.py"
            target.parent.mkdir()
            target.write_bytes(b"rpg-world-forge\nrpg-world-forge\n")
            allowlist = _write_allowlist(
                root,
                [
                    {
                        "category": "regression_fixture",
                        "count": 2,
                        "justification": "Both exact raw byte spans were reviewed.",
                        "path": "tests/reviewed.py",
                        "pattern": "rpg-world-forge",
                    }
                ],
            )
            document = json.loads(allowlist.read_text(encoding="utf-8"))
            entry = document["entries"][0]

            self.assertNotIn("count", entry)
            self.assertEqual([0, 16], entry["offsets"])
            self.assertEqual(2, audit_identities(root).occurrences)

            entry["offsets"] = [0, 1]
            allowlist.write_bytes(canonical_json_bytes(document))
            with self.assertRaisesRegex(IdentityAuditError, "overlap|offsets"):
                audit_identities(root)

    def test_refresh_writer_rebinds_existing_reviewed_rows_but_not_new_references(
        self,
    ) -> None:
        from worldforge.identity_audit import refresh_identity_allowlist_evidence

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "tests/reviewed.py"
            target.parent.mkdir()
            target.write_bytes(b"rpg-world-forge:alpha\n")
            allowlist = _write_allowlist(
                root,
                [
                    {
                        "category": "regression_fixture",
                        "count": 1,
                        "justification": "Existing reviewed semantic row is retained.",
                        "path": "tests/reviewed.py",
                        "pattern": "rpg-world-forge",
                    }
                ],
            )
            target.write_bytes(b"prefix:rpg-world-forge:omega\n")

            refresh_identity_allowlist_evidence(root)

            rebound = allowlist.read_bytes()
            self.assertEqual(canonical_json_bytes(json.loads(rebound)), rebound)
            self.assertEqual(1, audit_identities(root).occurrences)

            (root / "tests/unreviewed.py").write_bytes(b"RPG World Forge\n")
            with self.assertRaisesRegex(IdentityAuditError, "unallowlisted legacy identity"):
                refresh_identity_allowlist_evidence(root)

    def test_refresh_adds_only_rows_in_the_explicit_reviewed_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reviewed = root / "docs/SUPPORT_MATRIX.md"
            reviewed.parent.mkdir()
            reviewed.write_text("Retained: rpg-world-forge.project\n", encoding="utf-8")
            _write_allowlist(root, [])
            policy = {
                ("docs/SUPPORT_MATRIX.md", "rpg-world-forge"): ReviewedIdentityPolicy(
                    category="legacy_contract",
                    justification="Documents one retained published project discriminator.",
                )
            }

            refresh_identity_allowlist_evidence(root, reviewed_policy=policy)

            document = json.loads(
                (root / "contracts/legacy-identity-allowlist.json").read_text(encoding="utf-8")
            )
            self.assertEqual("legacy_contract", document["entries"][0]["category"])
            self.assertEqual(
                "Documents one retained published project discriminator.",
                document["entries"][0]["justification"],
            )
            self.assertEqual(1, audit_identities(root).occurrences)

            unknown = root / "docs/UNREVIEWED.md"
            unknown.write_text("RPG World Forge\n", encoding="utf-8")
            with self.assertRaisesRegex(
                IdentityAuditError,
                "unallowlisted legacy identity.*docs/UNREVIEWED.md",
            ):
                refresh_identity_allowlist_evidence(root, reviewed_policy=policy)

    def test_generator_reviewed_policy_accepts_hosted_workflow_bridge_only(self) -> None:
        from scripts.generate_identity_allowlist import REVIEWED_ADDITIONS

        key = (".github/workflows/ci.yml", "rpg-world-forge")
        policy = REVIEWED_ADDITIONS[key]
        self.assertEqual(policy.category, "migration")
        self.assertIn("trusted main pushes", policy.justification)
        self.assertIn("stable GitHub repository ID", policy.justification)

    def test_generator_reviewed_policy_accepts_hosted_receipt_bridge_only(self) -> None:
        from scripts.generate_identity_allowlist import REVIEWED_ADDITIONS

        source_schema = (
            Path(__file__).resolve().parents[1]
            / "schemas/hosted-native-release-attestation-receipt.schema.json"
        )
        key = (
            "schemas/hosted-native-release-attestation-receipt.schema.json",
            "rpg-world-forge",
        )
        policy = REVIEWED_ADDITIONS[key]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / key[0]
            target.parent.mkdir()
            target.write_bytes(source_schema.read_bytes())
            _write_allowlist(
                root,
                [
                    {
                        "category": policy.category,
                        "justification": policy.justification,
                        "path": key[0],
                        "pattern": key[1],
                    }
                ],
            )

            result = audit_identities(root)
            document = json.loads(
                (root / "contracts/legacy-identity-allowlist.json").read_text(encoding="utf-8")
            )
            self.assertEqual(1, result.occurrences)
            self.assertEqual(
                {
                    "category": "legacy_contract",
                    "justification": (
                        "Retains the explicit old/new hosted repository bridge for "
                        "attestation receipts beside the stable GitHub repository ID."
                    ),
                    "offsets": [2195],
                    "path": key[0],
                    "pattern": key[1],
                },
                {
                    field: document["entries"][0][field]
                    for field in ("category", "justification", "offsets", "path", "pattern")
                },
            )

            unknown = root / "schemas/hosted-native-release-attestation-receipt-copy.schema.json"
            unknown.write_bytes(source_schema.read_bytes())
            with self.assertRaisesRegex(
                IdentityAuditError,
                "unallowlisted legacy identity.*hosted-native-release-attestation-receipt-copy",
            ):
                audit_identities(root)

    def test_generator_reviewed_policy_accepts_hosted_receipt_cli_bridge_only(self) -> None:
        from scripts.generate_identity_allowlist import REVIEWED_ADDITIONS

        source_script = (
            Path(__file__).resolve().parents[1] / "scripts/verify_hosted_native_release.py"
        )
        key = ("scripts/verify_hosted_native_release.py", "rpg-world-forge")
        policy = REVIEWED_ADDITIONS[key]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / key[0]
            target.parent.mkdir()
            target.write_bytes(source_script.read_bytes())
            _write_allowlist(
                root,
                [
                    {
                        "category": policy.category,
                        "justification": policy.justification,
                        "path": key[0],
                        "pattern": key[1],
                    }
                ],
            )

            result = audit_identities(root)
            document = json.loads(
                (root / "contracts/legacy-identity-allowlist.json").read_text(encoding="utf-8")
            )
            self.assertEqual(1, result.occurrences)
            self.assertEqual(
                {
                    "category": "migration",
                    "justification": (
                        "Restricts the hosted receipt CLI old/new repository bridge to the "
                        "trusted GitHub repository context and stable repository ID."
                    ),
                    "offsets": [930],
                    "path": key[0],
                    "pattern": key[1],
                },
                {
                    field: document["entries"][0][field]
                    for field in ("category", "justification", "offsets", "path", "pattern")
                },
            )

            unknown = root / "scripts/verify_hosted_native_release_copy.py"
            unknown.write_bytes(source_script.read_bytes())
            with self.assertRaisesRegex(
                IdentityAuditError,
                "unallowlisted legacy identity.*verify_hosted_native_release_copy",
            ):
                audit_identities(root)

    def test_generator_reviewed_policy_accepts_multigenre_release_gate_bridge_only(
        self,
    ) -> None:
        from scripts.generate_identity_allowlist import REVIEWED_ADDITIONS

        key = ("tests/test_multigenre_release_gate.py", "rpg-world-forge")
        self.assertNotIn(key, REVIEWED_ADDITIONS)
        source_test = Path(__file__).resolve().parents[1] / key[0]
        self.assertNotIn(key[1].encode("utf-8"), source_test.read_bytes())

    def test_directory_swap_and_restore_cannot_hide_identity_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "source"
            original = root / "tests"
            decoy = workspace / "decoy"
            parked = workspace / "parked"
            original.mkdir(parents=True)
            decoy.mkdir()
            (original / "hidden.py").write_bytes(b"rpg-world-forge\n")
            (decoy / "safe.py").write_bytes(b"SAFE = True\n")
            _write_allowlist(root, [])
            swapped = False

            def hook(event: str, _relative: str | None) -> None:
                nonlocal swapped
                if event == "after_root_retained":
                    os.replace(original, parked)
                    os.replace(decoy, original)
                    swapped = True
                elif event == "before_final_verification" and swapped:
                    os.replace(original, decoy)
                    os.replace(parked, original)
                    swapped = False

            try:
                with self.assertRaisesRegex(
                    IdentityAuditError,
                    "directory binding changed|source tree changed",
                ):
                    audit_identities(root, _verification_hook=hook)
            finally:
                if swapped:
                    os.replace(original, decoy)
                    os.replace(parked, original)

    def test_only_the_validated_allowlist_file_is_excluded_from_matching(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_allowlist(root, [])
            decoy = root / "contracts/legacy-identity-allowlist-copy.json"
            decoy.write_text('{"pattern":"rpg-world-forge"}\n', encoding="utf-8")

            with self.assertRaisesRegex(
                IdentityAuditError,
                "unallowlisted legacy identity.*legacy-identity-allowlist-copy.json",
            ):
                audit_identities(root)

    def test_rejects_unallowlisted_stale_and_wrong_category_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "src/reader.py").write_text(
                'LEGACY = "rpg-world-forge"\n',
                encoding="utf-8",
            )
            allowlist = _write_allowlist(root, [])
            with self.assertRaisesRegex(IdentityAuditError, "unallowlisted legacy identity"):
                audit_identities(root, allowlist_path=allowlist)

            _write_allowlist(
                root,
                [
                    {
                        "category": "compatibility_reader",
                        "count": 2,
                        "justification": "Reader accepts the published legacy identity.",
                        "path": "src/reader.py",
                        "pattern": "rpg-world-forge",
                    }
                ],
            )
            with self.assertRaisesRegex(IdentityAuditError, "stale allowlist entry"):
                audit_identities(root, allowlist_path=allowlist)

            _write_allowlist(
                root,
                [
                    {
                        "category": "historical_provenance",
                        "count": 1,
                        "justification": "Invalid category for executable source.",
                        "path": "src/reader.py",
                        "pattern": "rpg-world-forge",
                    }
                ],
            )
            with self.assertRaisesRegex(IdentityAuditError, "category is not valid for path"):
                audit_identities(root, allowlist_path=allowlist)

    def test_cli_reports_contract_errors_on_stderr_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text("# RPG World Forge\n", encoding="utf-8")
            _write_allowlist(root, [])
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                unittest.mock.patch(
                    "sys.argv",
                    ["worldforge", "audit-identities", "--source-root", str(root)],
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                status = main()

            self.assertEqual(1, status)
            self.assertEqual("", stdout.getvalue())
            self.assertIn("ERROR", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
