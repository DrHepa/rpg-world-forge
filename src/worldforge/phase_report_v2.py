from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from worldforge.creation_contracts import (
    ACTIVITY_MODULE_FORMAT,
    CREATION_PROFILE_FORMAT,
    CREATION_PROJECT_FORMAT,
    CREATION_SOURCE_MANIFEST_FORMAT,
    LOGIC_MODULE_FORMAT,
    NARRATIVE_MODULE_FORMAT,
    SYSTEM_MODULE_FORMAT,
    WORLD_MODULE_FORMAT,
    CreationContractError,
    ExtensionValidator,
    LoadedCreationProject,
    _exact_keys,
    _extensions,
    _identifier,
    _non_empty_string,
    _object,
    _sha256,
    canonical_creation_hash,
    load_creation_project,
    read_creation_object,
    validate_creation_documents,
)

PHASE_REPORT_V2_FORMAT = "world-forge.phase_report"
PHASE_REPORT_V2_VERSION = 2
PHASE_OUTPUT_EVIDENCE_FORMAT = "world-forge.phase_output_evidence"
PHASE_OUTPUT_EVIDENCE_VERSION = 1
PHASE_REPORT_V2_PHASE_IDS = (
    "p00_brief",
    "p01_genre_style",
    "p02_world_laws",
    "p03_geography",
    "p04_timeline",
    "p05_societies",
    "p06_characters",
    "p07_systems",
    "p08_world_arcs",
    "p09_narrative_content",
    "p10_canon_lock",
)
PHASE_IDS = PHASE_REPORT_V2_PHASE_IDS

_REPORT_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "project",
        "profile",
        "source_manifest",
        "phase",
        "status",
        "rationale",
        "evidence",
        "output_evidence",
        "reviewer",
        "invalidation_dependencies",
        "extensions",
        "content_hash",
    }
)
_IDENTITY_FIELDS = frozenset({"format", "format_version", "id", "content_hash"})
_EVIDENCE_FIELDS = frozenset({"evidence_id", "claim", "subject"})
_OUTPUT_EVIDENCE_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "id",
        "phase",
        "role",
        "subject",
        "reviewer",
        "content_hash",
    }
)
_RATIONALE_FIELDS = frozenset({"code", "message"})
_REVIEWER_FIELDS = frozenset({"id", "role"})
_CREATION_FORMATS = frozenset(
    {
        CREATION_PROJECT_FORMAT,
        CREATION_PROFILE_FORMAT,
        CREATION_SOURCE_MANIFEST_FORMAT,
        WORLD_MODULE_FORMAT,
        ACTIVITY_MODULE_FORMAT,
        NARRATIVE_MODULE_FORMAT,
        SYSTEM_MODULE_FORMAT,
        LOGIC_MODULE_FORMAT,
    }
)
_NOT_APPLICABLE_CODES = {
    "p03_geography": "world_absent",
    "p04_timeline": "chronology_absent",
    "p05_societies": "group_structures_absent",
    "p06_characters": "actors_absent",
}
_LOGIC_EVIDENCE_PHASES = frozenset({"p02_world_laws", "p07_systems", "p09_narrative_content"})
_PHASE_ROLE_SUBJECT_FORMATS = {
    "p00_brief": {
        "brief_review": frozenset({CREATION_PROJECT_FORMAT}),
    },
    "p01_genre_style": {
        "experience_classification": frozenset({CREATION_PROFILE_FORMAT}),
    },
    "p02_world_laws": {
        "interaction_ontology": frozenset(
            {
                CREATION_PROFILE_FORMAT,
                ACTIVITY_MODULE_FORMAT,
                SYSTEM_MODULE_FORMAT,
                LOGIC_MODULE_FORMAT,
            }
        ),
    },
    "p03_geography": {
        "world_topology": frozenset({CREATION_PROFILE_FORMAT, WORLD_MODULE_FORMAT}),
    },
    "p04_timeline": {
        "chronology": frozenset({WORLD_MODULE_FORMAT, SYSTEM_MODULE_FORMAT}),
    },
    "p05_societies": {
        "group_structures": frozenset({WORLD_MODULE_FORMAT}),
    },
    "p06_characters": {
        "actors": frozenset(
            {
                WORLD_MODULE_FORMAT,
                ACTIVITY_MODULE_FORMAT,
                NARRATIVE_MODULE_FORMAT,
            }
        ),
    },
    "p07_systems": {
        "systems_design": frozenset(
            {
                CREATION_PROFILE_FORMAT,
                ACTIVITY_MODULE_FORMAT,
                SYSTEM_MODULE_FORMAT,
                LOGIC_MODULE_FORMAT,
            }
        ),
    },
    "p08_world_arcs": {
        "narrative_architecture": frozenset({CREATION_PROFILE_FORMAT, NARRATIVE_MODULE_FORMAT}),
    },
    "p09_narrative_content": {
        "typed_content": frozenset(
            {
                CREATION_SOURCE_MANIFEST_FORMAT,
                WORLD_MODULE_FORMAT,
                ACTIVITY_MODULE_FORMAT,
                NARRATIVE_MODULE_FORMAT,
                SYSTEM_MODULE_FORMAT,
                LOGIC_MODULE_FORMAT,
            }
        ),
    },
    "p10_canon_lock": {
        "content_lock": frozenset({CREATION_SOURCE_MANIFEST_FORMAT}),
    },
}


class PhaseReportV2Error(ValueError):
    """Raised when a generic phase-report v2 fails closed validation."""


def _validated_source_project(
    project: LoadedCreationProject,
    *,
    registered_extensions: Mapping[str, ExtensionValidator] | None = None,
) -> LoadedCreationProject:
    if not isinstance(project, LoadedCreationProject):
        raise PhaseReportV2Error("phase report requires a loaded creation project")
    try:
        return validate_creation_documents(
            project.project,
            project.profile,
            project.manifest,
            project.world_modules,
            project.activity_modules,
            project.narrative_modules,
            project.system_modules,
            project.logic_modules,
            registered_extensions=registered_extensions,
        )
    except CreationContractError as exc:
        raise PhaseReportV2Error(f"creation project is invalid: {exc}") from exc


def _document_identity(document: Mapping[str, Any]) -> dict[str, Any]:
    identity_field = {
        CREATION_PROJECT_FORMAT: "project_id",
        CREATION_PROFILE_FORMAT: "profile_id",
        CREATION_SOURCE_MANIFEST_FORMAT: "project_id",
        WORLD_MODULE_FORMAT: "module_id",
        ACTIVITY_MODULE_FORMAT: "module_id",
        NARRATIVE_MODULE_FORMAT: "module_id",
        SYSTEM_MODULE_FORMAT: "module_id",
        LOGIC_MODULE_FORMAT: "module_id",
    }.get(document.get("format"))
    if identity_field is None:
        raise PhaseReportV2Error("phase report evidence uses an unsupported subject format")
    return {
        "format": document["format"],
        "format_version": document["format_version"],
        "id": document[identity_field],
        "content_hash": document["content_hash"],
    }


def _identity_key(identity: Mapping[str, Any]) -> tuple[str, int, str, str]:
    return (
        str(identity["format"]),
        int(identity["format_version"]),
        str(identity["id"]),
        str(identity["content_hash"]),
    )


def _identity_sort_key(identity: Mapping[str, Any]) -> tuple[bytes, int, bytes, bytes]:
    format_name, version, subject_id, content_hash = _identity_key(identity)
    return (
        format_name.encode("utf-8"),
        version,
        subject_id.encode("utf-8"),
        content_hash.encode("ascii"),
    )


def _validate_identity(value: object, context: str) -> dict[str, Any]:
    identity = _object(value, context)
    _exact_keys(identity, _IDENTITY_FIELDS, context)
    format_name = _non_empty_string(identity.get("format"), f"{context}.format")
    if format_name not in _CREATION_FORMATS:
        raise PhaseReportV2Error(f"{context}.format is not a creation contract")
    if identity.get("format_version") != 1 or isinstance(identity.get("format_version"), bool):
        raise PhaseReportV2Error(f"{context}.format_version must be 1")
    _identifier(identity.get("id"), f"{context}.id")
    _sha256(identity.get("content_hash"), f"{context}.content_hash")
    return identity


def _subject_registry(
    project: LoadedCreationProject,
) -> dict[tuple[str, int, str, str], dict[str, Any]]:
    documents: Sequence[Mapping[str, Any]] = (
        project.project,
        project.profile,
        project.manifest,
        *project.world_modules,
        *project.activity_modules,
        *project.narrative_modules,
        *project.system_modules,
        *project.logic_modules,
    )
    return {_identity_key(_document_identity(document)): dict(document) for document in documents}


def _validate_sorted_identities(
    value: object,
    *,
    context: str,
    registry: Mapping[tuple[str, int, str, str], object],
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise PhaseReportV2Error(f"{context} must be a non-empty array")
    identities: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(value):
        identity = _validate_identity(raw, f"{context}/{index}")
        collision_key = (identity["format"].casefold(), identity["id"].casefold())
        if collision_key in seen:
            raise PhaseReportV2Error(f"{context} contains an NFC/casefold collision")
        seen.add(collision_key)
        if _identity_key(identity) not in registry:
            raise PhaseReportV2Error(
                f"{context}/{index} references an unknown or mismatched subject"
            )
        identities.append(identity)
    if identities != sorted(identities, key=_identity_sort_key):
        raise PhaseReportV2Error(f"{context} must use canonical sorted order")
    return identities


def _not_applicable_is_proven(phase: str, project: LoadedCreationProject) -> bool:
    profile = project.profile
    world_absent = profile["world"]["presence"] == "none" and not project.world_modules
    if phase in {"p03_geography", "p05_societies"}:
        return world_absent
    if phase == "p04_timeline":
        has_temporal_system = any(
            system["system_type"] in {"schedule", "season"} for system in project.systems
        )
        return world_absent and not has_temporal_system
    if phase == "p06_characters":
        no_participants = all(not activity["participant_ids"] for activity in project.activities)
        return (
            world_absent and profile["narrative"]["protagonist_model"] == "none" and no_participants
        )
    return False


def _validate_reviewer(value: object, context: str) -> dict[str, Any]:
    reviewer = _object(value, context)
    _exact_keys(reviewer, _REVIEWER_FIELDS, context)
    _identifier(reviewer.get("id"), f"{context}.id")
    _identifier(reviewer.get("role"), f"{context}.role")
    return reviewer


def _validate_output_evidence(
    value: object,
    *,
    phase: str,
    status: str,
    reviewer: Mapping[str, Any],
    registry: Mapping[tuple[str, int, str, str], object],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    context = "phase report v2.output_evidence"
    if status == "not_applicable":
        if value is not None:
            raise PhaseReportV2Error("not_applicable phase reports require null output_evidence")
        return None, None
    if value is None:
        raise PhaseReportV2Error("ready phase reports require caller-supplied output evidence")

    item = _object(value, context)
    _exact_keys(item, _OUTPUT_EVIDENCE_FIELDS, context)
    if item.get("format") != PHASE_OUTPUT_EVIDENCE_FORMAT:
        raise PhaseReportV2Error(f"{context}.format is unsupported")
    if item.get("format_version") != PHASE_OUTPUT_EVIDENCE_VERSION or isinstance(
        item.get("format_version"), bool
    ):
        raise PhaseReportV2Error(f"{context}.format_version is unsupported")
    _identifier(item.get("id"), f"{context}.id")
    evidence_phase = item.get("phase")
    if not isinstance(evidence_phase, str) or evidence_phase not in PHASE_REPORT_V2_PHASE_IDS:
        raise PhaseReportV2Error(f"{context}.phase is unsupported")
    if evidence_phase != phase:
        raise PhaseReportV2Error(f"{context}.phase must match report phase")
    role_formats = _PHASE_ROLE_SUBJECT_FORMATS[phase]
    role = item.get("role")
    if not isinstance(role, str) or role not in role_formats:
        raise PhaseReportV2Error(f"{context}.role is unsupported for {phase}")
    subject = _validate_identity(item.get("subject"), f"{context}.subject")
    if subject["format"] not in role_formats[role]:
        raise PhaseReportV2Error(f"{context}.subject format is unsupported for role {role}")
    if _identity_key(subject) not in registry:
        raise PhaseReportV2Error(f"{context}.subject references an unknown or mismatched subject")
    evidence_reviewer = _validate_reviewer(item.get("reviewer"), f"{context}.reviewer")
    if evidence_reviewer != reviewer:
        raise PhaseReportV2Error(f"{context}.reviewer must match report reviewer")
    if canonical_creation_hash(item) != item.get("content_hash"):
        raise PhaseReportV2Error(f"{context} output evidence content hash does not match")
    return item, subject


def validate_phase_report_v2(
    value: object,
    source_project: LoadedCreationProject,
    *,
    registered_extensions: Mapping[str, ExtensionValidator] | None = None,
) -> dict[str, Any]:
    project = _validated_source_project(
        source_project,
        registered_extensions=registered_extensions,
    )
    try:
        report = _object(value, "phase report v2")
        _exact_keys(report, _REPORT_FIELDS, "phase report v2")
        if report.get("format") != PHASE_REPORT_V2_FORMAT:
            raise PhaseReportV2Error("phase report v2 has an unsupported format")
        if report.get("format_version") != PHASE_REPORT_V2_VERSION or isinstance(
            report.get("format_version"), bool
        ):
            raise PhaseReportV2Error("phase report v2 has an unsupported version")
        phase = report.get("phase")
        if not isinstance(phase, str) or phase not in PHASE_REPORT_V2_PHASE_IDS:
            raise PhaseReportV2Error("phase report v2 has an unsupported phase")
        status = report.get("status")
        if not isinstance(status, str) or status not in {"ready", "not_applicable"}:
            raise PhaseReportV2Error("phase report v2 has an unsupported status")

        registry = _subject_registry(project)
        expected_top = {
            "project": _document_identity(project.project),
            "profile": _document_identity(project.profile),
            "source_manifest": _document_identity(project.manifest),
        }
        top_identities: list[dict[str, Any]] = []
        for field, expected in expected_top.items():
            identity = _validate_identity(report.get(field), f"phase report v2.{field}")
            if identity != expected:
                raise PhaseReportV2Error(
                    f"phase report v2.{field} does not match the validated creation project"
                )
            top_identities.append(identity)

        rationale = _object(report.get("rationale"), "phase report v2.rationale")
        _exact_keys(rationale, _RATIONALE_FIELDS, "phase report v2.rationale")
        rationale_code = _identifier(rationale.get("code"), "phase report v2.rationale.code")
        _non_empty_string(rationale.get("message"), "phase report v2.rationale.message")
        if status == "ready":
            if rationale_code != "phase_ready":
                raise PhaseReportV2Error("ready phase reports require rationale code phase_ready")
        else:
            expected_code = _NOT_APPLICABLE_CODES.get(phase)
            if expected_code is None:
                raise PhaseReportV2Error(
                    f"{phase} cannot be not_applicable; it requires reviewed output"
                )
            if rationale_code != expected_code:
                raise PhaseReportV2Error(
                    f"{phase} not_applicable requires rationale code {expected_code}"
                )
            if not _not_applicable_is_proven(phase, project):
                raise PhaseReportV2Error(
                    f"{phase} profile does not prove that the phase is irrelevant"
                )

        evidence = report.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise PhaseReportV2Error("phase report v2.evidence must be a non-empty array")
        evidence_ids: set[str] = set()
        evidence_subjects: list[dict[str, Any]] = []
        for index, raw in enumerate(evidence):
            context = f"phase report v2.evidence/{index}"
            item = _object(raw, context)
            _exact_keys(item, _EVIDENCE_FIELDS, context)
            evidence_id = _identifier(item.get("evidence_id"), f"{context}.evidence_id")
            folded = evidence_id.casefold()
            if folded in evidence_ids:
                raise PhaseReportV2Error(
                    "phase report v2.evidence contains an NFC/casefold collision"
                )
            evidence_ids.add(folded)
            _non_empty_string(item.get("claim"), f"{context}.claim")
            subject = _validate_identity(item.get("subject"), f"{context}.subject")
            if subject["format"] == LOGIC_MODULE_FORMAT and phase not in _LOGIC_EVIDENCE_PHASES:
                raise PhaseReportV2Error(
                    f"{context}.subject logic module is unsupported for phase {phase}"
                )
            if _identity_key(subject) not in registry:
                raise PhaseReportV2Error(
                    f"{context}.subject references an unknown or mismatched subject"
                )
            evidence_subjects.append(subject)
        evidence_order = [item["evidence_id"] for item in evidence]
        if evidence_order != sorted(evidence_order, key=lambda item: item.encode("utf-8")):
            raise PhaseReportV2Error("phase report v2.evidence must use canonical sorted order")

        reviewer = _validate_reviewer(report.get("reviewer"), "phase report v2.reviewer")
        _, output_subject = _validate_output_evidence(
            report.get("output_evidence"),
            phase=phase,
            status=status,
            reviewer=reviewer,
            registry=registry,
        )

        dependencies = _validate_sorted_identities(
            report.get("invalidation_dependencies"),
            context="phase report v2.invalidation_dependencies",
            registry=registry,
        )
        dependency_keys = {_identity_key(item) for item in dependencies}
        required_keys = {
            _identity_key(item)
            for item in (
                *top_identities,
                *evidence_subjects,
                *((output_subject,) if output_subject is not None else ()),
            )
        }
        if not required_keys.issubset(dependency_keys):
            raise PhaseReportV2Error(
                "phase report v2.invalidation_dependencies must cover every "
                "identity and evidence subject"
            )
        _extensions(
            report.get("extensions"),
            "phase report v2.extensions",
            {} if registered_extensions is None else registered_extensions,
        )
        if canonical_creation_hash(report) != report.get("content_hash"):
            raise PhaseReportV2Error("phase report v2 content hash does not match")
    except CreationContractError as exc:
        raise PhaseReportV2Error(str(exc)) from exc
    return copy.deepcopy(report)


def build_phase_output_evidence(
    *,
    evidence_id: str,
    phase: str,
    role: str,
    subject: Mapping[str, Any],
    reviewer_id: str,
    reviewer_role: str,
) -> dict[str, Any]:
    try:
        if not isinstance(subject, Mapping):
            raise PhaseReportV2Error("phase output evidence.subject must be an object")
        _identifier(evidence_id, "phase output evidence.id")
        if not isinstance(phase, str) or phase not in PHASE_REPORT_V2_PHASE_IDS:
            raise PhaseReportV2Error("phase output evidence.phase is unsupported")
        role_formats = _PHASE_ROLE_SUBJECT_FORMATS[phase]
        if not isinstance(role, str) or role not in role_formats:
            raise PhaseReportV2Error(f"phase output evidence.role is unsupported for {phase}")
        identity = _document_identity(subject)
        _validate_identity(identity, "phase output evidence.subject")
        if identity["format"] not in role_formats[role]:
            raise PhaseReportV2Error(
                f"phase output evidence.subject format is unsupported for role {role}"
            )
        reviewer = {
            "id": reviewer_id,
            "role": reviewer_role,
        }
        _validate_reviewer(reviewer, "phase output evidence.reviewer")
    except (CreationContractError, AttributeError, KeyError, TypeError) as exc:
        if isinstance(exc, PhaseReportV2Error):
            raise
        raise PhaseReportV2Error(str(exc)) from exc
    evidence: dict[str, Any] = {
        "format": PHASE_OUTPUT_EVIDENCE_FORMAT,
        "format_version": PHASE_OUTPUT_EVIDENCE_VERSION,
        "id": evidence_id,
        "phase": phase,
        "role": role,
        "subject": identity,
        "reviewer": reviewer,
    }
    evidence["content_hash"] = canonical_creation_hash(evidence)
    return evidence


def build_phase_report_v2(
    source_project: LoadedCreationProject,
    *,
    phase: str,
    status: str,
    rationale_code: str,
    rationale_message: str,
    reviewer_id: str,
    reviewer_role: str,
    output_evidence: object | None = None,
    registered_extensions: Mapping[str, ExtensionValidator] | None = None,
) -> dict[str, Any]:
    project = _validated_source_project(
        source_project,
        registered_extensions=registered_extensions,
    )
    if status == "ready" and output_evidence is None:
        raise PhaseReportV2Error("ready phase reports require caller-supplied output evidence")
    identities = {
        "creation_profile": _document_identity(project.profile),
        "creation_project": _document_identity(project.project),
        "source_manifest": _document_identity(project.manifest),
    }
    evidence = [
        {
            "evidence_id": evidence_id,
            "claim": {
                "creation_profile": "The exact creation profile was validated.",
                "creation_project": "The exact creation project was validated.",
                "source_manifest": "The exact typed source manifest was validated.",
            }[evidence_id],
            "subject": copy.deepcopy(identity),
        }
        for evidence_id, identity in sorted(
            identities.items(), key=lambda item: item[0].encode("utf-8")
        )
    ]
    dependency_by_key = {
        _identity_key(identity): copy.deepcopy(identity) for identity in identities.values()
    }
    if isinstance(output_evidence, Mapping) and isinstance(output_evidence.get("subject"), Mapping):
        subject = dict(output_evidence["subject"])
        try:
            dependency_by_key[_identity_key(subject)] = copy.deepcopy(subject)
        except (KeyError, TypeError, ValueError):
            pass
    dependencies = sorted(dependency_by_key.values(), key=_identity_sort_key)
    report: dict[str, Any] = {
        "format": PHASE_REPORT_V2_FORMAT,
        "format_version": PHASE_REPORT_V2_VERSION,
        "project": _document_identity(project.project),
        "profile": _document_identity(project.profile),
        "source_manifest": _document_identity(project.manifest),
        "phase": phase,
        "status": status,
        "rationale": {
            "code": rationale_code,
            "message": rationale_message,
        },
        "evidence": evidence,
        "output_evidence": copy.deepcopy(output_evidence),
        "reviewer": {
            "id": reviewer_id,
            "role": reviewer_role,
        },
        "invalidation_dependencies": dependencies,
        "extensions": [],
    }
    report["content_hash"] = canonical_creation_hash(report)
    return validate_phase_report_v2(
        report,
        project,
        registered_extensions=registered_extensions,
    )


def load_phase_report_v2(
    report_path: str | Path,
    *,
    project_path: str | Path,
    registered_extensions: Mapping[str, ExtensionValidator] | None = None,
) -> dict[str, Any]:
    try:
        project = load_creation_project(
            project_path,
            registered_extensions=registered_extensions,
        )
        report = read_creation_object(report_path)
        return validate_phase_report_v2(
            report,
            project,
            registered_extensions=registered_extensions,
        )
    except (CreationContractError, PhaseReportV2Error) as exc:
        if isinstance(exc, PhaseReportV2Error):
            raise
        raise PhaseReportV2Error(str(exc)) from exc
