"""Forge-side verification boundary for neutral game saves and replays."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gamepack_runtime import (
    MAX_GAME_REPLAY_BYTES,
    MAX_GAME_SAVE_BYTES,
    build_game_persistence_context,
    play_game_replay,
    restore_game_save,
    validate_game_replay_document,
    validate_game_save_document,
)
from gamepack_runtime.persistence_io import decode_json_object, read_json_object
from gamepack_runtime.runtime_io import load_gamepack_bytes
from worldforge.game_runtime_bundle import (
    VerifiedGameRuntimeBundle,
    verify_game_runtime_bundle,
)


def _bundle_document(
    bundle: VerifiedGameRuntimeBundle,
    relative: str,
    *,
    limit: int = MAX_GAME_REPLAY_BYTES,
) -> dict[str, Any]:
    return decode_json_object(
        bundle.read_bytes(relative),
        source=f"{bundle.root}/{relative}",
        limit=limit,
    )


def persistence_context_from_bundle(
    bundle: VerifiedGameRuntimeBundle,
):
    """Construct the neutral context from one integrally verified runtime bundle."""

    manifest = bundle.manifest
    contracts = manifest["contracts"]
    gamepack_path = contracts["gamepack"]["path"]
    composition_path = contracts["runtime_composition"]["path"]
    adapter_path = contracts["runtime_adapter"]["path"]
    gamepack = load_gamepack_bytes(
        bundle.read_bytes(gamepack_path),
        source=f"{bundle.root}/{gamepack_path}",
    )
    composition = _bundle_document(bundle, composition_path)
    adapter = _bundle_document(bundle, adapter_path)
    return build_game_persistence_context(
        gamepack,
        composition,
        manifest,
        adapter,
    )


def verify_game_save(
    source: str | Path,
    *,
    bundle_root: str | Path,
) -> dict[str, object]:
    """Verify one save against an exact integral runtime bundle."""

    bundle = verify_game_runtime_bundle(bundle_root)
    try:
        context = persistence_context_from_bundle(bundle)
        document = validate_game_save_document(
            read_json_object(source, limit=MAX_GAME_SAVE_BYTES),
            context,
        )
        restore_game_save(context, document)
        return {
            "content_hash": document["content_hash"],
            "format": document["format"],
            "format_version": document["format_version"],
            "id": document["save_id"],
            "restored_state_hash": document["state"]["restored_state_hash"],
            "runtime_bundle_hash": context.runtime_bundle_identity["content_hash"],
            "status": "verified",
            "type": "save",
        }
    finally:
        bundle.close()


def verify_game_replay(
    source: str | Path,
    *,
    bundle_root: str | Path,
) -> dict[str, object]:
    """Verify one replay structurally and re-execute its accepted trace."""

    bundle = verify_game_runtime_bundle(bundle_root)
    try:
        context = persistence_context_from_bundle(bundle)
        document = validate_game_replay_document(
            read_json_object(source, limit=MAX_GAME_REPLAY_BYTES),
            context,
        )
        session = play_game_replay(context, document)
        return {
            "actions": len(document["steps"]),
            "content_hash": document["content_hash"],
            "final_state_hash": session.state_hash,
            "format": document["format"],
            "format_version": document["format_version"],
            "id": document["replay_id"],
            "runtime_bundle_hash": context.runtime_bundle_identity["content_hash"],
            "status": "verified",
            "type": "replay",
        }
    finally:
        bundle.close()


__all__ = [
    "persistence_context_from_bundle",
    "verify_game_replay",
    "verify_game_save",
]
