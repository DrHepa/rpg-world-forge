"""Forge verification boundary for immutable persistence generations."""

from __future__ import annotations

from pathlib import Path

from gamepack_runtime import (
    MAX_PERSISTENCE_GENERATION_BYTES,
    validate_persistence_generation_document,
)
from gamepack_runtime.persistence_io import read_json_object
from worldforge.game_persistence import persistence_context_from_bundle
from worldforge.game_runtime_bundle import verify_game_runtime_bundle


def verify_persistence_generation(
    source: str | Path,
    *,
    bundle_root: str | Path,
) -> dict[str, object]:
    """Verify one generation and its embedded payload against an exact bundle."""

    bundle = verify_game_runtime_bundle(bundle_root)
    try:
        context = persistence_context_from_bundle(bundle)
        document = validate_persistence_generation_document(
            read_json_object(
                source,
                limit=MAX_PERSISTENCE_GENERATION_BYTES,
            ),
            context=context,
        )
        return {
            "content_hash": document["content_hash"],
            "format": document["format"],
            "format_version": document["format_version"],
            "kind": document["kind"],
            "operation": document["operation"],
            "payload_hash": document["payload_hash"],
            "sequence": document["sequence"],
            "slot": document["slot"],
            "status": "verified",
        }
    finally:
        bundle.close()


__all__ = ["verify_persistence_generation"]
