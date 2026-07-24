"""Strict, stdlib-only validation for runtime publication journals."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from isoworld.content.file_stat import (
    descriptor_file_stat,
    file_identity,
    is_link_or_reparse,
    path_file_stat,
)

JOURNAL_FRAME_MAGIC = b"\x1eRWFJ1 "
JOURNAL_FRAME_FOOTER = b"\x1fRWFJ1 "
PUBLICATION_JOURNAL_PATHS = (
    PurePosixPath("game_data/bundle-import.journal.json"),
    PurePosixPath("game_data/.composed-catalog-publication.json"),
)
MAX_JOURNAL_RECORD_BYTES = 16 * 1024 * 1024
MAX_JOURNAL_FILE_BYTES = MAX_JOURNAL_RECORD_BYTES * 16
MAX_JOURNAL_RECORDS = 4096
MAX_JOURNAL_JSON_DEPTH = 64

_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_WORLD_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_RELEASE_ID = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_BUNDLE_KEYS = frozenset(
    {
        "format",
        "format_version",
        "operation_id",
        "state",
        "world_id",
        "release_id",
        "temporary",
        "destination",
        "bundle_hash",
        "catalog_before_hash",
        "catalog_after_hash",
        "directory_identity",
        "created_directories",
    }
)
_CATALOG_KEYS = frozenset(
    {
        "format",
        "format_version",
        "operation_id",
        "state",
        "generation_hash",
        "directory_identity",
        "document",
    }
)
_CATALOG_DOCUMENT_KEYS = frozenset(
    {
        "format",
        "format_version",
        "previous_hash",
        "entries",
        "content_hash",
    }
)
_CATALOG_ENTRY_KEYS = frozenset(
    {
        "world_id",
        "world_content_hash",
        "release_id",
        "profile_id",
        "profile_hash",
        "adapter_id",
        "adapter_version",
        "adapter_hash",
        "composition_hash",
        "bundle_id",
        "bundle_version",
        "bundle_hash",
        "path",
    }
)
_MAX_CATALOG_ENTRIES = 10_000


class PublicationJournalError(ValueError):
    """Raised when a publication journal is unsafe or malformed."""


@dataclass(frozen=True, slots=True)
class PublicationJournalAudit:
    """Exact terminal journals and stable issues found under one game root."""

    terminal_paths: tuple[PurePosixPath, ...]
    issues: tuple[str, ...]


def canonical_journal_record(value: object) -> bytes:
    """Serialize one strict canonical journal record."""

    try:
        document = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        return (document + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise PublicationJournalError(f"journal record is not strict JSON: {exc}") from exc


def journal_frame(payload: bytes) -> bytes:
    """Frame one immutable canonical record for append-only persistence."""

    if not payload or len(payload) > MAX_JOURNAL_RECORD_BYTES:
        raise PublicationJournalError("journal record exceeds its byte limit")
    digest = hashlib.sha256(payload).hexdigest().encode("ascii")
    return (
        JOURNAL_FRAME_MAGIC
        + f"{len(payload):016x}".encode("ascii")
        + b" "
        + digest
        + b"\n"
        + payload
        + JOURNAL_FRAME_FOOTER
        + digest
        + b"\n"
    )


def _legacy_base_record(
    payload: bytes,
    *,
    max_record_bytes: int,
) -> tuple[bytes, int]:
    if not payload.startswith(b"{"):
        raise PublicationJournalError("append-only journal base record is invalid")
    closing: list[int] = []
    in_string = False
    escaped = False
    byte_end: int | None = None
    for index, value in enumerate(payload):
        if in_string:
            if escaped:
                escaped = False
            elif value == ord("\\"):
                escaped = True
            elif value == ord('"'):
                in_string = False
            continue
        if value == ord('"'):
            in_string = True
        elif value == ord("{"):
            closing.append(ord("}"))
        elif value == ord("["):
            closing.append(ord("]"))
        elif value in {ord("}"), ord("]")}:
            if not closing or closing.pop() != value:
                raise PublicationJournalError("append-only journal base record is invalid")
            if not closing:
                byte_end = index + 1
                break
    if byte_end is None:
        raise PublicationJournalError("append-only journal base record is invalid")
    base = payload[:byte_end]
    try:
        text = base.decode("utf-8")
        _value, character_end = json.JSONDecoder().raw_decode(text)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise PublicationJournalError("append-only journal base record is invalid") from exc
    if (
        character_end != len(text)
        or byte_end >= len(payload)
        or payload[byte_end : byte_end + 1] != b"\n"
        or byte_end + 1 > max_record_bytes
    ):
        raise PublicationJournalError("append-only journal base record is invalid")
    return payload[: byte_end + 1], byte_end + 1


def _complete_frame_at(
    payload: bytes,
    cursor: int,
    *,
    max_record_bytes: int,
) -> tuple[bytes, int] | None:
    header_size = len(JOURNAL_FRAME_MAGIC) + 16 + 1 + 64 + 1
    if payload[cursor : cursor + len(JOURNAL_FRAME_MAGIC)] != JOURNAL_FRAME_MAGIC:
        return None
    header_end = cursor + header_size
    if header_end > len(payload):
        return None
    header = payload[cursor + len(JOURNAL_FRAME_MAGIC) : header_end]
    if (
        any(value not in b"0123456789abcdef" for value in header[:16])
        or header[16:17] != b" "
        or header[-1:] != b"\n"
    ):
        return None
    try:
        record_size = int(header[:16], 16)
        expected_hash = header[17:81].decode("ascii")
    except (UnicodeDecodeError, ValueError):
        return None
    if (
        record_size < 1
        or record_size > max_record_bytes
        or _HEX_64.fullmatch(expected_hash) is None
    ):
        return None
    record_end = header_end + record_size
    footer = JOURNAL_FRAME_FOOTER + expected_hash.encode("ascii") + b"\n"
    footer_end = record_end + len(footer)
    if footer_end > len(payload):
        return None
    record = payload[header_end:record_end]
    if (
        payload[record_end:footer_end] != footer
        or hashlib.sha256(record).hexdigest() != expected_hash
    ):
        return None
    return record, footer_end


def _plausible_partial_frame(fragment: bytes) -> bool:
    if not fragment:
        return False
    if len(fragment) < len(JOURNAL_FRAME_MAGIC):
        return JOURNAL_FRAME_MAGIC.startswith(fragment)
    if not fragment.startswith(JOURNAL_FRAME_MAGIC):
        return False
    header_offset = len(JOURNAL_FRAME_MAGIC)
    header_length = 16 + 1 + 64 + 1
    header_fragment = fragment[header_offset : header_offset + header_length]
    for index, value in enumerate(header_fragment):
        if index < 16:
            if value not in b"0123456789abcdef":
                return False
        elif index == 16:
            if value != ord(" "):
                return False
        elif index < 81:
            if value not in b"0123456789abcdef":
                return False
        elif value != ord("\n"):
            return False
    if len(header_fragment) < header_length:
        return True
    record_size = int(header_fragment[:16], 16)
    expected_hash = header_fragment[17:81].decode("ascii")
    if record_size < 1 or record_size > MAX_JOURNAL_RECORD_BYTES:
        return False
    record_offset = header_offset + header_length
    available_record = fragment[record_offset : record_offset + record_size]
    if len(available_record) < record_size:
        return True
    if hashlib.sha256(available_record).hexdigest() != expected_hash:
        return False
    footer = JOURNAL_FRAME_FOOTER + expected_hash.encode("ascii") + b"\n"
    footer_fragment = fragment[record_offset + record_size :]
    return len(footer_fragment) < len(footer) and footer.startswith(footer_fragment)


def recover_last_complete_payload(
    payload: bytes,
    *,
    max_record_bytes: int,
) -> bytes:
    """Return the last complete valid record, tolerating interrupted append tails."""

    base, cursor = _legacy_base_record(
        payload,
        max_record_bytes=max_record_bytes,
    )
    last = base
    while cursor < len(payload):
        if not payload.startswith(JOURNAL_FRAME_MAGIC, cursor):
            break
        complete = _complete_frame_at(
            payload,
            cursor,
            max_record_bytes=max_record_bytes,
        )
        if complete is None:
            break
        last, cursor = complete
    return last


def _raise_invalid_frame_tail(payload: bytes, cursor: int) -> None:
    later = payload.find(JOURNAL_FRAME_MAGIC, cursor + 1)
    fragment_end = later if later >= 0 else len(payload)
    if _plausible_partial_frame(payload[cursor:fragment_end]):
        raise PublicationJournalError("journal contains a partial frame tail")
    raise PublicationJournalError("journal contains a malformed frame")


def _strict_records(payload: bytes) -> tuple[bytes, ...]:
    base, cursor = _legacy_base_record(
        payload,
        max_record_bytes=MAX_JOURNAL_RECORD_BYTES,
    )
    records = [base]
    while cursor < len(payload):
        if len(records) >= MAX_JOURNAL_RECORDS:
            raise PublicationJournalError("journal contains too many records")
        if not payload.startswith(JOURNAL_FRAME_MAGIC, cursor):
            _raise_invalid_frame_tail(payload, cursor)
        complete = _complete_frame_at(
            payload,
            cursor,
            max_record_bytes=MAX_JOURNAL_RECORD_BYTES,
        )
        if complete is None:
            _raise_invalid_frame_tail(payload, cursor)
        record, cursor = complete
        records.append(record)
    return tuple(records)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number: {value}")
    return parsed


def _validate_json_depth(value: object) -> None:
    pending = [(value, 0)]
    while pending:
        current, depth = pending.pop()
        if depth > MAX_JOURNAL_JSON_DEPTH:
            raise PublicationJournalError("journal record exceeds its JSON depth limit")
        if isinstance(current, dict):
            pending.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            pending.extend((child, depth + 1) for child in current)


def _decode_record(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
            parse_float=_finite_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise PublicationJournalError(f"journal record is not strict JSON: {exc}") from exc
    _validate_json_depth(value)
    if not isinstance(value, dict):
        raise PublicationJournalError("journal record root is not an object")
    if canonical_journal_record(value) != payload:
        raise PublicationJournalError("journal record is not canonically serialized")
    return value


def _identity(value: object, *, required: bool) -> tuple[int, int] | None:
    if value is None and not required:
        return None
    if (
        not isinstance(value, dict)
        or set(value) != {"device", "inode"}
        or type(value.get("device")) is not int
        or type(value.get("inode")) is not int
        or value["device"] < 0
        or value["inode"] < 0
    ):
        raise PublicationJournalError("journal directory identity is invalid")
    return value["device"], value["inode"]


def _sha256(value: object, context: str) -> str:
    if type(value) is not str or _HEX_64.fullmatch(value) is None:
        raise PublicationJournalError(f"{context} is not a lowercase SHA-256")
    return value


def _bundle_record(value: dict[str, Any]) -> dict[str, Any]:
    if set(value) != _BUNDLE_KEYS:
        raise PublicationJournalError("bundle journal keys are not closed")
    if (
        value["format"] != "isoworld.bundle_import_journal"
        or type(value["format_version"]) is not int
        or value["format_version"] != 1
    ):
        raise PublicationJournalError("bundle journal format is invalid")
    operation_id = value["operation_id"]
    state = value["state"]
    world_id = value["world_id"]
    release_id = value["release_id"]
    if type(operation_id) is not str or _HEX_32.fullmatch(operation_id) is None:
        raise PublicationJournalError("bundle journal operation_id is invalid")
    if type(state) is not str or state not in {"intent", "copying", "ready", "committed"}:
        raise PublicationJournalError("bundle journal state is invalid")
    if type(world_id) is not str or _WORLD_ID.fullmatch(world_id) is None:
        raise PublicationJournalError("bundle journal world_id is invalid")
    if type(release_id) is not str or _RELEASE_ID.fullmatch(release_id) is None:
        raise PublicationJournalError("bundle journal release_id is invalid")
    temporary = f"game_data/worlds/{world_id}/.{release_id}.import-{operation_id}"
    destination = f"game_data/worlds/{world_id}/{release_id}"
    if value["temporary"] != temporary or value["destination"] != destination:
        raise PublicationJournalError("bundle journal paths are inconsistent")
    for field in ("bundle_hash", "catalog_before_hash", "catalog_after_hash"):
        _sha256(value[field], f"bundle journal {field}")
    identity = _identity(
        value["directory_identity"],
        required=state in {"copying", "ready"},
    )
    if state == "intent" and identity is not None:
        raise PublicationJournalError("bundle journal intent claims a directory")
    created = value["created_directories"]
    if not isinstance(created, list):
        raise PublicationJournalError("bundle journal created_directories is invalid")
    allowed = {
        "game_data",
        "game_data/worlds",
        f"game_data/worlds/{world_id}",
    }
    seen: set[str] = set()
    for record in created:
        if (
            not isinstance(record, dict)
            or set(record) != {"path", "device", "inode"}
            or type(record["path"]) is not str
            or record["path"] not in allowed
            or record["path"] in seen
        ):
            raise PublicationJournalError("bundle journal created directory is invalid")
        _identity(
            {"device": record["device"], "inode": record["inode"]},
            required=True,
        )
        seen.add(record["path"])
    return value


def _canonical_payload_hash(value: dict[str, Any]) -> str:
    payload = dict(value)
    payload.pop("content_hash", None)
    try:
        canonical = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        encoded = canonical.encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise PublicationJournalError("catalog journal document is not strict JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _catalog_identifier(value: object, context: str) -> str:
    if type(value) is not str or _WORLD_ID.fullmatch(value) is None:
        raise PublicationJournalError(f"{context} is not a canonical identifier")
    return value


def _catalog_semver(value: object, context: str) -> str:
    if type(value) is not str or _RELEASE_ID.fullmatch(value) is None:
        raise PublicationJournalError(f"{context} is not a canonical semantic version")
    return value


def _catalog_entry(
    value: object,
    index: int,
) -> tuple[str, str, str, str, str, str, str]:
    if not isinstance(value, dict) or set(value) != _CATALOG_ENTRY_KEYS:
        raise PublicationJournalError(f"catalog journal entry {index} is not closed")
    world_id = _catalog_identifier(value["world_id"], f"catalog entry {index} world_id")
    release_id = _catalog_semver(value["release_id"], f"catalog entry {index} release_id")
    profile_id = _catalog_identifier(value["profile_id"], f"catalog entry {index} profile_id")
    adapter_id = _catalog_identifier(value["adapter_id"], f"catalog entry {index} adapter_id")
    adapter_version = _catalog_semver(
        value["adapter_version"],
        f"catalog entry {index} adapter_version",
    )
    bundle_id = _catalog_identifier(value["bundle_id"], f"catalog entry {index} bundle_id")
    bundle_version = _catalog_semver(
        value["bundle_version"],
        f"catalog entry {index} bundle_version",
    )
    for field in (
        "world_content_hash",
        "profile_hash",
        "adapter_hash",
        "composition_hash",
        "bundle_hash",
    ):
        _sha256(value[field], f"catalog entry {index} {field}")
    expected_path = (
        "game_data/compositions/"
        f"{world_id}/{release_id}/{profile_id}/{adapter_id}/{adapter_version}/"
        f"{bundle_id}/{bundle_version}"
    )
    if type(value["path"]) is not str or value["path"] != expected_path:
        raise PublicationJournalError(f"catalog entry {index} path is invalid")
    return (
        world_id,
        release_id,
        profile_id,
        adapter_id,
        adapter_version,
        bundle_id,
        bundle_version,
    )


def _catalog_entries(value: object) -> None:
    if not isinstance(value, list) or len(value) > _MAX_CATALOG_ENTRIES:
        raise PublicationJournalError("catalog journal entries are not a bounded list")
    identities = [_catalog_entry(entry, index) for index, entry in enumerate(value)]
    if identities != sorted(set(identities)):
        raise PublicationJournalError("catalog journal entries are not canonically ordered")

    world_hashes: dict[tuple[object, object], object] = {}
    profile_hashes: dict[object, object] = {}
    adapter_hashes: dict[tuple[object, object], object] = {}
    bundle_hashes: dict[tuple[object, object], object] = {}
    unique_bundle_hashes: set[object] = set()
    unique_paths: set[object] = set()
    for entry in value:
        assert isinstance(entry, dict)
        correlations = (
            (
                world_hashes,
                (entry["world_id"], entry["release_id"]),
                entry["world_content_hash"],
            ),
            (profile_hashes, entry["profile_id"], entry["profile_hash"]),
            (
                adapter_hashes,
                (entry["adapter_id"], entry["adapter_version"]),
                entry["adapter_hash"],
            ),
            (
                bundle_hashes,
                (entry["bundle_id"], entry["bundle_version"]),
                entry["bundle_hash"],
            ),
        )
        for known, key, digest in correlations:
            previous = known.setdefault(key, digest)
            if previous != digest:
                raise PublicationJournalError("catalog journal entry identity is inconsistent")
        bundle_hash = entry["bundle_hash"]
        path = entry["path"]
        if bundle_hash in unique_bundle_hashes or path in unique_paths:
            raise PublicationJournalError("catalog journal entries reuse immutable identity")
        unique_bundle_hashes.add(bundle_hash)
        unique_paths.add(path)


def _catalog_document(value: object, generation_hash: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _CATALOG_DOCUMENT_KEYS:
        raise PublicationJournalError("catalog journal document is not closed")
    if (
        value["format"] != "isoworld.composed_runtime_catalog_generation"
        or type(value["format_version"]) is not int
        or value["format_version"] != 1
    ):
        raise PublicationJournalError("catalog journal document format is invalid")
    previous_hash = value["previous_hash"]
    if previous_hash is not None:
        _sha256(previous_hash, "catalog journal document previous_hash")
    _catalog_entries(value["entries"])
    content_hash = _sha256(
        value["content_hash"],
        "catalog journal document content_hash",
    )
    if content_hash != generation_hash or _canonical_payload_hash(value) != generation_hash:
        raise PublicationJournalError("catalog journal document identity is invalid")
    return value


def _catalog_record(value: dict[str, Any]) -> dict[str, Any]:
    if set(value) != _CATALOG_KEYS:
        raise PublicationJournalError("catalog journal keys are not closed")
    if (
        value["format"] != "isoworld.composed_catalog_publication"
        or type(value["format_version"]) is not int
        or value["format_version"] not in {1, 2}
    ):
        raise PublicationJournalError("catalog journal format is invalid")
    operation_id = value["operation_id"]
    state = value["state"]
    if type(operation_id) is not str or _HEX_32.fullmatch(operation_id) is None:
        raise PublicationJournalError("catalog journal operation_id is invalid")
    if type(state) is not str or state not in {"intent", "copying", "ready", "committed"}:
        raise PublicationJournalError("catalog journal state is invalid")
    generation_hash = _sha256(
        value["generation_hash"],
        "catalog journal generation_hash",
    )
    identity = _identity(
        value["directory_identity"],
        required=state in {"copying", "ready"},
    )
    if state == "intent" and identity is not None:
        raise PublicationJournalError("catalog journal intent claims a directory")
    _catalog_document(value["document"], generation_hash)
    return value


def _immutable_fields(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in record.items() if key not in {"state", "directory_identity"}
    }


def _validate_transition(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> None:
    if previous["state"] != "committed":
        if current["operation_id"] != previous["operation_id"]:
            raise PublicationJournalError("active journal operation_id changed")
        previous_immutable = _immutable_fields(previous)
        current_immutable = _immutable_fields(current)
        if (
            previous["format"] == "isoworld.bundle_import_journal"
            and previous["state"] == "intent"
            and current["state"] == "copying"
        ):
            previous_created = previous_immutable.pop("created_directories")
            current_created = current_immutable.pop("created_directories")
            if previous_created and current_created != previous_created:
                raise PublicationJournalError("active journal created directory identities changed")
        if current_immutable != previous_immutable:
            raise PublicationJournalError("active journal immutable fields changed")
        allowed = {
            "intent": {"copying", "committed"},
            "copying": {"ready", "committed"},
            "ready": {"committed"},
        }
        if current["state"] not in allowed[previous["state"]]:
            raise PublicationJournalError("journal state transition is invalid")
        previous_identity = previous["directory_identity"]
        current_identity = current["directory_identity"]
        if previous["state"] == "intent" and current["state"] == "copying":
            if current_identity is None:
                raise PublicationJournalError("journal directory identity is unavailable")
        elif current_identity != previous_identity:
            raise PublicationJournalError("journal directory identity changed")
        return

    if current["state"] != "intent" or current["operation_id"] == previous["operation_id"]:
        raise PublicationJournalError("journal operation after committed is invalid")
    if previous["format"] == "isoworld.bundle_import_journal":
        expected_catalog_hash = (
            previous["catalog_after_hash"]
            if previous["directory_identity"] is not None
            else previous["catalog_before_hash"]
        )
        if current["catalog_before_hash"] != expected_catalog_hash:
            raise PublicationJournalError("bundle journal catalog hash chain is disconnected")
        return

    previous_version = previous["format_version"]
    current_version = current["format_version"]
    if previous_version == 2:
        if (
            current_version != 1
            or current["generation_hash"] != previous["generation_hash"]
            or current["document"] != previous["document"]
        ):
            raise PublicationJournalError(
                "composed import is not bound to its catalog publication phase"
            )
        return
    effective_head = (
        previous["generation_hash"]
        if previous["directory_identity"] is not None
        else previous["document"]["previous_hash"]
    )
    if current["document"]["previous_hash"] != effective_head:
        raise PublicationJournalError("catalog journal generation chain is disconnected")


def _journal_records(path: Path) -> tuple[dict[str, Any], ...]:
    payload = _read_journal_file(path)
    records = tuple(_decode_record(record) for record in _strict_records(payload))
    validator = _bundle_record if path.name == "bundle-import.journal.json" else _catalog_record
    validated = tuple(validator(record) for record in records)
    for previous, current in zip(validated, validated[1:], strict=False):
        _validate_transition(previous, current)
    return validated


def _read_journal_file(path: Path) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        before = descriptor_file_stat(descriptor)
        named_before = path_file_stat(path)
        if (
            is_link_or_reparse(before)
            or is_link_or_reparse(named_before)
            or not stat.S_ISREG(before.st_mode)
            or not stat.S_ISREG(named_before.st_mode)
            or before.st_nlink != 1
            or named_before.st_nlink != 1
            or file_identity(before) != file_identity(named_before)
            or before.st_size > MAX_JOURNAL_FILE_BYTES
        ):
            raise PublicationJournalError("journal file is unsafe")
        chunks = bytearray()
        while len(chunks) <= MAX_JOURNAL_FILE_BYTES:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, MAX_JOURNAL_FILE_BYTES + 1 - len(chunks)),
            )
            if not chunk:
                break
            chunks.extend(chunk)
        if len(chunks) > MAX_JOURNAL_FILE_BYTES:
            raise PublicationJournalError("journal file exceeds its byte limit")
        after = descriptor_file_stat(descriptor)
        named_after = path_file_stat(path)
        if (
            is_link_or_reparse(after)
            or is_link_or_reparse(named_after)
            or not stat.S_ISREG(after.st_mode)
            or not stat.S_ISREG(named_after.st_mode)
            or after.st_nlink != 1
            or named_after.st_nlink != 1
            or file_identity(after) != file_identity(before)
            or file_identity(named_after) != file_identity(before)
            or after.st_size != before.st_size
            or named_after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or named_after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
            or named_after.st_ctime_ns != before.st_ctime_ns
            or after.st_mode != before.st_mode
            or named_after.st_mode != before.st_mode
            or getattr(after, "st_file_attributes", 0) != getattr(before, "st_file_attributes", 0)
            or getattr(named_after, "st_file_attributes", 0)
            != getattr(before, "st_file_attributes", 0)
        ):
            raise PublicationJournalError("journal file changed while reading")
        return bytes(chunks)
    except PublicationJournalError:
        raise
    except OSError as exc:
        raise PublicationJournalError(f"could not read journal file: {exc}") from exc
    finally:
        primary = sys.exception()
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as exc:
                if primary is not None:
                    primary.add_note(f"publication journal cleanup failed: {exc}")
                else:
                    raise PublicationJournalError(f"could not close journal file: {exc}") from exc


def _safe_detail(error: BaseException) -> str:
    return " ".join(str(error).split())[:512] or error.__class__.__name__


def audit_publication_journals(root: str | Path) -> PublicationJournalAudit:
    """Validate exact publication journals and require a complete committed tip."""

    base = Path(root)
    terminal: list[PurePosixPath] = []
    issues: list[str] = []
    for relative in PUBLICATION_JOURNAL_PATHS:
        path = base / relative
        try:
            path_file_stat(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            issues.append(f"JOURNAL_INVALID:{relative.as_posix()}:{_safe_detail(exc)}")
            continue
        try:
            records = _journal_records(path)
            state = records[-1]["state"]
            if state != "committed":
                issues.append(f"JOURNAL_ACTIVE:{relative.as_posix()}:{state}")
            elif (
                relative == PurePosixPath("game_data/.composed-catalog-publication.json")
                and records[-1]["format_version"] == 2
            ):
                issues.append(f"JOURNAL_ACTIVE:{relative.as_posix()}:composed_import_committed")
            else:
                terminal.append(relative)
        except PublicationJournalError as exc:
            code = (
                "JOURNAL_PARTIAL"
                if str(exc).startswith("journal contains a partial")
                else "JOURNAL_INVALID"
            )
            issues.append(f"{code}:{relative.as_posix()}:{_safe_detail(exc)}")
    return PublicationJournalAudit(
        terminal_paths=tuple(sorted(terminal, key=PurePosixPath.as_posix)),
        issues=tuple(sorted(set(issues))),
    )
