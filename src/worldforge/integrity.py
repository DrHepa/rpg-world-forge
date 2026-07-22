from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json_bytes(payload: object) -> bytes:
    """Encode one deterministic pretty JSON document as explicit UTF-8 LF bytes."""

    document = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return (document + "\n").encode("utf-8")


def canonical_payload_hash(payload: dict[str, Any], *, hash_field: str = "content_hash") -> str:
    """Return the canonical SHA-256 used by compiled forge artifacts."""

    canonical_payload = dict(payload)
    canonical_payload.pop(hash_field, None)
    canonical = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def declared_hash_matches(payload: dict[str, Any], *, hash_field: str = "content_hash") -> bool:
    declared = payload.get(hash_field)
    return isinstance(declared, str) and declared == canonical_payload_hash(
        payload,
        hash_field=hash_field,
    )
