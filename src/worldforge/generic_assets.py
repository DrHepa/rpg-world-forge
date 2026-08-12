from __future__ import annotations

import copy
import hashlib
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from worldforge.asset_io import AssetContractError, write_json_atomic
from worldforge.creation_contracts import (
    CreationContractError,
    _exact_keys,
    _identifier,
    _identifier_array,
    _integer,
    _locale,
    _logic_runtime_string,
    _non_empty_string,
    _object,
    _portable_relative_path,
    _sha256,
    _string_array,
    _validate_json_structure,
    canonical_creation_hash,
    read_creation_object,
)
from worldforge.gamepack import (
    GAMEPACK_FORMAT,
    GAMEPACK_VERSION,
    GamepackError,
    PublishedGameArtifact,
    _published_artifact,
    load_gamepack,
    preflight_game_artifact_output,
    validate_gamepack_document,
)
from worldforge.generic_asset_limits import (
    MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS,
    MAX_GENERIC_ASSET_EVIDENCE,
)
from worldforge.integrity import canonical_json_bytes
from worldforge.validation_memo import memoize_document_validation

ASSET_SUBJECT_FORMAT = "world-forge.asset_subject"
ASSET_TARGET_FORMAT = "world-forge.asset_target"
ASSET_STYLE_FORMAT = "world-forge.asset_style"
ASSET_INVENTORY_FORMAT = "world-forge.asset_inventory"
ASSET_SPEC_FORMAT = "world-forge.asset_spec"
GENERIC_ASSET_VERSION = 1

MAX_GENERIC_ASSETS = 1024
MAX_GENERIC_ASSET_OUTPUTS = 4
MAX_GENERIC_ASSET_TEXT = 1024
MAX_GENERIC_ASSET_REFERENCES = 1024
MAX_GENERIC_ASSET_EXPANSION = 65_536
DERIVED_GENERIC_ASSET_ID_HASH_HEX = 48
GENERIC_ASSET_GLYPH_RANGE_PATTERN = r"^U\+[0-9A-F]{4,6}-[0-9A-F]{4,6}$"
_GENERIC_ASSET_GLYPH_RANGE_RE = re.compile(GENERIC_ASSET_GLYPH_RANGE_PATTERN)
_DERIVED_ID_PREFIXES = frozenset(
    {
        "asset_subject",
        "asset_target",
        "asset_style",
        "asset_inventory",
        "asset_spec",
    }
)


class GenericAssetError(ValueError):
    """Raised when a generic gamepack asset transition fails closed."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


@dataclass(frozen=True, slots=True)
class GenericAssetMatrixEntry:
    kind: str
    representation: str
    selected_format: str
    outputs: tuple[tuple[str, str], ...]


def _matrix_entries() -> tuple[GenericAssetMatrixEntry, ...]:
    entries: list[GenericAssetMatrixEntry] = []

    def add(
        kinds: Sequence[str],
        representations: Sequence[str],
        selected_format: str,
        outputs: Sequence[tuple[str, str]],
    ) -> None:
        canonical = tuple(sorted(outputs, key=lambda item: item[0].encode("utf-8")))
        for kind in kinds:
            for representation in representations:
                entries.append(
                    GenericAssetMatrixEntry(
                        kind=kind,
                        representation=representation,
                        selected_format=selected_format,
                        outputs=canonical,
                    )
                )

    add(
        ("ui", "portrait", "sprite", "vfx"),
        ("2d", "2_5d"),
        "asset:png",
        (("texture", "image/png"),),
    )
    add(
        ("spritesheet", "tileset"),
        ("2d", "2_5d"),
        "asset:png",
        (("clipset", "application/json"), ("texture", "image/png")),
    )
    add(
        ("font",),
        ("2d", "2_5d"),
        "asset:font",
        (("font", "font/ttf"),),
    )
    add(
        ("font",),
        ("2d", "2_5d"),
        "asset:font",
        (("font", "font/otf"),),
    )
    add(
        ("sfx", "music"),
        ("audio",),
        "asset:wav",
        (("audio", "audio/wav"),),
    )
    add(
        ("shader",),
        ("2d", "2_5d", "3d"),
        "asset:glsl",
        (
            ("fragment_shader", "text/x-glsl"),
            ("vertex_shader", "text/x-glsl"),
        ),
    )
    add(
        ("localization",),
        ("text",),
        "asset:json",
        (("localized_text", "application/json"),),
    )
    add(
        ("animation_3d",),
        ("3d",),
        "asset:glb",
        (("animation", "model/gltf-binary"),),
    )
    add(
        ("collision_3d",),
        ("3d",),
        "asset:glb",
        (("collision", "model/gltf-binary"),),
    )
    add(
        ("rig",),
        ("3d",),
        "asset:glb",
        (("skeleton", "model/gltf-binary"),),
    )
    add(
        ("character_3d",),
        ("3d",),
        "asset:glb",
        (
            ("model", "model/gltf-binary"),
            ("skeleton", "model/gltf-binary"),
        ),
    )
    add(
        ("environment_3d", "model_3d", "material_set", "vfx_3d"),
        ("3d",),
        "asset:glb",
        (("model", "model/gltf-binary"),),
    )
    return tuple(
        sorted(
            entries,
            key=lambda entry: (
                entry.kind.encode("utf-8"),
                entry.representation.encode("utf-8"),
                entry.selected_format.encode("utf-8"),
                entry.outputs,
            ),
        )
    )


GENERIC_ASSET_MATRIX = _matrix_entries()
_MATRIX_KEYS = {
    (entry.kind, entry.representation, entry.selected_format, entry.outputs)
    for entry in GENERIC_ASSET_MATRIX
}
_MATRIX_KINDS = frozenset(entry.kind for entry in GENERIC_ASSET_MATRIX)
_MATRIX_REPRESENTATIONS = frozenset(entry.representation for entry in GENERIC_ASSET_MATRIX)
_MATRIX_FORMATS = frozenset(entry.selected_format for entry in GENERIC_ASSET_MATRIX)
_OUTPUT_MEDIA = {
    role: frozenset(
        media
        for entry in GENERIC_ASSET_MATRIX
        for output_role, media in entry.outputs
        if output_role == role
    )
    for role in {output_role for entry in GENERIC_ASSET_MATRIX for output_role, _ in entry.outputs}
}

_IDENTITY_FIELDS = frozenset({"format", "format_version", "id", "content_hash"})
_SUBJECT_TUPLE_FIELDS = frozenset({"kind", "format", "format_version", "id", "content_hash"})
_SUBJECT_FIELDS = frozenset({"format", "format_version", "subject_id", "subject", "content_hash"})
_TARGET_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "target_id",
        "asset_subject",
        "gamepack",
        "review",
        "bindings",
        "content_hash",
    }
)
_REVIEW_FIELDS = frozenset({"reviewer_id", "rationale", "evidence"})
_EVIDENCE_FIELDS = frozenset({"evidence_id", "content_hash"})
_BINDING_FIELDS = frozenset(
    {
        "binding_id",
        "required",
        "roles",
        "usage_contexts",
        "referencing_subjects",
        "asset_id",
        "selected_format",
        "kind",
        "representation",
        "outputs",
        "sharing",
    }
)
_REFERENCE_FIELDS = frozenset({"kind", "id"})
_OUTPUT_CHOICE_FIELDS = frozenset({"role", "media_type"})
_SHARING_FIELDS = frozenset({"policy", "group_id"})
_FORBIDDEN_FIELDS = frozenset(
    {
        "absolute_path",
        "authoring_path",
        "callback",
        "command",
        "credential",
        "credentials",
        "endpoint",
        "executable",
        "executable_script",
        "expression",
        "import",
        "javascript",
        "model",
        "model_id",
        "mutable_path",
        "native_code",
        "project_path",
        "prompt",
        "provider",
        "provider_credentials",
        "provider_details",
        "provider_id",
        "python",
        "receipt",
        "runtime_ai",
        "script",
        "source_path",
        "token",
        "tool",
        "url",
    }
)


def _fail(reason_code: str, detail: str) -> None:
    raise GenericAssetError(reason_code, detail)


def _canonical_hash(document: Mapping[str, object]) -> str:
    try:
        return canonical_creation_hash(document)
    except CreationContractError as exc:
        _fail("asset_contract_invalid", str(exc))


def _derived_contract_id(prefix: str, seed: object) -> str:
    if prefix not in _DERIVED_ID_PREFIXES:
        _fail("asset_id_derivation_invalid", "derived ID prefix is unsupported")
    try:
        digest = hashlib.sha256(canonical_json_bytes(seed)).hexdigest()
    except (TypeError, ValueError) as exc:
        _fail("asset_id_derivation_invalid", str(exc))
    candidate = f"{prefix}_{digest[:DERIVED_GENERIC_ASSET_ID_HASH_HEX]}"
    try:
        return _identifier(candidate, f"derived {prefix} ID")
    except CreationContractError as exc:
        _fail("asset_id_derivation_invalid", str(exc))


def _identity(
    document: Mapping[str, object],
    *,
    id_field: str,
) -> dict[str, object]:
    return {
        "format": document["format"],
        "format_version": document["format_version"],
        "id": document[id_field],
        "content_hash": document["content_hash"],
    }


def _gamepack_identity(gamepack: Mapping[str, object]) -> dict[str, object]:
    game = gamepack["game"]
    assert isinstance(game, Mapping)
    return {
        "format": gamepack["format"],
        "format_version": gamepack["format_version"],
        "id": game["id"],
        "content_hash": gamepack["content_hash"],
    }


def _validate_identity(
    value: object,
    context: str,
    *,
    expected_format: str | None = None,
    expected_version: int | None = None,
) -> dict[str, Any]:
    identity = _object(value, context)
    _exact_keys(identity, _IDENTITY_FIELDS, context)
    format_name = _non_empty_string(identity.get("format"), f"{context}.format")
    version = _integer(identity.get("format_version"), f"{context}.format_version", minimum=1)
    _identifier(identity.get("id"), f"{context}.id")
    _sha256(identity.get("content_hash"), f"{context}.content_hash")
    if expected_format is not None and format_name != expected_format:
        _fail("asset_identity_mismatch", f"{context}.format does not match {expected_format}")
    if expected_version is not None and version != expected_version:
        _fail(
            "asset_identity_mismatch",
            f"{context}.format_version does not match {expected_version}",
        )
    return identity


def _bounded_text(value: object, context: str, *, maximum: int = MAX_GENERIC_ASSET_TEXT) -> str:
    text = _non_empty_string(value, context)
    if len(text) > maximum:
        _fail("asset_contract_limit", f"{context} exceeds the {maximum}-character limit")
    try:
        _logic_runtime_string(text, context)
    except CreationContractError as exc:
        _fail("unsafe_asset_contract", str(exc))
    return text


def _validate_content_hash(document: Mapping[str, object], context: str) -> None:
    _sha256(document.get("content_hash"), f"{context}.content_hash")
    if document["content_hash"] != _canonical_hash(document):
        _fail("content_hash_mismatch", f"{context}.content_hash is not canonical")


def _reject_unsafe_fields(value: object, *, context: str) -> None:
    try:
        _validate_json_structure(value, context=context)
    except CreationContractError as exc:
        _fail("asset_contract_invalid", str(exc))
    stack: list[tuple[str, object, str | None]] = [(context, value, None)]
    while stack:
        current_context, current, parent_key = stack.pop()
        if isinstance(current, dict):
            for key, child in current.items():
                folded = key.casefold().replace("-", "_")
                if folded in _FORBIDDEN_FIELDS:
                    _fail(
                        "forbidden_asset_field",
                        f"{current_context}.{key} is an authoring/provider/executable field",
                    )
                stack.append((f"{current_context}.{key}", child, folded))
        elif isinstance(current, list):
            stack.extend(
                (f"{current_context}/{index}", child, parent_key)
                for index, child in enumerate(current)
            )
        elif isinstance(current, str) and parent_key != "runtime_path":
            if len(current) > MAX_GENERIC_ASSET_TEXT:
                _fail(
                    "asset_contract_limit",
                    f"{current_context} exceeds the runtime string limit",
                )
            try:
                _logic_runtime_string(current, current_context)
            except CreationContractError as exc:
                _fail("unsafe_asset_contract", str(exc))


def _canonical_ids(values: Sequence[str], context: str) -> None:
    if list(values) != sorted(values, key=lambda item: item.encode("utf-8")):
        _fail("noncanonical_asset_contract", f"{context} must use canonical sorted order")
    if len({item.casefold() for item in values}) != len(values):
        _fail("asset_id_collision", f"{context} contains an NFC/casefold collision")


def _validate_runtime_path_tree(paths: Sequence[str], context: str) -> None:
    file_paths: dict[tuple[str, ...], str] = {}
    prefixes: dict[tuple[str, ...], str] = {}
    for path in paths:
        if not path.isascii():
            _fail(
                "asset_spec_portable_path_invalid",
                f"{context} paths must use printable ASCII for cross-platform portability",
            )
        relative = PurePosixPath(path)
        components = tuple(
            unicodedata.normalize("NFC", component).casefold() for component in relative.parts
        )
        previous_file = file_paths.get(components)
        if previous_file is not None:
            _fail(
                "asset_spec_path_collision",
                f"{context} contains an NFC/casefold path collision: {previous_file!r}, {path!r}",
            )
        exact_components: list[str] = []
        for index, component in enumerate(relative.parts):
            exact_components.append(component)
            key = components[: index + 1]
            exact_prefix = "/".join(exact_components)
            previous_prefix = prefixes.setdefault(key, exact_prefix)
            if previous_prefix != exact_prefix:
                _fail(
                    "asset_spec_path_collision",
                    f"{context} contains an NFC/casefold component-prefix collision: "
                    f"{previous_prefix!r}, {exact_prefix!r}",
                )
            if index + 1 < len(components) and key in file_paths:
                _fail(
                    "asset_spec_path_collision",
                    f"{context} contains a file/directory prefix collision: "
                    f"{file_paths[key]!r}, {path!r}",
                )
        for existing_key, existing_path in file_paths.items():
            if (
                len(components) < len(existing_key)
                and existing_key[: len(components)] == components
            ):
                _fail(
                    "asset_spec_path_collision",
                    f"{context} contains a file/directory prefix collision: "
                    f"{path!r}, {existing_path!r}",
                )
        file_paths[components] = path


def _validate_review(value: object, context: str) -> dict[str, Any]:
    review = _object(value, context)
    _exact_keys(review, _REVIEW_FIELDS, context)
    _identifier(review.get("reviewer_id"), f"{context}.reviewer_id")
    _bounded_text(review.get("rationale"), f"{context}.rationale")
    evidence = review.get("evidence")
    if not isinstance(evidence, list) or not evidence or len(evidence) > MAX_GENERIC_ASSET_EVIDENCE:
        _fail(
            "asset_review_invalid",
            f"{context}.evidence must be a bounded non-empty array",
        )
    evidence_ids: list[str] = []
    for index, raw in enumerate(evidence):
        item_context = f"{context}.evidence/{index}"
        item = _object(raw, item_context)
        _exact_keys(item, _EVIDENCE_FIELDS, item_context)
        evidence_ids.append(_identifier(item.get("evidence_id"), f"{item_context}.evidence_id"))
        _sha256(item.get("content_hash"), f"{item_context}.content_hash")
    _canonical_ids(evidence_ids, f"{context}.evidence")
    return review


def _validate_subject_structure(value: object) -> dict[str, Any]:
    document = _object(value, "asset subject")
    _exact_keys(document, _SUBJECT_FIELDS, "asset subject")
    if document.get("format") != ASSET_SUBJECT_FORMAT:
        _fail("asset_subject_format_invalid", f"format must be {ASSET_SUBJECT_FORMAT}")
    if document.get("format_version") != GENERIC_ASSET_VERSION:
        _fail("asset_subject_version_invalid", "asset subject version must be 1")
    subject_id = _identifier(document.get("subject_id"), "asset subject.subject_id")
    subject = _object(document.get("subject"), "asset subject.subject")
    _exact_keys(subject, _SUBJECT_TUPLE_FIELDS, "asset subject.subject")
    kind = subject.get("kind")
    if kind not in {"gamepack", "legacy_worldpack"}:
        _fail("asset_subject_kind_invalid", "subject kind is unsupported")
    format_name = _non_empty_string(subject.get("format"), "asset subject.subject.format")
    version = _integer(
        subject.get("format_version"),
        "asset subject.subject.format_version",
        minimum=1,
    )
    source_id = _identifier(subject.get("id"), "asset subject.subject.id")
    _sha256(subject.get("content_hash"), "asset subject.subject.content_hash")
    if kind == "gamepack":
        if format_name != GAMEPACK_FORMAT or version != GAMEPACK_VERSION:
            _fail(
                "asset_subject_format_invalid",
                "gamepack subjects must be world-forge.gamepack@1",
            )
    else:
        if format_name != "isoworld.worldpack" or version not in {1, 2, 3, 4, 5}:
            _fail(
                "asset_subject_format_invalid",
                "legacy_worldpack subjects recognize only isoworld.worldpack@1..5",
            )
    expected_subject_id = _derived_contract_id(
        "asset_subject",
        {
            "kind": kind,
            "format": format_name,
            "format_version": version,
            "id": source_id,
            "content_hash": subject["content_hash"],
        },
    )
    if subject_id != expected_subject_id:
        _fail(
            "asset_subject_id_mismatch",
            "subject_id must be the canonical subject-tuple-derived ID",
        )
    _validate_content_hash(document, "asset subject")
    _reject_unsafe_fields(document, context="asset subject")
    return copy.deepcopy(document)


def build_asset_subject(gamepack: object) -> dict[str, Any]:
    checked = validate_gamepack_document(gamepack)
    game = checked["game"]
    subject_tuple = {
        "kind": "gamepack",
        "format": checked["format"],
        "format_version": checked["format_version"],
        "id": game["id"],
        "content_hash": checked["content_hash"],
    }
    document: dict[str, Any] = {
        "format": ASSET_SUBJECT_FORMAT,
        "format_version": GENERIC_ASSET_VERSION,
        "subject_id": _derived_contract_id("asset_subject", subject_tuple),
        "subject": subject_tuple,
        "content_hash": "",
    }
    document["content_hash"] = _canonical_hash(document)
    return _validate_subject_structure(document)


def validate_asset_subject_document(value: object) -> dict[str, Any]:
    try:
        document = _validate_subject_structure(value)
    except GenericAssetError:
        raise
    except (CreationContractError, TypeError, ValueError) as exc:
        _fail("asset_subject_invalid", str(exc))
    return copy.deepcopy(document)


def _validate_asset_subject_uncached(
    value: object,
    *,
    gamepack: object,
) -> dict[str, Any]:
    document = validate_asset_subject_document(value)
    try:
        checked_gamepack = validate_gamepack_document(gamepack)
        if document["subject"]["kind"] != "gamepack":
            _fail(
                "gamepack_subject_required",
                "the generic D1 derivation path requires a gamepack subject",
            )
        if document["subject"] != {
            "kind": "gamepack",
            **_gamepack_identity(checked_gamepack),
        }:
            _fail(
                "asset_subject_mismatch",
                "asset subject tuple does not match the integrally validated gamepack",
            )
    except GenericAssetError:
        raise
    except (CreationContractError, TypeError, ValueError) as exc:
        _fail("asset_subject_invalid", str(exc))
    return copy.deepcopy(document)


def validate_asset_subject(
    value: object,
    *,
    gamepack: object,
) -> dict[str, Any]:
    return memoize_document_validation(
        "validate_asset_subject",
        value,
        lambda candidate: _validate_asset_subject_uncached(
            candidate,
            gamepack=gamepack,
        ),
        dependencies=(gamepack,),
    )


def _validate_outputs(value: object, context: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list) or not value or len(value) > MAX_GENERIC_ASSET_OUTPUTS:
        _fail("asset_matrix_invalid", f"{context} must be a bounded non-empty array")
    outputs: list[tuple[str, str]] = []
    for index, raw in enumerate(value):
        item_context = f"{context}/{index}"
        output = _object(raw, item_context)
        _exact_keys(output, _OUTPUT_CHOICE_FIELDS, item_context)
        role = _identifier(output.get("role"), f"{item_context}.role")
        media = _non_empty_string(output.get("media_type"), f"{item_context}.media_type")
        if role not in _OUTPUT_MEDIA or media not in _OUTPUT_MEDIA[role]:
            _fail(
                "asset_matrix_invalid",
                f"{item_context} is not a supported runtime role/media pair",
            )
        outputs.append((role, media))
    expected = sorted(outputs, key=lambda item: item[0].encode("utf-8"))
    if outputs != expected:
        _fail("noncanonical_asset_contract", f"{context} must use canonical role order")
    if len({role.casefold() for role, _ in outputs}) != len(outputs):
        _fail("asset_matrix_invalid", f"{context} contains duplicate output roles")
    return tuple(outputs)


def _validate_sharing(value: object, context: str) -> dict[str, Any]:
    sharing = _object(value, context)
    _exact_keys(sharing, _SHARING_FIELDS, context)
    policy = sharing.get("policy")
    group_id = sharing.get("group_id")
    if policy == "exclusive":
        if group_id is not None:
            _fail("asset_sharing_invalid", f"{context}.group_id must be null for exclusive")
    elif policy == "shared_exact":
        _identifier(group_id, f"{context}.group_id")
    else:
        _fail("asset_sharing_invalid", f"{context}.policy is unsupported")
    return sharing


def _validate_referencing_subjects(value: object, context: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > MAX_GENERIC_ASSET_REFERENCES:
        _fail("asset_binding_invalid", f"{context} must be a bounded non-empty array")
    checked: list[dict[str, Any]] = []
    keys: list[tuple[str, str]] = []
    for index, raw in enumerate(value):
        item_context = f"{context}/{index}"
        item = _object(raw, item_context)
        _exact_keys(item, _REFERENCE_FIELDS, item_context)
        kind = _identifier(item.get("kind"), f"{item_context}.kind")
        subject_id = _identifier(item.get("id"), f"{item_context}.id")
        keys.append((kind, subject_id))
        checked.append(item)
    if keys != sorted(keys, key=lambda item: (item[0].encode(), item[1].encode())):
        _fail("noncanonical_asset_contract", f"{context} must use canonical order")
    if len({(kind.casefold(), item.casefold()) for kind, item in keys}) != len(keys):
        _fail("asset_binding_invalid", f"{context} contains duplicate references")
    return checked


def _validate_binding_structure(value: object, context: str) -> dict[str, Any]:
    binding = _object(value, context)
    _exact_keys(binding, _BINDING_FIELDS, context)
    _identifier(binding.get("binding_id"), f"{context}.binding_id")
    if not isinstance(binding.get("required"), bool):
        _fail("asset_binding_invalid", f"{context}.required must be boolean")
    roles = _identifier_array(binding.get("roles"), f"{context}.roles", allow_empty=False)
    contexts = _string_array(
        binding.get("usage_contexts"),
        f"{context}.usage_contexts",
        allow_empty=False,
        canonical_order=True,
    )
    if len(roles) > MAX_GENERIC_ASSET_REFERENCES or len(contexts) > MAX_GENERIC_ASSET_REFERENCES:
        _fail("asset_contract_limit", f"{context} source arrays exceed their limits")
    _validate_referencing_subjects(
        binding.get("referencing_subjects"),
        f"{context}.referencing_subjects",
    )
    _identifier(binding.get("asset_id"), f"{context}.asset_id")
    selected_format = _non_empty_string(
        binding.get("selected_format"),
        f"{context}.selected_format",
    )
    if selected_format not in _MATRIX_FORMATS:
        _fail("asset_matrix_invalid", f"{context}.selected_format is unsupported")
    kind = _identifier(binding.get("kind"), f"{context}.kind")
    representation = _non_empty_string(
        binding.get("representation"),
        f"{context}.representation",
    )
    if kind not in _MATRIX_KINDS or representation not in _MATRIX_REPRESENTATIONS:
        _fail("asset_matrix_invalid", f"{context} kind/representation is unsupported")
    outputs = _validate_outputs(binding.get("outputs"), f"{context}.outputs")
    if (kind, representation, selected_format, outputs) not in _MATRIX_KEYS:
        _fail(
            "asset_matrix_invalid",
            f"{context} kind/representation/format/output combination is incomplete or impossible",
        )
    _validate_sharing(binding.get("sharing"), f"{context}.sharing")
    return binding


def _preflight_bindings(value: object) -> list[object]:
    if not isinstance(value, list):
        _fail("asset_target_preflight", "bindings must be an array")
    if len(value) > MAX_GENERIC_ASSETS:
        _fail(
            "asset_target_preflight_limit",
            f"bindings exceeds the {MAX_GENERIC_ASSETS}-item preflight limit",
        )
    expansion = 0
    for raw in value:
        if isinstance(raw, Mapping):
            for field in ("roles", "usage_contexts", "referencing_subjects", "outputs"):
                candidate = raw.get(field)
                if isinstance(candidate, list):
                    expansion += len(candidate)
                    if expansion > MAX_GENERIC_ASSET_EXPANSION:
                        _fail(
                            "asset_target_preflight_limit",
                            "binding fan-out exceeds the pre-expansion limit",
                        )
    return value


def _validate_target_structure(value: object) -> dict[str, Any]:
    document = _object(value, "asset target")
    _exact_keys(document, _TARGET_FIELDS, "asset target")
    if document.get("format") != ASSET_TARGET_FORMAT:
        _fail("asset_target_format_invalid", f"format must be {ASSET_TARGET_FORMAT}")
    if document.get("format_version") != GENERIC_ASSET_VERSION:
        _fail("asset_target_version_invalid", "asset target version must be 1")
    _identifier(document.get("target_id"), "asset target.target_id")
    _validate_identity(
        document.get("asset_subject"),
        "asset target.asset_subject",
        expected_format=ASSET_SUBJECT_FORMAT,
        expected_version=1,
    )
    _validate_identity(
        document.get("gamepack"),
        "asset target.gamepack",
        expected_format=GAMEPACK_FORMAT,
        expected_version=1,
    )
    _validate_review(document.get("review"), "asset target.review")
    raw_bindings = _preflight_bindings(document.get("bindings"))
    if not raw_bindings:
        _fail(
            "asset_target_coverage_mismatch",
            "bindings must exactly cover the non-empty gamepack asset requirements",
        )
    binding_ids: list[str] = []
    for index, raw in enumerate(raw_bindings):
        binding = _validate_binding_structure(raw, f"asset target.bindings/{index}")
        binding_ids.append(binding["binding_id"])
    _canonical_ids(binding_ids, "asset target.bindings")
    _validate_sharing_groups(document["bindings"])
    _validate_content_hash(document, "asset target")
    _reject_unsafe_fields(document, context="asset target")
    return copy.deepcopy(document)


def _validate_sharing_groups(bindings: Sequence[Mapping[str, Any]]) -> None:
    by_asset: dict[str, list[Mapping[str, Any]]] = {}
    for binding in bindings:
        by_asset.setdefault(str(binding["asset_id"]).casefold(), []).append(binding)
    global_edges = 0
    global_nodes = len(bindings) + len(by_asset)
    for members in by_asset.values():
        first = members[0]
        binding_ids: set[str] = set()
        roles: set[str] = set()
        usage_contexts: set[str] = set()
        referencing_subjects: set[tuple[str, str]] = set()
        for member in members:
            binding_ids.add(member["binding_id"].casefold())
            roles.update(role.casefold() for role in member["roles"])
            usage_contexts.update(context.casefold() for context in member["usage_contexts"])
            referencing_subjects.update(
                (subject["kind"].casefold(), subject["id"].casefold())
                for subject in member["referencing_subjects"]
            )
            global_edges += (
                len(member["roles"])
                + len(member["usage_contexts"])
                + len(member["referencing_subjects"])
                + len(member["outputs"])
            )
            for label, union in (
                ("binding IDs", binding_ids),
                ("roles", roles),
                ("usage contexts", usage_contexts),
                ("referencing subjects", referencing_subjects),
            ):
                if len(union) > MAX_GENERIC_ASSET_REFERENCES:
                    _fail(
                        "asset_sharing_expansion_limit",
                        f"asset {first['asset_id']} shared {label} exceed "
                        f"{MAX_GENERIC_ASSET_REFERENCES}",
                    )
        global_nodes += (
            len(binding_ids) + len(roles) + len(usage_contexts) + len(referencing_subjects)
        )
        if global_edges > MAX_GENERIC_ASSET_EXPANSION or global_nodes > MAX_GENERIC_ASSET_EXPANSION:
            _fail(
                "asset_sharing_expansion_limit",
                "target sharing graph exceeds the global edge or node limit",
            )
        if len(members) == 1:
            if first["sharing"]["policy"] == "shared_exact":
                _fail(
                    "asset_sharing_invalid",
                    f"asset {first['asset_id']} declares sharing without another binding",
                )
            continue
        if any(member["sharing"]["policy"] != "shared_exact" for member in members):
            _fail(
                "asset_sharing_invalid",
                f"asset {first['asset_id']} sharing must be explicit for every binding",
            )
        group_ids = {member["sharing"]["group_id"].casefold() for member in members}
        if len(group_ids) != 1:
            _fail(
                "asset_sharing_invalid",
                f"asset {first['asset_id']} bindings use inconsistent sharing groups",
            )
        physical = {
            (
                member["kind"],
                member["representation"],
                member["selected_format"],
                tuple((output["role"], output["media_type"]) for output in member["outputs"]),
            )
            for member in members
        }
        if len(physical) != 1:
            _fail(
                "asset_sharing_incompatible",
                f"asset {first['asset_id']} has incompatible physical choices",
            )


def _exact_requirement_binding(
    requirement: Mapping[str, Any],
    selection: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "binding_id": requirement["binding_id"],
        "required": requirement["required"],
        "roles": copy.deepcopy(requirement["roles"]),
        "usage_contexts": copy.deepcopy(requirement["usage_contexts"]),
        "referencing_subjects": copy.deepcopy(requirement["referencing_subjects"]),
        "asset_id": selection["asset_id"],
        "selected_format": selection["selected_format"],
        "kind": selection["kind"],
        "representation": selection["representation"],
        "outputs": copy.deepcopy(selection["outputs"]),
        "sharing": copy.deepcopy(selection["sharing"]),
    }


def build_asset_target(
    gamepack: object,
    subject: object,
    *,
    review: object,
    bindings: object,
    target_id: str | None = None,
) -> dict[str, Any]:
    checked_gamepack = validate_gamepack_document(gamepack)
    checked_subject = validate_asset_subject(subject, gamepack=checked_gamepack)
    raw_bindings = _preflight_bindings(bindings)
    by_id: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(raw_bindings):
        selection = _object(raw, f"asset target selection/{index}")
        selection_fields = _BINDING_FIELDS - {
            "required",
            "roles",
            "usage_contexts",
            "referencing_subjects",
        }
        _exact_keys(selection, selection_fields, f"asset target selection/{index}")
        binding_id = _identifier(
            selection.get("binding_id"),
            f"asset target selection/{index}.binding_id",
        )
        key = binding_id.casefold()
        if key in by_id:
            _fail("asset_binding_duplicate", f"duplicate binding selection {binding_id}")
        by_id[key] = selection
    requirements = checked_gamepack["asset_requirements"]
    requirement_keys = {item["binding_id"].casefold() for item in requirements}
    if set(by_id) != requirement_keys:
        _fail(
            "asset_target_coverage_mismatch",
            "binding selections must exactly cover every gamepack asset requirement",
        )
    exact_bindings: list[dict[str, Any]] = []
    for requirement in requirements:
        selection = by_id[requirement["binding_id"].casefold()]
        if selection["selected_format"] not in requirement["accepted_formats"]:
            _fail(
                "asset_target_format_mismatch",
                f"binding {requirement['binding_id']} selected_format is not in accepted_formats",
            )
        exact_bindings.append(_exact_requirement_binding(requirement, selection))
    exact_bindings.sort(key=lambda item: item["binding_id"].encode("utf-8"))
    asset_subject_identity = _identity(checked_subject, id_field="subject_id")
    gamepack_identity = _gamepack_identity(checked_gamepack)
    target_seed = {
        "asset_subject": asset_subject_identity,
        "gamepack": gamepack_identity,
        "review": copy.deepcopy(review),
        "bindings": exact_bindings,
    }
    document: dict[str, Any] = {
        "format": ASSET_TARGET_FORMAT,
        "format_version": GENERIC_ASSET_VERSION,
        "target_id": target_id or _derived_contract_id("asset_target", target_seed),
        "asset_subject": asset_subject_identity,
        "gamepack": gamepack_identity,
        "review": copy.deepcopy(review),
        "bindings": exact_bindings,
        "content_hash": "",
    }
    document["content_hash"] = _canonical_hash(document)
    return validate_asset_target(
        document,
        gamepack=checked_gamepack,
        subject=checked_subject,
    )


def validate_asset_target_document(value: object) -> dict[str, Any]:
    try:
        document = _validate_target_structure(value)
    except GenericAssetError:
        raise
    except (CreationContractError, TypeError, ValueError) as exc:
        _fail("asset_target_invalid", str(exc))
    return copy.deepcopy(document)


def _validate_asset_target_uncached(
    value: object,
    *,
    gamepack: object,
    subject: object,
) -> dict[str, Any]:
    document = validate_asset_target_document(value)
    try:
        checked_gamepack = validate_gamepack_document(gamepack)
        checked_subject = validate_asset_subject(subject, gamepack=checked_gamepack)
        if document["asset_subject"] != _identity(
            checked_subject,
            id_field="subject_id",
        ):
            _fail("asset_target_subject_mismatch", "target asset_subject identity mismatch")
        if document["gamepack"] != _gamepack_identity(checked_gamepack):
            _fail("asset_target_gamepack_mismatch", "target gamepack identity mismatch")
        requirements = {item["binding_id"]: item for item in checked_gamepack["asset_requirements"]}
        bindings = {item["binding_id"]: item for item in document["bindings"]}
        if set(bindings) != set(requirements):
            _fail(
                "asset_target_coverage_mismatch",
                "target bindings must exactly cover gamepack requirements",
            )
        for binding_id, requirement in requirements.items():
            binding = bindings[binding_id]
            for field in (
                "binding_id",
                "required",
                "roles",
                "usage_contexts",
                "referencing_subjects",
            ):
                if binding[field] != requirement[field]:
                    _fail(
                        "asset_target_requirement_mismatch",
                        f"binding {binding_id} does not preserve exact {field}",
                    )
            if binding["selected_format"] not in requirement["accepted_formats"]:
                _fail(
                    "asset_target_format_mismatch",
                    f"binding {binding_id} selected_format is not in accepted_formats",
                )
    except GenericAssetError:
        raise
    except (CreationContractError, TypeError, ValueError) as exc:
        _fail("asset_target_invalid", str(exc))
    return copy.deepcopy(document)


def validate_asset_target(
    value: object,
    *,
    gamepack: object,
    subject: object,
) -> dict[str, Any]:
    return memoize_document_validation(
        "validate_asset_target",
        value,
        lambda candidate: _validate_asset_target_uncached(
            candidate,
            gamepack=gamepack,
            subject=subject,
        ),
        dependencies=(gamepack, subject),
    )


_STYLE_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "style_id",
        "asset_subject",
        "target",
        "gamepack",
        "review",
        "visual",
        "audio",
        "content_hash",
    }
)
_VISUAL_FIELDS = frozenset(
    {
        "presentation_mode",
        "visual_language",
        "camera",
        "coordinate_system",
        "reference_resolution",
        "aspect_ratio",
        "palette",
        "readability",
        "typography",
        "motion",
        "ui",
        "accessibility",
        "localization",
    }
)
_DIMENSIONS_FIELDS = frozenset({"width", "height"})
_PALETTE_FIELDS = frozenset({"direction", "minimum_contrast_ratio", "color_independent"})
_READABILITY_FIELDS = frozenset({"silhouette_direction", "minimum_feature_pixels"})
_TYPOGRAPHY_FIELDS = frozenset({"direction", "minimum_text_scale_percent"})
_MOTION_FIELDS = frozenset({"direction", "reduced_motion"})
_UI_FIELDS = frozenset({"hierarchy", "density"})
_ACCESSIBILITY_FIELDS = frozenset({"captions", "screen_reader_structure", "keyboard_only"})
_LOCALIZATION_STYLE_FIELDS = frozenset(
    {
        "source_locale",
        "supported_locales",
        "expansion_budget_percent",
    }
)
_AUDIO_NOT_APPLICABLE_FIELDS = frozenset({"status", "rationale"})
_AUDIO_DEFINED_FIELDS = frozenset(
    {
        "status",
        "role_direction",
        "mix_direction",
        "music_direction",
        "sfx_direction",
        "voice_direction",
        "caption_direction",
        "runtime_formats",
    }
)
_PRESENTATION_MODES = frozenset({"text", "2d", "2_5d", "3d", "mixed", "vr", "ar"})
_COORDINATE_SYSTEMS = frozenset({"text_flow", "screen_2d", "world_2_5d", "world_3d", "mixed"})


def _validate_dimensions(
    value: object,
    context: str,
    *,
    maximum: int,
) -> dict[str, Any]:
    dimensions = _object(value, context)
    _exact_keys(dimensions, _DIMENSIONS_FIELDS, context)
    width = _integer(dimensions.get("width"), f"{context}.width", minimum=1)
    height = _integer(dimensions.get("height"), f"{context}.height", minimum=1)
    if width > maximum or height > maximum:
        _fail("asset_style_invalid", f"{context} exceeds the {maximum}-unit limit")
    return dimensions


def _validate_visual(value: object, context: str) -> dict[str, Any]:
    visual = _object(value, context)
    _exact_keys(visual, _VISUAL_FIELDS, context)
    if visual.get("presentation_mode") not in _PRESENTATION_MODES:
        _fail("asset_style_invalid", f"{context}.presentation_mode is unsupported")
    _bounded_text(visual.get("visual_language"), f"{context}.visual_language")
    _bounded_text(visual.get("camera"), f"{context}.camera", maximum=256)
    if visual.get("coordinate_system") not in _COORDINATE_SYSTEMS:
        _fail("asset_style_invalid", f"{context}.coordinate_system is unsupported")
    _validate_dimensions(
        visual.get("reference_resolution"),
        f"{context}.reference_resolution",
        maximum=16_384,
    )
    aspect = _validate_dimensions(
        visual.get("aspect_ratio"),
        f"{context}.aspect_ratio",
        maximum=1_000,
    )
    resolution = visual["reference_resolution"]
    if resolution["width"] * aspect["height"] != resolution["height"] * aspect["width"]:
        _fail(
            "asset_style_invalid",
            f"{context}.aspect_ratio does not match reference_resolution",
        )
    palette = _object(visual.get("palette"), f"{context}.palette")
    _exact_keys(palette, _PALETTE_FIELDS, f"{context}.palette")
    _bounded_text(palette.get("direction"), f"{context}.palette.direction")
    contrast = _integer(
        palette.get("minimum_contrast_ratio"),
        f"{context}.palette.minimum_contrast_ratio",
        minimum=1,
    )
    if contrast > 21:
        _fail(
            "asset_style_invalid",
            f"{context}.palette.minimum_contrast_ratio exceeds 21",
        )
    if not isinstance(palette.get("color_independent"), bool):
        _fail("asset_style_invalid", f"{context}.palette.color_independent must be boolean")
    readability = _object(visual.get("readability"), f"{context}.readability")
    _exact_keys(readability, _READABILITY_FIELDS, f"{context}.readability")
    _bounded_text(
        readability.get("silhouette_direction"),
        f"{context}.readability.silhouette_direction",
    )
    feature_pixels = _integer(
        readability.get("minimum_feature_pixels"),
        f"{context}.readability.minimum_feature_pixels",
        minimum=1,
    )
    if feature_pixels > 1024:
        _fail("asset_style_invalid", f"{context}.readability minimum is excessive")
    typography = _object(visual.get("typography"), f"{context}.typography")
    _exact_keys(typography, _TYPOGRAPHY_FIELDS, f"{context}.typography")
    _bounded_text(typography.get("direction"), f"{context}.typography.direction")
    scale = _integer(
        typography.get("minimum_text_scale_percent"),
        f"{context}.typography.minimum_text_scale_percent",
        minimum=100,
    )
    if scale > 400:
        _fail("asset_style_invalid", f"{context}.typography scale exceeds 400 percent")
    motion = _object(visual.get("motion"), f"{context}.motion")
    _exact_keys(motion, _MOTION_FIELDS, f"{context}.motion")
    _bounded_text(motion.get("direction"), f"{context}.motion.direction")
    if not isinstance(motion.get("reduced_motion"), bool):
        _fail("asset_style_invalid", f"{context}.motion.reduced_motion must be boolean")
    ui = _object(visual.get("ui"), f"{context}.ui")
    _exact_keys(ui, _UI_FIELDS, f"{context}.ui")
    _bounded_text(ui.get("hierarchy"), f"{context}.ui.hierarchy")
    _bounded_text(ui.get("density"), f"{context}.ui.density", maximum=256)
    accessibility = _object(visual.get("accessibility"), f"{context}.accessibility")
    _exact_keys(accessibility, _ACCESSIBILITY_FIELDS, f"{context}.accessibility")
    for field in _ACCESSIBILITY_FIELDS:
        if not isinstance(accessibility.get(field), bool):
            _fail("asset_style_invalid", f"{context}.accessibility.{field} must be boolean")
    localization = _object(visual.get("localization"), f"{context}.localization")
    _exact_keys(localization, _LOCALIZATION_STYLE_FIELDS, f"{context}.localization")
    source_locale = _locale(
        localization.get("source_locale"),
        f"{context}.localization.source_locale",
    )
    locales = _string_array(
        localization.get("supported_locales"),
        f"{context}.localization.supported_locales",
        allow_empty=False,
        canonical_order=True,
    )
    if len(locales) > 64:
        _fail("asset_style_invalid", f"{context}.localization locale limit exceeded")
    for index, locale in enumerate(locales):
        _locale(locale, f"{context}.localization.supported_locales/{index}")
    if source_locale.casefold() not in {locale.casefold() for locale in locales}:
        _fail(
            "asset_style_invalid",
            f"{context}.localization.supported_locales omits source_locale",
        )
    expansion = _integer(
        localization.get("expansion_budget_percent"),
        f"{context}.localization.expansion_budget_percent",
        minimum=0,
    )
    if expansion > 200:
        _fail("asset_style_invalid", f"{context}.localization expansion exceeds 200 percent")
    return visual


def _validate_audio(value: object, context: str) -> dict[str, Any]:
    audio = _object(value, context)
    status = audio.get("status")
    if status == "not_applicable":
        _exact_keys(audio, _AUDIO_NOT_APPLICABLE_FIELDS, context)
        _bounded_text(audio.get("rationale"), f"{context}.rationale")
    elif status == "defined":
        _exact_keys(audio, _AUDIO_DEFINED_FIELDS, context)
        for field in (
            "role_direction",
            "mix_direction",
            "music_direction",
            "sfx_direction",
            "voice_direction",
            "caption_direction",
        ):
            _bounded_text(audio.get(field), f"{context}.{field}")
        formats = _string_array(
            audio.get("runtime_formats"),
            f"{context}.runtime_formats",
            allow_empty=False,
            canonical_order=True,
        )
        if formats != ["asset:wav"]:
            _fail(
                "asset_style_invalid",
                f"{context}.runtime_formats must be the closed PCM16 WAV format",
            )
    else:
        _fail("asset_style_invalid", f"{context}.status is unsupported")
    return audio


def _validate_style_structure(value: object) -> dict[str, Any]:
    document = _object(value, "asset style")
    _exact_keys(document, _STYLE_FIELDS, "asset style")
    if document.get("format") != ASSET_STYLE_FORMAT:
        _fail("asset_style_format_invalid", f"format must be {ASSET_STYLE_FORMAT}")
    if document.get("format_version") != GENERIC_ASSET_VERSION:
        _fail("asset_style_version_invalid", "asset style version must be 1")
    _identifier(document.get("style_id"), "asset style.style_id")
    _validate_identity(
        document.get("asset_subject"),
        "asset style.asset_subject",
        expected_format=ASSET_SUBJECT_FORMAT,
        expected_version=1,
    )
    _validate_identity(
        document.get("target"),
        "asset style.target",
        expected_format=ASSET_TARGET_FORMAT,
        expected_version=1,
    )
    _validate_identity(
        document.get("gamepack"),
        "asset style.gamepack",
        expected_format=GAMEPACK_FORMAT,
        expected_version=1,
    )
    _validate_review(document.get("review"), "asset style.review")
    _validate_visual(document.get("visual"), "asset style.visual")
    _validate_audio(document.get("audio"), "asset style.audio")
    _validate_content_hash(document, "asset style")
    _reject_unsafe_fields(document, context="asset style")
    return copy.deepcopy(document)


def _validate_style_bindings(
    document: Mapping[str, Any],
    *,
    gamepack: Mapping[str, Any],
    target: Mapping[str, Any],
) -> None:
    presentation = gamepack["presentation"]
    runtime = gamepack["runtime_requirements"]
    visual = document["visual"]
    if visual["presentation_mode"] != presentation["mode"]:
        _fail(
            "asset_style_presentation_mismatch",
            "style presentation_mode does not match the gamepack",
        )
    if visual["visual_language"] != presentation["visual_language"]:
        _fail(
            "asset_style_presentation_mismatch",
            "style visual_language does not match the gamepack",
        )
    if visual["camera"] != presentation["camera"]:
        _fail("asset_style_camera_mismatch", "style camera does not match the gamepack")
    if visual["ui"]["density"] != presentation["ui_density"]:
        _fail("asset_style_presentation_mismatch", "style UI density does not match gamepack")
    for field in _ACCESSIBILITY_FIELDS:
        if visual["accessibility"][field] != presentation["accessibility"][field]:
            _fail(
                "asset_style_accessibility_mismatch",
                f"style accessibility.{field} does not match gamepack",
            )
    if (
        visual["palette"]["color_independent"]
        != presentation["accessibility"]["color_independence"]
    ):
        _fail(
            "asset_style_accessibility_mismatch",
            "style color independence does not match gamepack",
        )
    if visual["motion"]["reduced_motion"] != presentation["accessibility"]["reduced_motion"]:
        _fail(
            "asset_style_accessibility_mismatch",
            "style reduced motion does not match gamepack",
        )
    game_localization = presentation["localization"]
    if (
        visual["localization"]["source_locale"] != game_localization["source_locale"]
        or visual["localization"]["supported_locales"] != game_localization["supported_locales"]
    ):
        _fail(
            "asset_style_localization_mismatch",
            "style localization does not match gamepack",
        )
    if any(binding["representation"] == "3d" for binding in target["bindings"]) and presentation[
        "mode"
    ] not in {"3d", "mixed", "vr", "ar"}:
        _fail(
            "asset_style_3d_unsupported",
            "3d target resources are forbidden under this 2d/text-only presentation",
        )
    audio_bindings = [
        binding for binding in target["bindings"] if binding["representation"] == "audio"
    ]
    required_audio = bool(audio_bindings) or any(
        feature.startswith("audio:") for feature in runtime["required_features"]
    )
    if required_audio and document["audio"]["status"] != "defined":
        _fail(
            "asset_style_audio_required",
            "audio direction cannot be not_applicable when audio is required",
        )
    if document["audio"]["status"] == "defined":
        if "asset:wav" not in runtime["asset_formats"]:
            _fail(
                "asset_style_audio_unsupported",
                "defined audio is not accepted by the gamepack runtime target",
            )
    elif audio_bindings:
        _fail("asset_style_audio_required", "audio bindings require defined audio direction")


def build_asset_style(
    gamepack: object,
    subject: object,
    target: object,
    *,
    reviewer: object,
    visual: object,
    audio: object,
    style_id: str | None = None,
) -> dict[str, Any]:
    checked_gamepack = validate_gamepack_document(gamepack)
    checked_subject = validate_asset_subject(subject, gamepack=checked_gamepack)
    checked_target = validate_asset_target(
        target,
        gamepack=checked_gamepack,
        subject=checked_subject,
    )
    asset_subject_identity = _identity(checked_subject, id_field="subject_id")
    target_identity = _identity(checked_target, id_field="target_id")
    gamepack_identity = _gamepack_identity(checked_gamepack)
    style_seed = {
        "asset_subject": asset_subject_identity,
        "target": target_identity,
        "gamepack": gamepack_identity,
        "review": copy.deepcopy(reviewer),
        "visual": copy.deepcopy(visual),
        "audio": copy.deepcopy(audio),
    }
    document: dict[str, Any] = {
        "format": ASSET_STYLE_FORMAT,
        "format_version": GENERIC_ASSET_VERSION,
        "style_id": style_id or _derived_contract_id("asset_style", style_seed),
        "asset_subject": asset_subject_identity,
        "target": target_identity,
        "gamepack": gamepack_identity,
        "review": copy.deepcopy(reviewer),
        "visual": copy.deepcopy(visual),
        "audio": copy.deepcopy(audio),
        "content_hash": "",
    }
    document["content_hash"] = _canonical_hash(document)
    return validate_asset_style(
        document,
        gamepack=checked_gamepack,
        subject=checked_subject,
        target=checked_target,
    )


def validate_asset_style_document(value: object) -> dict[str, Any]:
    try:
        document = _validate_style_structure(value)
    except GenericAssetError:
        raise
    except (CreationContractError, TypeError, ValueError) as exc:
        _fail("asset_style_invalid", str(exc))
    return copy.deepcopy(document)


def _validate_asset_style_uncached(
    value: object,
    *,
    gamepack: object,
    subject: object,
    target: object,
) -> dict[str, Any]:
    document = validate_asset_style_document(value)
    try:
        checked_gamepack = validate_gamepack_document(gamepack)
        checked_subject = validate_asset_subject(subject, gamepack=checked_gamepack)
        checked_target = validate_asset_target(
            target,
            gamepack=checked_gamepack,
            subject=checked_subject,
        )
        if document["asset_subject"] != _identity(
            checked_subject,
            id_field="subject_id",
        ):
            _fail("asset_style_subject_mismatch", "style subject identity mismatch")
        if document["target"] != _identity(checked_target, id_field="target_id"):
            _fail("asset_style_target_mismatch", "style target identity mismatch")
        if document["gamepack"] != _gamepack_identity(checked_gamepack):
            _fail("asset_style_gamepack_mismatch", "style gamepack identity mismatch")
        _validate_style_bindings(
            document,
            gamepack=checked_gamepack,
            target=checked_target,
        )
    except GenericAssetError:
        raise
    except (CreationContractError, TypeError, ValueError) as exc:
        _fail("asset_style_invalid", str(exc))
    return copy.deepcopy(document)


def validate_asset_style(
    value: object,
    *,
    gamepack: object,
    subject: object,
    target: object,
) -> dict[str, Any]:
    return memoize_document_validation(
        "validate_asset_style",
        value,
        lambda candidate: _validate_asset_style_uncached(
            candidate,
            gamepack=gamepack,
            subject=subject,
            target=target,
        ),
        dependencies=(gamepack, subject, target),
    )


_INVENTORY_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "inventory_id",
        "asset_subject",
        "target",
        "style",
        "gamepack",
        "assets",
        "content_hash",
    }
)
_INVENTORY_ASSET_FIELDS = frozenset(
    {
        "asset_id",
        "required",
        "kind",
        "representation",
        "selected_format",
        "outputs",
        "binding_ids",
        "source_roles",
        "usage_contexts",
        "referencing_subjects",
        "sharing",
        "target",
        "style",
        "provenance_reason",
    }
)


def _build_inventory_assets(
    target: Mapping[str, Any],
    style: Mapping[str, Any],
) -> list[dict[str, Any]]:
    bindings = target["bindings"]
    if len(bindings) > MAX_GENERIC_ASSETS:
        _fail("asset_inventory_preflight_limit", "target binding limit exceeded")
    expansion = 0
    for binding in bindings:
        expansion += (
            len(binding["roles"])
            + len(binding["usage_contexts"])
            + len(binding["referencing_subjects"])
            + len(binding["outputs"])
        )
        if expansion > MAX_GENERIC_ASSET_EXPANSION:
            _fail(
                "asset_inventory_preflight_limit",
                "inventory grouping exceeds the pre-expansion limit",
            )
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    display_ids: dict[str, str] = {}
    for binding in bindings:
        key = binding["asset_id"].casefold()
        grouped.setdefault(key, []).append(binding)
        display_ids.setdefault(key, binding["asset_id"])
    assets: list[dict[str, Any]] = []
    target_identity = _identity(target, id_field="target_id")
    style_identity = _identity(style, id_field="style_id")
    for key in sorted(grouped, key=lambda item: display_ids[item].encode("utf-8")):
        members = grouped[key]
        first = members[0]
        source_roles = sorted(
            {role for member in members for role in member["roles"]},
            key=lambda item: item.encode("utf-8"),
        )
        usage_context_spellings: dict[str, str] = {}
        for member in members:
            for usage_context in member["usage_contexts"]:
                key = usage_context.casefold()
                current = usage_context_spellings.get(key)
                if current is None or usage_context.encode("utf-8") < current.encode("utf-8"):
                    usage_context_spellings[key] = usage_context
        usage_contexts = sorted(
            usage_context_spellings.values(),
            key=lambda item: item.encode("utf-8"),
        )
        subjects = {
            (subject["kind"], subject["id"])
            for member in members
            for subject in member["referencing_subjects"]
        }
        referencing_subjects = [
            {"kind": kind, "id": subject_id}
            for kind, subject_id in sorted(
                subjects,
                key=lambda item: (item[0].encode(), item[1].encode()),
            )
        ]
        binding_ids = sorted(
            (member["binding_id"] for member in members),
            key=lambda item: item.encode("utf-8"),
        )
        reason = (
            "Reviewed target maps one exact binding to this asset."
            if len(binding_ids) == 1
            else (
                "Reviewed target explicitly shares "
                f"{len(binding_ids)} compatible bindings in one reviewed group."
            )
        )
        assets.append(
            {
                "asset_id": first["asset_id"],
                "required": any(member["required"] for member in members),
                "kind": first["kind"],
                "representation": first["representation"],
                "selected_format": first["selected_format"],
                "outputs": copy.deepcopy(first["outputs"]),
                "binding_ids": binding_ids,
                "source_roles": source_roles,
                "usage_contexts": usage_contexts,
                "referencing_subjects": referencing_subjects,
                "sharing": copy.deepcopy(first["sharing"]),
                "target": target_identity,
                "style": style_identity,
                "provenance_reason": reason,
            }
        )
    return assets


def _validate_inventory_asset(value: object, context: str) -> dict[str, Any]:
    asset = _object(value, context)
    _exact_keys(asset, _INVENTORY_ASSET_FIELDS, context)
    _identifier(asset.get("asset_id"), f"{context}.asset_id")
    if not isinstance(asset.get("required"), bool):
        _fail("asset_inventory_invalid", f"{context}.required must be boolean")
    kind = _identifier(asset.get("kind"), f"{context}.kind")
    representation = _non_empty_string(
        asset.get("representation"),
        f"{context}.representation",
    )
    selected_format = _non_empty_string(
        asset.get("selected_format"),
        f"{context}.selected_format",
    )
    outputs = _validate_outputs(asset.get("outputs"), f"{context}.outputs")
    if (kind, representation, selected_format, outputs) not in _MATRIX_KEYS:
        _fail("asset_matrix_invalid", f"{context} has an impossible physical matrix")
    binding_ids = _identifier_array(
        asset.get("binding_ids"),
        f"{context}.binding_ids",
        allow_empty=False,
    )
    source_roles = _identifier_array(
        asset.get("source_roles"),
        f"{context}.source_roles",
        allow_empty=False,
    )
    usage_contexts = _string_array(
        asset.get("usage_contexts"),
        f"{context}.usage_contexts",
        allow_empty=False,
        canonical_order=True,
    )
    referencing_subjects = _validate_referencing_subjects(
        asset.get("referencing_subjects"),
        f"{context}.referencing_subjects",
    )
    for label, values in (
        ("binding_ids", binding_ids),
        ("source_roles", source_roles),
        ("usage_contexts", usage_contexts),
        ("referencing_subjects", referencing_subjects),
    ):
        if len(values) > MAX_GENERIC_ASSET_REFERENCES:
            _fail(
                "asset_inventory_preflight_limit",
                f"{context}.{label} exceeds {MAX_GENERIC_ASSET_REFERENCES}",
            )
    _validate_sharing(asset.get("sharing"), f"{context}.sharing")
    _validate_identity(
        asset.get("target"),
        f"{context}.target",
        expected_format=ASSET_TARGET_FORMAT,
        expected_version=1,
    )
    _validate_identity(
        asset.get("style"),
        f"{context}.style",
        expected_format=ASSET_STYLE_FORMAT,
        expected_version=1,
    )
    _bounded_text(asset.get("provenance_reason"), f"{context}.provenance_reason")
    return asset


def _validate_inventory_structure(value: object) -> dict[str, Any]:
    document = _object(value, "asset inventory")
    _exact_keys(document, _INVENTORY_FIELDS, "asset inventory")
    if document.get("format") != ASSET_INVENTORY_FORMAT:
        _fail("asset_inventory_format_invalid", f"format must be {ASSET_INVENTORY_FORMAT}")
    if document.get("format_version") != GENERIC_ASSET_VERSION:
        _fail("asset_inventory_version_invalid", "asset inventory version must be 1")
    _identifier(document.get("inventory_id"), "asset inventory.inventory_id")
    for field, expected_format in (
        ("asset_subject", ASSET_SUBJECT_FORMAT),
        ("target", ASSET_TARGET_FORMAT),
        ("style", ASSET_STYLE_FORMAT),
        ("gamepack", GAMEPACK_FORMAT),
    ):
        _validate_identity(
            document.get(field),
            f"asset inventory.{field}",
            expected_format=expected_format,
            expected_version=1,
        )
    assets = document.get("assets")
    if not isinstance(assets, list) or not assets or len(assets) > MAX_GENERIC_ASSETS:
        _fail(
            "asset_inventory_preflight_limit",
            "asset inventory assets must be a bounded non-empty array",
        )
    asset_ids: list[str] = []
    paths_and_refs = 0
    for index, raw in enumerate(assets):
        asset = _validate_inventory_asset(raw, f"asset inventory.assets/{index}")
        asset_ids.append(asset["asset_id"])
        paths_and_refs += (
            len(asset["binding_ids"])
            + len(asset["source_roles"])
            + len(asset["usage_contexts"])
            + len(asset["referencing_subjects"])
        )
        if paths_and_refs > MAX_GENERIC_ASSET_EXPANSION:
            _fail(
                "asset_inventory_preflight_limit",
                "asset inventory references exceed the pre-expansion limit",
            )
    _canonical_ids(asset_ids, "asset inventory.assets")
    _validate_content_hash(document, "asset inventory")
    _reject_unsafe_fields(document, context="asset inventory")
    return copy.deepcopy(document)


def build_asset_inventory(
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    *,
    inventory_id: str | None = None,
) -> dict[str, Any]:
    checked_gamepack = validate_gamepack_document(gamepack)
    checked_subject = validate_asset_subject(subject, gamepack=checked_gamepack)
    checked_target = validate_asset_target(
        target,
        gamepack=checked_gamepack,
        subject=checked_subject,
    )
    checked_style = validate_asset_style(
        style,
        gamepack=checked_gamepack,
        subject=checked_subject,
        target=checked_target,
    )
    asset_subject_identity = _identity(checked_subject, id_field="subject_id")
    target_identity = _identity(checked_target, id_field="target_id")
    style_identity = _identity(checked_style, id_field="style_id")
    gamepack_identity = _gamepack_identity(checked_gamepack)
    assets = _build_inventory_assets(checked_target, checked_style)
    inventory_seed = {
        "asset_subject": asset_subject_identity,
        "target": target_identity,
        "style": style_identity,
        "gamepack": gamepack_identity,
        "assets": assets,
    }
    document: dict[str, Any] = {
        "format": ASSET_INVENTORY_FORMAT,
        "format_version": GENERIC_ASSET_VERSION,
        "inventory_id": inventory_id or _derived_contract_id("asset_inventory", inventory_seed),
        "asset_subject": asset_subject_identity,
        "target": target_identity,
        "style": style_identity,
        "gamepack": gamepack_identity,
        "assets": assets,
        "content_hash": "",
    }
    document["content_hash"] = _canonical_hash(document)
    return validate_asset_inventory(
        document,
        gamepack=checked_gamepack,
        subject=checked_subject,
        target=checked_target,
        style=checked_style,
    )


def validate_asset_inventory_document(value: object) -> dict[str, Any]:
    try:
        document = _validate_inventory_structure(value)
    except GenericAssetError:
        raise
    except (CreationContractError, TypeError, ValueError) as exc:
        _fail("asset_inventory_invalid", str(exc))
    return copy.deepcopy(document)


def _validate_asset_inventory_uncached(
    value: object,
    *,
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    require_rebuild: bool = True,
) -> dict[str, Any]:
    document = validate_asset_inventory_document(value)
    try:
        checked_gamepack = validate_gamepack_document(gamepack)
        checked_subject = validate_asset_subject(subject, gamepack=checked_gamepack)
        checked_target = validate_asset_target(
            target,
            gamepack=checked_gamepack,
            subject=checked_subject,
        )
        checked_style = validate_asset_style(
            style,
            gamepack=checked_gamepack,
            subject=checked_subject,
            target=checked_target,
        )
        expected_identities = {
            "asset_subject": _identity(checked_subject, id_field="subject_id"),
            "target": _identity(checked_target, id_field="target_id"),
            "style": _identity(checked_style, id_field="style_id"),
            "gamepack": _gamepack_identity(checked_gamepack),
        }
        for field, expected in expected_identities.items():
            if document[field] != expected:
                _fail(
                    "asset_inventory_binding_mismatch",
                    f"inventory {field} identity mismatch",
                )
        if require_rebuild:
            expected_assets = _build_inventory_assets(checked_target, checked_style)
            if document["assets"] != expected_assets:
                _fail(
                    "asset_inventory_rebuild_mismatch",
                    "inventory assets do not exactly rebuild from target and style",
                )
    except GenericAssetError:
        raise
    except (CreationContractError, TypeError, ValueError) as exc:
        _fail("asset_inventory_invalid", str(exc))
    return copy.deepcopy(document)


def validate_asset_inventory(
    value: object,
    *,
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    require_rebuild: bool = True,
) -> dict[str, Any]:
    return memoize_document_validation(
        f"validate_asset_inventory:{int(require_rebuild)}",
        value,
        lambda candidate: _validate_asset_inventory_uncached(
            candidate,
            gamepack=gamepack,
            subject=subject,
            target=target,
            style=style,
            require_rebuild=require_rebuild,
        ),
        dependencies=(gamepack, subject, target, style),
    )


_SPEC_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "spec_id",
        "asset_subject",
        "target",
        "style",
        "inventory",
        "asset",
        "outputs",
        "acceptance_criteria",
        "production_class",
        "review_requirements",
        "content_hash",
    }
)
_SPEC_ASSET_FIELDS = frozenset({"asset_id", "content_hash"})
_SPEC_OUTPUT_FIELDS = frozenset({"role", "media_type", "runtime_path", "expectations"})
_REVIEW_REQUIREMENT_FIELDS = frozenset({"human_review_required", "qa_profile", "evidence_required"})
_PNG_EXPECTATION_FIELDS = frozenset({"kind", "width", "height", "color_type", "max_bytes"})
_WAV_EXPECTATION_FIELDS = frozenset({"kind", "channels", "sample_rate", "frames", "max_bytes"})
_FONT_EXPECTATION_FIELDS = frozenset(
    {"kind", "container", "glyph_ranges", "max_glyphs", "max_bytes"}
)
_GLSL_EXPECTATION_FIELDS = frozenset({"kind", "stage", "max_lines", "max_bytes"})
_JSON_EXPECTATION_FIELDS = frozenset(
    {"kind", "schema_id", "schema_version", "max_records", "max_bytes"}
)
_GLB_EXPECTATION_FIELDS = frozenset(
    {
        "kind",
        "max_nodes",
        "max_meshes",
        "max_primitives",
        "max_materials",
        "max_joints",
        "max_animations",
        "max_triangles",
        "max_bytes",
    }
)
_PRODUCTION_CLASSES = frozenset(
    {"human", "procedural_offline", "external_authoring", "generative_authoring"}
)


def _asset_item_hash(asset: Mapping[str, object]) -> str:
    return _canonical_hash(asset)


def _bounded_metric(
    value: object,
    context: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    metric = _integer(value, context, minimum=minimum)
    if metric > maximum:
        _fail("asset_spec_invalid", f"{context} exceeds {maximum}")
    return metric


def _validate_glyph_ranges(value: object, context: str) -> list[str]:
    glyph_ranges = _string_array(
        value,
        context,
        allow_empty=False,
    )
    if len(glyph_ranges) > 256:
        _fail("asset_spec_invalid", f"{context} exceeds 256")
    previous_end = -1
    for index, glyph_range in enumerate(glyph_ranges):
        match = _GENERIC_ASSET_GLYPH_RANGE_RE.fullmatch(glyph_range)
        if match is None:
            _fail(
                "asset_spec_invalid",
                f"{context}/{index} must match {GENERIC_ASSET_GLYPH_RANGE_PATTERN}",
            )
        start_text, end_text = glyph_range.removeprefix("U+").split("-", 1)
        start = int(start_text, 16)
        end = int(end_text, 16)
        if (
            start_text != f"{start:04X}"
            or end_text != f"{end:04X}"
            or end > 0x10FFFF
            or start > end
        ):
            _fail(
                "asset_spec_invalid",
                f"{context}/{index} is not a canonical bounded Unicode range",
            )
        if start <= previous_end:
            _fail(
                "asset_spec_invalid",
                f"{context} must be sorted, unique, and non-overlapping",
            )
        previous_end = end
    return glyph_ranges


def _validate_expectations(
    value: object,
    context: str,
    *,
    role: str,
    media_type: str,
) -> dict[str, Any]:
    expectations = _object(value, context)
    kind = expectations.get("kind")
    if media_type == "image/png":
        _exact_keys(expectations, _PNG_EXPECTATION_FIELDS, context)
        if kind != "png":
            _fail("asset_spec_invalid", f"{context}.kind must be png")
        _bounded_metric(
            expectations.get("width"),
            f"{context}.width",
            minimum=1,
            maximum=16_384,
        )
        _bounded_metric(
            expectations.get("height"),
            f"{context}.height",
            minimum=1,
            maximum=16_384,
        )
        if expectations.get("color_type") not in {"rgba8", "rgb8", "grayscale8"}:
            _fail("asset_spec_invalid", f"{context}.color_type is unsupported")
    elif media_type == "audio/wav":
        _exact_keys(expectations, _WAV_EXPECTATION_FIELDS, context)
        if kind != "wav_pcm16":
            _fail("asset_spec_invalid", f"{context}.kind must be wav_pcm16")
        if expectations.get("channels") not in {1, 2}:
            _fail("asset_spec_invalid", f"{context}.channels must be one or two")
        _bounded_metric(
            expectations.get("sample_rate"),
            f"{context}.sample_rate",
            minimum=8000,
            maximum=192000,
        )
        _bounded_metric(
            expectations.get("frames"),
            f"{context}.frames",
            minimum=1,
            maximum=192_000_000,
        )
    elif media_type in {"font/ttf", "font/otf"}:
        _exact_keys(expectations, _FONT_EXPECTATION_FIELDS, context)
        if kind != "font":
            _fail("asset_spec_invalid", f"{context}.kind must be font")
        expected_container = "ttf" if media_type == "font/ttf" else "otf"
        if expectations.get("container") != expected_container:
            _fail(
                "asset_spec_invalid",
                f"{context}.container does not match {media_type}",
            )
        _validate_glyph_ranges(
            expectations.get("glyph_ranges"),
            f"{context}.glyph_ranges",
        )
        _bounded_metric(
            expectations.get("max_glyphs"),
            f"{context}.max_glyphs",
            minimum=1,
            maximum=1_114_112,
        )
    elif media_type == "text/x-glsl":
        _exact_keys(expectations, _GLSL_EXPECTATION_FIELDS, context)
        if kind != "glsl":
            _fail("asset_spec_invalid", f"{context}.kind must be glsl")
        expected_stage = "vertex" if role == "vertex_shader" else "fragment"
        if expectations.get("stage") != expected_stage:
            _fail(
                "asset_spec_invalid",
                f"{context}.stage does not match output role",
            )
        _bounded_metric(
            expectations.get("max_lines"),
            f"{context}.max_lines",
            minimum=1,
            maximum=65_536,
        )
    elif media_type == "application/json":
        _exact_keys(expectations, _JSON_EXPECTATION_FIELDS, context)
        if kind != "schema_json":
            _fail("asset_spec_invalid", f"{context}.kind must be schema_json")
        _bounded_text(
            expectations.get("schema_id"),
            f"{context}.schema_id",
            maximum=256,
        )
        _bounded_metric(
            expectations.get("schema_version"),
            f"{context}.schema_version",
            minimum=1,
            maximum=65_535,
        )
        _bounded_metric(
            expectations.get("max_records"),
            f"{context}.max_records",
            minimum=1,
            maximum=1_000_000,
        )
    elif media_type == "model/gltf-binary":
        _exact_keys(expectations, _GLB_EXPECTATION_FIELDS, context)
        if kind != "glb":
            _fail("asset_spec_invalid", f"{context}.kind must be glb")
        for field, maximum in (
            ("max_nodes", 65_536),
            ("max_meshes", 16_384),
            ("max_primitives", 262_144),
            ("max_materials", 16_384),
            ("max_joints", 4096),
            ("max_animations", 16_384),
            ("max_triangles", 100_000_000),
        ):
            _bounded_metric(
                expectations.get(field),
                f"{context}.{field}",
                minimum=0,
                maximum=maximum,
            )
    else:
        _fail("asset_spec_invalid", f"{context} media type is unsupported")
    _bounded_metric(
        expectations.get("max_bytes"),
        f"{context}.max_bytes",
        minimum=1,
        maximum=2_147_483_647,
    )
    return expectations


def _validate_spec_output(value: object, context: str) -> dict[str, Any]:
    output = _object(value, context)
    _exact_keys(output, _SPEC_OUTPUT_FIELDS, context)
    role = _identifier(output.get("role"), f"{context}.role")
    media_type = _non_empty_string(output.get("media_type"), f"{context}.media_type")
    if role not in _OUTPUT_MEDIA or media_type not in _OUTPUT_MEDIA[role]:
        _fail("asset_spec_invalid", f"{context} role/media type is unsupported")
    try:
        _portable_relative_path(output.get("runtime_path"), f"{context}.runtime_path")
    except CreationContractError as exc:
        _fail("asset_spec_portable_path_invalid", str(exc))
    _validate_expectations(
        output.get("expectations"),
        f"{context}.expectations",
        role=role,
        media_type=media_type,
    )
    return output


def _validate_spec_structure(value: object) -> dict[str, Any]:
    document = _object(value, "asset specification")
    _exact_keys(document, _SPEC_FIELDS, "asset specification")
    if document.get("format") != ASSET_SPEC_FORMAT:
        _fail("asset_spec_format_invalid", f"format must be {ASSET_SPEC_FORMAT}")
    if document.get("format_version") != GENERIC_ASSET_VERSION:
        _fail("asset_spec_version_invalid", "asset specification version must be 1")
    _identifier(document.get("spec_id"), "asset specification.spec_id")
    for field, expected_format in (
        ("asset_subject", ASSET_SUBJECT_FORMAT),
        ("target", ASSET_TARGET_FORMAT),
        ("style", ASSET_STYLE_FORMAT),
        ("inventory", ASSET_INVENTORY_FORMAT),
    ):
        _validate_identity(
            document.get(field),
            f"asset specification.{field}",
            expected_format=expected_format,
            expected_version=1,
        )
    asset = _object(document.get("asset"), "asset specification.asset")
    _exact_keys(asset, _SPEC_ASSET_FIELDS, "asset specification.asset")
    _identifier(asset.get("asset_id"), "asset specification.asset.asset_id")
    _sha256(asset.get("content_hash"), "asset specification.asset.content_hash")
    outputs = document.get("outputs")
    if not isinstance(outputs, list) or not outputs or len(outputs) > MAX_GENERIC_ASSET_OUTPUTS:
        _fail(
            "asset_spec_preflight_limit",
            "asset specification outputs must be a bounded non-empty array",
        )
    roles: list[str] = []
    runtime_paths: list[str] = []
    for index, raw in enumerate(outputs):
        output = _validate_spec_output(raw, f"asset specification.outputs/{index}")
        roles.append(output["role"])
        runtime_paths.append(output["runtime_path"])
    _canonical_ids(roles, "asset specification.outputs")
    _validate_runtime_path_tree(
        runtime_paths,
        "asset specification runtime paths",
    )
    criteria = _string_array(
        document.get("acceptance_criteria"),
        "asset specification.acceptance_criteria",
        allow_empty=False,
        canonical_order=True,
    )
    if len(criteria) > MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS:
        _fail("asset_spec_preflight_limit", "acceptance criteria exceeds its limit")
    for index, criterion in enumerate(criteria):
        _bounded_text(
            criterion,
            f"asset specification.acceptance_criteria/{index}",
        )
    if document.get("production_class") not in _PRODUCTION_CLASSES:
        _fail("asset_spec_invalid", "asset specification production_class is unsupported")
    review = _object(
        document.get("review_requirements"),
        "asset specification.review_requirements",
    )
    _exact_keys(
        review,
        _REVIEW_REQUIREMENT_FIELDS,
        "asset specification.review_requirements",
    )
    for field in ("human_review_required", "evidence_required"):
        if review.get(field) is not True:
            _fail(
                "asset_spec_invalid",
                f"asset specification.review_requirements.{field} must be true",
            )
    _identifier(
        review.get("qa_profile"),
        "asset specification.review_requirements.qa_profile",
    )
    _validate_content_hash(document, "asset specification")
    _reject_unsafe_fields(document, context="asset specification")
    return copy.deepcopy(document)


def build_asset_specification(
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    inventory: object,
    *,
    asset_id: str,
    outputs: object,
    acceptance_criteria: object,
    production_class: str,
    review_requirements: object,
    spec_id: str | None = None,
) -> dict[str, Any]:
    checked_gamepack = validate_gamepack_document(gamepack)
    checked_subject = validate_asset_subject(subject, gamepack=checked_gamepack)
    checked_target = validate_asset_target(
        target,
        gamepack=checked_gamepack,
        subject=checked_subject,
    )
    checked_style = validate_asset_style(
        style,
        gamepack=checked_gamepack,
        subject=checked_subject,
        target=checked_target,
    )
    checked_inventory = validate_asset_inventory(
        inventory,
        gamepack=checked_gamepack,
        subject=checked_subject,
        target=checked_target,
        style=checked_style,
    )
    _identifier(asset_id, "asset specification asset_id")
    asset = next(
        (
            item
            for item in checked_inventory["assets"]
            if item["asset_id"].casefold() == asset_id.casefold()
        ),
        None,
    )
    if asset is None:
        _fail("asset_spec_asset_missing", f"inventory does not contain asset {asset_id}")
    asset_subject_identity = _identity(checked_subject, id_field="subject_id")
    target_identity = _identity(checked_target, id_field="target_id")
    style_identity = _identity(checked_style, id_field="style_id")
    inventory_identity = _identity(checked_inventory, id_field="inventory_id")
    asset_identity = {
        "asset_id": asset["asset_id"],
        "content_hash": _asset_item_hash(asset),
    }
    specification_seed = {
        "asset_subject": asset_subject_identity,
        "target": target_identity,
        "style": style_identity,
        "inventory": inventory_identity,
        "asset": asset_identity,
        "outputs": copy.deepcopy(outputs),
        "acceptance_criteria": copy.deepcopy(acceptance_criteria),
        "production_class": production_class,
        "review_requirements": copy.deepcopy(review_requirements),
    }
    document: dict[str, Any] = {
        "format": ASSET_SPEC_FORMAT,
        "format_version": GENERIC_ASSET_VERSION,
        "spec_id": spec_id or _derived_contract_id("asset_spec", specification_seed),
        "asset_subject": asset_subject_identity,
        "target": target_identity,
        "style": style_identity,
        "inventory": inventory_identity,
        "asset": asset_identity,
        "outputs": copy.deepcopy(outputs),
        "acceptance_criteria": copy.deepcopy(acceptance_criteria),
        "production_class": production_class,
        "review_requirements": copy.deepcopy(review_requirements),
        "content_hash": "",
    }
    document["content_hash"] = _canonical_hash(document)
    return validate_asset_specification(
        document,
        gamepack=checked_gamepack,
        inventory=checked_inventory,
        subject=checked_subject,
        target=checked_target,
        style=checked_style,
    )


def validate_asset_specification_document(value: object) -> dict[str, Any]:
    try:
        document = _validate_spec_structure(value)
    except GenericAssetError:
        raise
    except (CreationContractError, TypeError, ValueError) as exc:
        _fail("asset_spec_invalid", str(exc))
    return copy.deepcopy(document)


def _validate_asset_specification_uncached(
    value: object,
    *,
    gamepack: object,
    inventory: object,
    subject: object,
    target: object,
    style: object,
) -> dict[str, Any]:
    document = validate_asset_specification_document(value)
    try:
        checked_gamepack = validate_gamepack_document(gamepack)
        checked_subject = validate_asset_subject(subject, gamepack=checked_gamepack)
        checked_target = validate_asset_target(
            target,
            gamepack=checked_gamepack,
            subject=checked_subject,
        )
        checked_style = validate_asset_style(
            style,
            gamepack=checked_gamepack,
            subject=checked_subject,
            target=checked_target,
        )
        checked_inventory = validate_asset_inventory(
            inventory,
            gamepack=checked_gamepack,
            subject=checked_subject,
            target=checked_target,
            style=checked_style,
        )
        expected_identities = {
            "asset_subject": _identity(checked_subject, id_field="subject_id"),
            "target": _identity(checked_target, id_field="target_id"),
            "style": _identity(checked_style, id_field="style_id"),
            "inventory": _identity(checked_inventory, id_field="inventory_id"),
        }
        for field, expected in expected_identities.items():
            if document[field] != expected:
                _fail(
                    "asset_spec_binding_mismatch",
                    f"specification {field} identity mismatch",
                )
        asset_id = document["asset"]["asset_id"]
        asset = next(
            (
                item
                for item in checked_inventory["assets"]
                if item["asset_id"].casefold() == asset_id.casefold()
            ),
            None,
        )
        if asset is None:
            _fail(
                "asset_spec_asset_missing",
                f"inventory does not contain asset {asset_id}",
            )
        if document["asset"]["content_hash"] != _asset_item_hash(asset):
            _fail(
                "asset_spec_asset_hash_mismatch",
                f"specification asset {asset_id} hash mismatch",
            )
        expected_outputs = [(item["role"], item["media_type"]) for item in asset["outputs"]]
        actual_outputs = [(item["role"], item["media_type"]) for item in document["outputs"]]
        if actual_outputs != expected_outputs:
            _fail(
                "asset_spec_output_mismatch",
                f"specification outputs do not exactly fulfill asset {asset_id}",
            )
    except GenericAssetError:
        raise
    except (CreationContractError, TypeError, ValueError) as exc:
        _fail("asset_spec_invalid", str(exc))
    return copy.deepcopy(document)


def validate_asset_specification(
    value: object,
    *,
    gamepack: object,
    inventory: object,
    subject: object,
    target: object,
    style: object,
) -> dict[str, Any]:
    return memoize_document_validation(
        "validate_asset_specification",
        value,
        lambda candidate: _validate_asset_specification_uncached(
            candidate,
            gamepack=gamepack,
            inventory=inventory,
            subject=subject,
            target=target,
            style=style,
        ),
        dependencies=(gamepack, subject, target, style, inventory),
    )


def validate_asset_specification_set(
    values: object,
    *,
    gamepack: object,
    inventory: object,
    subject: object,
    target: object,
    style: object,
    require_rebuild: bool = True,
) -> list[dict[str, Any]]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        _fail("asset_spec_set_invalid", "specification set must be an array")
    if len(values) > MAX_GENERIC_ASSETS:
        _fail(
            "asset_spec_set_preflight_limit",
            f"specification set exceeds {MAX_GENERIC_ASSETS}",
        )
    checked_gamepack = validate_gamepack_document(gamepack)
    checked_subject = validate_asset_subject(subject, gamepack=checked_gamepack)
    checked_target = validate_asset_target(
        target,
        gamepack=checked_gamepack,
        subject=checked_subject,
    )
    checked_style = validate_asset_style(
        style,
        gamepack=checked_gamepack,
        subject=checked_subject,
        target=checked_target,
    )
    checked_inventory = validate_asset_inventory(
        inventory,
        gamepack=checked_gamepack,
        subject=checked_subject,
        target=checked_target,
        style=checked_style,
        require_rebuild=require_rebuild,
    )
    checked: list[dict[str, Any]] = []
    asset_ids: list[str] = []
    spec_ids: list[str] = []
    runtime_paths: list[str] = []
    for value in values:
        specification = validate_asset_specification(
            value,
            gamepack=checked_gamepack,
            inventory=checked_inventory,
            subject=checked_subject,
            target=checked_target,
            style=checked_style,
        )
        asset_id = specification["asset"]["asset_id"]
        if asset_id.casefold() in {item.casefold() for item in asset_ids}:
            _fail("asset_spec_duplicate", f"duplicate specification for asset {asset_id}")
        asset_ids.append(asset_id)
        spec_id = specification["spec_id"]
        if spec_id.casefold() in {item.casefold() for item in spec_ids}:
            _fail("asset_spec_duplicate", f"duplicate specification ID {spec_id}")
        spec_ids.append(spec_id)
        for output in specification["outputs"]:
            runtime_paths.append(output["runtime_path"])
        checked.append(specification)
    _validate_runtime_path_tree(runtime_paths, "asset specification set runtime paths")
    expected_assets = [item["asset_id"] for item in checked_inventory["assets"]]
    if sorted(asset_ids, key=lambda item: item.encode()) != expected_assets:
        _fail(
            "asset_spec_set_coverage_mismatch",
            "specification set must contain exactly one specification per inventory asset",
        )
    checked.sort(key=lambda item: item["asset"]["asset_id"].encode("utf-8"))
    if require_rebuild and list(values) != checked:
        _fail(
            "asset_spec_set_noncanonical",
            "specification set must use canonical inventory asset order",
        )
    return checked


def serialize_asset_contract(value: object) -> bytes:
    if not isinstance(value, Mapping):
        _fail("asset_contract_invalid", "asset contract must be an object")
    validators = {
        ASSET_SUBJECT_FORMAT: validate_asset_subject_document,
        ASSET_TARGET_FORMAT: validate_asset_target_document,
        ASSET_STYLE_FORMAT: validate_asset_style_document,
        ASSET_INVENTORY_FORMAT: validate_asset_inventory_document,
        ASSET_SPEC_FORMAT: validate_asset_specification_document,
    }
    validator = validators.get(value.get("format"))
    if validator is None:
        _fail("asset_contract_format_invalid", "generic asset contract format is unsupported")
    return canonical_json_bytes(validator(value))


def _read_contract(path: str | Path, validator: Any) -> dict[str, Any]:
    try:
        document = read_creation_object(path)
        return validator(document)
    except GenericAssetError:
        raise
    except (CreationContractError, OSError, TypeError, ValueError) as exc:
        _fail("asset_contract_read_failed", str(exc))


def load_asset_subject(
    path: str | Path,
    *,
    gamepack_path: str | Path,
) -> dict[str, Any]:
    gamepack = load_gamepack(gamepack_path)
    document = _read_contract(path, validate_asset_subject_document)
    return validate_asset_subject(document, gamepack=gamepack)


def load_asset_target(
    path: str | Path,
    *,
    gamepack_path: str | Path,
    subject_path: str | Path,
) -> dict[str, Any]:
    gamepack = load_gamepack(gamepack_path)
    subject = load_asset_subject(subject_path, gamepack_path=gamepack_path)
    document = _read_contract(path, validate_asset_target_document)
    return validate_asset_target(document, gamepack=gamepack, subject=subject)


def load_asset_style(
    path: str | Path,
    *,
    gamepack_path: str | Path,
    subject_path: str | Path,
    target_path: str | Path,
) -> dict[str, Any]:
    gamepack = load_gamepack(gamepack_path)
    subject = load_asset_subject(subject_path, gamepack_path=gamepack_path)
    target = load_asset_target(
        target_path,
        gamepack_path=gamepack_path,
        subject_path=subject_path,
    )
    document = _read_contract(path, validate_asset_style_document)
    return validate_asset_style(
        document,
        gamepack=gamepack,
        subject=subject,
        target=target,
    )


def load_asset_inventory(
    path: str | Path,
    *,
    gamepack_path: str | Path,
    subject_path: str | Path,
    target_path: str | Path,
    style_path: str | Path,
) -> dict[str, Any]:
    gamepack = load_gamepack(gamepack_path)
    subject = load_asset_subject(subject_path, gamepack_path=gamepack_path)
    target = load_asset_target(
        target_path,
        gamepack_path=gamepack_path,
        subject_path=subject_path,
    )
    style = load_asset_style(
        style_path,
        gamepack_path=gamepack_path,
        subject_path=subject_path,
        target_path=target_path,
    )
    document = _read_contract(path, validate_asset_inventory_document)
    return validate_asset_inventory(
        document,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
    )


def load_asset_specification(
    path: str | Path,
    *,
    gamepack: object,
    inventory: object,
    subject: object,
    target: object,
    style: object,
) -> dict[str, Any]:
    document = _read_contract(path, validate_asset_specification_document)
    return validate_asset_specification(
        document,
        gamepack=gamepack,
        inventory=inventory,
        subject=subject,
        target=target,
        style=style,
    )


def _publish_asset_contract(
    path: str | Path,
    document: Mapping[str, Any],
) -> PublishedGameArtifact:
    try:
        destination = preflight_game_artifact_output(path)
        write_json_atomic(destination, document, durable_parent=True)
        return _published_artifact(destination, document)
    except GamepackError as exc:
        _fail(exc.reason_code, exc.detail)
    except AssetContractError as exc:
        reason = "output_exists" if "overwrite" in str(exc).casefold() else "output_publish_failed"
        _fail(reason, str(exc))


def publish_asset_subject(
    path: str | Path,
    value: object,
    *,
    gamepack: object,
) -> PublishedGameArtifact:
    document = validate_asset_subject(value, gamepack=gamepack)
    return _publish_asset_contract(path, document)


def publish_asset_target(
    path: str | Path,
    value: object,
    *,
    gamepack: object,
    subject: object,
) -> PublishedGameArtifact:
    document = validate_asset_target(value, gamepack=gamepack, subject=subject)
    return _publish_asset_contract(path, document)


def publish_asset_style(
    path: str | Path,
    value: object,
    *,
    gamepack: object,
    subject: object,
    target: object,
) -> PublishedGameArtifact:
    document = validate_asset_style(
        value,
        gamepack=gamepack,
        subject=subject,
        target=target,
    )
    return _publish_asset_contract(path, document)


def publish_asset_inventory(
    path: str | Path,
    value: object,
    *,
    gamepack: object,
    subject: object,
    target: object,
    style: object,
) -> PublishedGameArtifact:
    document = validate_asset_inventory(
        value,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
    )
    return _publish_asset_contract(path, document)


def publish_asset_specification(
    path: str | Path,
    value: object,
    *,
    gamepack: object,
    inventory: object,
    subject: object,
    target: object,
    style: object,
) -> PublishedGameArtifact:
    document = validate_asset_specification(
        value,
        gamepack=gamepack,
        inventory=inventory,
        subject=subject,
        target=target,
        style=style,
    )
    return _publish_asset_contract(path, document)
