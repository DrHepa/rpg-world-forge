from __future__ import annotations

import os
import sqlite3
import stat
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from isoworld.content.file_stat import path_file_stat
from worldforge.repository_boundary import (
    repository_kind,
    require_standalone_bundle_root,
    require_standalone_game_root,
)
from worldforge.studio.contracts import WORKSPACE_ID_PATTERN, validate_forge_workspace
from worldforge.studio.errors import (
    StudioContractError,
    StudioError,
    conflict,
    invalid_request,
    not_found,
)
from worldforge.studio.storage import StudioStore, decode_object, encode_json, utc_now
from worldforge.world_lifecycle import inspect_world_project

_PARAM_FIELDS = frozenset({"workspace_id", "forge_root", "world_root", "game_root", "bundle_root"})
_ROOT_FIELDS = ("forge_root", "world_root", "game_root", "bundle_root")


def _identity(path: Path, *, context: str) -> tuple[int, int]:
    try:
        info = path_file_stat(path)
    except OSError as exc:
        raise invalid_request(f"Could not inspect {context}: {exc}") from exc
    is_link = stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )
    if is_link or not stat.S_ISDIR(info.st_mode):
        raise invalid_request(f"{context} must be a real directory")
    return info.st_dev, info.st_ino


def _resolved_root(value: object, *, context: str, required: bool) -> Path | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value:
        raise invalid_request(f"{context} must be a non-empty path")
    if unicodedata.normalize("NFC", value) != value:
        raise invalid_request(f"{context} must be NFC normalized")
    supplied = Path(value)
    if supplied.is_symlink():
        raise invalid_request(f"{context} cannot be a symbolic link")
    resolved = supplied.resolve()
    if unicodedata.normalize("NFC", str(resolved)) != str(resolved):
        raise invalid_request(f"{context} resolves to a non-NFC path")
    _identity(resolved, context=context)
    return resolved


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


class WorkspaceManager:
    def __init__(self, store: StudioStore) -> None:
        self.store = store

    def register(self, params: object) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise invalid_request("workspace.register params must be an object")
        unknown = set(params) - _PARAM_FIELDS
        missing = {"forge_root", "world_root"} - set(params)
        if unknown:
            raise invalid_request(
                f"workspace.register contains unknown fields: {', '.join(sorted(unknown))}"
            )
        if missing:
            raise invalid_request(
                f"workspace.register is missing fields: {', '.join(sorted(missing))}"
            )
        workspace_id = params.get("workspace_id") or f"workspace_{uuid.uuid4().hex}"
        if (
            not isinstance(workspace_id, str)
            or WORKSPACE_ID_PATTERN.fullmatch(workspace_id) is None
        ):
            raise invalid_request("workspace_id is not a valid identifier")

        roots: dict[str, Path | None] = {
            "forge_root": _resolved_root(
                params.get("forge_root"), context="Forge root", required=True
            ),
            "world_root": _resolved_root(
                params.get("world_root"), context="world root", required=True
            ),
            "game_root": _resolved_root(
                params.get("game_root"), context="game root", required=False
            ),
            "bundle_root": _resolved_root(
                params.get("bundle_root"), context="bundle root", required=False
            ),
        }
        present = [(field, path) for field, path in roots.items() if path is not None]
        for field, path in present:
            assert path is not None
            if _overlaps(self.store.data_dir, path):
                raise invalid_request(
                    f"Studio data directory must remain outside the {field.replace('_', ' ')}"
                )
        folded: dict[str, tuple[str, Path]] = {}
        identities: dict[str, tuple[int, int]] = {}
        for field, path in present:
            assert path is not None
            folded_path = unicodedata.normalize("NFC", os.path.normcase(str(path))).casefold()
            prior = folded.setdefault(folded_path, (field, path))
            if prior[0] != field:
                raise invalid_request(
                    f"Workspace roots have a casefold collision: {prior[0]}, {field}"
                )
            identities[field] = _identity(path, context=field.replace("_", " "))
        for index, (left_field, left) in enumerate(present):
            for right_field, right in present[index + 1 :]:
                assert left is not None and right is not None
                if identities[left_field] == identities[right_field] or _overlaps(left, right):
                    raise invalid_request(
                        f"Workspace roots overlap or share an identity: {left_field}, {right_field}"
                    )

        forge_root = roots["forge_root"]
        world_root = roots["world_root"]
        assert forge_root is not None and world_root is not None
        if repository_kind(forge_root) != "forge":
            raise invalid_request("Forge root is not the RPG World Forge repository")
        try:
            inspect_world_project(world_root)
        except ValueError as exc:
            raise invalid_request(f"World root is not a canonical world repository: {exc}") from exc
        if roots["game_root"] is not None:
            try:
                roots["game_root"] = require_standalone_game_root(roots["game_root"])
            except ValueError as exc:
                raise invalid_request(str(exc)) from exc
        if roots["bundle_root"] is not None:
            try:
                roots["bundle_root"] = require_standalone_bundle_root(roots["bundle_root"])
            except ValueError as exc:
                raise invalid_request(str(exc)) from exc

        self._reject_registered_collisions(workspace_id, roots, identities)
        record = {
            "format": "rpg-world-forge.forge_workspace",
            "format_version": 1,
            "workspace_id": workspace_id,
            **{
                field: None if roots[field] is None else str(roots[field]) for field in _ROOT_FIELDS
            },
            "created_at": utc_now(),
        }
        try:
            validate_forge_workspace(record)
        except StudioContractError as exc:
            raise invalid_request(str(exc)) from exc
        game_identity = identities.get("game_root")
        bundle_identity = identities.get("bundle_root")
        try:
            with self.store.connection:
                self.store.connection.execute(
                    "INSERT INTO workspaces "
                    "(workspace_id, record_json, forge_dev, forge_ino, world_dev, world_ino, "
                    "game_dev, game_ino, bundle_dev, bundle_ino) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        workspace_id,
                        encode_json(record),
                        *(str(value) for value in identities["forge_root"]),
                        *(str(value) for value in identities["world_root"]),
                        *(
                            (None, None)
                            if game_identity is None
                            else tuple(str(value) for value in game_identity)
                        ),
                        *(
                            (None, None)
                            if bundle_identity is None
                            else tuple(str(value) for value in bundle_identity)
                        ),
                    ),
                )
                self.store.record_event(
                    workspace_id=workspace_id,
                    topic="workspace.registered",
                    entity_type="workspace",
                    entity_id=workspace_id,
                    payload={},
                )
        except sqlite3.IntegrityError as exc:
            raise conflict(f"Workspace {workspace_id} is already registered") from exc
        return record

    def _reject_registered_collisions(
        self,
        workspace_id: str,
        roots: dict[str, Path | None],
        identities: dict[str, tuple[int, int]],
    ) -> None:
        rows = self.store.connection.execute("SELECT * FROM workspaces").fetchall()
        for row in rows:
            if row["workspace_id"] == workspace_id:
                raise conflict(f"Workspace {workspace_id} is already registered")
            existing = decode_object(row["record_json"], context="workspace")
            for field in ("world_root", "game_root", "bundle_root"):
                candidate = roots[field]
                if candidate is None:
                    continue
                candidate_identity = identities[field]
                for existing_field in ("world_root", "game_root", "bundle_root"):
                    existing_value = existing.get(existing_field)
                    if existing_value is None:
                        continue
                    existing_path = Path(existing_value)
                    existing_identity = (
                        int(row[f"{existing_field.removesuffix('_root')}_dev"]),
                        int(row[f"{existing_field.removesuffix('_root')}_ino"]),
                    )
                    same_fold = str(candidate).casefold() == str(existing_path).casefold()
                    if candidate_identity == existing_identity or same_fold:
                        raise conflict(
                            "Repository root is already registered by workspace "
                            f"{row['workspace_id']}"
                        )
                    if _overlaps(candidate, existing_path):
                        raise conflict(f"Repository root overlaps workspace {row['workspace_id']}")

    def get(self, workspace_id: object) -> dict[str, Any]:
        if not isinstance(workspace_id, str):
            raise invalid_request("workspace_id must be a string")
        row = self.store.connection.execute(
            "SELECT record_json FROM workspaces WHERE workspace_id = ?", (workspace_id,)
        ).fetchone()
        if row is None:
            raise not_found(f"Workspace {workspace_id} was not found")
        record = decode_object(row["record_json"], context="workspace")
        try:
            return validate_forge_workspace(record)
        except StudioContractError as exc:
            raise StudioError("internal_error", "Stored workspace is invalid") from exc

    def list(self) -> list[dict[str, Any]]:
        rows = self.store.connection.execute(
            "SELECT record_json FROM workspaces ORDER BY workspace_id"
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            record = decode_object(row["record_json"], context="workspace")
            try:
                result.append(validate_forge_workspace(record))
            except StudioContractError as exc:
                raise StudioError("internal_error", "Stored workspace is invalid") from exc
        return result

    def root_identity(self, workspace_id: str, field: str) -> tuple[int, int] | None:
        if field not in _ROOT_FIELDS:
            raise ValueError(field)
        column = field.removesuffix("_root")
        row = self.store.connection.execute(
            f"SELECT {column}_dev, {column}_ino FROM workspaces WHERE workspace_id = ?",  # noqa: S608
            (workspace_id,),
        ).fetchone()
        if row is None:
            raise not_found(f"Workspace {workspace_id} was not found")
        dev, ino = row[0], row[1]
        return None if dev is None or ino is None else (int(dev), int(ino))
