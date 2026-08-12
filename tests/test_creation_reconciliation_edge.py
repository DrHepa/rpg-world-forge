from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from worldforge.creation_contracts import canonical_creation_hash, load_creation_project
from worldforge.creation_workflow import CreationWorkflowError, reconcile_creation_workflow
from worldforge.phase_report_v3 import (
    build_phase_output_evidence_v2,
    build_phase_report_v3,
    document_identity,
)

ROOT = Path(__file__).resolve().parents[1]
SYSTEMIC_ROOT = ROOT / "examples/multigenre-contracts/systemic-simulation"


def _write_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _workflow_with_dependency(
    destination: Path,
    mutate_dependency: object,
) -> tuple[Path, str, bytes]:
    root = destination / "project"
    shutil.copytree(SYSTEMIC_ROOT, root)
    project = load_creation_project(root / "project.json")
    reviewer = {"id": "lead_reviewer", "role": "validation_analyst"}
    output = build_phase_output_evidence_v2(
        evidence_id="p00_output",
        phase="p00_brief",
        role="project_brief",
        subject=document_identity(project.project),
        reviewer_id=reviewer["id"],
        reviewer_role=reviewer["role"],
        source_project=project,
    )
    report = build_phase_report_v3(
        project,
        phase="p00_brief",
        status="ready",
        rationale_code="phase_ready",
        rationale_message="The project brief was reviewed.",
        evidence=(
            {
                "evidence_id": "reviewed_project",
                "claim": "The exact project was reviewed.",
                "subject": document_identity(project.project),
            },
        ),
        output_evidence=output,
        reviewer_id=reviewer["id"],
        reviewer_role=reviewer["role"],
        invalidation_dependencies=None,
    )
    malformed = copy.deepcopy(report)
    mutate_dependency(malformed["invalidation_dependencies"][0])
    malformed["content_hash"] = canonical_creation_hash(malformed)
    report_path = (
        root / ".worldforge" / "phase_reports" / f"p00_brief-{malformed['content_hash']}.json"
    )
    _write_json(report_path, malformed)

    status_path = root / ".worldforge/status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status.update(
        {
            "completed_phases": ["p00_brief"],
            "current_phase": "p01_genre_style",
            "reports": [
                {
                    "phase": "p00_brief",
                    "status": "ready",
                    "path": report_path.relative_to(root).as_posix(),
                    "content_hash": malformed["content_hash"],
                    "invalidation_dependencies": copy.deepcopy(
                        malformed["invalidation_dependencies"]
                    ),
                }
            ],
            "revision": 1,
            "content_hash": "",
        }
    )
    status["content_hash"] = canonical_creation_hash(status)
    _write_json(status_path, status)
    return root, status["content_hash"], status_path.read_bytes()


class CreationReconciliationEdgeTests(unittest.TestCase):
    def test_expected_hash_is_a_required_keyword_only_cas_precondition(self) -> None:
        with self.assertRaises(TypeError):
            reconcile_creation_workflow(Path("unused"))

    def test_non_string_expected_hash_is_rejected_before_filesystem_access(self) -> None:
        with self.assertRaises(CreationWorkflowError) as raised:
            reconcile_creation_workflow(  # type: ignore[arg-type]
                Path("unused"),
                expected_status_hash=1,
            )
        self.assertEqual(
            "creation_workflow_expected_status_hash_invalid",
            raised.exception.reason_code,
        )

    def test_non_canonical_expected_hashes_are_rejected_before_filesystem_access(self) -> None:
        for expected_hash in ("a" * 63, "A" * 64, "g" * 64, ""):
            with self.subTest(expected_hash=expected_hash):
                with self.assertRaises(CreationWorkflowError) as raised:
                    reconcile_creation_workflow(
                        Path("unused"),
                        expected_status_hash=expected_hash,
                    )
                self.assertEqual(
                    "creation_workflow_expected_status_hash_invalid",
                    raised.exception.reason_code,
                )

    def test_malformed_report_dependency_identities_fail_closed_without_mutation(self) -> None:
        mutations = {
            "missing_field": lambda identity: identity.pop("id"),
            "unknown_field": lambda identity: identity.__setitem__("extra", "forbidden"),
            "empty_format": lambda identity: identity.__setitem__("format", ""),
            "unsupported_format": lambda identity: identity.__setitem__(
                "format", "world-forge.unknown"
            ),
            "null_version": lambda identity: identity.__setitem__("format_version", None),
            "boolean_version": lambda identity: identity.__setitem__("format_version", True),
            "string_version": lambda identity: identity.__setitem__("format_version", "1"),
            "future_version": lambda identity: identity.__setitem__("format_version", 2),
            "empty_id": lambda identity: identity.__setitem__("id", ""),
            "whitespace_id": lambda identity: identity.__setitem__("id", " "),
            "numeric_id": lambda identity: identity.__setitem__("id", 1),
            "uppercase_hash": lambda identity: identity.__setitem__("content_hash", "A" * 64),
            "short_hash": lambda identity: identity.__setitem__("content_hash", "a" * 63),
            "numeric_hash": lambda identity: identity.__setitem__("content_hash", 1),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root, expected_hash, before = _workflow_with_dependency(Path(temp), mutation)
                with self.assertRaises(CreationWorkflowError) as raised:
                    reconcile_creation_workflow(
                        root,
                        expected_status_hash=expected_hash,
                    )
                self.assertEqual(
                    "creation_workflow_dependency_identity_invalid",
                    raised.exception.reason_code,
                )
                self.assertEqual(before, (root / ".worldforge/status.json").read_bytes())

    def test_stale_or_missing_current_status_fails_closed(self) -> None:
        for mutation, expected_reason in (
            ("stale", "creation_workflow_expected_status_hash_mismatch"),
            ("missing", "creation_workflow_invalid"),
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "project"
                shutil.copytree(SYSTEMIC_ROOT, root)
                status_path = root / ".worldforge/status.json"
                status = json.loads(status_path.read_text(encoding="utf-8"))
                before = status_path.read_bytes()
                expected_hash = "0" * 64
                if mutation == "missing":
                    expected_hash = status["content_hash"]
                    status_path.unlink()
                with self.assertRaises(CreationWorkflowError) as raised:
                    reconcile_creation_workflow(
                        root,
                        expected_status_hash=expected_hash,
                    )
                self.assertEqual(expected_reason, raised.exception.reason_code)
                if mutation == "missing":
                    self.assertFalse(status_path.exists())
                else:
                    self.assertEqual(before, status_path.read_bytes())

    def test_float_dependency_version_is_rejected_before_hash_coercion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root, expected_hash, _ = _workflow_with_dependency(Path(temp), lambda identity: None)
            status_path = root / ".worldforge/status.json"
            status_text = status_path.read_text(encoding="utf-8")
            dependency_marker = '"invalidation_dependencies": ['
            prefix, suffix = status_text.split(dependency_marker, 1)
            suffix = suffix.replace('"format_version": 1', '"format_version": 1.0', 1)
            status_path.write_text(prefix + dependency_marker + suffix, encoding="utf-8")
            before = status_path.read_bytes()

            with self.assertRaises(CreationWorkflowError) as raised:
                reconcile_creation_workflow(
                    root,
                    expected_status_hash=expected_hash,
                )
            self.assertEqual(
                "creation_workflow_dependency_identity_invalid",
                raised.exception.reason_code,
            )
            self.assertEqual(before, status_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
