#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hashlib
import importlib.metadata
import io
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PINNED_TOOLS = {
    "build": "1.5.0",
    "setuptools": "83.0.0",
    "wheel": "0.47.0",
}


class ReleaseBuildError(RuntimeError):
    pass


def _run(
    command: list[str], *, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        raise ReleaseBuildError(f"command failed: {' '.join(command)}\n{detail}")
    return completed


def _verify_toolchain() -> None:
    mismatches: list[str] = []
    for name, expected in PINNED_TOOLS.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            mismatches.append(f"{name} is not installed")
            continue
        if actual != expected:
            mismatches.append(f"{name}=={actual}, expected {name}=={expected}")
    if mismatches:
        raise ReleaseBuildError("audited build toolchain mismatch: " + "; ".join(mismatches))


def _require_supported_platform(platform_name: str | None = None) -> None:
    platform_name = sys.platform if platform_name is None else platform_name
    if platform_name != "win32" and not platform_name.startswith("linux"):
        raise ReleaseBuildError(
            "reproducible release publication is supported only on Linux and Windows"
        )
    if not hasattr(os, "link"):
        raise ReleaseBuildError("exclusive hard-link publication is unavailable")


def _head_oid(repo: Path) -> str:
    completed = _run(["git", "rev-parse", "--verify", "HEAD^{commit}"], cwd=repo)
    oid = completed.stdout.strip().lower()
    if len(oid) not in {40, 64} or any(character not in "0123456789abcdef" for character in oid):
        raise ReleaseBuildError("could not resolve HEAD to an immutable commit object")
    return oid


def _source_date_epoch(repo: Path, commit_oid: str) -> int:
    completed = _run(["git", "log", "-1", "--format=%ct", commit_oid], cwd=repo)
    value = completed.stdout.strip()
    if not value.isdigit():
        raise ReleaseBuildError(f"could not derive SOURCE_DATE_EPOCH from {commit_oid}")
    return int(value)


def _git_archive(repo: Path, commit_oid: str) -> bytes:
    completed = subprocess.run(
        ["git", "archive", "--format=tar", commit_oid],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ReleaseBuildError(
            f"git archive {commit_oid} failed\n"
            + completed.stderr.decode("utf-8", errors="replace")
        )
    return completed.stdout


def _portable_archive_key(parts: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(unicodedata.normalize("NFC", part).casefold() for part in parts)


def _archive_member_parts(member: tarfile.TarInfo) -> tuple[str, ...]:
    name = member.name[:-1] if member.isdir() and member.name.endswith("/") else member.name
    if not name or name.startswith("/") or "\\" in name or "\x00" in name:
        raise ReleaseBuildError(f"unsafe archive member: {member.name}")
    parts = tuple(name.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise ReleaseBuildError(f"unsafe archive member: {member.name}")
    normalized = PurePosixPath(*parts)
    if normalized.is_absolute() or normalized.parts != parts:
        raise ReleaseBuildError(f"unsafe archive member: {member.name}")
    return parts


def _extract_archive(archive: bytes, destination: Path) -> None:
    entries: list[tuple[tarfile.TarInfo, tuple[str, ...], bytes | None]] = []
    portable_paths: dict[tuple[str, ...], tuple[str, ...]] = {}
    member_paths: set[tuple[str, ...]] = set()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as source:
        for member in source.getmembers():
            if not member.isdir() and not member.isfile():
                raise ReleaseBuildError(f"archive member is not a regular file: {member.name}")
            parts = _archive_member_parts(member)
            if parts in member_paths:
                raise ReleaseBuildError(f"duplicate archive member: {member.name}")
            member_paths.add(parts)
            for length in range(1, len(parts) + 1):
                path = parts[:length]
                key = _portable_archive_key(path)
                previous = portable_paths.get(key)
                if previous is not None and previous != path:
                    raise ReleaseBuildError(
                        "portable archive path collision: "
                        f"{'/'.join(previous)!r} and {'/'.join(path)!r}"
                    )
                portable_paths[key] = path
            payload: bytes | None = None
            if member.isfile():
                extracted = source.extractfile(member)
                if extracted is None:
                    raise ReleaseBuildError(f"could not read archive member: {member.name}")
                payload = extracted.read()
            entries.append((member, parts, payload))

    entry_types = {parts: member.isdir() for member, parts, _payload in entries}
    for parts, is_directory in entry_types.items():
        for length in range(1, len(parts)):
            ancestor = parts[:length]
            if ancestor in entry_types and not entry_types[ancestor]:
                raise ReleaseBuildError(
                    f"archive file is used as a directory: {'/'.join(ancestor)}"
                )
        if not is_directory and any(
            len(candidate) > len(parts) and candidate[: len(parts)] == parts
            for candidate in entry_types
        ):
            raise ReleaseBuildError(f"archive file is used as a directory: {'/'.join(parts)}")

    destination.mkdir(parents=True)
    directories = {
        parts[:length]
        for _member, parts, _payload in entries
        for length in range(1, len(parts) + 1)
        if length < len(parts) or entry_types.get(parts, False)
    }
    for parts in sorted(directories, key=lambda item: (len(item), item)):
        target = destination.joinpath(*parts)
        try:
            target.mkdir(mode=0o755)
        except FileExistsError:
            if not target.is_dir() or target.is_symlink():
                raise ReleaseBuildError(f"unsafe archive directory: {'/'.join(parts)}") from None

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for member, parts, payload in sorted(entries, key=lambda item: item[1]):
        if member.isdir():
            continue
        assert payload is not None
        target = destination.joinpath(*parts)
        descriptor: int | None = None
        try:
            descriptor = os.open(target, flags, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                descriptor = None
                output.write(payload)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
        except OSError as exc:
            raise ReleaseBuildError(
                f"could not extract archive member {member.name}: {exc}"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)


def _build_environment(epoch: int, environment_root: Path) -> dict[str, str]:
    home = environment_root / "home"
    temporary = environment_root / "tmp"
    xdg = environment_root / "xdg"
    for directory in (home, temporary, xdg):
        directory.mkdir(parents=True, exist_ok=True)
    env = {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "TMP": str(temporary),
        "TEMP": str(temporary),
        "TMPDIR": str(temporary),
        "XDG_CACHE_HOME": str(xdg / "cache"),
        "XDG_CONFIG_HOME": str(xdg / "config"),
        "XDG_DATA_HOME": str(xdg / "data"),
        "SOURCE_DATE_EPOCH": str(epoch),
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INDEX": "1",
        "TZ": "UTC",
        "LC_ALL": "C",
        "LANG": "C",
    }
    for name, value in os.environ.items():
        if name.upper() in {"SYSTEMROOT", "WINDIR"}:
            env[name] = value
    return env


def _build_from_source(source_root: Path, output_root: Path, epoch: int) -> tuple[Path, Path]:
    dist = output_root / "dist"
    dist.mkdir(parents=True)
    _run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--sdist",
            "--wheel",
            "--outdir",
            str(dist),
        ],
        cwd=source_root,
        env=_build_environment(epoch, output_root / "environment"),
    )
    sdists = sorted(dist.glob("*.tar.gz"))
    wheels = sorted(dist.glob("*.whl"))
    if len(sdists) != 1 or len(wheels) != 1:
        raise ReleaseBuildError("build did not produce exactly one sdist and one wheel")
    canonical_sdist = output_root / sdists[0].name
    canonical_wheel = output_root / wheels[0].name
    _canonicalize_sdist(sdists[0], canonical_sdist, epoch)
    _canonicalize_wheel(wheels[0], canonical_wheel)
    return canonical_sdist, canonical_wheel


def _normalized_tarinfo(info: tarfile.TarInfo, epoch: int) -> tarfile.TarInfo:
    normalized = tarfile.TarInfo(info.name)
    normalized.type = info.type
    normalized.linkname = info.linkname
    normalized.mtime = epoch
    normalized.uid = 0
    normalized.gid = 0
    normalized.uname = ""
    normalized.gname = ""
    normalized.pax_headers = {}
    if info.isdir():
        normalized.mode = 0o755
    elif info.mode & 0o111:
        normalized.mode = 0o755
    else:
        normalized.mode = 0o644
    if info.isfile():
        normalized.size = info.size
    return normalized


def _canonicalize_sdist(source: Path, destination: Path, epoch: int) -> None:
    with tarfile.open(source, mode="r:gz") as archive:
        entries = sorted(archive.getmembers(), key=lambda item: item.name)
        payloads: dict[str, bytes] = {}
        seen: set[str] = set()
        for entry in entries:
            if entry.name in seen:
                raise ReleaseBuildError(f"duplicate sdist member: {entry.name}")
            seen.add(entry.name)
            if not entry.isdir() and not entry.isfile():
                raise ReleaseBuildError(f"sdist member is not a regular file: {entry.name}")
            if entry.isfile():
                extracted = archive.extractfile(entry)
                if extracted is None:
                    raise ReleaseBuildError(f"could not read sdist member: {entry.name}")
                payloads[entry.name] = extracted.read()
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.USTAR_FORMAT) as target:
        for entry in entries:
            normalized = _normalized_tarinfo(entry, epoch)
            if entry.isfile():
                payload = payloads[entry.name]
                normalized.size = len(payload)
                target.addfile(normalized, io.BytesIO(payload))
            else:
                target.addfile(normalized)
    with destination.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as gzipped:
            gzipped.write(tar_buffer.getvalue())


def _record_line(path: str, data: bytes) -> list[str]:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii").rstrip("=")
    return [path, f"sha256={digest}", str(len(data))]


def _render_record(rows: Iterable[list[str]]) -> bytes:
    text = io.StringIO(newline="")
    writer = csv.writer(text, lineterminator="\n")
    writer.writerows(rows)
    return text.getvalue().encode("utf-8")


def _zip_datetime() -> tuple[int, int, int, int, int, int]:
    return (1980, 1, 1, 0, 0, 0)


def _canonicalize_wheel(source: Path, destination: Path) -> None:
    members: dict[str, bytes] = {}
    modes: dict[str, int] = {}
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            if info.filename in members:
                raise ReleaseBuildError(f"duplicate wheel member: {info.filename}")
            members[info.filename] = archive.read(info.filename)
            mode = (info.external_attr >> 16) & 0o777
            modes[info.filename] = mode or 0o644
    record_paths = [name for name in members if name.endswith(".dist-info/RECORD")]
    if len(record_paths) != 1:
        raise ReleaseBuildError("wheel must contain exactly one RECORD file")
    record_path = record_paths[0]
    rows = [_record_line(name, members[name]) for name in sorted(members) if name != record_path]
    rows.append([record_path, "", ""])
    members[record_path] = _render_record(rows)
    modes[record_path] = 0o644

    with zipfile.ZipFile(
        destination, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name in sorted(members):
            info = zipfile.ZipInfo(filename=name, date_time=_zip_datetime())
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if modes.get(name, 0o644) & 0o111 else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.internal_attr = 0
            info.extra = b""
            info.comment = b""
            archive.writestr(info, members[name])
    _verify_wheel_record(destination)


def _verify_wheel_record(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos if not info.is_dir()]
        if len(names) != len(set(names)):
            raise ReleaseBuildError("wheel contains duplicate members")
        record_paths = [name for name in names if name.endswith(".dist-info/RECORD")]
        if len(record_paths) != 1:
            raise ReleaseBuildError("wheel must contain exactly one RECORD file")
        record_path = record_paths[0]
        try:
            rows = list(csv.reader(io.StringIO(archive.read(record_path).decode("utf-8"))))
        except (UnicodeDecodeError, csv.Error) as exc:
            raise ReleaseBuildError(f"wheel RECORD is invalid: {exc}") from exc
        if any(len(row) != 3 for row in rows):
            raise ReleaseBuildError("wheel RECORD rows must contain exactly three fields")
        records: dict[str, tuple[str, str]] = {}
        for name, digest, size in rows:
            if name in records:
                raise ReleaseBuildError(f"wheel RECORD contains duplicate path: {name}")
            records[name] = (digest, size)
        if set(records) != set(names):
            raise ReleaseBuildError("wheel RECORD paths do not exactly match wheel members")
        for name in names:
            digest, size = records[name]
            if name == record_path:
                if digest or size:
                    raise ReleaseBuildError("wheel RECORD must not hash itself")
                continue
            expected = _record_line(name, archive.read(name))
            if [name, digest, size] != expected:
                raise ReleaseBuildError(f"wheel RECORD does not match member: {name}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_bytes(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as left_stream, right.open("rb") as right_stream:
        while True:
            left_chunk = left_stream.read(1024 * 1024)
            right_chunk = right_stream.read(1024 * 1024)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def _entry_identity(path: Path) -> tuple[int, int] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ReleaseBuildError(f"could not inspect release path {path}: {exc}") from exc
    return info.st_dev, info.st_ino


def _unlink_owned_file(path: Path, identity: tuple[int, int]) -> None:
    try:
        info = path.lstat()
    except OSError:
        return
    if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == identity:
        try:
            path.unlink()
        except OSError:
            pass


def _stage_artifact(source: Path, output_dir: Path) -> tuple[Path, tuple[int, int]]:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    stage: Path | None = None
    for _ in range(100):
        candidate = output_dir / f".{source.name}.stage-{secrets.token_hex(8)}"
        try:
            descriptor = os.open(candidate, flags, 0o600)
            stage = candidate
            break
        except FileExistsError:
            continue
        except OSError as exc:
            raise ReleaseBuildError(
                f"could not stage release artifact {source.name}: {exc}"
            ) from exc
    if descriptor is None or stage is None:
        raise ReleaseBuildError(f"could not allocate release staging file for {source.name}")
    opened = os.fstat(descriptor)
    identity = (opened.st_dev, opened.st_ino)
    try:
        with source.open("rb") as input_stream, os.fdopen(descriptor, "wb") as output_stream:
            descriptor = None
            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
            output_stream.flush()
            if hasattr(os, "fchmod"):
                os.fchmod(output_stream.fileno(), 0o644)
            else:
                stage.chmod(0o644)
            os.fsync(output_stream.fileno())
            staged_info = os.fstat(output_stream.fileno())
            if (staged_info.st_dev, staged_info.st_ino) != identity:
                raise ReleaseBuildError(f"release staging identity changed: {stage}")
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        _unlink_owned_file(stage, identity)
        raise
    return stage, identity


def _publish_verified(
    first: tuple[Path, Path], second: tuple[Path, Path], output_dir: Path
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_info = output_dir.lstat()
    if output_dir.is_symlink() or not stat.S_ISDIR(output_info.st_mode):
        raise ReleaseBuildError(f"release output is not a safe directory: {output_dir}")
    verified: list[Path] = []
    for left, right in zip(first, second, strict=True):
        if left.name != right.name or not _same_bytes(left, right):
            raise ReleaseBuildError(f"non-reproducible artifact: {left.name}")
        target = output_dir / left.name
        if _entry_identity(target) is not None:
            raise ReleaseBuildError(f"refusing to replace existing artifact: {target}")
        verified.append(left)

    stages: list[tuple[Path, tuple[int, int]]] = []
    published: list[tuple[Path, tuple[int, int]]] = []
    try:
        for source in verified:
            stages.append(_stage_artifact(source, output_dir))
        for source, (stage, identity) in zip(verified, stages, strict=True):
            target = output_dir / source.name
            try:
                os.link(stage, target)
            except FileExistsError as exc:
                raise ReleaseBuildError(f"refusing to replace existing artifact: {target}") from exc
            except OSError as exc:
                raise ReleaseBuildError(
                    f"could not publish release artifact {target}: {exc}"
                ) from exc
            published.append((target, identity))
            if _entry_identity(target) != identity:
                raise ReleaseBuildError(
                    f"release artifact identity changed during publication: {target}"
                )
        return [path for path, _identity in published]
    except Exception:
        for target, identity in reversed(published):
            _unlink_owned_file(target, identity)
        raise
    finally:
        for stage, identity in stages:
            _unlink_owned_file(stage, identity)


def build_release(repo: Path, output_dir: Path) -> list[Path]:
    repo = repo.resolve()
    output_dir = Path(os.path.abspath(output_dir))
    _require_supported_platform()
    _verify_toolchain()
    commit_oid = _head_oid(repo)
    epoch = _source_date_epoch(repo, commit_oid)
    archive = _git_archive(repo, commit_oid)
    with tempfile.TemporaryDirectory(prefix="rwf-release-") as scratch_text:
        scratch = Path(scratch_text)
        source_a = scratch / "source-a"
        source_b = scratch / "source-b"
        build_a = scratch / "build-a"
        build_b = scratch / "build-b"
        _extract_archive(archive, source_a)
        _extract_archive(archive, source_b)
        first = _build_from_source(source_a, build_a, epoch)
        second = _build_from_source(source_b, build_b, epoch)
        return _publish_verified(first, second, output_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build reproducible RPG World Forge releases")
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="directory where verified artifacts are published without replacement",
    )
    args = parser.parse_args(argv)
    try:
        artifacts = build_release(ROOT, args.output_dir)
    except ReleaseBuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for artifact in artifacts:
        print(f"artifact={artifact} sha256={_sha256(artifact)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
