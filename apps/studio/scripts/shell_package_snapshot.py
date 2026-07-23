from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

MAX_NODES = 20_000
MAX_DEPTH = 32
MAX_FILE_BYTES = 1_073_741_824
MAX_TOTAL_BYTES = 3_221_225_472
READ_CHUNK_BYTES = 1024 * 1024
MAX_CONTROL_BYTES = 8 * 1024 * 1024
SHELL_MANIFEST_PATH = "resources/shell-package-manifest.json"
APP_ASAR_PATH = "resources/app.asar"
PROTOCOL_ROOT = "resources/protocol/codex-app-server-0.144.6"
SOURCE_PROTOCOL_ROOT = "protocol/codex-app-server-0.144.6"
CONTROL_PATHS = (
    "resources/runtime-manifest.json",
    "resources/packaging/runtime-sources.json",
    "resources/packaging/runtime-package-manifest.schema.json",
    "resources/packaging/runtime-sources.schema.json",
    "resources/packaging/shell-package-manifest.schema.json",
    f"{PROTOCOL_ROOT}/manifest.json",
    SHELL_MANIFEST_PATH,
)
SOURCE_COPY_MAP = (
    ("resources/runtime-manifest.json", "resources/runtime-manifest.json"),
    ("packaging/runtime-sources.json", "resources/packaging/runtime-sources.json"),
    (
        "packaging/runtime-package-manifest.schema.json",
        "resources/packaging/runtime-package-manifest.schema.json",
    ),
    (
        "packaging/runtime-sources.schema.json",
        "resources/packaging/runtime-sources.schema.json",
    ),
    (
        "packaging/shell-package-manifest.schema.json",
        "resources/packaging/shell-package-manifest.schema.json",
    ),
)


class SnapshotError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> NoReturn:
    raise SnapshotError(code)


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _portable_segment(value: str) -> None:
    forbidden = '<>:"/\\|?*'
    folded = value.casefold()
    stem = folded.split(".", 1)[0]
    if (
        not value
        or len(value.encode("utf-8", "strict")) > 255
        or value != __import__("unicodedata").normalize("NFC", value)
        or any(ord(character) < 0x20 or ord(character) > 0x7E for character in value)
        or any(character in forbidden for character in value)
        or value.endswith((" ", "."))
        or value in {".", ".."}
        or stem in {"aux", "con", "nul", "prn"}
        or (len(stem) == 4 and stem[:3] in {"com", "lpt"} and stem[3] in "123456789")
    ):
        _fail("nonportable_package_path")


def _portable_path(value: str) -> None:
    if not value or value.startswith("/") or "\\" in value or len(value.encode()) > 1024:
        _fail("nonportable_package_path")
    for component in value.split("/"):
        _portable_segment(component)


def _source_paths(source_root: Path) -> None:
    repo_root = source_root.parent.parent
    source = repo_root / "src"
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))


@dataclass(slots=True)
class _Directory:
    absolute: Path
    children: tuple[str, ...]
    handle: int
    identity: tuple[int, int]
    name: str
    parent: _Directory | None
    relative: str


@dataclass(slots=True)
class _File:
    handle: int
    identity: tuple[int, int]
    name: str
    nlink: int
    parent: _Directory
    payload: bytes | None
    relative: str
    sha256: str
    size: int
    snapshot_name: str | None = None


class _WindowsReader:
    DELETE = 0x00010000
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_READ_ATTRIBUTES = 0x0080
    FILE_WRITE_ATTRIBUTES = 0x0100
    SYNCHRONIZE = 0x00100000
    FILE_SHARE_READ = 0x00000001
    FILE_OPEN = 0x00000001
    FILE_CREATE = 0x00000002
    FILE_WRITE_THROUGH = 0x00000002
    FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
    FILE_NON_DIRECTORY_FILE = 0x00000040
    FILE_OPEN_REPARSE_POINT = 0x00200000
    FILE_ATTRIBUTE_NORMAL = 0x00000080
    OBJ_CASE_INSENSITIVE = 0x00000040
    STATUS_OBJECT_NAME_COLLISION = 0xC0000035
    FILE_DISPOSITION_INFO_CLASS = 4
    FILE_BEGIN = 0

    def __init__(self, api: Any) -> None:
        self.api = api
        self.ctypes = api.ctypes
        self.wintypes = api.wintypes
        self.ReadFile = api.kernel32.ReadFile
        self.ReadFile.argtypes = [
            self.wintypes.HANDLE,
            self.wintypes.LPVOID,
            self.wintypes.DWORD,
            self.ctypes.POINTER(self.wintypes.DWORD),
            self.wintypes.LPVOID,
        ]
        self.ReadFile.restype = self.wintypes.BOOL
        self.WriteFile = api.kernel32.WriteFile
        self.WriteFile.argtypes = [
            self.wintypes.HANDLE,
            self.wintypes.LPCVOID,
            self.wintypes.DWORD,
            self.ctypes.POINTER(self.wintypes.DWORD),
            self.wintypes.LPVOID,
        ]
        self.WriteFile.restype = self.wintypes.BOOL
        self.SetFilePointerEx = api.kernel32.SetFilePointerEx
        self.SetFilePointerEx.argtypes = [
            self.wintypes.HANDLE,
            self.ctypes.c_longlong,
            self.ctypes.POINTER(self.ctypes.c_longlong),
            self.wintypes.DWORD,
        ]
        self.SetFilePointerEx.restype = self.wintypes.BOOL

        class FileDispositionInfo(self.ctypes.Structure):
            _fields_ = [("DeleteFile", self.wintypes.BOOL)]

        self.FileDispositionInfo = FileDispositionInfo
        self.SetFileInformationByHandle = api.kernel32.SetFileInformationByHandle
        self.SetFileInformationByHandle.argtypes = [
            self.wintypes.HANDLE,
            self.ctypes.c_int,
            self.wintypes.LPVOID,
            self.wintypes.DWORD,
        ]
        self.SetFileInformationByHandle.restype = self.wintypes.BOOL

    def _relative(
        self,
        parent: int,
        name: str,
        *,
        create: bool,
        delete_capable: bool = False,
        field: str,
    ) -> int:
        _portable_segment(name)
        encoded = name.encode("utf-16-le", "strict")
        if len(encoded) > 65_534:
            _fail("nonportable_package_path")
        buffer = self.ctypes.create_unicode_buffer(name)
        unicode_name = self.api.UnicodeString(
            len(encoded),
            len(encoded) + 2,
            self.ctypes.cast(buffer, self.wintypes.LPWSTR),
        )
        attributes = self.api.ObjectAttributes(
            self.ctypes.sizeof(self.api.ObjectAttributes),
            self.wintypes.HANDLE(parent),
            self.ctypes.pointer(unicode_name),
            self.OBJ_CASE_INSENSITIVE,
            None,
            None,
        )
        io_status = self.api.IoStatusBlock()
        output = self.wintypes.HANDLE()
        access = (
            self.GENERIC_READ
            | self.FILE_READ_ATTRIBUTES
            | self.SYNCHRONIZE
            | (self.GENERIC_WRITE | self.FILE_WRITE_ATTRIBUTES if create else 0)
            | (self.DELETE if delete_capable else 0)
        )
        status = int(
            self.api.NtCreateFile(
                self.ctypes.byref(output),
                access,
                self.ctypes.byref(attributes),
                self.ctypes.byref(io_status),
                None,
                self.FILE_ATTRIBUTE_NORMAL,
                self.FILE_SHARE_READ,
                self.FILE_CREATE if create else self.FILE_OPEN,
                self.FILE_NON_DIRECTORY_FILE
                | self.FILE_OPEN_REPARSE_POINT
                | self.FILE_SYNCHRONOUS_IO_NONALERT
                | self.FILE_WRITE_THROUGH,
                None,
                0,
            )
        )
        if status < 0:
            if create and (status & 0xFFFFFFFF) == self.STATUS_OBJECT_NAME_COLLISION:
                _fail("shell_manifest_already_exists")
            _fail("package_entry_changed")
        value = self.ctypes.cast(output, self.ctypes.c_void_p).value
        if value is None:
            _fail("secure_primitive_unavailable")
        handle = int(value)
        retained = False
        try:
            state = self.api.state(handle, field)
            if state.is_directory or state.is_reparse or state.nlink != 1:
                _fail("package_non_regular_entry")
            retained = True
            return handle
        finally:
            if not retained:
                self.api.close(handle)

    def open(self, parent: int, name: str) -> int:
        return self._relative(parent, name, create=False, field="package")

    def create(self, parent: int, name: str) -> int:
        return self._relative(parent, name, create=True, field="package")

    def create_owned_snapshot(self, parent: int, name: str) -> int:
        return self._relative(
            parent,
            name,
            create=True,
            delete_capable=True,
            field="package",
        )

    def delete_owned_snapshot(self, handle: int) -> None:
        disposition = self.FileDispositionInfo(True)
        if not self.SetFileInformationByHandle(
            self.wintypes.HANDLE(handle),
            self.FILE_DISPOSITION_INFO_CLASS,
            self.ctypes.byref(disposition),
            self.ctypes.sizeof(disposition),
        ):
            _fail("snapshot_cleanup_failed")

    def reset(self, handle: int) -> None:
        if not self.SetFilePointerEx(
            self.wintypes.HANDLE(handle),
            0,
            None,
            self.FILE_BEGIN,
        ):
            _fail("package_file_changed")

    def chunks(self, handle: int, size: int):
        self.reset(handle)
        remaining = size
        while remaining:
            requested = min(remaining, READ_CHUNK_BYTES)
            buffer = (self.ctypes.c_ubyte * requested)()
            received = self.wintypes.DWORD()
            if not self.ReadFile(
                self.wintypes.HANDLE(handle),
                buffer,
                requested,
                self.ctypes.byref(received),
                None,
            ):
                _fail("package_file_changed")
            if received.value != requested:
                _fail("package_file_changed")
            yield bytes(buffer)
            remaining -= requested

    def write(self, handle: int, payload: bytes) -> None:
        offset = 0
        while offset < len(payload):
            chunk = payload[offset : offset + READ_CHUNK_BYTES]
            buffer = (self.ctypes.c_ubyte * len(chunk)).from_buffer_copy(chunk)
            written = self.wintypes.DWORD()
            if not self.WriteFile(
                self.wintypes.HANDLE(handle),
                buffer,
                len(chunk),
                self.ctypes.byref(written),
                None,
            ):
                _fail("shell_manifest_publish_failed")
            if written.value != len(chunk):
                _fail("shell_manifest_publish_failed")
            offset += len(chunk)
        if not self.api.FlushFileBuffers(self.wintypes.HANDLE(handle)):
            _fail("shell_manifest_publish_failed")


class _WindowsPinnedTree:
    def __init__(
        self,
        root: Path,
        *,
        snapshot_chain: Any | None = None,
        snapshot_root: Path | None = None,
        snapshot_paths: dict[str, str] | None = None,
    ) -> None:
        from scripts.studio_runtime_assembly import _WindowsDirectoryChain

        self.root = root
        self.chain = _WindowsDirectoryChain(root, "package")
        self.api = self.chain.api
        self.reader = _WindowsReader(self.api)
        self.snapshot_chain = snapshot_chain
        self.snapshot_root = snapshot_root
        self.snapshot_paths = snapshot_paths or {}
        self.directories: dict[str, _Directory] = {}
        self.files: dict[str, _File] = {}
        self.extra_handles: list[int] = []
        self.snapshot_handles: dict[str, int] = {}
        self.aliases: dict[str, str] = {}
        self.nodes = 0
        self.total_bytes = 0
        root_state = self.api.state(self.chain.leaf, "package")
        self.directories[""] = _Directory(
            absolute=root,
            children=(),
            handle=self.chain.leaf,
            identity=root_state.identity,
            name="",
            parent=None,
            relative="",
        )
        try:
            self._scan(self.directories[""], 0)
        except BaseException:
            self.close()
            raise

    def _open_snapshot(self, relative: str) -> tuple[int, str] | None:
        name = self.snapshot_paths.get(relative)
        if name is None:
            return None
        if self.snapshot_chain is None:
            _fail("secure_primitive_unavailable")
        handle = self.reader.create_owned_snapshot(self.snapshot_chain.leaf, name)
        self.snapshot_handles[name] = handle
        return handle, name

    def _scan(self, directory: _Directory, depth: int) -> None:
        if depth > MAX_DEPTH:
            _fail("package_tree_too_deep")
        try:
            entries = sorted(
                os.scandir(directory.absolute),
                key=lambda entry: entry.name.encode("utf-8", "strict"),
            )
        except OSError:
            _fail("package_directory_changed")
        directory.children = tuple(entry.name for entry in entries)
        for entry in entries:
            self.nodes += 1
            if self.nodes > MAX_NODES:
                _fail("package_tree_too_large")
            name = entry.name
            _portable_segment(name)
            relative = f"{directory.relative}/{name}" if directory.relative else name
            _portable_path(relative)
            alias = relative.casefold()
            previous = self.aliases.get(alias)
            if previous is not None and previous != relative:
                _fail("package_path_alias")
            self.aliases[alias] = relative
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                _fail("package_entry_changed")
            is_reparse = bool(
                getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
            if is_reparse or stat.S_ISLNK(info.st_mode):
                _fail("package_non_regular_entry")
            if stat.S_ISDIR(info.st_mode):
                handle = self.api.relative(
                    directory.handle,
                    name,
                    directory=True,
                    create=False,
                    field="package",
                )
                self.extra_handles.append(handle)
                state = self.api.state(handle, "package")
                child = _Directory(
                    absolute=directory.absolute / name,
                    children=(),
                    handle=handle,
                    identity=state.identity,
                    name=name,
                    parent=directory,
                    relative=relative,
                )
                self.directories[relative] = child
                self._scan(child, depth + 1)
                continue
            if not stat.S_ISREG(info.st_mode):
                _fail("package_non_regular_entry")
            handle = self.reader.open(directory.handle, name)
            self.extra_handles.append(handle)
            state = self.api.state(handle, "package")
            if (
                state.is_directory
                or state.is_reparse
                or state.nlink != 1
                or state.size > MAX_FILE_BYTES
            ):
                _fail("package_non_regular_entry")
            self.total_bytes += state.size
            if self.total_bytes > MAX_TOTAL_BYTES:
                _fail("package_tree_too_large")
            digest = hashlib.sha256()
            captured = bytearray() if relative in CONTROL_PATHS else None
            snapshot = self._open_snapshot(relative)
            for chunk in self.reader.chunks(handle, state.size):
                digest.update(chunk)
                if captured is not None:
                    if len(captured) + len(chunk) > MAX_CONTROL_BYTES:
                        _fail("package_file_too_large")
                    captured.extend(chunk)
                if snapshot is not None:
                    self.reader.write(snapshot[0], chunk)
            if snapshot is not None:
                if not self.api.FlushFileBuffers(self.api.wintypes.HANDLE(snapshot[0])):
                    _fail("package_file_changed")
            self.files[relative] = _File(
                handle=handle,
                identity=state.identity,
                name=name,
                nlink=state.nlink,
                parent=directory,
                payload=bytes(captured) if captured is not None else None,
                relative=relative,
                sha256=digest.hexdigest(),
                size=state.size,
                snapshot_name=snapshot[1] if snapshot is not None else None,
            )

    def publish_manifest(self, payload: bytes) -> None:
        if len(payload) > MAX_CONTROL_BYTES:
            _fail("shell_manifest_publish_failed")
        if SHELL_MANIFEST_PATH in self.files:
            _fail("shell_manifest_already_exists")
        resources = self.directories.get("resources")
        if resources is None:
            _fail("package_resource_directory_missing")
        handle = self.reader.create(resources.handle, "shell-package-manifest.json")
        self.extra_handles.append(handle)
        self.reader.write(handle, payload)
        state = self.api.state(handle, "package")
        record = _File(
            handle=handle,
            identity=state.identity,
            name="shell-package-manifest.json",
            nlink=state.nlink,
            parent=resources,
            payload=payload,
            relative=SHELL_MANIFEST_PATH,
            sha256=hashlib.sha256(payload).hexdigest(),
            size=len(payload),
        )
        self.files[SHELL_MANIFEST_PATH] = record
        resources.children = tuple(
            sorted(
                (*resources.children, record.name),
                key=lambda value: value.encode("utf-8"),
            )
        )

    def _hash(self, record: _File) -> str:
        digest = hashlib.sha256()
        for chunk in self.reader.chunks(record.handle, record.size):
            digest.update(chunk)
        return digest.hexdigest()

    def finalize(self) -> None:
        self.chain.require_bindings()
        for directory in self.directories.values():
            state = self.api.state(directory.handle, "package")
            if state.identity != directory.identity or state.is_reparse or not state.is_directory:
                _fail("package_entry_replaced")
            if directory.parent is not None:
                reopened = self.api.relative(
                    directory.parent.handle,
                    directory.name,
                    directory=True,
                    create=False,
                    field="package",
                )
                try:
                    if self.api.state(reopened, "package").identity != directory.identity:
                        _fail("package_entry_replaced")
                finally:
                    self.api.close(reopened)
            try:
                current = tuple(
                    sorted(
                        (entry.name for entry in os.scandir(directory.absolute)),
                        key=lambda value: value.encode("utf-8"),
                    )
                )
            except OSError:
                _fail("package_directory_replaced")
            if current != directory.children:
                _fail("package_directory_replaced")
        for record in self.files.values():
            state = self.api.state(record.handle, "package")
            if (
                state.identity != record.identity
                or state.is_directory
                or state.is_reparse
                or state.nlink != 1
                or state.size != record.size
                or self._hash(record) != record.sha256
            ):
                _fail("package_entry_replaced")
            reopened = self.reader.open(record.parent.handle, record.name)
            try:
                if self.api.state(reopened, "package").identity != record.identity:
                    _fail("package_entry_replaced")
            finally:
                self.api.close(reopened)
        if self.snapshot_chain is not None:
            self.snapshot_chain.require_bindings()
            for record in self.files.values():
                if record.snapshot_name is None:
                    continue
                write_handle = self.snapshot_handles[record.snapshot_name]
                current = self.api.state(write_handle, "package")
                digest = hashlib.sha256()
                for chunk in self.reader.chunks(write_handle, current.size):
                    digest.update(chunk)
                if (
                    current.is_directory
                    or current.is_reparse
                    or current.nlink != 1
                    or current.size != record.size
                    or digest.hexdigest() != record.sha256
                ):
                    _fail("package_entry_replaced")

    def cleanup_snapshots(self) -> None:
        if self.snapshot_chain is None:
            return
        self.snapshot_chain.require_bindings()
        for record in self.files.values():
            if record.snapshot_name is None:
                continue
            handle = self.snapshot_handles.get(record.snapshot_name)
            if handle is None:
                _fail("snapshot_cleanup_failed")
            self.reader.delete_owned_snapshot(handle)
        while self.snapshot_handles:
            _name, handle = self.snapshot_handles.popitem()
            self.api.close(handle)
        if self.snapshot_root is None:
            _fail("snapshot_cleanup_failed")
        try:
            with os.scandir(self.snapshot_root) as entries:
                if any(entries):
                    _fail("snapshot_cleanup_failed")
        except OSError:
            _fail("snapshot_cleanup_failed")

    def close(self) -> None:
        while self.snapshot_handles:
            _name, handle = self.snapshot_handles.popitem()
            try:
                self.api.close(handle)
            except BaseException:
                pass
        while self.extra_handles:
            try:
                self.api.close(self.extra_handles.pop())
            except BaseException:
                pass
        try:
            self.chain.close()
        except BaseException:
            pass

    def report(self, target: str, snapshot_root: Path) -> dict[str, Any]:
        files = [
            {
                "path": record.relative,
                "sha256": record.sha256,
                "size": record.size,
            }
            for record in sorted(
                self.files.values(),
                key=lambda record: record.relative.encode("utf-8"),
            )
        ]
        controls = {
            relative: base64.b64encode(record.payload).decode("ascii")
            for relative, record in self.files.items()
            if record.payload is not None
        }
        protocol = _tree_identity(
            {
                relative.removeprefix(f"{PROTOCOL_ROOT}/"): record
                for relative, record in self.files.items()
                if relative.startswith(f"{PROTOCOL_ROOT}/")
            }
        )
        snapshots = {
            relative: str(snapshot_root / record.snapshot_name)
            for relative, record in self.files.items()
            if record.snapshot_name is not None
        }
        return {
            "controls": controls,
            "directories": sorted(self.directories, key=lambda value: value.encode("utf-8")),
            "files": files,
            "format": "rpg-world-forge.studio_shell_snapshot",
            "format_version": 1,
            "protocol": protocol,
            "snapshots": snapshots,
            "status": "ready",
            "target_id": target,
        }


def _tree_identity(files: dict[str, _File]) -> dict[str, Any]:
    digest = hashlib.sha256()
    total = 0
    for relative, record in sorted(files.items(), key=lambda item: item[0].encode("utf-8")):
        total += record.size
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(record.size).encode("ascii"))
        digest.update(b"\0")
        digest.update(record.sha256.encode("ascii"))
        digest.update(b"\0")
    return {
        "bytes": total,
        "files": len(files),
        "inventory_sha256": digest.hexdigest(),
    }


def _compare_sources(
    package: _WindowsPinnedTree,
    source_root: Path,
) -> tuple[list[_WindowsPinnedTree], list[dict[str, Any]]]:
    source_trees: list[_WindowsPinnedTree] = []
    try:
        packaging = _WindowsPinnedTree(source_root / "packaging")
        source_trees.append(packaging)
        resources = _WindowsPinnedTree(source_root / "resources")
        source_trees.append(resources)
        protocol = _WindowsPinnedTree(source_root / SOURCE_PROTOCOL_ROOT)
        source_trees.append(protocol)
        dist_electron = _WindowsPinnedTree(source_root / "dist-electron")
        source_trees.append(dist_electron)
        dist_renderer = _WindowsPinnedTree(source_root / "dist-renderer")
        source_trees.append(dist_renderer)
        for source_relative, packaged_relative in SOURCE_COPY_MAP:
            if source_relative.startswith("packaging/"):
                source_record = packaging.files.get(source_relative.removeprefix("packaging/"))
            else:
                source_record = resources.files.get(source_relative.removeprefix("resources/"))
            packaged = package.files.get(packaged_relative)
            if (
                source_record is None
                or packaged is None
                or source_record.size != packaged.size
                or source_record.sha256 != packaged.sha256
            ):
                _fail("packaged_resource_mismatch")
        package_protocol = {
            relative.removeprefix(f"{PROTOCOL_ROOT}/"): record
            for relative, record in package.files.items()
            if relative.startswith(f"{PROTOCOL_ROOT}/")
        }
        if set(package_protocol) != set(protocol.files):
            _fail("codex_protocol_tree_mismatch")
        for relative, record in package_protocol.items():
            source_record = protocol.files[relative]
            if record.size != source_record.size or record.sha256 != source_record.sha256:
                _fail("codex_protocol_tree_mismatch")
        asar_source = [
            {
                "path": f"{root}/{record.relative}",
                "sha256": record.sha256,
                "size": record.size,
            }
            for root, tree in (
                ("dist-electron", dist_electron),
                ("dist-renderer", dist_renderer),
            )
            for record in tree.files.values()
        ]
        asar_source.sort(key=lambda item: item["path"].encode("utf-8"))
        return source_trees, asar_source
    except BaseException:
        for tree in reversed(source_trees):
            tree.close()
        raise


def _strict_arguments(argv: list[str]) -> tuple[Path, str, Path, Path]:
    if (
        len(argv) != 9
        or argv[0] != "serve"
        or argv[1] != "--path"
        or argv[3] != "--target"
        or argv[5] != "--source-root"
        or argv[7] != "--snapshot-dir"
        or argv[4] not in {"linux-x64", "win32-x64"}
    ):
        _fail("invalid_arguments")
    roots = (Path(argv[2]), Path(argv[6]), Path(argv[8]))
    for root in roots:
        if not root.is_absolute() or Path(os.path.normpath(root)) != root:
            _fail("invalid_arguments")
    return roots[0], argv[4], roots[1], roots[2]


def _strict_guard_arguments(argv: list[str]) -> tuple[Path, Path, Path]:
    if (
        len(argv) != 7
        or argv[0] != "guard-output"
        or argv[1] != "--path"
        or argv[3] != "--source-root"
        or argv[5] != "--repository-root"
    ):
        _fail("invalid_arguments")
    roots = (Path(argv[2]), Path(argv[4]), Path(argv[6]))
    for root in roots:
        if not root.is_absolute() or Path(os.path.normpath(root)) != root:
            _fail("invalid_arguments")
    return roots


def _inside(parent: Path, candidate: Path) -> bool:
    try:
        common = os.path.commonpath((os.path.normcase(parent), os.path.normcase(candidate)))
    except ValueError:
        return False
    return common == os.path.normcase(parent)


def _guard_output(argv: list[str]) -> None:
    if os.name != "nt":
        _fail("secure_primitive_unavailable")
    if not (sys.version_info >= (3, 11) and sys.version_info < (3, 13)):
        _fail("unsupported_python")
    output_root, source_root, repository_root = _strict_guard_arguments(argv)
    _source_paths(source_root)
    from scripts.studio_runtime_assembly import (
        RuntimeAssemblyError,
        _WindowsDirectoryChain,
    )

    try:
        parent_root = output_root.parent.resolve(strict=True)
        repository_real = repository_root.resolve(strict=True)
    except OSError:
        _fail("package_output_parent_invalid")
    if _inside(repository_real, parent_root):
        _fail("package_output_inside_repository")
    _portable_segment(output_root.name)
    chain = _WindowsDirectoryChain(parent_root, "package")
    output_handle: int | None = None
    try:
        try:
            output_handle = chain.api.relative(
                chain.leaf,
                output_root.name,
                directory=True,
                create=True,
                writable=True,
                field="package",
            )
        except RuntimeAssemblyError:
            _fail("package_output_reservation_failed")
        expected = chain.api.state(output_handle, "package")
        if expected.is_reparse or not expected.is_directory:
            _fail("package_output_reservation_failed")
        try:
            with os.scandir(output_root) as entries:
                if any(entries):
                    _fail("package_output_reservation_failed")
        except OSError:
            _fail("package_output_reservation_failed")
        sys.stdout.buffer.write(
            _canonical_bytes({"output_path": str(output_root), "status": "ready"})
        )
        sys.stdout.buffer.flush()
        line = sys.stdin.buffer.readline(MAX_CONTROL_BYTES)
        if not line or len(line) >= MAX_CONTROL_BYTES:
            _fail("invalid_backend_command")
        try:
            command = json.loads(line.decode("utf-8", "strict"))
        except (UnicodeError, json.JSONDecodeError):
            _fail("invalid_backend_command")
        if command != {"action": "finalize"}:
            _fail("invalid_backend_command")
        chain.require_bindings()
        retained = chain.api.state(output_handle, "package")
        reopened = chain.api.relative(
            chain.leaf,
            output_root.name,
            directory=True,
            create=False,
            field="package",
        )
        try:
            current = chain.api.state(reopened, "package")
            if (
                retained.identity != expected.identity
                or current.identity != expected.identity
                or retained.is_reparse
                or not retained.is_directory
            ):
                _fail("package_output_changed")
        finally:
            chain.api.close(reopened)
        try:
            final_real = output_root.resolve(strict=True)
        except OSError:
            _fail("package_output_changed")
        if _inside(repository_real, final_real):
            _fail("package_output_changed")
        sys.stdout.buffer.write(_canonical_bytes({"status": "finalized"}))
        sys.stdout.buffer.flush()
    finally:
        if output_handle is not None:
            chain.api.close(output_handle)
        chain.close()


def _serve(argv: list[str]) -> None:
    if os.name != "nt":
        _fail("secure_primitive_unavailable")
    if not (sys.version_info >= (3, 11) and sys.version_info < (3, 13)):
        _fail("unsupported_python")
    package_root, target, source_root, snapshot_root = _strict_arguments(argv)
    _source_paths(source_root)
    from isoworld.content.resource_snapshot import (
        _windows_close_handle,
        _windows_lock_directory,
    )
    from scripts.studio_runtime_assembly import _WindowsDirectoryChain

    package_guard = _windows_lock_directory(package_root)
    snapshot_guard: int | None = None
    snapshot_chain: Any | None = None
    package: _WindowsPinnedTree | None = None
    source_trees: list[_WindowsPinnedTree] = []
    try:
        snapshot_guard = _windows_lock_directory(snapshot_root)
        snapshot_chain = _WindowsDirectoryChain(snapshot_root, "package")
        with os.scandir(snapshot_root) as entries:
            if any(entries):
                _fail("snapshot_directory_not_empty")
        executable = (
            "rpg-world-forge-studio" if target == "linux-x64" else "RPG World Forge Studio.exe"
        )
        package = _WindowsPinnedTree(
            package_root,
            snapshot_chain=snapshot_chain,
            snapshot_root=snapshot_root,
            snapshot_paths={
                APP_ASAR_PATH: "app.asar",
                executable: "electron-executable.bin",
            },
        )
        source_trees, asar_source = _compare_sources(package, source_root)
        report = package.report(target, snapshot_root)
        report["asar_source"] = asar_source
        sys.stdout.buffer.write(_canonical_bytes(report))
        sys.stdout.buffer.flush()
        line = sys.stdin.buffer.readline(MAX_CONTROL_BYTES * 2)
        if not line or len(line) >= MAX_CONTROL_BYTES * 2:
            _fail("invalid_backend_command")
        try:
            command = json.loads(line.decode("utf-8", "strict"))
        except (UnicodeError, json.JSONDecodeError):
            _fail("invalid_backend_command")
        if set(command) == {"action"} and command["action"] == "finalize":
            pass
        elif set(command) == {"action", "payload"} and command["action"] == "publish":
            try:
                payload = base64.b64decode(command["payload"], validate=True)
            except (TypeError, ValueError):
                _fail("invalid_backend_command")
            package.publish_manifest(payload)
        else:
            _fail("invalid_backend_command")
        package.finalize()
        for tree in source_trees:
            tree.finalize()
        snapshot_chain.require_bindings()
        package.cleanup_snapshots()
        sys.stdout.buffer.write(_canonical_bytes({"status": "finalized"}))
        sys.stdout.buffer.flush()
    finally:
        for tree in reversed(source_trees):
            tree.close()
        if package is not None:
            package.close()
        if snapshot_chain is not None:
            snapshot_chain.close()
        if snapshot_guard is not None:
            _windows_close_handle(snapshot_guard)
        _windows_close_handle(package_guard)


def main() -> int:
    try:
        if sys.argv[1:2] == ["guard-output"]:
            _guard_output(sys.argv[1:])
        else:
            _serve(sys.argv[1:])
    except SnapshotError as exc:
        sys.stderr.write(f"Studio shell snapshot failed: {exc.code}\n")
        return 1
    except Exception:
        sys.stderr.write("Studio shell snapshot failed: backend_failure\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
