from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from worldforge.creation_contracts import (
    CREATION_PROFILE_FORMAT,
    CREATION_PROJECT_FORMAT,
    CREATION_SOURCE_MANIFEST_FORMAT,
    NARRATIVE_MODULE_FORMAT,
    WORLD_MODULE_FORMAT,
    CreationContractError,
    ExtensionValidator,
    LoadedCreationProject,
    _exact_keys,
    _extensions,
    _identifier,
    _identifier_array,
    _integer,
    _locale,
    _non_empty_string,
    _object,
    _sha256,
    _string_array,
    canonical_creation_hash,
    load_creation_project,
    read_creation_object,
    validate_creation_documents,
)
from worldforge.integrity import canonical_json_bytes

LOREPACK_FORMAT = "world-forge.lorepack"
LOREPACK_VERSION = 1
LORE_WORLD_PROJECTION_FORMAT = "world-forge.lore_world_projection"
LORE_NARRATIVE_PROJECTION_FORMAT = "world-forge.lore_narrative_projection"
LORE_PROJECTION_VERSION = 1
MAX_LOREPACK_DEPENDENCIES = 15
MAX_LOREPACK_GRAPH_DEPTH = 16
MAX_LOREPACK_AGGREGATE_BYTES = 64 * 1024 * 1024

_LOREPACK_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "lorepack_id",
        "project",
        "profile",
        "source_manifest",
        "dependencies",
        "world_projections",
        "narrative_projections",
        "localization",
        "provenance",
        "extensions",
        "content_hash",
    }
)
_IDENTITY_FIELDS = frozenset({"format", "format_version", "id", "content_hash"})
_WORLD_PROJECTION_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "projection_id",
        "source",
        "module_type",
        "title",
        "records",
        "content_hash",
    }
)
_NARRATIVE_PROJECTION_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "projection_id",
        "source",
        "title",
        "entry_unit_ids",
        "units",
        "content_hash",
    }
)
_NARRATIVE_UNIT_COMMON_FIELDS = frozenset({"id", "unit_type", "title", "next_unit_ids"})
_CHOICE_OPTION_FIELDS = frozenset({"id", "label", "next_unit_id"})
_LOCALIZATION_FIELDS = frozenset({"source_locale", "supported_locales", "references"})
_LOCALIZATION_REFERENCE_FIELDS = frozenset(
    {
        "key",
        "locale",
        "module_id",
        "subject_kind",
        "subject_id",
        "parent_id",
        "field",
    }
)
_PROVENANCE_FIELDS = frozenset({"provenance_id", "kind", "subject"})
_WORLD_PAYLOAD_FIELDS = {
    "canon": "facts",
    "chronology": "events",
    "space": "spaces",
    "group": "groups",
    "character": "characters",
    "knowledge": "knowledge_items",
}
_WORLD_PROJECTION_RECORD_FIELDS = {
    "canon": frozenset({"id", "statement", "status"}),
    "chronology": frozenset({"id", "sequence", "summary"}),
    "space": frozenset({"id", "name", "topology"}),
    "group": frozenset({"id", "name", "group_type"}),
    "character": frozenset({"id", "name", "role"}),
    "knowledge": frozenset({"id", "statement", "access"}),
}
_WORLD_LOCALIZABLE_FIELDS = frozenset({"statement", "summary", "name", "role", "group_type"})
_NARRATIVE_UNIT_TYPES = frozenset(
    {
        "arc",
        "beat",
        "scene",
        "dialogue",
        "storylet",
        "clue",
        "reveal",
        "memory",
        "episode",
        "choice",
        "ending",
    }
)
_SOURCE_FORMATS = frozenset(
    {
        CREATION_PROJECT_FORMAT,
        CREATION_PROFILE_FORMAT,
        CREATION_SOURCE_MANIFEST_FORMAT,
        WORLD_MODULE_FORMAT,
        NARRATIVE_MODULE_FORMAT,
    }
)


class LorepackError(ValueError):
    """Raised when a generic immutable lorepack fails closed validation."""


def _validated_source_project(
    project: LoadedCreationProject,
    *,
    registered_extensions: Mapping[str, ExtensionValidator] | None = None,
) -> LoadedCreationProject:
    if not isinstance(project, LoadedCreationProject):
        raise LorepackError("lorepack requires a loaded creation project")
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
        raise LorepackError(f"creation project is invalid: {exc}") from exc


def _creation_identity(
    document: Mapping[str, Any],
    *,
    identity_field: str,
) -> dict[str, Any]:
    return {
        "format": document["format"],
        "format_version": document["format_version"],
        "id": document[identity_field],
        "content_hash": document["content_hash"],
    }


def _lorepack_identity(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "format": LOREPACK_FORMAT,
        "format_version": LOREPACK_VERSION,
        "id": document["lorepack_id"],
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


def _validate_identity(
    value: object,
    context: str,
    *,
    allowed_formats: frozenset[str],
) -> dict[str, Any]:
    identity = _object(value, context)
    _exact_keys(identity, _IDENTITY_FIELDS, context)
    format_name = _non_empty_string(identity.get("format"), f"{context}.format")
    if format_name not in allowed_formats:
        raise LorepackError(f"{context}.format is unsupported")
    expected_version = LOREPACK_VERSION if format_name == LOREPACK_FORMAT else 1
    if identity.get("format_version") != expected_version or isinstance(
        identity.get("format_version"), bool
    ):
        raise LorepackError(f"{context}.format_version must be {expected_version}")
    _identifier(identity.get("id"), f"{context}.id")
    _sha256(identity.get("content_hash"), f"{context}.content_hash")
    return identity


def _projection_sort_key(document: Mapping[str, Any]) -> bytes:
    return str(document["projection_id"]).encode("utf-8")


def _localization_sort_key(reference: Mapping[str, Any]) -> bytes:
    return str(reference["key"]).encode("utf-8")


def _localization_reference(
    *,
    key: str,
    locale: str,
    module_id: str,
    subject_kind: str,
    subject_id: str,
    parent_id: str,
    field: str,
) -> dict[str, str]:
    return {
        "key": key,
        "locale": locale,
        "module_id": module_id,
        "subject_kind": subject_kind,
        "subject_id": subject_id,
        "parent_id": parent_id,
        "field": field,
    }


def _build_world_projection(module: Mapping[str, Any]) -> dict[str, Any]:
    module_type = str(module["module_type"])
    payload_field = _WORLD_PAYLOAD_FIELDS[module_type]
    allowed_fields = _WORLD_PROJECTION_RECORD_FIELDS[module_type]
    records = sorted(
        (
            {
                field: copy.deepcopy(record[field])
                for field in sorted(allowed_fields, key=lambda item: item.encode("utf-8"))
            }
            for record in module[payload_field]
        ),
        key=lambda record: str(record["id"]).encode("utf-8"),
    )
    projection: dict[str, Any] = {
        "format": LORE_WORLD_PROJECTION_FORMAT,
        "format_version": LORE_PROJECTION_VERSION,
        "projection_id": module["module_id"],
        "source": _creation_identity(module, identity_field="module_id"),
        "module_type": module_type,
        "title": module["title"],
        "records": records,
    }
    projection["content_hash"] = canonical_creation_hash(projection)
    return projection


def _build_narrative_projection(module: Mapping[str, Any]) -> dict[str, Any]:
    units: list[dict[str, Any]] = []
    for source_unit in module["units"]:
        unit: dict[str, Any] = {
            "id": source_unit["id"],
            "unit_type": source_unit["unit_type"],
            "title": source_unit["title"],
            "next_unit_ids": copy.deepcopy(source_unit["next_unit_ids"]),
        }
        if source_unit["unit_type"] == "choice":
            unit["options"] = sorted(
                (
                    {
                        "id": option["id"],
                        "label": option["label"],
                        "next_unit_id": option["next_unit_id"],
                    }
                    for option in source_unit["options"]
                ),
                key=lambda option: str(option["id"]).encode("utf-8"),
            )
        elif source_unit["unit_type"] == "ending":
            unit["ending_kind"] = source_unit["ending_kind"]
        units.append(unit)
    units.sort(key=lambda unit: str(unit["id"]).encode("utf-8"))
    projection: dict[str, Any] = {
        "format": LORE_NARRATIVE_PROJECTION_FORMAT,
        "format_version": LORE_PROJECTION_VERSION,
        "projection_id": module["module_id"],
        "source": _creation_identity(module, identity_field="module_id"),
        "title": module["title"],
        "entry_unit_ids": copy.deepcopy(module["entry_unit_ids"]),
        "units": units,
    }
    projection["content_hash"] = canonical_creation_hash(projection)
    return projection


def _validate_world_projection(value: object, context: str) -> dict[str, Any]:
    projection = _object(value, context)
    _exact_keys(projection, _WORLD_PROJECTION_FIELDS, context)
    if projection.get("format") != LORE_WORLD_PROJECTION_FORMAT:
        raise LorepackError(f"{context}.format is unsupported")
    if projection.get("format_version") != LORE_PROJECTION_VERSION or isinstance(
        projection.get("format_version"), bool
    ):
        raise LorepackError(f"{context}.format_version is unsupported")
    projection_id = _identifier(projection.get("projection_id"), f"{context}.projection_id")
    source = _validate_identity(
        projection.get("source"),
        f"{context}.source",
        allowed_formats=frozenset({WORLD_MODULE_FORMAT}),
    )
    if source["id"] != projection_id:
        raise LorepackError(f"{context}.source ID must match projection_id")
    module_type = projection.get("module_type")
    if not isinstance(module_type, str) or module_type not in _WORLD_PAYLOAD_FIELDS:
        raise LorepackError(f"{context}.module_type is unsupported")
    _non_empty_string(projection.get("title"), f"{context}.title")
    records = projection.get("records")
    if not isinstance(records, list) or not records:
        raise LorepackError(f"{context}.records must be a non-empty array")
    checked_records: list[dict[str, Any]] = []
    seen: set[str] = set()
    expected_fields = _WORLD_PROJECTION_RECORD_FIELDS[module_type]
    for index, raw in enumerate(records):
        record_context = f"{context}.records/{index}"
        record = _object(raw, record_context)
        _exact_keys(record, expected_fields, record_context)
        record_id = _identifier(record.get("id"), f"{record_context}.id")
        folded = record_id.casefold()
        if folded in seen:
            raise LorepackError(f"{context}.records contains an NFC/casefold collision")
        seen.add(folded)
        if module_type == "canon":
            _non_empty_string(record.get("statement"), f"{record_context}.statement")
            status = record.get("status")
            if not isinstance(status, str) or status not in {"canon", "provisional"}:
                raise LorepackError(f"{record_context}.status is unsupported")
        elif module_type == "chronology":
            _integer(record.get("sequence"), f"{record_context}.sequence")
            _non_empty_string(record.get("summary"), f"{record_context}.summary")
        elif module_type == "space":
            _non_empty_string(record.get("name"), f"{record_context}.name")
            topology = record.get("topology")
            if not isinstance(topology, str) or topology not in {
                "abstract",
                "symbolic",
                "diegetic",
            }:
                raise LorepackError(f"{record_context}.topology is unsupported")
        elif module_type == "group":
            _non_empty_string(record.get("name"), f"{record_context}.name")
            _non_empty_string(record.get("group_type"), f"{record_context}.group_type")
        elif module_type == "character":
            _non_empty_string(record.get("name"), f"{record_context}.name")
            _non_empty_string(record.get("role"), f"{record_context}.role")
        else:
            _non_empty_string(record.get("statement"), f"{record_context}.statement")
            access = record.get("access")
            if not isinstance(access, str) or access not in {
                "public",
                "restricted",
                "secret",
            }:
                raise LorepackError(f"{record_context}.access is unsupported")
        checked_records.append(record)
    if checked_records != sorted(
        checked_records,
        key=lambda record: record["id"].encode("utf-8"),
    ):
        raise LorepackError(f"{context}.records must use canonical sorted order")
    if canonical_creation_hash(projection) != projection.get("content_hash"):
        raise LorepackError(f"{context} content hash does not match")
    return projection


def _validate_narrative_projection(value: object, context: str) -> dict[str, Any]:
    projection = _object(value, context)
    _exact_keys(projection, _NARRATIVE_PROJECTION_FIELDS, context)
    if projection.get("format") != LORE_NARRATIVE_PROJECTION_FORMAT:
        raise LorepackError(f"{context}.format is unsupported")
    if projection.get("format_version") != LORE_PROJECTION_VERSION or isinstance(
        projection.get("format_version"), bool
    ):
        raise LorepackError(f"{context}.format_version is unsupported")
    projection_id = _identifier(projection.get("projection_id"), f"{context}.projection_id")
    source = _validate_identity(
        projection.get("source"),
        f"{context}.source",
        allowed_formats=frozenset({NARRATIVE_MODULE_FORMAT}),
    )
    if source["id"] != projection_id:
        raise LorepackError(f"{context}.source ID must match projection_id")
    _non_empty_string(projection.get("title"), f"{context}.title")
    entries = _identifier_array(
        projection.get("entry_unit_ids"),
        f"{context}.entry_unit_ids",
        allow_empty=False,
    )
    units = projection.get("units")
    if not isinstance(units, list) or not units:
        raise LorepackError(f"{context}.units must be a non-empty array")
    checked_units: list[dict[str, Any]] = []
    unit_ids: set[str] = set()
    for index, raw in enumerate(units):
        unit_context = f"{context}.units/{index}"
        unit = _object(raw, unit_context)
        unit_type = unit.get("unit_type")
        if not isinstance(unit_type, str) or unit_type not in _NARRATIVE_UNIT_TYPES:
            raise LorepackError(f"{unit_context}.unit_type is unsupported")
        extra_fields = (
            frozenset({"options"})
            if unit_type == "choice"
            else frozenset({"ending_kind"})
            if unit_type == "ending"
            else frozenset()
        )
        _exact_keys(unit, _NARRATIVE_UNIT_COMMON_FIELDS | extra_fields, unit_context)
        unit_id = _identifier(unit.get("id"), f"{unit_context}.id")
        folded = unit_id.casefold()
        if folded in unit_ids:
            raise LorepackError(f"{context}.units contains an NFC/casefold collision")
        unit_ids.add(folded)
        _non_empty_string(unit.get("title"), f"{unit_context}.title")
        next_ids = _identifier_array(unit.get("next_unit_ids"), f"{unit_context}.next_unit_ids")
        if unit_type == "choice":
            options = unit.get("options")
            if not isinstance(options, list) or len(options) < 2:
                raise LorepackError(f"{unit_context}.options must contain at least two")
            checked_options: list[dict[str, Any]] = []
            option_ids: set[str] = set()
            targets: list[str] = []
            for option_index, raw_option in enumerate(options):
                option_context = f"{unit_context}.options/{option_index}"
                option = _object(raw_option, option_context)
                _exact_keys(option, _CHOICE_OPTION_FIELDS, option_context)
                option_id = _identifier(option.get("id"), f"{option_context}.id")
                folded_option = option_id.casefold()
                if folded_option in option_ids:
                    raise LorepackError(
                        f"{unit_context}.options contains an NFC/casefold collision"
                    )
                option_ids.add(folded_option)
                _non_empty_string(option.get("label"), f"{option_context}.label")
                targets.append(
                    _identifier(option.get("next_unit_id"), f"{option_context}.next_unit_id")
                )
                checked_options.append(option)
            if checked_options != sorted(
                checked_options,
                key=lambda option: option["id"].encode("utf-8"),
            ):
                raise LorepackError(f"{unit_context}.options must use canonical sorted order")
            if next_ids != sorted(targets, key=lambda item: item.encode("utf-8")):
                raise LorepackError(
                    f"{unit_context} choice next_unit_ids must equal sorted option targets"
                )
        elif unit_type == "ending":
            ending_kind = unit.get("ending_kind")
            if not isinstance(ending_kind, str) or ending_kind not in {
                "success",
                "failure",
                "neutral",
            }:
                raise LorepackError(f"{unit_context}.ending_kind is unsupported")
            if next_ids:
                raise LorepackError(f"{unit_context} ending units cannot have outgoing edges")
        checked_units.append(unit)
    if checked_units != sorted(
        checked_units,
        key=lambda unit: unit["id"].encode("utf-8"),
    ):
        raise LorepackError(f"{context}.units must use canonical sorted order")
    local_ids = {unit["id"] for unit in checked_units}
    unknown_entries = set(entries) - local_ids
    if unknown_entries:
        raise LorepackError(
            f"{context}.entry_unit_ids reference unknown units: "
            + ", ".join(sorted(unknown_entries))
        )
    if canonical_creation_hash(projection) != projection.get("content_hash"):
        raise LorepackError(f"{context} content hash does not match")
    return projection


def _validate_projection_collections(
    *,
    world_value: object,
    narrative_value: object,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(world_value, list):
        raise LorepackError("lorepack.world_projections must be an array")
    if not isinstance(narrative_value, list):
        raise LorepackError("lorepack.narrative_projections must be an array")
    if not world_value and not narrative_value:
        raise LorepackError("lorepack is not applicable without world or narrative projections")
    world = [
        _validate_world_projection(raw, f"lorepack.world_projections/{index}")
        for index, raw in enumerate(world_value)
    ]
    narrative = [
        _validate_narrative_projection(raw, f"lorepack.narrative_projections/{index}")
        for index, raw in enumerate(narrative_value)
    ]
    if world != sorted(world, key=_projection_sort_key):
        raise LorepackError("lorepack.world_projections must use canonical sorted order")
    if narrative != sorted(narrative, key=_projection_sort_key):
        raise LorepackError("lorepack.narrative_projections must use canonical sorted order")
    projection_ids: set[str] = set()
    for projection in (*world, *narrative):
        folded = projection["projection_id"].casefold()
        if folded in projection_ids:
            raise LorepackError("lorepack projections contain an NFC/casefold collision")
        projection_ids.add(folded)
    _validate_global_narrative_graph(narrative)
    return world, narrative


def _validate_global_narrative_graph(
    projections: Sequence[Mapping[str, Any]],
) -> None:
    units: dict[str, Mapping[str, Any]] = {}
    display_ids: dict[str, str] = {}
    entries: list[str] = []
    for projection in projections:
        entries.extend(projection["entry_unit_ids"])
        for unit in projection["units"]:
            folded = unit["id"].casefold()
            if folded in units:
                raise LorepackError("lorepack narrative units contain an NFC/casefold collision")
            units[folded] = unit
            display_ids[folded] = unit["id"]
    for entry in entries:
        if entry.casefold() not in units:
            raise LorepackError(f"lorepack narrative entry references missing unit {entry}")
    adjacency: dict[str, list[str]] = {}
    for folded, unit in units.items():
        targets = list(unit["next_unit_ids"])
        if unit["unit_type"] == "choice":
            option_targets = [option["next_unit_id"] for option in unit["options"]]
            if targets != sorted(option_targets, key=lambda item: item.encode("utf-8")):
                raise LorepackError(
                    f"lorepack narrative choice {unit['id']} target set is inconsistent"
                )
        for target in targets:
            if target.casefold() not in units:
                raise LorepackError(
                    f"lorepack narrative unit {unit['id']} references missing unit {target}"
                )
        adjacency[folded] = [target.casefold() for target in targets]
    reachable: set[str] = set()
    pending = [entry.casefold() for entry in reversed(entries)]
    while pending:
        unit_id = pending.pop()
        if unit_id in reachable:
            continue
        reachable.add(unit_id)
        pending.extend(reversed(adjacency[unit_id]))
    unreachable = set(units) - reachable
    if unreachable:
        raise LorepackError(
            "lorepack narrative graph contains unreachable units: "
            + ", ".join(sorted(display_ids[item] for item in unreachable))
        )


def _expected_localization_references(
    world_projections: Sequence[Mapping[str, Any]],
    narrative_projections: Sequence[Mapping[str, Any]],
    *,
    source_locale: str,
) -> list[dict[str, str]]:
    references: list[dict[str, str]] = []
    for projection in world_projections:
        projection_id = str(projection["projection_id"])
        references.append(
            _localization_reference(
                key=f"module.{projection_id}.title",
                locale=source_locale,
                module_id=projection_id,
                subject_kind="module",
                subject_id=projection_id,
                parent_id=projection_id,
                field="title",
            )
        )
        for record in projection["records"]:
            for field in sorted(
                _WORLD_LOCALIZABLE_FIELDS.intersection(record),
                key=lambda item: item.encode("utf-8"),
            ):
                references.append(
                    _localization_reference(
                        key=f"world.{projection_id}.{record['id']}.{field}",
                        locale=source_locale,
                        module_id=projection_id,
                        subject_kind="world_record",
                        subject_id=str(record["id"]),
                        parent_id=projection_id,
                        field=field,
                    )
                )
    for projection in narrative_projections:
        projection_id = str(projection["projection_id"])
        references.append(
            _localization_reference(
                key=f"module.{projection_id}.title",
                locale=source_locale,
                module_id=projection_id,
                subject_kind="module",
                subject_id=projection_id,
                parent_id=projection_id,
                field="title",
            )
        )
        for unit in projection["units"]:
            unit_id = str(unit["id"])
            references.append(
                _localization_reference(
                    key=f"narrative.{projection_id}.{unit_id}.title",
                    locale=source_locale,
                    module_id=projection_id,
                    subject_kind="narrative_unit",
                    subject_id=unit_id,
                    parent_id=projection_id,
                    field="title",
                )
            )
            for option in unit.get("options", ()):
                references.append(
                    _localization_reference(
                        key=(f"narrative.{projection_id}.{unit_id}.{option['id']}.label"),
                        locale=source_locale,
                        module_id=projection_id,
                        subject_kind="choice_option",
                        subject_id=str(option["id"]),
                        parent_id=unit_id,
                        field="label",
                    )
                )
    return sorted(references, key=_localization_sort_key)


def _validate_localization(
    value: object,
    *,
    world_projections: Sequence[Mapping[str, Any]],
    narrative_projections: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    context = "lorepack.localization"
    localization = _object(value, context)
    _exact_keys(localization, _LOCALIZATION_FIELDS, context)
    source_locale = _locale(localization.get("source_locale"), f"{context}.source_locale")
    supported = _string_array(
        localization.get("supported_locales"),
        f"{context}.supported_locales",
        allow_empty=False,
        canonical_order=True,
    )
    for index, locale in enumerate(supported):
        _locale(locale, f"{context}.supported_locales/{index}")
    if source_locale.casefold() not in {locale.casefold() for locale in supported}:
        raise LorepackError("lorepack.localization.supported_locales must contain source_locale")
    references = localization.get("references")
    if not isinstance(references, list) or not references:
        raise LorepackError("lorepack.localization.references must be non-empty")
    reference_keys: set[str] = set()
    for index, raw in enumerate(references):
        reference_context = f"{context}.references/{index}"
        reference = _object(raw, reference_context)
        _exact_keys(reference, _LOCALIZATION_REFERENCE_FIELDS, reference_context)
        key = _non_empty_string(reference.get("key"), f"{reference_context}.key")
        if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_." for character in key):
            raise LorepackError(
                f"{reference_context}.key must be a lowercase portable localization key"
            )
        folded = key.casefold()
        if folded in reference_keys:
            raise LorepackError(
                "lorepack.localization.references contains an NFC/casefold collision"
            )
        reference_keys.add(folded)
        _locale(reference.get("locale"), f"{reference_context}.locale")
        for field in ("module_id", "subject_id", "parent_id"):
            _identifier(reference.get(field), f"{reference_context}.{field}")
        subject_kind = reference.get("subject_kind")
        if not isinstance(subject_kind, str) or subject_kind not in {
            "module",
            "world_record",
            "narrative_unit",
            "choice_option",
        }:
            raise LorepackError(f"{reference_context}.subject_kind is unsupported")
        field_name = reference.get("field")
        if not isinstance(field_name, str) or field_name not in {
            "title",
            "statement",
            "summary",
            "name",
            "role",
            "group_type",
            "label",
        }:
            raise LorepackError(f"{reference_context}.field is unsupported")
    if references != sorted(references, key=_localization_sort_key):
        raise LorepackError("lorepack.localization.references must use canonical sorted order")
    expected = _expected_localization_references(
        world_projections,
        narrative_projections,
        source_locale=source_locale,
    )
    if references != expected:
        raise LorepackError(
            "lorepack localization references do not exactly resolve every "
            "localizable projection target"
        )
    return localization


def _validate_provenance(
    value: object,
    *,
    subject_registry: Mapping[tuple[str, int, str, str], object],
) -> list[dict[str, Any]]:
    context = "lorepack.provenance"
    if not isinstance(value, list) or not value:
        raise LorepackError(f"{context} must be a non-empty array")
    entries: list[dict[str, Any]] = []
    ids: set[str] = set()
    subject_keys: set[tuple[str, int, str, str]] = set()
    for index, raw in enumerate(value):
        item_context = f"{context}/{index}"
        item = _object(raw, item_context)
        _exact_keys(item, _PROVENANCE_FIELDS, item_context)
        provenance_id = _identifier(item.get("provenance_id"), f"{item_context}.provenance_id")
        folded = provenance_id.casefold()
        if folded in ids:
            raise LorepackError(f"{context} contains an NFC/casefold collision")
        ids.add(folded)
        kind = item.get("kind")
        if not isinstance(kind, str) or kind not in {
            "source_contract",
            "dependency_lorepack",
        }:
            raise LorepackError(f"{item_context}.kind is unsupported")
        subject = _validate_identity(
            item.get("subject"),
            f"{item_context}.subject",
            allowed_formats=_SOURCE_FORMATS | frozenset({LOREPACK_FORMAT}),
        )
        subject_key = _identity_key(subject)
        if subject_key in subject_keys:
            raise LorepackError(f"{context} contains a duplicate subject identity")
        subject_keys.add(subject_key)
        if subject_key not in subject_registry:
            raise LorepackError(f"{item_context}.subject is unknown or mismatched")
        if kind == "dependency_lorepack" and subject["format"] != LOREPACK_FORMAT:
            raise LorepackError(
                f"{item_context}.kind dependency_lorepack requires a lorepack subject"
            )
        if kind == "source_contract" and subject["format"] == LOREPACK_FORMAT:
            raise LorepackError(
                f"{item_context}.kind source_contract cannot name a lorepack subject"
            )
        entries.append(item)
    if entries != sorted(entries, key=lambda item: item["provenance_id"].encode("utf-8")):
        raise LorepackError(f"{context} must use canonical sorted order")
    expected_keys = set(subject_registry)
    if subject_keys != expected_keys:
        raise LorepackError(
            "lorepack provenance must cover every exact source and dependency identity once"
        )
    return entries


def validate_lorepack_document(
    value: object,
    *,
    registered_extensions: Mapping[str, ExtensionValidator] | None = None,
) -> dict[str, Any]:
    registry = {} if registered_extensions is None else dict(registered_extensions)
    try:
        document = _object(value, "lorepack")
        _exact_keys(document, _LOREPACK_FIELDS, "lorepack")
        if document.get("format") != LOREPACK_FORMAT:
            raise LorepackError("lorepack format is unsupported")
        if document.get("format_version") != LOREPACK_VERSION or isinstance(
            document.get("format_version"), bool
        ):
            raise LorepackError("lorepack version is unsupported")
        _identifier(document.get("lorepack_id"), "lorepack.lorepack_id")
        project_identity = _validate_identity(
            document.get("project"),
            "lorepack.project",
            allowed_formats=frozenset({CREATION_PROJECT_FORMAT}),
        )
        profile_identity = _validate_identity(
            document.get("profile"),
            "lorepack.profile",
            allowed_formats=frozenset({CREATION_PROFILE_FORMAT}),
        )
        manifest_identity = _validate_identity(
            document.get("source_manifest"),
            "lorepack.source_manifest",
            allowed_formats=frozenset({CREATION_SOURCE_MANIFEST_FORMAT}),
        )
        if project_identity["id"] != manifest_identity["id"]:
            raise LorepackError("lorepack project and source manifest identities differ")

        dependencies = document.get("dependencies")
        if not isinstance(dependencies, list):
            raise LorepackError("lorepack.dependencies must be an array")
        if len(dependencies) > MAX_LOREPACK_DEPENDENCIES:
            raise LorepackError(
                f"lorepack.dependencies exceeds the {MAX_LOREPACK_DEPENDENCIES}-item limit"
            )
        dependency_ids: set[str] = set()
        checked_dependencies: list[dict[str, Any]] = []
        for index, raw in enumerate(dependencies):
            dependency = _validate_identity(
                raw,
                f"lorepack.dependencies/{index}",
                allowed_formats=frozenset({LOREPACK_FORMAT}),
            )
            folded = dependency["id"].casefold()
            if folded in dependency_ids:
                raise LorepackError("lorepack.dependencies contains an NFC/casefold collision")
            dependency_ids.add(folded)
            checked_dependencies.append(dependency)
        if checked_dependencies != sorted(checked_dependencies, key=_identity_sort_key):
            raise LorepackError("lorepack.dependencies must use canonical sorted order")

        world_projections, narrative_projections = _validate_projection_collections(
            world_value=document.get("world_projections"),
            narrative_value=document.get("narrative_projections"),
        )
        _validate_localization(
            document.get("localization"),
            world_projections=world_projections,
            narrative_projections=narrative_projections,
        )
        subject_identities = [
            project_identity,
            profile_identity,
            manifest_identity,
            *checked_dependencies,
            *(projection["source"] for projection in world_projections),
            *(projection["source"] for projection in narrative_projections),
        ]
        subject_registry = {_identity_key(identity): identity for identity in subject_identities}
        if len(subject_registry) != len(subject_identities):
            raise LorepackError("lorepack source identities contain a duplicate subject")
        _validate_provenance(
            document.get("provenance"),
            subject_registry=subject_registry,
        )
        _extensions(
            document.get("extensions"),
            "lorepack.extensions",
            registry,
        )
        if canonical_creation_hash(document) != document.get("content_hash"):
            raise LorepackError("lorepack content hash does not match")
    except CreationContractError as exc:
        raise LorepackError(str(exc)) from exc
    return copy.deepcopy(document)


def _bounded_dependency_input(
    value: object,
    *,
    context: str,
) -> tuple[object, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise LorepackError(f"{context} must be an array")
    if len(value) > MAX_LOREPACK_DEPENDENCIES:
        raise LorepackError(f"{context} exceeds the {MAX_LOREPACK_DEPENDENCIES}-item limit")
    return tuple(value)


def _minimal_lorepack_document(value: object, context: str) -> dict[str, Any]:
    try:
        document = _object(value, context)
        if document.get("format") != LOREPACK_FORMAT:
            raise LorepackError(f"{context}.format is unsupported")
        if document.get("format_version") != LOREPACK_VERSION or isinstance(
            document.get("format_version"), bool
        ):
            raise LorepackError(f"{context}.format_version is unsupported")
        lorepack_id = _identifier(document.get("lorepack_id"), f"{context}.lorepack_id")
        content_hash = _sha256(document.get("content_hash"), f"{context}.content_hash")
        raw_dependencies = document.get("dependencies")
        if not isinstance(raw_dependencies, list):
            raise LorepackError(f"{context}.dependencies must be an array")
        if len(raw_dependencies) > MAX_LOREPACK_DEPENDENCIES:
            raise LorepackError(
                f"{context}.dependencies exceeds the {MAX_LOREPACK_DEPENDENCIES}-item limit"
            )
        dependencies: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, raw in enumerate(raw_dependencies):
            reference = _validate_identity(
                raw,
                f"{context}.dependencies/{index}",
                allowed_formats=frozenset({LOREPACK_FORMAT}),
            )
            folded = reference["id"].casefold()
            if folded in seen:
                raise LorepackError(f"{context}.dependencies contains an NFC/casefold collision")
            seen.add(folded)
            dependencies.append(copy.deepcopy(reference))
        if dependencies != sorted(dependencies, key=_identity_sort_key):
            raise LorepackError(f"{context}.dependencies must use canonical sorted order")
    except CreationContractError as exc:
        raise LorepackError(str(exc)) from exc
    return {
        "format": LOREPACK_FORMAT,
        "format_version": LOREPACK_VERSION,
        "lorepack_id": lorepack_id,
        "dependencies": dependencies,
        "content_hash": content_hash,
    }


def _preflight_dependency_documents(
    root: object,
    dependencies: Sequence[object],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    minimal_root = _minimal_lorepack_document(root, "lorepack")
    minimal_dependencies = [
        _minimal_lorepack_document(dependency, f"lorepack dependency input/{index}")
        for index, dependency in enumerate(dependencies)
    ]
    _graph_documents(minimal_root, minimal_dependencies)
    return minimal_root, minimal_dependencies


def _preflight_dependency_source_mapping(
    dependencies: Sequence[Mapping[str, Any]] | None,
    dependency_sources: Mapping[str, object] | None,
    *,
    context: str,
) -> dict[str, object]:
    if dependency_sources is None:
        sources: Mapping[str, object] = {}
    elif isinstance(dependency_sources, Mapping):
        sources = dependency_sources
    else:
        raise LorepackError(f"{context} must be an object")
    if len(sources) > MAX_LOREPACK_DEPENDENCIES:
        raise LorepackError(f"{context} exceeds the {MAX_LOREPACK_DEPENDENCIES}-item limit")
    normalized: dict[str, object] = {}
    display_keys: dict[str, str] = {}
    ordered_ids: list[str] = []
    for raw_id, source in sources.items():
        try:
            dependency_id = _identifier(raw_id, f"{context} key")
        except CreationContractError as exc:
            raise LorepackError(str(exc)) from exc
        folded = dependency_id.casefold()
        if folded in normalized:
            raise LorepackError(f"{context} contains an NFC/casefold collision")
        normalized[folded] = source
        display_keys[folded] = dependency_id
        ordered_ids.append(dependency_id)
    if ordered_ids != sorted(ordered_ids, key=lambda item: item.encode("utf-8")):
        raise LorepackError(f"{context} must use canonical UTF-8 key order")
    if dependencies is None:
        return normalized
    expected_by_folded = {
        str(document["lorepack_id"]).casefold(): str(document["lorepack_id"])
        for document in dependencies
    }
    if set(normalized) != set(expected_by_folded):
        missing = set(expected_by_folded) - set(normalized)
        extras = set(normalized) - set(expected_by_folded)
        details: list[str] = []
        if missing:
            details.append(
                "missing " + ", ".join(sorted(expected_by_folded[item] for item in missing))
            )
        if extras:
            details.append("unexpected " + ", ".join(sorted(display_keys[item] for item in extras)))
        raise LorepackError(f"{context} is not exact: " + "; ".join(details))
    for folded, expected_id in expected_by_folded.items():
        if display_keys[folded] != expected_id:
            raise LorepackError(
                f"{context} key {display_keys[folded]} must exactly match {expected_id}"
            )
    return normalized


def _graph_documents(
    root: Mapping[str, Any],
    dependencies: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    by_id: dict[str, Mapping[str, Any]] = {}
    display_ids: dict[str, str] = {}
    for document in (root, *dependencies):
        folded = document["lorepack_id"].casefold()
        existing = by_id.get(folded)
        if existing is not None:
            raise LorepackError("lorepack dependency IDs contain an NFC/casefold collision")
        by_id[folded] = document
        display_ids[folded] = document["lorepack_id"]
    if len(by_id) > MAX_LOREPACK_DEPENDENCIES + 1:
        raise LorepackError("lorepack dependency graph exceeds the document limit")

    for document in by_id.values():
        for reference in document["dependencies"]:
            target = by_id.get(reference["id"].casefold())
            if target is None:
                raise LorepackError(
                    f"lorepack {document['lorepack_id']} has missing dependency {reference['id']}"
                )

    root_id = root["lorepack_id"].casefold()
    visited: set[str] = set()
    active: set[str] = set()

    def visit(node_id: str, depth: int) -> None:
        if depth > MAX_LOREPACK_GRAPH_DEPTH:
            raise LorepackError(
                f"lorepack dependency graph exceeds depth {MAX_LOREPACK_GRAPH_DEPTH}"
            )
        if node_id in active:
            raise LorepackError(
                f"lorepack dependency graph contains a cycle at {display_ids[node_id]}"
            )
        if node_id in visited:
            return
        active.add(node_id)
        node = by_id[node_id]
        for reference in node["dependencies"]:
            visit(reference["id"].casefold(), depth + 1)
        active.remove(node_id)
        visited.add(node_id)

    visit(root_id, 1)
    for document in by_id.values():
        for reference in document["dependencies"]:
            target = by_id[reference["id"].casefold()]
            if reference != _lorepack_identity(target):
                raise LorepackError(
                    f"lorepack {document['lorepack_id']} dependency "
                    f"{reference['id']} identity/hash mismatch"
                )
    extras = set(by_id) - visited
    if extras:
        raise LorepackError(
            "lorepack dependency input contains unreachable documents: "
            + ", ".join(sorted(display_ids[item] for item in extras))
        )
    return by_id


def _validated_dependency_sources(
    dependency_sources: Mapping[str, object],
    *,
    registered_extensions: Mapping[str, ExtensionValidator] | None,
) -> dict[str, LoadedCreationProject]:
    sources: dict[str, LoadedCreationProject] = {}
    for folded, source in dependency_sources.items():
        sources[folded] = _validated_source_project(
            source,
            registered_extensions=registered_extensions,
        )
    return sources


def _validate_lorepack_source_binding(
    document: Mapping[str, Any],
    project: LoadedCreationProject,
) -> None:
    expected_identities = {
        "project": _creation_identity(project.project, identity_field="project_id"),
        "profile": _creation_identity(project.profile, identity_field="profile_id"),
        "source_manifest": _creation_identity(
            project.manifest,
            identity_field="project_id",
        ),
    }
    for field, expected in expected_identities.items():
        if document[field] != expected:
            raise LorepackError(f"lorepack.{field} does not match the validated creation project")
    expected_world = sorted(
        (_build_world_projection(module) for module in project.world_modules),
        key=_projection_sort_key,
    )
    expected_narrative = sorted(
        (_build_narrative_projection(module) for module in project.narrative_modules),
        key=_projection_sort_key,
    )
    if document["world_projections"] != expected_world:
        raise LorepackError("lorepack world projection does not match its exact source modules")
    if document["narrative_projections"] != expected_narrative:
        raise LorepackError("lorepack narrative projection does not match its exact source modules")
    profile_localization = project.profile["presentation"]["localization"]
    if (
        document["localization"]["source_locale"] != profile_localization["source_locale"]
        or document["localization"]["supported_locales"]
        != profile_localization["supported_locales"]
    ):
        raise LorepackError("lorepack localization does not match the creation profile")


def validate_lorepack(
    value: object,
    *,
    source_project: LoadedCreationProject,
    dependencies: Sequence[object] = (),
    dependency_sources: Mapping[str, LoadedCreationProject] | None = None,
    registered_extensions: Mapping[str, ExtensionValidator] | None = None,
) -> dict[str, Any]:
    supplied_dependencies = _bounded_dependency_input(
        dependencies,
        context="lorepack dependency input",
    )
    _, minimal_dependencies = _preflight_dependency_documents(
        value,
        supplied_dependencies,
    )
    source_input = _preflight_dependency_source_mapping(
        minimal_dependencies,
        dependency_sources,
        context="dependency source mapping",
    )
    root = validate_lorepack_document(
        value,
        registered_extensions=registered_extensions,
    )
    project = _validated_source_project(
        source_project,
        registered_extensions=registered_extensions,
    )
    checked_dependencies = [
        validate_lorepack_document(
            dependency,
            registered_extensions=registered_extensions,
        )
        for dependency in supplied_dependencies
    ]
    _graph_documents(root, checked_dependencies)
    sources = _validated_dependency_sources(
        source_input,
        registered_extensions=registered_extensions,
    )
    _validate_lorepack_source_binding(root, project)
    for dependency in checked_dependencies:
        _validate_lorepack_source_binding(
            dependency,
            sources[dependency["lorepack_id"].casefold()],
        )
    return copy.deepcopy(root)


def _provenance_entry(
    *,
    kind: str,
    subject: Mapping[str, Any],
) -> dict[str, Any]:
    prefix = "dependency" if kind == "dependency_lorepack" else "source"
    return {
        "provenance_id": f"{prefix}_{subject['content_hash'][:48]}",
        "kind": kind,
        "subject": copy.deepcopy(subject),
    }


def build_lorepack(
    source_project: LoadedCreationProject,
    *,
    lorepack_id: str,
    dependencies: Sequence[object] = (),
    dependency_sources: Mapping[str, LoadedCreationProject] | None = None,
    registered_extensions: Mapping[str, ExtensionValidator] | None = None,
) -> dict[str, Any]:
    supplied_dependencies = _bounded_dependency_input(
        dependencies,
        context="lorepack dependency input",
    )
    try:
        checked_lorepack_id = _identifier(lorepack_id, "lorepack.lorepack_id")
    except CreationContractError as exc:
        raise LorepackError(str(exc)) from exc
    minimal_dependencies = [
        _minimal_lorepack_document(
            dependency,
            f"lorepack dependency input/{index}",
        )
        for index, dependency in enumerate(supplied_dependencies)
    ]
    synthetic_root = {
        "format": LOREPACK_FORMAT,
        "format_version": LOREPACK_VERSION,
        "lorepack_id": checked_lorepack_id,
        "dependencies": sorted(
            (_lorepack_identity(dependency) for dependency in minimal_dependencies),
            key=_identity_sort_key,
        ),
        "content_hash": "0" * 64,
    }
    _graph_documents(synthetic_root, minimal_dependencies)
    source_input = _preflight_dependency_source_mapping(
        minimal_dependencies,
        dependency_sources,
        context="dependency source mapping",
    )
    project = _validated_source_project(
        source_project,
        registered_extensions=registered_extensions,
    )
    if not project.world_modules and not project.narrative_modules:
        raise LorepackError(
            "lorepack is not applicable: the validated project has no world or narrative modules"
        )
    checked_dependencies = [
        validate_lorepack_document(
            dependency,
            registered_extensions=registered_extensions,
        )
        for dependency in supplied_dependencies
    ]
    _validated_dependency_sources(
        source_input,
        registered_extensions=registered_extensions,
    )
    dependency_identities = sorted(
        (_lorepack_identity(dependency) for dependency in checked_dependencies),
        key=_identity_sort_key,
    )
    source_locale = project.profile["presentation"]["localization"]["source_locale"]
    supported_locales = project.profile["presentation"]["localization"]["supported_locales"]
    world_projections = sorted(
        (_build_world_projection(module) for module in project.world_modules),
        key=_projection_sort_key,
    )
    narrative_projections = sorted(
        (_build_narrative_projection(module) for module in project.narrative_modules),
        key=_projection_sort_key,
    )
    project_identity = _creation_identity(project.project, identity_field="project_id")
    profile_identity = _creation_identity(project.profile, identity_field="profile_id")
    manifest_identity = _creation_identity(
        project.manifest,
        identity_field="project_id",
    )
    source_identities = [
        project_identity,
        profile_identity,
        manifest_identity,
        *(projection["source"] for projection in world_projections),
        *(projection["source"] for projection in narrative_projections),
    ]
    document: dict[str, Any] = {
        "format": LOREPACK_FORMAT,
        "format_version": LOREPACK_VERSION,
        "lorepack_id": checked_lorepack_id,
        "project": project_identity,
        "profile": profile_identity,
        "source_manifest": manifest_identity,
        "dependencies": dependency_identities,
        "world_projections": world_projections,
        "narrative_projections": narrative_projections,
        "localization": {
            "source_locale": source_locale,
            "supported_locales": copy.deepcopy(supported_locales),
            "references": _expected_localization_references(
                world_projections,
                narrative_projections,
                source_locale=source_locale,
            ),
        },
        "provenance": sorted(
            [
                *(
                    _provenance_entry(kind="source_contract", subject=identity)
                    for identity in source_identities
                ),
                *(
                    _provenance_entry(kind="dependency_lorepack", subject=identity)
                    for identity in dependency_identities
                ),
            ],
            key=lambda item: item["provenance_id"].encode("utf-8"),
        ),
        "extensions": [],
    }
    document["content_hash"] = canonical_creation_hash(document)
    return validate_lorepack(
        document,
        source_project=project,
        dependencies=checked_dependencies,
        dependency_sources=dependency_sources,
        registered_extensions=registered_extensions,
    )


def serialize_lorepack(
    value: object,
    *,
    registered_extensions: Mapping[str, ExtensionValidator] | None = None,
) -> bytes:
    document = validate_lorepack_document(
        value,
        registered_extensions=registered_extensions,
    )
    return canonical_json_bytes(document)


def load_lorepack(
    lorepack_path: str | Path,
    *,
    project_path: str | Path,
    dependency_paths: Sequence[str | Path] = (),
    dependency_project_paths: Mapping[str, str | Path] | None = None,
    registered_extensions: Mapping[str, ExtensionValidator] | None = None,
) -> dict[str, Any]:
    supplied_paths = _bounded_dependency_input(
        dependency_paths,
        context="lorepack dependency path input",
    )
    source_path_input = _preflight_dependency_source_mapping(
        None,
        dependency_project_paths,
        context="lorepack dependency project paths",
    )
    if len(source_path_input) != len(supplied_paths):
        raise LorepackError(
            "lorepack dependency project paths must contain exactly one entry per dependency path"
        )
    try:
        root = read_creation_object(lorepack_path)
        dependencies = [read_creation_object(path) for path in supplied_paths]
        aggregate_bytes = sum(
            len(canonical_json_bytes(document)) for document in (root, *dependencies)
        )
        if aggregate_bytes > MAX_LOREPACK_AGGREGATE_BYTES:
            raise LorepackError("lorepack dependency input exceeds the aggregate byte limit")
        _, minimal_dependencies = _preflight_dependency_documents(root, dependencies)
        exact_source_paths = _preflight_dependency_source_mapping(
            minimal_dependencies,
            dependency_project_paths,
            context="lorepack dependency project paths",
        )
        project = load_creation_project(
            project_path,
            registered_extensions=registered_extensions,
        )
        dependency_sources = {
            dependency["lorepack_id"]: load_creation_project(
                exact_source_paths[dependency["lorepack_id"].casefold()],
                registered_extensions=registered_extensions,
            )
            for dependency in minimal_dependencies
        }
        return validate_lorepack(
            root,
            source_project=project,
            dependencies=dependencies,
            dependency_sources=dependency_sources,
            registered_extensions=registered_extensions,
        )
    except (CreationContractError, LorepackError) as exc:
        if isinstance(exc, LorepackError):
            raise
        raise LorepackError(str(exc)) from exc
