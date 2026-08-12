"""Immutable, cross-platform generation storage for save and replay slots."""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass
from pathlib import Path

from gamepack_runtime.contracts import GameLogicError
from gamepack_runtime.persistence import (
    MAX_GAME_REPLAY_BYTES,
    MAX_GAME_SAVE_BYTES,
    GamePersistenceContext,
    _content_hash_matches,
    _exact_keys,
    _object,
    _own,
    _pretty_bytes,
    _require_context,
    _sha256,
    _slot_path,
    canonical_persistence_hash,
    play_game_replay,
    validate_game_replay_document,
    validate_game_save_document,
    validate_slot_name,
)
from gamepack_runtime.persistence_io import (
    PersistenceIOError,
    decode_json_object,
    held_persistence_lock,
    inspect_safe_entry,
    publish_json_noreplace,
    read_directory_entries,
    read_directory_files,
    read_json_object,
)

PERSISTENCE_GENERATION_FORMAT = "world-forge.persistence_generation"
PERSISTENCE_GENERATION_VERSION = 1
MAX_PERSISTENCE_GENERATIONS = 128
MAX_PERSISTENCE_GENERATION_DEPTH = 128
MAX_PERSISTENCE_GENERATION_PARENTS = 128
MAX_PERSISTENCE_GENERATION_BYTES = MAX_GAME_REPLAY_BYTES + 64 * 1024
MAX_PERSISTENCE_GENERATION_TOTAL_BYTES = 512 * 1024 * 1024
MAX_PERSISTENCE_SEQUENCE = 9_007_199_254_740_991

_GENERATION_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "kind",
        "slot",
        "sequence",
        "parent_hashes",
        "operation",
        "payload",
        "payload_hash",
        "content_hash",
    }
)
_OPERATIONS = frozenset(
    {
        "write",
        "legacy_migration",
        "rollback",
        "conflict_resolution",
    }
)
_GENERATION_FILENAME_RE = re.compile(r"^[0-9]{20}-[0-9a-f]{64}\.json$")


def _fail(reason_code: str, detail: str) -> None:
    raise GameLogicError(reason_code, detail)


def _sequence(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_PERSISTENCE_SEQUENCE:
        _fail(
            "persistence_generation_invalid",
            "generation.sequence must be a non-negative safe integer",
        )
    return value


def _parents(value: object) -> list[str]:
    if type(value) is not list or len(value) > MAX_PERSISTENCE_GENERATION_PARENTS:
        _fail(
            "persistence_generation_invalid",
            "generation.parent_hashes must be a bounded exact array",
        )
    result = [
        _sha256(item, f"generation.parent_hashes[{index}]") for index, item in enumerate(value)
    ]
    if result != sorted(result) or len(result) != len(set(result)):
        _fail(
            "persistence_generation_invalid",
            "generation.parent_hashes must be sorted and unique",
        )
    return result


def _validate_operation(
    operation: object,
    sequence: int,
    parent_hashes: list[str],
) -> str:
    if type(operation) is not str or operation not in _OPERATIONS:
        _fail(
            "persistence_generation_invalid",
            "generation.operation is unsupported",
        )
    if sequence == 0:
        if parent_hashes:
            _fail(
                "persistence_generation_invalid",
                "generation zero must not have parents",
            )
        if operation not in {"write", "legacy_migration"}:
            _fail(
                "persistence_generation_invalid",
                "generation zero must be a write or legacy migration",
            )
    else:
        if not parent_hashes:
            _fail(
                "persistence_generation_invalid",
                "non-zero generations require parents",
            )
        if operation in {"write", "rollback"} and len(parent_hashes) != 1:
            _fail(
                "persistence_generation_invalid",
                f"{operation} generations require exactly one parent",
            )
        if operation == "legacy_migration":
            _fail(
                "persistence_generation_invalid",
                "legacy migration is valid only at generation zero",
            )
        if operation == "conflict_resolution" and len(parent_hashes) < 2:
            _fail(
                "persistence_generation_invalid",
                "conflict resolution requires at least two parents",
            )
    return operation


def _validated_payload(
    value: object,
    *,
    kind: str,
    context: GamePersistenceContext,
) -> dict[str, object]:
    if kind == "save":
        return validate_game_save_document(value, context)
    if kind == "replay":
        document = validate_game_replay_document(value, context)
        play_game_replay(context, document)
        return document
    _fail(
        "persistence_generation_invalid",
        "generation.kind must be save or replay",
    )


def build_persistence_generation(
    payload: object,
    *,
    kind: object,
    slot: object,
    sequence: object,
    parent_hashes: object,
    operation: object,
    context: GamePersistenceContext,
) -> dict[str, object]:
    """Build one immutable generation envelope around an unchanged payload."""

    checked_context = _require_context(context)
    if type(kind) is not str or kind not in {"save", "replay"}:
        _fail(
            "persistence_generation_invalid",
            "generation.kind must be save or replay",
        )
    checked_slot = validate_slot_name(slot)
    checked_sequence = _sequence(sequence)
    checked_parents = _parents(parent_hashes)
    checked_operation = _validate_operation(
        operation,
        checked_sequence,
        checked_parents,
    )
    checked_payload = _validated_payload(
        payload,
        kind=kind,
        context=checked_context,
    )
    generation: dict[str, object] = {
        "format": PERSISTENCE_GENERATION_FORMAT,
        "format_version": PERSISTENCE_GENERATION_VERSION,
        "kind": kind,
        "slot": checked_slot,
        "sequence": checked_sequence,
        "parent_hashes": checked_parents,
        "operation": checked_operation,
        "payload": checked_payload,
        "payload_hash": checked_payload["content_hash"],
        "content_hash": "",
    }
    generation["content_hash"] = canonical_persistence_hash(generation)
    return generation


def validate_persistence_generation_document(
    value: object,
    *,
    context: GamePersistenceContext,
    expected_kind: str | None = None,
    expected_slot: str | None = None,
) -> dict[str, object]:
    """Validate and own one exact generation envelope."""

    checked_context = _require_context(context)
    document = _own(
        value,
        maximum_bytes=MAX_PERSISTENCE_GENERATION_BYTES,
        context="persistence generation",
    )
    document = _object(document, "persistence generation")
    try:
        _exact_keys(document, _GENERATION_FIELDS, "persistence generation")
    except GameLogicError as exc:
        _fail("persistence_generation_invalid", exc.detail)
    if (
        document.get("format") != PERSISTENCE_GENERATION_FORMAT
        or document.get("format_version") != PERSISTENCE_GENERATION_VERSION
    ):
        _fail(
            "persistence_generation_invalid",
            "unsupported persistence generation format or version",
        )
    kind = document.get("kind")
    if type(kind) is not str or kind not in {"save", "replay"}:
        _fail(
            "persistence_generation_invalid",
            "generation.kind must be save or replay",
        )
    slot = validate_slot_name(document.get("slot"))
    sequence = _sequence(document.get("sequence"))
    parents = _parents(document.get("parent_hashes"))
    _validate_operation(document.get("operation"), sequence, parents)
    payload = _validated_payload(
        document.get("payload"),
        kind=kind,
        context=checked_context,
    )
    if document.get("payload_hash") != payload["content_hash"]:
        _fail(
            "persistence_generation_invalid",
            "generation.payload_hash must equal the embedded payload content hash",
        )
    if expected_kind is not None and kind != expected_kind:
        _fail(
            "persistence_generation_context_mismatch",
            "generation kind does not match the logical slot",
        )
    if expected_slot is not None and slot != expected_slot:
        _fail(
            "persistence_generation_context_mismatch",
            "generation slot does not match its path",
        )
    try:
        _content_hash_matches(document, "persistence generation")
    except GameLogicError as exc:
        if exc.reason_code == "persistence_hash_mismatch":
            raise
        _fail("persistence_generation_invalid", exc.detail)
    return document


def load_persistence_generation_bytes(
    payload: bytes,
    *,
    context: GamePersistenceContext,
    expected_kind: str | None = None,
    expected_slot: str | None = None,
    source: str = "<persistence generation bytes>",
) -> dict[str, object]:
    """Load strict bounded generation JSON bytes."""

    document = decode_json_object(
        payload,
        source=source,
        limit=MAX_PERSISTENCE_GENERATION_BYTES,
    )
    return validate_persistence_generation_document(
        document,
        context=context,
        expected_kind=expected_kind,
        expected_slot=expected_slot,
    )


def serialize_persistence_generation(
    value: object,
    *,
    context: GamePersistenceContext,
) -> bytes:
    """Serialize one validated generation as stable pretty UTF-8 JSON."""

    document = validate_persistence_generation_document(
        value,
        context=context,
    )
    return _pretty_bytes(
        document,
        maximum_bytes=MAX_PERSISTENCE_GENERATION_BYTES,
    )


@dataclass(frozen=True, slots=True)
class _SlotPaths:
    legacy: Path
    slot_root: Path
    version_root: Path
    lock: Path
    generations: Path
    staging: Path


@dataclass(frozen=True, slots=True)
class _SlotState:
    paths: _SlotPaths
    generations: dict[str, dict[str, object]]
    tips: tuple[str, ...]
    legacy: dict[str, object] | None
    total_bytes: int


def _paths(
    root: str | os.PathLike[str],
    slot: object,
    context: GamePersistenceContext,
    *,
    kind: str,
) -> _SlotPaths:
    legacy = _slot_path(
        root,
        slot,
        context,
        kind="saves" if kind == "save" else "replays",
    )
    checked_slot = validate_slot_name(slot)
    slot_root = legacy.with_name(f"{checked_slot}.slot")
    version_root = slot_root / "v1"
    return _SlotPaths(
        legacy=legacy,
        slot_root=slot_root,
        version_root=version_root,
        lock=version_root / ".write.lock",
        generations=version_root / "generations",
        staging=version_root / "staging",
    )


def _caused_by_missing(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, FileNotFoundError):
            return True
        current = current.__cause__ or current.__context__
    return False


def _inspect_optional(path: Path) -> str | None:
    try:
        return inspect_safe_entry(path)
    except PersistenceIOError as exc:
        if _caused_by_missing(exc):
            return None
        raise


def _load_legacy(
    paths: _SlotPaths,
    *,
    kind: str,
    context: GamePersistenceContext,
) -> dict[str, object] | None:
    entry_kind = _inspect_optional(paths.legacy)
    if entry_kind is None:
        return None
    if entry_kind != "file":
        _fail(
            "persistence_legacy_unsafe",
            "legacy persistence slot must be a standalone regular file",
        )
    limit = MAX_GAME_SAVE_BYTES if kind == "save" else MAX_GAME_REPLAY_BYTES
    document = read_json_object(paths.legacy, limit=limit)
    return _validated_payload(document, kind=kind, context=context)


def _validate_layout(paths: _SlotPaths) -> bool:
    slot_kind = _inspect_optional(paths.slot_root)
    if slot_kind is None:
        return False
    if slot_kind != "directory":
        _fail(
            "persistence_generation_inventory_unsafe",
            "logical persistence slot root must be a safe directory",
        )
    slot_entries = read_directory_entries(paths.slot_root)
    if slot_entries != {"v1": "directory"}:
        _fail(
            "persistence_generation_inventory_unsafe",
            "logical persistence slot root must contain only the v1 directory",
        )
    version_entries = read_directory_entries(paths.version_root)
    allowed = {
        ".write.lock": "file",
        "generations": "directory",
        "staging": "directory",
    }
    if not set(version_entries).issubset(allowed):
        _fail(
            "persistence_generation_inventory_unsafe",
            "persistence generation version directory contains ambiguous entries",
        )
    if any(kind != allowed[name] for name, kind in version_entries.items()):
        _fail(
            "persistence_generation_inventory_unsafe",
            "persistence generation layout contains an entry of the wrong kind",
        )
    return "generations" in version_entries


def _verify_dag(
    generations: dict[str, dict[str, object]],
) -> tuple[str, ...]:
    if len(generations) > MAX_PERSISTENCE_GENERATIONS:
        _fail(
            "persistence_generation_limit",
            "persistence slot contains too many generations",
        )
    referenced: set[str] = set()
    roots: list[str] = []
    for content_hash, document in generations.items():
        sequence = document["sequence"]
        assert type(sequence) is int
        parents = document["parent_hashes"]
        assert type(parents) is list
        if sequence == 0:
            roots.append(content_hash)
        for parent_hash in parents:
            if parent_hash not in generations:
                _fail(
                    "persistence_generation_missing_parent",
                    f"generation {content_hash} references a missing parent",
                )
            referenced.add(parent_hash)

    visiting: set[str] = set()
    depths: dict[str, int] = {}

    def depth(content_hash: str) -> int:
        if content_hash in depths:
            return depths[content_hash]
        if content_hash in visiting:
            _fail(
                "persistence_generation_cycle",
                "persistence generation graph contains a cycle",
            )
        visiting.add(content_hash)
        document = generations[content_hash]
        parents = document["parent_hashes"]
        assert type(parents) is list
        if not parents:
            result = 1
        else:
            parent_depths = [depth(parent_hash) for parent_hash in parents]
            parent_sequences = []
            for parent_hash in parents:
                parent = generations[parent_hash]
                parent_sequence = parent["sequence"]
                assert type(parent_sequence) is int
                parent_sequences.append(parent_sequence)
            sequence = document["sequence"]
            assert type(sequence) is int
            if sequence != max(parent_sequences) + 1:
                _fail(
                    "persistence_generation_sequence_mismatch",
                    "generation sequence must be one greater than its newest parent",
                )
            result = max(parent_depths) + 1
        visiting.remove(content_hash)
        if result > MAX_PERSISTENCE_GENERATION_DEPTH:
            _fail(
                "persistence_generation_limit",
                "persistence generation graph exceeds its maximum depth",
            )
        depths[content_hash] = result
        return result

    for content_hash in generations:
        depth(content_hash)
    return tuple(
        sorted(
            set(generations) - referenced,
            key=lambda item: item.encode("ascii"),
        )
    )


def _scan_slot(
    root: str | os.PathLike[str],
    slot: object,
    context: GamePersistenceContext,
    *,
    kind: str,
) -> _SlotState:
    checked_context = _require_context(context)
    paths = _paths(root, slot, checked_context, kind=kind)
    legacy = _load_legacy(paths, kind=kind, context=checked_context)
    generations: dict[str, dict[str, object]] = {}
    total_bytes = 0
    if _validate_layout(paths):
        inventory = read_directory_files(
            paths.generations,
            maximum_entries=MAX_PERSISTENCE_GENERATIONS,
            maximum_file_bytes=MAX_PERSISTENCE_GENERATION_BYTES,
            maximum_total_bytes=MAX_PERSISTENCE_GENERATION_TOTAL_BYTES,
        )
        total_bytes = sum(len(payload) for payload in inventory.values())
        for filename, payload in inventory.items():
            if _GENERATION_FILENAME_RE.fullmatch(filename) is None:
                _fail(
                    "persistence_generation_inventory_unsafe",
                    f"unexpected generation inventory entry {filename!r}",
                )
            document = load_persistence_generation_bytes(
                payload,
                context=checked_context,
                expected_kind=kind,
                expected_slot=validate_slot_name(slot),
                source=str(paths.generations / filename),
            )
            content_hash = document["content_hash"]
            sequence = document["sequence"]
            assert type(content_hash) is str and type(sequence) is int
            expected_filename = f"{sequence:020d}-{content_hash}.json"
            if filename != expected_filename:
                _fail(
                    "persistence_generation_filename_mismatch",
                    "generation filename does not match its sequence and content hash",
                )
            if content_hash in generations:
                _fail(
                    "persistence_generation_duplicate",
                    "persistence generation content hash appears more than once",
                )
            generations[content_hash] = document
    tips = _verify_dag(generations)

    if legacy is not None and generations:
        roots = [document for document in generations.values() if document["sequence"] == 0]
        if (
            len(roots) != 1
            or roots[0]["operation"] != "legacy_migration"
            or roots[0]["payload_hash"] != legacy["content_hash"]
            or roots[0]["payload"] != legacy
        ):
            _fail(
                "persistence_legacy_anchor_mismatch",
                "generation zero no longer anchors the exact legacy slot content hash",
            )
    return _SlotState(
        paths=paths,
        generations=generations,
        tips=tips,
        legacy=legacy,
        total_bytes=total_bytes,
    )


def _publish(
    state: _SlotState,
    generation: dict[str, object],
    *,
    root: str | os.PathLike[str],
    slot: object,
    context: GamePersistenceContext,
    kind: str,
) -> Path:
    operation = generation["operation"]
    assert type(operation) is str
    candidate_bytes = serialize_persistence_generation(
        generation,
        context=context,
    )
    _ensure_publication_capacity(
        state,
        operation=operation,
        candidate_bytes=len(candidate_bytes),
    )
    sequence = generation["sequence"]
    content_hash = generation["content_hash"]
    assert type(sequence) is int and type(content_hash) is str
    filename = f"{sequence:020d}-{content_hash}.json"
    published = publish_json_noreplace(
        state.paths.staging,
        state.paths.generations,
        filename,
        generation,
    )
    verified = _scan_slot(root, slot, context, kind=kind)
    if verified.generations.get(content_hash) != generation:
        _fail(
            "persistence_publication_indeterminate",
            "published persistence generation could not be verified in its final DAG",
        )
    return published


def _ensure_publication_capacity(
    state: _SlotState,
    *,
    operation: str,
    candidate_bytes: int | None = None,
) -> None:
    """Reserve the final immutable slot exclusively for conflict resolution."""

    maximum_before_publish = (
        MAX_PERSISTENCE_GENERATIONS
        if operation == "conflict_resolution"
        else MAX_PERSISTENCE_GENERATIONS - 1
    )
    if maximum_before_publish < 1 or len(state.generations) >= maximum_before_publish:
        _fail(
            "persistence_generation_limit",
            "persistence slot has no reserved capacity for this operation",
        )
    if candidate_bytes is not None:
        if (
            type(candidate_bytes) is not int
            or candidate_bytes < 1
            or candidate_bytes > MAX_PERSISTENCE_GENERATION_BYTES
        ):
            _fail(
                "persistence_bytes_exceeded",
                "persistence generation exceeds its per-file byte capacity",
            )
        if state.total_bytes > MAX_PERSISTENCE_GENERATION_TOTAL_BYTES - candidate_bytes:
            _fail(
                "persistence_generation_limit",
                "persistence slot has no remaining total byte capacity",
            )


def _write_slot(
    root: str | os.PathLike[str],
    slot: object,
    value: object,
    context: GamePersistenceContext,
    *,
    kind: str,
) -> Path:
    checked_context = _require_context(context)
    paths = _paths(root, slot, checked_context, kind=kind)
    payload = _validated_payload(value, kind=kind, context=checked_context)
    with held_persistence_lock(paths.lock):
        state = _scan_slot(root, slot, checked_context, kind=kind)
        _ensure_publication_capacity(state, operation="write")
        if state.legacy is not None and not state.generations:
            _fail(
                "persistence_legacy_migration_required",
                "legacy persistence must be migrated explicitly before appending",
            )
        if len(state.tips) > 1:
            _fail(
                "persistence_generation_fork",
                "persistence slot has multiple current tips",
            )
        if not state.tips:
            sequence = 0
            parents: list[str] = []
        else:
            parent_hash = state.tips[0]
            parent_sequence = state.generations[parent_hash]["sequence"]
            assert type(parent_sequence) is int
            sequence = parent_sequence + 1
            parents = [parent_hash]
        generation = build_persistence_generation(
            payload,
            kind=kind,
            slot=slot,
            sequence=sequence,
            parent_hashes=parents,
            operation="write",
            context=checked_context,
        )
        return _publish(
            state,
            generation,
            root=root,
            slot=slot,
            context=checked_context,
            kind=kind,
        )


def _read_slot(
    root: str | os.PathLike[str],
    slot: object,
    context: GamePersistenceContext,
    *,
    kind: str,
) -> dict[str, object]:
    state = _scan_slot(root, slot, context, kind=kind)
    if len(state.tips) > 1:
        _fail(
            "persistence_generation_fork",
            "persistence slot has multiple current tips",
        )
    if state.tips:
        return copy.deepcopy(state.generations[state.tips[0]]["payload"])
    if state.legacy is not None:
        return copy.deepcopy(state.legacy)
    _fail("persistence_slot_missing", "persistence slot does not exist")


def _migrate_legacy_slot(
    root: str | os.PathLike[str],
    slot: object,
    context: GamePersistenceContext,
    *,
    kind: str,
) -> Path:
    checked_context = _require_context(context)
    paths = _paths(root, slot, checked_context, kind=kind)
    with held_persistence_lock(paths.lock):
        state = _scan_slot(root, slot, checked_context, kind=kind)
        _ensure_publication_capacity(state, operation="legacy_migration")
        if state.generations:
            _fail(
                "persistence_migration_conflict",
                "persistence slot already contains generations",
            )
        if state.legacy is None:
            _fail(
                "persistence_legacy_missing",
                "legacy persistence slot does not exist",
            )
        generation = build_persistence_generation(
            state.legacy,
            kind=kind,
            slot=slot,
            sequence=0,
            parent_hashes=[],
            operation="legacy_migration",
            context=checked_context,
        )
        return _publish(
            state,
            generation,
            root=root,
            slot=slot,
            context=checked_context,
            kind=kind,
        )


def _resolve_slot_conflict(
    root: str | os.PathLike[str],
    slot: object,
    value: object,
    context: GamePersistenceContext,
    *,
    kind: str,
) -> Path:
    checked_context = _require_context(context)
    paths = _paths(root, slot, checked_context, kind=kind)
    payload = _validated_payload(value, kind=kind, context=checked_context)
    with held_persistence_lock(paths.lock):
        state = _scan_slot(root, slot, checked_context, kind=kind)
        _ensure_publication_capacity(state, operation="conflict_resolution")
        if len(state.tips) < 2:
            _fail(
                "persistence_generation_no_conflict",
                "persistence slot does not have multiple current tips",
            )
        newest = max(state.generations[content_hash]["sequence"] for content_hash in state.tips)
        assert type(newest) is int
        generation = build_persistence_generation(
            payload,
            kind=kind,
            slot=slot,
            sequence=newest + 1,
            parent_hashes=list(state.tips),
            operation="conflict_resolution",
            context=checked_context,
        )
        return _publish(
            state,
            generation,
            root=root,
            slot=slot,
            context=checked_context,
            kind=kind,
        )


def _rollback_slot(
    root: str | os.PathLike[str],
    slot: object,
    generation_hash: object,
    context: GamePersistenceContext,
    *,
    kind: str,
) -> Path:
    checked_context = _require_context(context)
    target_hash = _sha256(generation_hash, "rollback generation hash")
    paths = _paths(root, slot, checked_context, kind=kind)
    with held_persistence_lock(paths.lock):
        state = _scan_slot(root, slot, checked_context, kind=kind)
        _ensure_publication_capacity(state, operation="rollback")
        if len(state.tips) > 1:
            _fail(
                "persistence_generation_fork",
                "persistence slot has multiple current tips",
            )
        if not state.tips:
            _fail(
                "persistence_generation_unknown",
                "persistence slot has no verified generations",
            )
        target = state.generations.get(target_hash)
        if target is None:
            _fail(
                "persistence_generation_unknown",
                "rollback target is not a verified generation in this slot",
            )
        current_hash = state.tips[0]
        current_sequence = state.generations[current_hash]["sequence"]
        assert type(current_sequence) is int
        generation = build_persistence_generation(
            target["payload"],
            kind=kind,
            slot=slot,
            sequence=current_sequence + 1,
            parent_hashes=[current_hash],
            operation="rollback",
            context=checked_context,
        )
        return _publish(
            state,
            generation,
            root=root,
            slot=slot,
            context=checked_context,
            kind=kind,
        )


def write_game_save_slot(
    root: str | os.PathLike[str],
    slot: object,
    value: object,
    context: GamePersistenceContext,
) -> Path:
    return _write_slot(root, slot, value, context, kind="save")


def write_game_replay_slot(
    root: str | os.PathLike[str],
    slot: object,
    value: object,
    context: GamePersistenceContext,
) -> Path:
    return _write_slot(root, slot, value, context, kind="replay")


def read_game_save_slot(
    root: str | os.PathLike[str],
    slot: object,
    context: GamePersistenceContext,
) -> dict[str, object]:
    return _read_slot(root, slot, context, kind="save")


def read_game_replay_slot(
    root: str | os.PathLike[str],
    slot: object,
    context: GamePersistenceContext,
) -> dict[str, object]:
    return _read_slot(root, slot, context, kind="replay")


def migrate_legacy_game_save_slot(
    root: str | os.PathLike[str],
    slot: object,
    context: GamePersistenceContext,
) -> Path:
    return _migrate_legacy_slot(root, slot, context, kind="save")


def migrate_legacy_game_replay_slot(
    root: str | os.PathLike[str],
    slot: object,
    context: GamePersistenceContext,
) -> Path:
    return _migrate_legacy_slot(root, slot, context, kind="replay")


def resolve_game_save_slot_conflict(
    root: str | os.PathLike[str],
    slot: object,
    value: object,
    context: GamePersistenceContext,
) -> Path:
    return _resolve_slot_conflict(root, slot, value, context, kind="save")


def resolve_game_replay_slot_conflict(
    root: str | os.PathLike[str],
    slot: object,
    value: object,
    context: GamePersistenceContext,
) -> Path:
    return _resolve_slot_conflict(root, slot, value, context, kind="replay")


def rollback_game_save_slot(
    root: str | os.PathLike[str],
    slot: object,
    generation_hash: object,
    context: GamePersistenceContext,
) -> Path:
    return _rollback_slot(root, slot, generation_hash, context, kind="save")


def rollback_game_replay_slot(
    root: str | os.PathLike[str],
    slot: object,
    generation_hash: object,
    context: GamePersistenceContext,
) -> Path:
    return _rollback_slot(root, slot, generation_hash, context, kind="replay")


__all__ = [
    "MAX_PERSISTENCE_GENERATION_BYTES",
    "MAX_PERSISTENCE_GENERATION_DEPTH",
    "MAX_PERSISTENCE_GENERATION_PARENTS",
    "MAX_PERSISTENCE_GENERATION_TOTAL_BYTES",
    "MAX_PERSISTENCE_GENERATIONS",
    "PERSISTENCE_GENERATION_FORMAT",
    "PERSISTENCE_GENERATION_VERSION",
    "build_persistence_generation",
    "load_persistence_generation_bytes",
    "migrate_legacy_game_replay_slot",
    "migrate_legacy_game_save_slot",
    "read_game_replay_slot",
    "read_game_save_slot",
    "resolve_game_replay_slot_conflict",
    "resolve_game_save_slot_conflict",
    "rollback_game_replay_slot",
    "rollback_game_save_slot",
    "serialize_persistence_generation",
    "validate_persistence_generation_document",
    "write_game_replay_slot",
    "write_game_save_slot",
]
