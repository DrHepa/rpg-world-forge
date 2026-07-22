from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from isoworld.content.loader import load_worldpack
from isoworld.content.renderpack import RenderPackError, load_renderpack
from worldforge.assets import (
    AssetManifestError,
    _read_json,
    _resolve_inside,
    validate_asset_manifest,
)
from worldforge.integrity import canonical_payload_hash


class RenderPackBuildError(ValueError):
    """Raised when approved assets cannot become a runtime renderpack."""


_DIR_FD_PUBLICATION = os.name == "posix" and all(
    function in os.supports_dir_fd for function in (os.mkdir, os.open, os.stat, os.unlink)
)


def _safe_directory_identity(path: Path) -> tuple[int, int]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise RenderPackBuildError(f"Could not verify publication directory {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise RenderPackBuildError(f"Publication parent is not a safe directory: {path}")
    return info.st_dev, info.st_ino


@contextmanager
def _open_verified_parent(path: Path, expected: tuple[int, int]) -> Iterator[int | None]:
    if _safe_directory_identity(path) != expected:
        raise RenderPackBuildError(f"Publication parent changed before use: {path}")
    if not _DIR_FD_PUBLICATION:
        yield None
        if _safe_directory_identity(path) != expected:
            raise RenderPackBuildError(f"Publication parent changed during use: {path}")
        return
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RenderPackBuildError(f"Could not pin publication parent {path}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != expected:
            raise RenderPackBuildError(f"Publication parent changed before use: {path}")
        yield descriptor
    finally:
        os.close(descriptor)


def _safe_new_output_path(path: str | Path) -> Path:
    """Create safe parents and require a brand-new standalone deliverable path."""

    requested = Path(path)
    destination = Path(os.path.abspath(requested))
    if not destination.name or destination.name in {".", "..", "runtime-assets"}:
        raise RenderPackBuildError("The renderpack output path must name a regular file")
    parent = destination.parent
    current = Path(parent.anchor)
    for part in parent.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            try:
                current.mkdir()
            except FileExistsError:
                pass
            try:
                info = current.lstat()
            except OSError as exc:
                raise RenderPackBuildError(
                    f"Could not verify renderpack output parent {current}: {exc}"
                ) from exc
        except OSError as exc:
            raise RenderPackBuildError(
                f"Could not verify renderpack output parent {current}: {exc}"
            ) from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise RenderPackBuildError(
                f"Renderpack output parent is not a safe directory: {current}"
            )
    try:
        destination.lstat()
    except FileNotFoundError:
        return destination
    except OSError as exc:
        raise RenderPackBuildError(
            f"Could not inspect renderpack output {destination}: {exc}"
        ) from exc
    raise RenderPackBuildError(f"Refusing to overwrite renderpack output {destination}")


def _new_directory(path: Path, parent_identity: tuple[int, int]) -> tuple[int, int]:
    with _open_verified_parent(path.parent, parent_identity) as parent_fd:
        if parent_fd is not None:
            os.mkdir(path.name, dir_fd=parent_fd)
            info = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        else:
            path.mkdir()
            info = path.lstat()
    try:
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise RenderPackBuildError(f"Published directory is not safe: {path}")
    except Exception:
        _remove_owned_directory(path, (info.st_dev, info.st_ino))
        raise
    return info.st_dev, info.st_ino


def _remove_owned_file_at(
    parent_fd: int | None,
    parent: Path,
    name: str,
    identity: tuple[int, int],
) -> None:
    try:
        if parent_fd is not None:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        else:
            info = (parent / name).lstat()
        if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != identity:
            return
        if parent_fd is not None:
            os.unlink(name, dir_fd=parent_fd)
        else:
            (parent / name).unlink()
    except OSError:
        pass


def _publish_new_file(
    source: Path,
    destination: Path,
    parent_identity: tuple[int, int],
) -> tuple[int, int]:
    """Copy a staged file with kernel-enforced no-overwrite semantics."""

    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    with _open_verified_parent(destination.parent, parent_identity) as parent_fd:
        descriptor: int | None = None
        identity: tuple[int, int] | None = None
        try:
            if parent_fd is not None:
                descriptor = os.open(destination.name, flags, 0o666, dir_fd=parent_fd)
            else:
                descriptor = os.open(destination, flags, 0o666)
            opened = os.fstat(descriptor)
            identity = (opened.st_dev, opened.st_ino)
            with source.open("rb") as input_file, os.fdopen(descriptor, "wb") as output_file:
                descriptor = None
                shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
                output_file.flush()
                os.fsync(output_file.fileno())
                info = os.fstat(output_file.fileno())
            return info.st_dev, info.st_ino
        except Exception:
            if descriptor is not None:
                os.close(descriptor)
            if identity is not None:
                _remove_owned_file_at(
                    parent_fd,
                    destination.parent,
                    destination.name,
                    identity,
                )
            raise


def _remove_owned_file(path: Path, identity: tuple[int, int]) -> None:
    try:
        info = path.lstat()
    except OSError:
        return
    if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == identity:
        path.unlink(missing_ok=True)


def _remove_owned_directory(path: Path, identity: tuple[int, int]) -> None:
    try:
        info = path.lstat()
    except OSError:
        return
    if stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == identity:
        try:
            path.rmdir()
        except OSError:
            # Never remove files introduced by another writer during cleanup.
            pass


def build_renderpack(
    manifest_path: str | Path,
    worldpack_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    manifest_file = Path(manifest_path)
    worldpack_file = Path(worldpack_path)
    try:
        manifest = _read_json(manifest_file)
    except AssetManifestError as exc:
        raise RenderPackBuildError(str(exc)) from exc
    version = manifest.get("format_version")
    issues = validate_asset_manifest(
        manifest_file,
        profile="build" if version == 3 else "release",
        worldpack_path=worldpack_file,
    )
    if issues:
        raise RenderPackBuildError("; ".join(str(issue) for issue in issues))
    if version not in {2, 3}:
        raise RenderPackBuildError("Building a renderpack requires asset manifest version 2 or 3")
    if version == 3:
        from worldforge.asset_io import read_json_object, verify_artifact_reference

        try:
            target_path = verify_artifact_reference(
                manifest_file.parent.resolve(),
                manifest.get("target"),
                context="target",
            )
            target = read_json_object(target_path)
        except ValueError as exc:
            raise RenderPackBuildError(str(exc)) from exc
        if target.get("delivery_profile") != "renderpack_v1" or target.get("dimension") not in {
            "2d",
            "2_5d",
        }:
            raise RenderPackBuildError("3d targets compile to assetpack, not renderpack")

    output = _safe_new_output_path(output_path)
    source_root = manifest_file.parent.resolve()
    runtime_root = output.parent
    runtime_root_identity = _safe_directory_identity(runtime_root)
    runtime_assets = runtime_root / "runtime-assets"
    try:
        runtime_assets.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RenderPackBuildError(f"Could not inspect runtime asset output: {exc}") from exc
    else:
        raise RenderPackBuildError(f"Refusing to overwrite runtime asset output {runtime_assets}")

    stage = Path(tempfile.mkdtemp(prefix=f"worldforge-{output.name}.stage-"))
    published_files: list[tuple[Path, tuple[int, int]]] = []
    published_directories: list[tuple[Path, tuple[int, int]]] = []
    directory_identities = {runtime_root: runtime_root_identity}
    try:
        compiled_assets: list[dict[str, Any]] = []
        for asset in sorted(manifest["assets"], key=lambda item: item["id"]):
            if version == 3 and asset.get("status") != "processed":
                continue
            files: list[dict[str, Any]] = []
            for index, item in enumerate(asset["outputs"]):
                source = _resolve_inside(source_root, item["runtime_file"])
                if source is None or not source.is_file():
                    raise RenderPackBuildError(
                        f"Processed output disappeared: {item['runtime_file']}"
                    )
                relative = Path("runtime-assets") / asset["id"] / f"{index:02d}_{source.name}"
                destination = stage / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                files.append(
                    {
                        "role": item["role"],
                        "path": relative.as_posix(),
                        "sha256": item["sha256"],
                        "media_type": item["media_type"],
                    }
                )
            compiled_assets.append({"id": asset["id"], "kind": asset["kind"], "files": files})

        compiled_ids = {asset["id"] for asset in compiled_assets}
        binding_keys = {"slot", "asset_id", "clip", "moving_clip", "scale", "layer"}
        runtime_bindings = [
            {key: value for key, value in binding.items() if key in binding_keys}
            for binding in manifest["bindings"]
            if binding.get("asset_id") in compiled_ids
        ]
        payload: dict[str, Any] = {
            "format": "isoworld.renderpack",
            "format_version": 1,
            "world_id": manifest["world_id"],
            "world_content_hash": manifest["world_content_hash"],
            "assets": compiled_assets,
            "bindings": sorted(runtime_bindings, key=lambda item: item["slot"]),
        }
        payload["content_hash"] = canonical_payload_hash(payload)
        staged_output = stage / output.name
        staged_output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            loaded_worldpack = load_worldpack(worldpack_file)
            with load_renderpack(staged_output, loaded_worldpack):
                pass
        except RenderPackError as exc:
            raise RenderPackBuildError(
                f"Compiled renderpack failed runtime validation: {exc}"
            ) from exc

        runtime_assets_identity = _new_directory(runtime_assets, runtime_root_identity)
        published_directories.append((runtime_assets, runtime_assets_identity))
        directory_identities[runtime_assets] = runtime_assets_identity
        staged_runtime_assets = stage / "runtime-assets"
        for staged_directory in sorted(
            (path for path in staged_runtime_assets.rglob("*") if path.is_dir()),
            key=lambda path: (len(path.relative_to(staged_runtime_assets).parts), path.as_posix()),
        ):
            final_directory = runtime_assets / staged_directory.relative_to(staged_runtime_assets)
            final_identity = _new_directory(
                final_directory,
                directory_identities[final_directory.parent],
            )
            published_directories.append((final_directory, final_identity))
            directory_identities[final_directory] = final_identity
        for staged_file in sorted(
            (path for path in staged_runtime_assets.rglob("*") if path.is_file()),
            key=lambda path: path.relative_to(staged_runtime_assets).as_posix(),
        ):
            final_file = runtime_assets / staged_file.relative_to(staged_runtime_assets)
            published_files.append(
                (
                    final_file,
                    _publish_new_file(
                        staged_file,
                        final_file,
                        directory_identities[final_file.parent],
                    ),
                )
            )
        published_files.append(
            (output, _publish_new_file(staged_output, output, runtime_root_identity))
        )
        try:
            with load_renderpack(output, loaded_worldpack):
                pass
        except RenderPackError as exc:
            raise RenderPackBuildError(
                f"Published renderpack failed runtime validation: {exc}"
            ) from exc
        return payload
    except Exception as exc:
        for path, identity in reversed(published_files):
            _remove_owned_file(path, identity)
        for path, identity in reversed(published_directories):
            _remove_owned_directory(path, identity)
        if isinstance(exc, RenderPackBuildError):
            raise
        if isinstance(exc, OSError):
            raise RenderPackBuildError(f"Could not publish renderpack: {exc}") from exc
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
