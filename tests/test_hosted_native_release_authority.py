from __future__ import annotations

import copy
import hashlib
import json
import os
import pickle
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import worldforge.hosted_native_release_authority as hosted_authority
from scripts import verify_multigenre_release as gate
from tests.test_multigenre_release_gate import _matrix_report
from worldforge.hosted_native_release_authority import (
    HOSTED_NATIVE_RELEASE_AUTHORITY_FORMAT,
    HostedNativeReleaseAuthorityError,
    HostedNativeReleaseCandidate,
    VerifiedHostedNativeAggregate,
    VerifiedHostedNativeReleaseAuthority,
    VerifiedHostedNativeRow,
    build_hosted_native_release_authority,
    canonical_hosted_authority_id,
    derive_hosted_native_release_authority,
    load_hosted_native_release_authority,
    serialize_hosted_native_release_authority,
    validate_hosted_native_release_authority_document,
    verify_attested_hosted_native_release_authority_file,
    verify_hosted_native_aggregate,
    verify_hosted_native_release_authority_file,
    verify_hosted_native_row_file,
)
from worldforge.integrity import canonical_json_bytes
from worldforge.runtime_support_authority import (
    RUNTIME_SUPPORT_AUTHORITY_NATIVE_UNAVAILABLE,
    attach_native_evidence,
)

ROOT = Path(__file__).resolve().parents[1]


def _source_context(revision: str = "e" * 40, input_tree_hash: str = "d" * 64) -> dict[str, object]:
    return {
        "repository_id": "1305601753",
        "repository": "DrHepa/rpg-world-forge",
        "workflow_ref": "DrHepa/rpg-world-forge/.github/workflows/ci.yml@refs/heads/main",
        "workflow_sha": revision,
        "revision": revision,
        "input_tree_hash": input_tree_hash,
        "ref": "refs/heads/main",
        "event": "push",
        "run_id": "30125999537",
        "run_attempt": 1,
    }


def _write_rows(root: Path) -> list[Path]:
    root.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for os_name, python_minor in gate.REQUIRED_MATRIX:
        report = _matrix_report(os_name, python_minor)
        path = root / f"{os_name}-cp{python_minor.replace('.', '')}.json"
        path.write_bytes(canonical_json_bytes(report))
        paths.append(path)
    return paths


def _verified_authority(root: Path) -> tuple[dict[str, object], VerifiedHostedNativeAggregate]:
    rows = [verify_hosted_native_row_file(path) for path in _write_rows(root)]
    aggregate = verify_hosted_native_aggregate(rows)
    return build_hosted_native_release_authority(aggregate, source=_source_context()), aggregate


def _official_gh_verifier_stdout(
    *,
    subject_name: str = "authority.json",
    subject_sha256: str = "a" * 64,
    attestation: dict[str, object] | None = None,
    timestamp_payload: dict[str, object] | None = None,
    certificate_payload: dict[str, object] | None = None,
) -> bytes:
    statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "predicateType": hosted_authority.HOSTED_NATIVE_SLSA_PREDICATE_TYPE,
        "subject": [
            {
                "name": subject_name,
                "digest": {"sha256": subject_sha256},
            }
        ],
        "predicate": {"buildDefinition": {"buildType": "https://github.com/actions"}},
    }
    attestation_object = attestation or {
        "mediaType": "application/vnd.dsse.envelope.v1+json",
        "predicateType": hosted_authority.HOSTED_NATIVE_SLSA_PREDICATE_TYPE,
        "statement": statement,
    }
    verification_result = {
        "statement": statement,
        "verifiedTimestamps": [
            timestamp_payload or {"kind": "rfc3161", "token": "stable-even-if-gh-renames-this"}
        ],
        "signature": {
            "certificate": certificate_payload
            or {"raw": "cert-bytes", "extensions": {"issuer": "github"}}
        },
    }
    return canonical_json_bytes(
        [{"attestation": attestation_object, "verificationResult": verification_result}]
    )


def _write_bundle(path: Path, attestation: dict[str, object], *, jsonl: bool = False) -> None:
    if jsonl:
        payload = json.dumps(
            attestation, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        path.write_bytes(payload + b"\n")
    else:
        path.write_bytes(canonical_json_bytes(attestation))


class HostedNativeReleaseAuthorityTests(unittest.TestCase):
    def test_cataloged_hosted_symbols_import_with_scripts_unavailable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-installed-boundary-") as temporary:
            root = Path(temporary)
            probe = root / "probe.py"
            probe.write_text(
                """
from __future__ import annotations

import importlib.abc
import sys
from pathlib import Path

source_root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(source_root / 'src'))
for value in tuple(sys.path):
    if Path(value or '.').resolve() == source_root:
        sys.path.remove(value)

class BlockScripts(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'scripts' or fullname.startswith('scripts.'):
            raise ModuleNotFoundError('repo-only scripts package is unavailable')
        return None

sys.meta_path.insert(0, BlockScripts())
from worldforge.contract_catalog import audit_contracts
from worldforge.hosted_native_release_authority import (
    build_hosted_native_release_authority,
    validate_hosted_native_release_authority_document,
    verify_hosted_native_row_file,
)

audit_contracts(source_root=source_root)
print(build_hosted_native_release_authority.__name__)
print(validate_hosted_native_release_authority_document.__name__)
print(verify_hosted_native_row_file.__name__)
""",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = str(ROOT / "src")
            result = subprocess.run(
                [sys.executable, "-I", str(probe), str(ROOT)],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "build_hosted_native_release_authority",
                "validate_hosted_native_release_authority_document",
                "verify_hosted_native_row_file",
            ],
        )

    def test_file_verified_authority_has_deterministic_bytes_hash_id_and_opaque_handles(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-hosted-authority-") as temporary:
            root = Path(temporary)
            document, aggregate = _verified_authority(root)
            payload = serialize_hosted_native_release_authority(document)
            self.assertEqual(payload, canonical_json_bytes(document))
            without_hash = dict(document)
            without_hash.pop("content_hash")
            self.assertEqual(
                document["content_hash"],
                hashlib.sha256(canonical_json_bytes(without_hash)).hexdigest(),
            )
            self.assertEqual(document["authority_id"], canonical_hosted_authority_id(document))
            self.assertEqual(document["format"], HOSTED_NATIVE_RELEASE_AUTHORITY_FORMAT)
            self.assertEqual(
                [row["os"] for row in document["rows"]], ["linux", "linux", "windows", "windows"]
            )
            self.assertEqual(
                [row["python_abi"] for row in document["rows"]],
                ["cp311", "cp312", "cp311", "cp312"],
            )
            self.assertEqual(
                [subject["subject_id"] for subject in document["subjects"]], list(gate.CASES)
            )
            self.assertEqual(document, validate_hosted_native_release_authority_document(document))
            candidate = derive_hosted_native_release_authority(aggregate, document)
            self.assertIs(type(candidate), HostedNativeReleaseCandidate)
            self.assertEqual(candidate.document, document)
            authority_path = root / f"{document['authority_id']}.json"
            authority_path.write_bytes(payload)
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError,
                "^hosted_native_attestation_unavailable:",
            ):
                verify_hosted_native_release_authority_file(authority_path, candidate)
            self.assertIsInstance(aggregate, VerifiedHostedNativeAggregate)
            self.assertIs(
                type(verify_hosted_native_row_file(root / "linux-cp311.json")),
                VerifiedHostedNativeRow,
            )

    def test_raw_json_loaded_reports_and_public_loading_never_mint_verified_authority(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-hosted-boundary-") as temporary:
            root = Path(temporary)
            paths = _write_rows(root)
            reports = [gate.load_release_report(path) for path in paths]
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_row_required:"
            ):
                verify_hosted_native_aggregate(reports)  # type: ignore[arg-type]
            document, aggregate = _verified_authority(root / "sealed")
            authority_path = root / f"{document['authority_id']}.json"
            authority_path.write_bytes(canonical_json_bytes(document))
            loaded = load_hosted_native_release_authority(authority_path)
            self.assertEqual(loaded, document)
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_aggregate_required:"
            ):
                derive_hosted_native_release_authority(loaded, document)  # type: ignore[arg-type]
            with self.assertRaisesRegex(TypeError, "trusted verification"):
                VerifiedHostedNativeReleaseAuthority(
                    object(), document=document, aggregate=aggregate.document
                )

    def test_authority_module_has_no_public_global_minting_tokens_and_rejects_handle_forgery(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-hosted-forgery-") as temporary:
            root = Path(temporary)
            document, aggregate = _verified_authority(root)
            candidate = derive_hosted_native_release_authority(aggregate, document)
            row = aggregate.rows[0]
            self.assertFalse(
                [
                    name
                    for name, value in vars(hosted_authority).items()
                    if name.endswith("_TOKEN") or (name.startswith("_") and type(value) is object)
                ],
                "module globals must not expose minting tokens",
            )
            for handle in (row, aggregate, candidate):
                for operation in (copy.copy, copy.deepcopy, pickle.dumps):
                    with (
                        self.subTest(handle=type(handle).__name__, operation=operation.__name__),
                        self.assertRaisesRegex(TypeError, "cannot be copied|cannot be pickled"),
                    ):
                        operation(handle)
            with self.assertRaisesRegex(TypeError, "cannot be subclassed"):
                type("EvilCandidate", (HostedNativeReleaseCandidate,), {})
            fake = object.__new__(HostedNativeReleaseCandidate)
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_candidate_required:"
            ):
                verify_hosted_native_release_authority_file(root / "missing.json", fake)
            with self.assertRaises(TypeError):
                candidate._document["authority_id"] = "hosted_native_release_" + "0" * 40
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_read_failed:"
            ):
                verify_hosted_native_release_authority_file(root / "missing.json", candidate)

    def test_hostile_structural_handles_are_not_mintable_or_reusable_from_module_globals(
        self,
    ) -> None:
        exposed = {
            name: value
            for name, value in vars(hosted_authority).items()
            if callable(value) and ("mint" in name.lower() or "trust" in name.lower())
        }
        self.assertEqual({}, exposed)
        self.assertNotIn("_make_trust_types", vars(hosted_authority))
        self.assertNotIn("_mint_row", vars(hosted_authority))
        self.assertNotIn("_mint_aggregate", vars(hosted_authority))
        self.assertNotIn("_mint_candidate", vars(hosted_authority))

    def test_hostile_structural_handles_reject_manual_copy_pickle_subclass_slot_and_consumers(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-hosted-hostile-") as temporary:
            root = Path(temporary)
            document, aggregate = _verified_authority(root / "rows")
            candidate = derive_hosted_native_release_authority(aggregate, document)
            authority_path = root / f"{document['authority_id']}.json"
            authority_path.write_bytes(canonical_json_bytes(document))
            row = aggregate.rows[0]

            for cls in (
                VerifiedHostedNativeRow,
                VerifiedHostedNativeAggregate,
                HostedNativeReleaseCandidate,
            ):
                with self.subTest(cls=cls.__name__, operation="constructor"):
                    with self.assertRaisesRegex(TypeError, "trusted verification"):
                        cls()

            for handle in (row, aggregate, candidate):
                for operation in (copy.copy, copy.deepcopy, pickle.dumps):
                    with (
                        self.subTest(handle=type(handle).__name__, operation=operation.__name__),
                        self.assertRaisesRegex(TypeError, "cannot be copied|cannot be pickled"),
                    ):
                        operation(handle)
                with self.subTest(handle=type(handle).__name__, operation="slot assignment"):
                    with self.assertRaisesRegex(TypeError, "immutable"):
                        handle._document = {}
                with self.subTest(handle=type(handle).__name__, operation="nested mutation"):
                    with self.assertRaises((TypeError, AttributeError)):
                        handle._document["format"] = "forged"

            with self.assertRaisesRegex(TypeError, "cannot be subclassed"):
                type("EvilCandidate", (HostedNativeReleaseCandidate,), {})

            fake_row = object.__new__(VerifiedHostedNativeRow)
            fake_aggregate = object.__new__(VerifiedHostedNativeAggregate)
            fake_candidate = object.__new__(HostedNativeReleaseCandidate)
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_row_required:"
            ):
                verify_hosted_native_aggregate([fake_row])
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_aggregate_required:"
            ):
                build_hosted_native_release_authority(fake_aggregate, source=_source_context())
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_aggregate_required:"
            ):
                derive_hosted_native_release_authority(fake_aggregate, document)
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_candidate_required:"
            ):
                verify_hosted_native_release_authority_file(authority_path, fake_candidate)
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_candidate_required:"
            ):
                verify_attested_hosted_native_release_authority_file(fake_candidate, {}, object())

    def test_matrix_closure_rejects_missing_extra_duplicate_crossed_dirty_and_noncanonical_rows(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-hosted-rows-") as temporary:
            root = Path(temporary)
            paths = _write_rows(root)
            rows = [verify_hosted_native_row_file(path) for path in paths]
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_matrix_incomplete:"
            ):
                verify_hosted_native_aggregate(rows[:3])
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_matrix_duplicate:"
            ):
                verify_hosted_native_aggregate([rows[0], *rows])

            crossed = _matrix_report("windows", "3.12")
            crossed["cases"][0]["identities"]["package_id"] += "_crossed"
            crossed_path = root / "crossed.json"
            crossed_path.write_bytes(canonical_json_bytes(crossed))
            crossed_row = verify_hosted_native_row_file(crossed_path)
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_row_invalid:"
            ):
                verify_hosted_native_aggregate([*rows[:3], crossed_row])

            dirty = _matrix_report("linux", "3.11")
            dirty["source"]["tree_state"] = "dirty"
            dirty_path = root / "dirty.json"
            dirty_path.write_bytes(canonical_json_bytes(dirty))
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_row_invalid:"
            ):
                verify_hosted_native_row_file(dirty_path)

            noncanonical_path = root / "noncanonical.json"
            noncanonical_path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_row_invalid:"
            ):
                verify_hosted_native_row_file(noncanonical_path)

    def test_file_verification_rejects_copies_links_symlinks_and_source_context_mismatches(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-hosted-files-") as temporary:
            root = Path(temporary)
            document, aggregate = _verified_authority(root / "source")
            payload = canonical_json_bytes(document)
            authority_path = root / f"{document['authority_id']}.json"
            authority_path.write_bytes(payload)
            candidate = derive_hosted_native_release_authority(aggregate, document)
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_attestation_unavailable:"
            ):
                verify_hosted_native_release_authority_file(authority_path, candidate)

            copied = root / "copied.json"
            copied.write_bytes(payload)
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_attestation_unavailable:"
            ):
                verify_hosted_native_release_authority_file(copied, candidate)

            noncanonical = root / "noncanonical-authority.json"
            noncanonical.write_text(json.dumps(document, indent=2), encoding="utf-8")
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_encoding_invalid:"
            ):
                verify_hosted_native_release_authority_file(noncanonical, candidate)

            mismatched_document = copy.deepcopy(document)
            mismatched_document["source"]["run_id"] = "30125999538"
            mismatched_document["authority_id"] = canonical_hosted_authority_id(mismatched_document)
            without_hash = dict(mismatched_document)
            without_hash.pop("content_hash")
            mismatched_document["content_hash"] = hashlib.sha256(
                canonical_json_bytes(without_hash)
            ).hexdigest()
            mismatched = root / "mismatched-authority.json"
            mismatched.write_bytes(canonical_json_bytes(mismatched_document))
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_hash_mismatch:"
            ):
                verify_hosted_native_release_authority_file(mismatched, candidate)

            if hasattr(os, "symlink"):
                symlink = root / "authority-link.json"
                os.symlink(authority_path, symlink)
                with self.assertRaisesRegex(
                    HostedNativeReleaseAuthorityError, "^hosted_native_read_failed:"
                ):
                    verify_hosted_native_release_authority_file(symlink, candidate)
            if hasattr(os, "link"):
                hardlink = root / "authority-hardlink.json"
                os.link(authority_path, hardlink)
                with self.assertRaisesRegex(
                    HostedNativeReleaseAuthorityError, "^hosted_native_read_failed:"
                ):
                    verify_hosted_native_release_authority_file(authority_path, candidate)

            mismatches = {
                "repository_id": "42",
                "repository": "example/fork",
                "workflow_ref": "DrHepa/rpg-world-forge/.github/workflows/ci.yml@refs/pull/1/merge",
                "workflow_sha": "f" * 40,
                "revision": "f" * 40,
                "input_tree_hash": "f" * 64,
                "ref": "refs/pull/1/merge",
                "event": "pull_request",
                "run_id": "",
                "run_attempt": 0,
            }
            for field, value in mismatches.items():
                bad_source = _source_context()
                bad_source[field] = value
                with (
                    self.subTest(field=field),
                    self.assertRaisesRegex(
                        HostedNativeReleaseAuthorityError, "^hosted_native_source_invalid:"
                    ),
                ):
                    build_hosted_native_release_authority(aggregate, source=bad_source)

            fake_context = _source_context()
            forged = build_hosted_native_release_authority(aggregate, source=fake_context)
            forged["source"]["run_id"] = "99999999999"
            forged["authority_id"] = canonical_hosted_authority_id(forged)
            without_hash = dict(forged)
            without_hash.pop("content_hash")
            forged["content_hash"] = hashlib.sha256(canonical_json_bytes(without_hash)).hexdigest()
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_candidate_required:"
            ):
                verify_hosted_native_release_authority_file(
                    authority_path,
                    forged,  # type: ignore[arg-type]
                )

            bridge_source = _source_context()
            bridge_source["repository"] = "DrHepa/world-forge"
            bridge_source["workflow_ref"] = (
                "DrHepa/world-forge/.github/workflows/ci.yml@refs/heads/main"
            )
            self.assertEqual(
                build_hosted_native_release_authority(aggregate, source=bridge_source)["source"][
                    "repository"
                ],
                "DrHepa/world-forge",
            )

    def test_legacy_runtime_support_authority_stays_native_unavailable_and_not_distributed(
        self,
    ) -> None:
        with self.assertRaisesRegex(Exception, f"^{RUNTIME_SUPPORT_AUTHORITY_NATIVE_UNAVAILABLE}:"):
            attach_native_evidence()
        forbidden_markers = (
            b"world-forge.hosted_native_release_authority",
            b"world-forge.hosted_native_release_attestation_receipt",
        )
        distributed_roots = [
            Path("examples/multigenre-contracts"),
            Path("content"),
        ]
        for root in distributed_roots:
            if root.exists():
                for path in root.rglob("*"):
                    if path.is_file():
                        payload = path.read_bytes()
                        for marker in forbidden_markers:
                            self.assertNotIn(marker, payload)

    def test_schema_catalog_and_generated_studio_types_expose_hosted_authority_contract(
        self,
    ) -> None:
        schema = Path("schemas/hosted-native-release-authority.schema.json").read_text(
            encoding="utf-8"
        )
        catalog = Path("contracts/catalog.json").read_text(encoding="utf-8")
        types = Path("apps/studio/src/generated/world-forge-contracts.d.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "https://world-forge.local/schemas/hosted-native-release-authority.schema.json", schema
        )
        self.assertIn("world-forge.hosted_native_release_authority", schema)
        self.assertIn("hosted-native-release-authority", catalog)
        self.assertIn(
            "worldforge.hosted_native_release_authority:verify_hosted_native_release_authority_file",
            catalog,
        )
        receipt_schema = Path(
            "schemas/hosted-native-release-attestation-receipt.schema.json"
        ).read_text(encoding="utf-8")
        self.assertIn("world-forge.hosted_native_release_attestation_receipt", receipt_schema)
        self.assertIn("hosted-native-release-attestation-receipt", catalog)
        self.assertIn("WorldForgeHostedNativeReleaseAuthorityV1", types)
        self.assertIn("WorldForgeHostedNativeReleaseAttestationReceiptV1", types)
        open_objects: list[str] = []

        def visit(node: object, path: str) -> None:
            if isinstance(node, dict):
                if node.get("type") == "object" and node.get("additionalProperties") is not False:
                    open_objects.append(path)
                for key, value in node.items():
                    visit(value, f"{path}/{key}")
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    visit(value, f"{path}/{index}")

        for contract_name, payload in {
            "authority": schema,
            "attestation_receipt": receipt_schema,
        }.items():
            visit(json.loads(payload), f"#/{contract_name}")
        self.assertEqual([], open_objects)

    def test_subjects_bind_distinct_runtime_support_report_identity_and_hash(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-hosted-subjects-") as temporary:
            root = Path(temporary)
            document, aggregate = _verified_authority(root)
            for subject in document["subjects"]:
                with self.subTest(subject=subject["subject_id"]):
                    self.assertNotEqual(
                        subject["runtime_support_report"]["id"],
                        subject["runtime_support_authority"]["id"],
                    )
                    self.assertNotEqual(
                        subject["runtime_support_report"]["content_hash"],
                        subject["runtime_support_authority"]["content_hash"],
                    )
            mutated = copy.deepcopy(document)
            mutated["subjects"][0]["runtime_support_report"]["content_hash"] = mutated["subjects"][
                0
            ]["runtime_support_authority"]["content_hash"]
            mutated["authority_id"] = canonical_hosted_authority_id(mutated)
            without_hash = dict(mutated)
            without_hash.pop("content_hash")
            mutated["content_hash"] = hashlib.sha256(canonical_json_bytes(without_hash)).hexdigest()
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_subject_mismatch:"
            ):
                derive_hosted_native_release_authority(aggregate, mutated)


if __name__ == "__main__":
    unittest.main()


class HostedNativeAttestationReceiptTests(unittest.TestCase):
    def _authority_file(
        self, root: Path
    ) -> tuple[dict[str, object], HostedNativeReleaseCandidate, Path]:
        document, aggregate = _verified_authority(root / "rows")
        candidate = derive_hosted_native_release_authority(aggregate, document)
        path = root / f"{document['authority_id']}.json"
        path.write_bytes(canonical_json_bytes(document))
        return document, candidate, path

    def test_receipt_contract_is_canonical_closed_and_not_circular(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-attestation-receipt-") as temporary:
            root = Path(temporary)
            _document, candidate, authority_path = self._authority_file(root)
            attestation = {"bundle": "exact-gh-bundle", "statement": {"stable": True}}
            bundle_path = root / "attestation.bundle.jsonl"
            _write_bundle(bundle_path, attestation, jsonl=True)
            verifier_stdout = _official_gh_verifier_stdout(
                subject_name=authority_path.name,
                subject_sha256=hashlib.sha256(authority_path.read_bytes()).hexdigest(),
                attestation=attestation,
            )

            receipt = hosted_authority.build_hosted_native_attestation_receipt_document(
                candidate=candidate,
                authority_path=authority_path,
                bundle_path=bundle_path,
                verifier_result=hosted_authority.parse_hosted_native_verifier_result(
                    verifier_stdout,
                    expected_subject_name=authority_path.name,
                    expected_subject_sha256=hashlib.sha256(authority_path.read_bytes()).hexdigest(),
                ),
                verification_result_sha256=hashlib.sha256(verifier_stdout).hexdigest(),
                verifier={
                    "name": "gh",
                    "version": "2.97.0",
                    "archive_sha256": hosted_authority.PINNED_GH_ARCHIVE_SHA256,
                    "binary_sha256": hosted_authority.PINNED_GH_BINARY_SHA256,
                    "closed_policy_id": hosted_authority.HOSTED_NATIVE_ATTESTATION_POLICY_ID,
                },
                command_policy={
                    "argv": hosted_authority._gh_verify_argv(
                        "gh", authority_path, bundle_path, _source_context()
                    ),
                    "timeout_seconds": 60,
                    "environment": {"GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1"},
                },
                informational_attestation={
                    "id": "att-1",
                    "url": "https://github.com/DrHepa/world-forge/attestations/1",
                },
            )
            payload = hosted_authority.serialize_hosted_native_attestation_receipt(receipt)
            self.assertEqual(payload, canonical_json_bytes(receipt))
            self.assertEqual(
                "world-forge.hosted_native_release_attestation_receipt", receipt["format"]
            )
            self.assertEqual(
                receipt,
                hosted_authority.validate_hosted_native_attestation_receipt_document(receipt),
            )
            self.assertNotIn("attestation", receipt)
            self.assertEqual(
                receipt["bundle"]["payload_sha256"],
                hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
            )
            mutated = copy.deepcopy(receipt)
            mutated["extra"] = True
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_receipt_invalid:"
            ):
                hosted_authority.validate_hosted_native_attestation_receipt_document(mutated)

            for forbidden_name in ("PATH", "GH_TOKEN"):
                with self.subTest(forbidden_environment=forbidden_name):
                    mutated = copy.deepcopy(receipt)
                    mutated["command_policy"]["environment"][forbidden_name] = "unsafe"
                    with self.assertRaisesRegex(
                        HostedNativeReleaseAuthorityError, "^hosted_native_receipt_invalid:"
                    ):
                        hosted_authority.validate_hosted_native_attestation_receipt_document(
                            mutated
                        )

            good_command_policy = {
                "argv": hosted_authority._gh_verify_argv(
                    "gh", authority_path, bundle_path, _source_context()
                ),
                "timeout_seconds": 60,
                "environment": {"GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1"},
            }
            for forbidden_name in ("PATH", "GH_TOKEN"):
                with self.subTest(builder_forbidden_environment=forbidden_name):
                    command_policy = copy.deepcopy(good_command_policy)
                    command_policy["environment"][forbidden_name] = "unsafe"
                    with self.assertRaisesRegex(
                        HostedNativeReleaseAuthorityError, "^hosted_native_receipt_invalid:"
                    ):
                        hosted_authority.build_hosted_native_attestation_receipt_document(
                            candidate=candidate,
                            authority_path=authority_path,
                            bundle_path=bundle_path,
                            verifier_result=hosted_authority.parse_hosted_native_verifier_result(
                                verifier_stdout,
                                expected_subject_name=authority_path.name,
                                expected_subject_sha256=hashlib.sha256(
                                    authority_path.read_bytes()
                                ).hexdigest(),
                            ),
                            verification_result_sha256=hashlib.sha256(verifier_stdout).hexdigest(),
                            verifier={
                                "name": "gh",
                                "version": "2.97.0",
                                "archive_sha256": hosted_authority.PINNED_GH_ARCHIVE_SHA256,
                                "binary_sha256": hosted_authority.PINNED_GH_BINARY_SHA256,
                                "closed_policy_id": (
                                    hosted_authority.HOSTED_NATIVE_ATTESTATION_POLICY_ID
                                ),
                            },
                            command_policy=command_policy,
                            informational_attestation={
                                "id": "att-1",
                                "url": "https://github.com/DrHepa/world-forge/attestations/1",
                            },
                        )

    def test_bounded_verifier_environment_is_closed_and_credential_free(self) -> None:
        ambient_environment = {
            "GH_TOKEN": "secret-gh",
            "GITHUB_TOKEN": "secret-github",
            "PATH": "/tmp/unsafe",
            "HOME": "/tmp/home",
            "WORLD_FORGE_SECRET": "secret-custom",
            "GH_NO_UPDATE_NOTIFIER": "caller-value",
            "NO_COLOR": "caller-value",
        }
        captured_envs: list[dict[str, str]] = []

        def fake_runner(*_args, **kwargs):
            env = kwargs["env"]
            captured_envs.append(dict(env))
            env["GH_TOKEN"] = "runner-mutation"
            return mock.Mock(returncode=0, stdout=b"{}", stderr=b"")

        with mock.patch.dict(os.environ, ambient_environment, clear=True):
            hosted_authority._run_bounded(
                ["/opt/world-forge/bin/gh", "attestation", "verify"], runner=fake_runner
            )
            hosted_authority._run_bounded(
                ["/opt/world-forge/bin/gh", "--version"], runner=fake_runner
            )

        expected = hosted_authority.HOSTED_NATIVE_VERIFIER_ENVIRONMENT
        self.assertEqual(captured_envs, [expected, expected])
        self.assertEqual(expected, {"GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1"})

    def test_gh_bundle_verification_records_the_environment_it_actually_executes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-gh-env-") as temporary:
            root = Path(temporary)
            _document, _candidate, authority_path = self._authority_file(root)
            bundle_path = root / "bundle.jsonl"
            _write_bundle(bundle_path, {"bundle": "exact"}, jsonl=True)
            archive_path = root / "gh.tar.gz"
            binary_path = root / "gh"
            archive_path.write_bytes(b"archive")
            binary_path.write_bytes(b"binary")
            expected_stdout = _official_gh_verifier_stdout(
                subject_name=authority_path.name,
                subject_sha256=hashlib.sha256(authority_path.read_bytes()).hexdigest(),
                attestation={"bundle": "exact"},
            )
            captured: dict[str, object] = {}

            def fake_run(argv, **kwargs):
                captured["argv"] = list(argv)
                captured["env"] = dict(kwargs["env"])
                kwargs["env"].clear()
                return mock.Mock(returncode=0, stdout=expected_stdout, stderr=b"")

            ambient_environment = {
                "GH_TOKEN": "secret-gh",
                "GITHUB_TOKEN": "secret-github",
                "PATH": "/tmp/unsafe",
                "HOME": "/tmp/home",
                "WORLD_FORGE_SECRET": "secret-custom",
            }
            with (
                mock.patch.dict(os.environ, ambient_environment, clear=True),
                mock.patch.object(
                    hosted_authority,
                    "verify_pinned_github_cli",
                    return_value={
                        "name": "gh",
                        "version": "2.97.0",
                        "archive_sha256": hosted_authority.PINNED_GH_ARCHIVE_SHA256,
                        "binary_sha256": hosted_authority.PINNED_GH_BINARY_SHA256,
                        "closed_policy_id": hosted_authority.HOSTED_NATIVE_ATTESTATION_POLICY_ID,
                    },
                ),
                mock.patch.object(hosted_authority.subprocess, "run", side_effect=fake_run),
            ):
                _stdout, _verifier, command_policy = hosted_authority._run_gh_verify(
                    authority_path, bundle_path, archive_path, binary_path, _source_context()
                )

            self.assertEqual(captured["env"], hosted_authority.HOSTED_NATIVE_VERIFIER_ENVIRONMENT)
            self.assertEqual(command_policy["environment"], captured["env"])
            self.assertEqual(
                command_policy["environment"], {"GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1"}
            )
            self.assertIn("--bundle", captured["argv"])
            self.assertNotIn("GH_TOKEN", command_policy["environment"])
            self.assertNotIn("GITHUB_TOKEN", command_policy["environment"])
            self.assertEqual(str(binary_path), captured["argv"][0])

    def test_pinned_verifier_rejects_bad_files_and_never_uses_shell(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-gh-pin-") as temporary:
            root = Path(temporary)
            archive = root / "gh.tar.gz"
            binary = root / "gh"
            archive.write_bytes(b"archive")
            binary.write_bytes(b"#!/bin/sh\necho fake\n")
            binary.chmod(0o755)
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_verifier_archive_mismatch:"
            ):
                hosted_authority._verify_pinned_github_cli_for_tests(
                    archive,
                    binary,
                    runner=lambda *a, **k: None,
                    expected_archive_sha256="0" * 64,
                    expected_binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                )
            if hasattr(os, "symlink"):
                link = root / "gh-link"
                os.symlink(binary, link)
                with self.assertRaisesRegex(
                    HostedNativeReleaseAuthorityError, "^hosted_native_verifier_file_invalid:"
                ):
                    hosted_authority.verify_pinned_github_cli(archive, link)
            if hasattr(os, "link"):
                hard = root / "gh-hard"
                os.link(binary, hard)
                with self.assertRaisesRegex(
                    HostedNativeReleaseAuthorityError, "^hosted_native_verifier_file_invalid:"
                ):
                    hosted_authority.verify_pinned_github_cli(archive, binary)
                hard.unlink()

            calls: list[dict[str, object]] = []

            def runner(argv, **kwargs):
                calls.append({"argv": argv, **kwargs})

                class Result:
                    returncode = 0
                    stdout = b"gh version 2.97.0 (2026-08-01)\n"
                    stderr = b""

                return Result()

            info = hosted_authority._verify_pinned_github_cli_for_tests(
                archive,
                binary,
                runner=runner,
                expected_archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                expected_binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
            )
            self.assertEqual(info["version"], "2.97.0")
            self.assertEqual(calls[0]["argv"], [str(binary), "--version"])
            self.assertIs(calls[0].get("shell"), False)

    def test_verifier_json_parser_accepts_official_array_and_hashes_unstable_nested_evidence(
        self,
    ) -> None:
        good = _official_gh_verifier_stdout(
            subject_name="authority.json",
            subject_sha256="a" * 64,
            timestamp_payload={"renamedByGh": {"still": "hashed"}},
            certificate_payload={"whateverGhCallsThis": ["bounded", "hashed"]},
        )
        parsed = hosted_authority.parse_hosted_native_verifier_result(
            good,
            expected_subject_name="authority.json",
            expected_subject_sha256="a" * 64,
        )
        self.assertEqual(parsed["verified_subject"], {"name": "authority.json", "sha256": "a" * 64})
        self.assertEqual(
            parsed["verified_timestamps"],
            {"count": 1, "sha256": parsed["verified_timestamps"]["sha256"]},
        )
        self.assertEqual(
            parsed["signature_certificate"],
            {"count": 1, "sha256": parsed["signature_certificate"]["sha256"]},
        )

    def test_verifier_json_parser_rejects_malformed_and_wrong_subject(
        self,
    ) -> None:
        good = _official_gh_verifier_stdout(subject_name="authority.json", subject_sha256="a" * 64)
        decoded = json.loads(good.decode("utf-8"))
        empty_timestamps = copy.deepcopy(decoded)
        empty_timestamps[0]["verificationResult"]["verifiedTimestamps"] = []
        wrong_subject = copy.deepcopy(decoded)
        wrong_subject[0]["verificationResult"]["statement"]["subject"][0]["digest"]["sha256"] = (
            "b" * 64
        )
        negatives = [
            b'{"attestations":[{"x":1}]}',
            b'[{"attestation":{},"verificationResult":{"x":NaN}}]',
            b'[{"attestation":{},"verificationResult":{},"extra":true}]',
            canonical_json_bytes(wrong_subject),
            canonical_json_bytes(empty_timestamps),
        ]
        for payload in negatives:
            with self.subTest(payload=payload[:60]):
                with self.assertRaisesRegex(
                    HostedNativeReleaseAuthorityError, "^hosted_native_verifier_result_invalid:"
                ):
                    hosted_authority.parse_hosted_native_verifier_result(
                        payload,
                        expected_subject_name="authority.json",
                        expected_subject_sha256="a" * 64,
                    )

    def test_bundle_attestation_must_match_verifier_output_exactly(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-bundle-bind-") as temporary:
            root = Path(temporary)
            _document, candidate, authority_path = self._authority_file(root)
            good_attestation = {"statement": {"stable": True}, "bundle": "exact"}
            bundle_path = root / "bundle.json"
            _write_bundle(bundle_path, good_attestation)
            parsed = hosted_authority.parse_hosted_native_verifier_result(
                _official_gh_verifier_stdout(
                    subject_name=authority_path.name,
                    subject_sha256=hashlib.sha256(authority_path.read_bytes()).hexdigest(),
                    attestation={"statement": {"stable": False}, "bundle": "exact"},
                ),
                expected_subject_name=authority_path.name,
                expected_subject_sha256=hashlib.sha256(authority_path.read_bytes()).hexdigest(),
            )
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_bundle_mismatch:"
            ):
                hosted_authority.build_hosted_native_attestation_receipt_document(
                    candidate=candidate,
                    authority_path=authority_path,
                    bundle_path=bundle_path,
                    verifier_result=parsed,
                    verification_result_sha256="a" * 64,
                    verifier={
                        "name": "gh",
                        "version": "2.97.0",
                        "archive_sha256": hosted_authority.PINNED_GH_ARCHIVE_SHA256,
                        "binary_sha256": hosted_authority.PINNED_GH_BINARY_SHA256,
                        "closed_policy_id": hosted_authority.HOSTED_NATIVE_ATTESTATION_POLICY_ID,
                    },
                    command_policy={
                        "argv": ["gh"],
                        "timeout_seconds": 60,
                        "environment": {"GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1"},
                    },
                    informational_attestation={"id": "untrusted", "url": "https://github.com/x/y"},
                )

    def test_reverify_requires_rerunning_pinned_verifier_and_raw_receipt_cannot_mint(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-reverify-") as temporary:
            root = Path(temporary)
            _document, candidate, authority_path = self._authority_file(root)
            receipt = {"format": hosted_authority.HOSTED_NATIVE_ATTESTATION_RECEIPT_FORMAT}
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_receipt_invalid:"
            ):
                verify_attested_hosted_native_release_authority_file(candidate, receipt, object())
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_attestation_unavailable:"
            ):
                verify_hosted_native_release_authority_file(authority_path, candidate)

    def test_secure_write_and_reverify_rerun_gh_with_archive_and_binary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-secure-reverify-") as temporary:
            root = Path(temporary)
            _document, candidate, authority_path = self._authority_file(root)
            attestation = {"bundle": "exact"}
            bundle_path = root / "bundle.json"
            _write_bundle(bundle_path, attestation)
            receipt_path = root / "receipt.json"
            archive_path = root / "gh.tar.gz"
            binary_path = root / "gh"
            archive_path.write_bytes(b"archive")
            binary_path.write_bytes(b"binary")
            stdout = _official_gh_verifier_stdout(
                subject_name=authority_path.name,
                subject_sha256=hashlib.sha256(authority_path.read_bytes()).hexdigest(),
                attestation=attestation,
            )
            verifier = {
                "name": "gh",
                "version": "2.97.0",
                "archive_sha256": hosted_authority.PINNED_GH_ARCHIVE_SHA256,
                "binary_sha256": hosted_authority.PINNED_GH_BINARY_SHA256,
                "closed_policy_id": hosted_authority.HOSTED_NATIVE_ATTESTATION_POLICY_ID,
            }
            command = {
                "argv": hosted_authority._gh_verify_argv(
                    binary_path, authority_path, bundle_path, _source_context()
                ),
                "timeout_seconds": 60,
                "environment": {"GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1"},
            }
            calls: list[tuple[object, ...]] = []

            writes: list[dict[str, object]] = []

            def fake_run(*args):
                calls.append(args)
                return stdout, verifier, command

            def fake_write(path, payload, **kwargs):
                writes.append({"path": path, "payload": payload, **kwargs})
                Path(path).write_bytes(payload)

            with (
                mock.patch.object(hosted_authority, "_run_gh_verify", side_effect=fake_run),
                mock.patch.object(hosted_authority, "write_bytes_atomic", side_effect=fake_write),
            ):
                receipt = hosted_authority.write_hosted_native_attestation_receipt(
                    candidate,
                    authority_path=authority_path,
                    bundle_path=bundle_path,
                    gh_archive_path=archive_path,
                    gh_path=binary_path,
                    receipt_path=receipt_path,
                    attestation_id="untrusted-action-id",
                    attestation_url="https://github.com/evil/fork/attestations/1",
                )
                authority = verify_attested_hosted_native_release_authority_file(
                    candidate, receipt_path, archive_path, binary_path
                )
            self.assertEqual(receipt, json.loads(receipt_path.read_text(encoding="utf-8")))
            self.assertEqual(writes[0]["path"], receipt_path)
            self.assertIs(writes[0]["durable_parent"], True)
            self.assertEqual(authority.document, candidate.document)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0][2], archive_path)
            self.assertEqual(calls[0][3], binary_path)
            self.assertEqual(calls[1][2], archive_path)
            self.assertEqual(calls[1][3], binary_path)
            with self.assertRaisesRegex(
                HostedNativeReleaseAuthorityError, "^hosted_native_verifier_file_invalid:"
            ):
                verify_attested_hosted_native_release_authority_file(
                    candidate, receipt_path, binary_path
                )

    def test_action_metadata_is_informational_and_cannot_upgrade_trust(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-info-attestation-") as temporary:
            root = Path(temporary)
            _document, candidate, authority_path = self._authority_file(root)
            attestation = {"bundle": "exact"}
            bundle_path = root / "bundle.jsonl"
            _write_bundle(bundle_path, attestation, jsonl=True)
            parsed = hosted_authority.parse_hosted_native_verifier_result(
                _official_gh_verifier_stdout(
                    subject_name=authority_path.name,
                    subject_sha256=hashlib.sha256(authority_path.read_bytes()).hexdigest(),
                    attestation=attestation,
                ),
                expected_subject_name=authority_path.name,
                expected_subject_sha256=hashlib.sha256(authority_path.read_bytes()).hexdigest(),
            )
            receipt = hosted_authority.build_hosted_native_attestation_receipt_document(
                candidate=candidate,
                authority_path=authority_path,
                bundle_path=bundle_path,
                verifier_result=parsed,
                verification_result_sha256="b" * 64,
                verifier={
                    "name": "gh",
                    "version": "2.97.0",
                    "archive_sha256": hosted_authority.PINNED_GH_ARCHIVE_SHA256,
                    "binary_sha256": hosted_authority.PINNED_GH_BINARY_SHA256,
                    "closed_policy_id": hosted_authority.HOSTED_NATIVE_ATTESTATION_POLICY_ID,
                },
                command_policy={
                    "argv": ["gh"],
                    "timeout_seconds": 60,
                    "environment": {"GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1"},
                },
                informational_attestation={
                    "id": "totally-arbitrary",
                    "url": "https://github.com/evil/fork",
                },
            )
            self.assertEqual(
                receipt["informational_attestation"]["trust_role"], "informational-only"
            )
            self.assertNotIn(
                "totally-arbitrary", canonical_json_bytes(receipt["verifier"]).decode("utf-8")
            )
