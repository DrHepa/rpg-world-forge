"""Native exclusive directory publication for supported desktop platforms."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import stat
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath

import isoworld.content.file_stat as file_stat_module
from isoworld.content.file_stat import (
    FileStat,
    descriptor_file_stat,
    file_identity,
    is_link_or_reparse,
    path_file_stat,
)
from isoworld.content.publication_journal import (
    PublicationJournalError,
    journal_frame,
    recover_validated_journal_history,
    recover_validated_journal_prefix,
)
from worldforge.asset_io import (
    AssetContractError,
    PinnedOutputParent,
    open_verified_output_parent,
)
from worldforge.file_stat import windows_handle_file_stat

DirectoryIdentity = tuple[int, int]
RetainedStageHook = Callable[[str, str | None], None]
RetainedDirectoryVerifier = Callable[[Path, int | None], None]


class DirectoryPublishError(OSError):
    """Raised when a directory cannot be published without replacement."""


class DirectoryPublishIndeterminateError(DirectoryPublishError):
    """Raised when a native publication mutated names but durability is unproven."""


class DirectoryPublishRecoveryRequiredError(DirectoryPublishError):
    """Raised when exact owned evidence is retained instead of pathname-deleted."""


def _retained_stage_state(info: FileStat) -> tuple[int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


class RetainedStageWriter:
    """Write one exclusive directory tree through retained directory/file anchors."""

    def __init__(
        self,
        stage: Path,
        parent: PinnedOutputParent,
        *,
        root_native: int,
        root_identity: DirectoryIdentity,
        require_guard: Callable[[], None],
        hook: RetainedStageHook | None,
    ) -> None:
        self.stage = stage
        self.parent = parent
        self.identity = root_identity
        self._require_guard = require_guard
        self._hook_callback = hook
        self._directory_identities: dict[str, DirectoryIdentity] = {
            "": root_identity,
        }
        self._posix_directories: dict[str, int] = {}
        self._windows_directories: dict[str, int] = {}
        if parent.parent_fd is not None:
            self._posix_directories[""] = root_native
        else:
            self._windows_directories[""] = root_native
        self._posix_files: dict[
            str,
            tuple[int, tuple[int, int, int, int, int, int, int]],
        ] = {}
        self._windows_files: dict[
            str,
            tuple[int, tuple[int, int, int, int, int, int, int]],
        ] = {}
        self._closed = False

    def _hook(self, event: str, relative: str | None = None) -> None:
        if self._hook_callback is not None:
            self._hook_callback(event, relative)

    def _directory_path(self, relative: str) -> Path:
        return self.stage if not relative else self.stage / PurePosixPath(relative)

    def _directory_native(self, relative: str) -> int:
        if self.parent.parent_fd is not None:
            return self._posix_directories[relative]
        return self._windows_directories[relative]

    def require_binding(self) -> None:
        self._require_guard()
        self.parent.assert_current()
        for relative, expected_identity in self._directory_identities.items():
            native = self._directory_native(relative)
            opened = (
                descriptor_file_stat(native)
                if self.parent.parent_fd is not None
                else windows_handle_file_stat(native)
            )
            named = path_file_stat(self._directory_path(relative))
            if (
                is_link_or_reparse(opened)
                or is_link_or_reparse(named)
                or not stat.S_ISDIR(opened.st_mode)
                or not stat.S_ISDIR(named.st_mode)
                or file_identity(opened) != expected_identity
                or file_identity(named) != expected_identity
            ):
                raise DirectoryPublishError(
                    f"Retained stage directory changed: {self._directory_path(relative)}"
                )
        file_records = (
            self._posix_files if self.parent.parent_fd is not None else self._windows_files
        )
        for relative, (native, expected_state) in file_records.items():
            opened = (
                descriptor_file_stat(native)
                if self.parent.parent_fd is not None
                else windows_handle_file_stat(native)
            )
            named = path_file_stat(self.stage / PurePosixPath(relative))
            if (
                is_link_or_reparse(opened)
                or is_link_or_reparse(named)
                or not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(named.st_mode)
                or opened.st_nlink != 1
                or named.st_nlink != 1
                or _retained_stage_state(opened) != expected_state
                or _retained_stage_state(named) != expected_state
            ):
                raise DirectoryPublishError(f"Retained staged file changed: {relative}")

    def _ensure_parent(self, relative: PurePosixPath) -> str:
        current = ""
        for part in relative.parts[:-1]:
            child = part if not current else f"{current}/{part}"
            if child in self._directory_identities:
                current = child
                continue
            self.require_binding()
            parent_native = self._directory_native(current)
            if self.parent.parent_fd is not None:
                os.mkdir(part, mode=0o700, dir_fd=parent_native)
                native = os.open(
                    part,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_native,
                )
                os.fchmod(native, 0o700)
                opened = descriptor_file_stat(native)
                self._posix_directories[child] = native
            else:
                api = self.parent.windows_api
                if api is None:
                    raise DirectoryPublishError("Windows retained stage API is unavailable")
                native = api.create_directory(
                    parent_native,
                    part,
                    request_delete=False,
                )
                opened = windows_handle_file_stat(native)
                self._windows_directories[child] = native
            if is_link_or_reparse(opened) or not stat.S_ISDIR(opened.st_mode):
                raise DirectoryPublishError(f"New retained stage directory is unsafe: {child}")
            self._directory_identities[child] = file_identity(opened)
            self._hook("after_stage_directory_created", child)
            self.require_binding()
            current = child
        return current

    def write_file(self, relative: str, payload: bytes) -> None:
        if type(relative) is not str or type(payload) is not bytes:
            raise DirectoryPublishError("Retained stage files require exact string paths and bytes")
        relative_path = PurePosixPath(relative)
        if (
            not relative
            or relative_path.is_absolute()
            or ".." in relative_path.parts
            or "." in relative_path.parts
        ):
            raise DirectoryPublishError(f"Unsafe retained stage path: {relative!r}")
        parent_relative = self._ensure_parent(relative_path)
        self.require_binding()
        self._hook("before_stage_file_write", relative)
        self.require_binding()
        parent_native = self._directory_native(parent_relative)
        native: int | None = None
        descriptor: int | None = None
        try:
            if self.parent.parent_fd is not None:
                native = os.open(
                    relative_path.name,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=parent_native,
                )
                descriptor = native
            else:
                api = self.parent.windows_api
                if api is None:
                    raise DirectoryPublishError("Windows retained stage API is unavailable")
                native = api.create_file(
                    parent_native,
                    relative_path.name,
                    request_delete=False,
                )
                descriptor = api.duplicate_to_descriptor(native, writable=True)
            opened = descriptor_file_stat(descriptor)
            if (
                is_link_or_reparse(opened)
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or opened.st_size != 0
            ):
                raise DirectoryPublishError(f"New retained stage file is unsafe: {relative}")
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short retained stage write")
                view = view[written:]
            os.fsync(descriptor)
            sealed = descriptor_file_stat(descriptor)
            if (
                not stat.S_ISREG(sealed.st_mode)
                or sealed.st_nlink != 1
                or sealed.st_size != len(payload)
            ):
                raise DirectoryPublishError(
                    f"Retained stage file changed while writing: {relative}"
                )
            state = _retained_stage_state(sealed)
            if self.parent.parent_fd is not None:
                assert native is not None
                self._posix_files[relative] = (native, state)
                descriptor = None
            else:
                assert native is not None
                self._windows_files[relative] = (native, state)
                native = None
            self._hook("after_stage_file_write", relative)
            self.require_binding()
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if native is not None and self.parent.parent_fd is None:
                api = self.parent.windows_api
                if api is not None:
                    api.close(native)

    def fsync(self) -> None:
        self.require_binding()
        if self.parent.parent_fd is not None:
            for relative in sorted(
                self._posix_directories,
                key=lambda item: (
                    -len(PurePosixPath(item).parts),
                    item.encode("utf-8"),
                ),
            ):
                os.fsync(self._posix_directories[relative])
            os.fsync(self.parent.parent_fd)
        else:
            api = self.parent.windows_api
            if api is None:
                raise DirectoryPublishError("Windows retained stage API is unavailable")
            for relative in sorted(
                self._windows_directories,
                key=lambda item: (
                    -len(PurePosixPath(item).parts),
                    item.encode("utf-8"),
                ),
            ):
                api.flush_handle(
                    self._windows_directories[relative],
                    context=f"retained stage directory {relative or '.'}",
                )
            fsync_directory(self.stage.parent, context="retained stage parent")
        self.require_binding()

    def close(self) -> None:
        if self._closed:
            return
        errors: list[BaseException] = []
        if self.parent.parent_fd is not None:
            descriptors = [native for native, _state in self._posix_files.values()] + [
                self._posix_directories[relative]
                for relative in sorted(
                    self._posix_directories,
                    key=lambda item: len(PurePosixPath(item).parts),
                    reverse=True,
                )
            ]
            for descriptor in descriptors:
                try:
                    os.close(descriptor)
                except OSError as exc:
                    errors.append(exc)
        else:
            api = self.parent.windows_api
            if api is not None:
                handles = [native for native, _state in self._windows_files.values()] + [
                    self._windows_directories[relative]
                    for relative in sorted(
                        self._windows_directories,
                        key=lambda item: len(PurePosixPath(item).parts),
                        reverse=True,
                    )
                ]
                for handle in handles:
                    try:
                        api.close(handle)
                    except AssetContractError as exc:
                        errors.append(exc)
        self._closed = True
        if errors:
            raise DirectoryPublishError(f"Could not release retained stage handles: {errors[0]}")


@contextmanager
def create_retained_stage(
    stage: Path,
    *,
    expected_parent_identity: DirectoryIdentity | None = None,
    require_guard: Callable[[], None] = lambda: None,
    hook: RetainedStageHook | None = None,
) -> Iterator[RetainedStageWriter]:
    """Create and retain an exclusive stage root until all staged files are durable."""

    writer: RetainedStageWriter | None = None
    orphan_native: int | None = None
    orphan_close: Callable[[int], None] | None = None
    try:
        with open_verified_output_parent(stage.parent) as parent:
            if (
                expected_parent_identity is not None
                and parent.identities[-1] != expected_parent_identity
            ):
                raise DirectoryPublishError(
                    "Retained stage parent differs from the expected identity"
                )
            require_guard()
            parent.assert_current()
            if parent.parent_fd is not None:
                os.mkdir(stage.name, mode=0o700, dir_fd=parent.parent_fd)
                root_native = os.open(
                    stage.name,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent.parent_fd,
                )
                orphan_native = root_native
                os.fchmod(root_native, 0o700)
                opened = descriptor_file_stat(root_native)
            else:
                api = parent.windows_api
                parent_handle = parent.windows_parent_handle
                if api is None or parent_handle is None:
                    raise DirectoryPublishError("Windows retained stage API is unavailable")
                root_native = api.create_directory(
                    parent_handle,
                    stage.name,
                    request_delete=False,
                )
                orphan_native = root_native
                orphan_close = api.close
                opened = windows_handle_file_stat(root_native)
            root_identity = file_identity(opened)
            named = path_file_stat(stage)
            if (
                is_link_or_reparse(opened)
                or is_link_or_reparse(named)
                or not stat.S_ISDIR(opened.st_mode)
                or not stat.S_ISDIR(named.st_mode)
                or file_identity(named) != root_identity
            ):
                raise DirectoryPublishError("New retained stage root has an unsafe identity")
            writer = RetainedStageWriter(
                stage,
                parent,
                root_native=root_native,
                root_identity=root_identity,
                require_guard=require_guard,
                hook=hook,
            )
            orphan_native = None
            writer._hook("after_stage_created")  # noqa: SLF001
            writer.require_binding()
            try:
                yield writer
                writer.require_binding()
            finally:
                primary = sys.exception()
                try:
                    writer.close()
                except DirectoryPublishError as cleanup_error:
                    if primary is not None:
                        primary.add_note(str(cleanup_error))
                    else:
                        raise
                writer = None
    except (AssetContractError, OSError) as exc:
        raise DirectoryPublishError(f"Could not create retained stage: {exc}") from exc
    finally:
        if writer is not None:
            writer.close()
        if orphan_native is not None:
            primary = sys.exception()
            try:
                if orphan_close is None:
                    os.close(orphan_native)
                else:
                    orphan_close(orphan_native)
            except (AssetContractError, OSError) as cleanup_error:
                if primary is not None:
                    primary.add_note(str(cleanup_error))
                else:
                    raise DirectoryPublishError(
                        f"Could not release retained stage root: {cleanup_error}"
                    ) from cleanup_error


def _indeterminate_cause(
    error: BaseException,
) -> DirectoryPublishIndeterminateError | None:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, DirectoryPublishIndeterminateError):
            return current
        current = current.__cause__
    return None


def _read_descriptor_bytes(descriptor: int, *, limit: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    payload = bytearray()
    while len(payload) <= limit:
        chunk = os.read(
            descriptor,
            min(1024 * 1024, limit + 1 - len(payload)),
        )
        if not chunk:
            break
        payload.extend(chunk)
    if len(payload) > limit:
        raise DirectoryPublishError("Append-only journal exceeds its byte limit")
    return bytes(payload)


def _last_complete_journal_payload(
    payload: bytes,
    *,
    max_record_bytes: int,
) -> bytes:
    try:
        recovered, _prefix_size, _partial_tail = recover_validated_journal_prefix(
            payload,
            max_record_bytes=max_record_bytes,
        )
        return recovered
    except PublicationJournalError as exc:
        raise DirectoryPublishError(str(exc)) from exc


def _validated_journal_prefix(
    payload: bytes,
    *,
    max_record_bytes: int,
) -> tuple[bytes, int, bool]:
    try:
        return recover_validated_journal_prefix(
            payload,
            max_record_bytes=max_record_bytes,
        )
    except PublicationJournalError as exc:
        raise DirectoryPublishError(str(exc)) from exc


def _validated_journal_history(
    payload: bytes,
    *,
    max_record_bytes: int,
) -> tuple[tuple[bytes, ...], int, bool]:
    try:
        return recover_validated_journal_history(
            payload,
            max_record_bytes=max_record_bytes,
        )
    except PublicationJournalError as exc:
        raise DirectoryPublishError(str(exc)) from exc


def _journal_frame(payload: bytes) -> bytes:
    try:
        return journal_frame(payload)
    except PublicationJournalError as exc:
        raise DirectoryPublishError(str(exc)) from exc


def _write_descriptor_bytes(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short append-only journal write")
        view = view[written:]


def _require_journal_binding(
    path: Path,
    descriptor: int,
    expected_identity: DirectoryIdentity,
    *,
    retained_parent: PinnedOutputParent | None = None,
) -> None:
    retained = descriptor_file_stat(descriptor)
    named_handle: int | None = None
    try:
        if retained_parent is None:
            named = path_file_stat(path)
        else:
            _require_journal_retained_parent(path, retained_parent)
            if retained_parent.parent_fd is not None:
                named = os.stat(
                    path.name,
                    dir_fd=retained_parent.parent_fd,
                    follow_symlinks=False,
                )
            else:
                api = retained_parent.windows_api
                parent_handle = retained_parent.windows_parent_handle
                if api is None or parent_handle is None:
                    raise DirectoryPublishError(
                        "Append-only journal retained Windows parent is unavailable"
                    )
                named_handle = api.open_existing_file_strict(
                    parent_handle,
                    path.name,
                    share_delete=True,
                )
                named = api.strict_entry_info(
                    named_handle,
                    context=f"retained append-only journal {path.name}",
                )
    finally:
        if named_handle is not None and retained_parent is not None:
            api = retained_parent.windows_api
            if api is not None:
                api.close(named_handle)
    if (
        is_link_or_reparse(retained)
        or is_link_or_reparse(named)
        or not stat.S_ISREG(retained.st_mode)
        or not stat.S_ISREG(named.st_mode)
        or retained.st_nlink != 1
        or named.st_nlink != 1
        or file_identity(retained) != expected_identity
        or file_identity(named) != expected_identity
    ):
        raise DirectoryPublishError("Append-only journal path binding changed")


def _require_journal_retained_parent(
    path: Path,
    parent: PinnedOutputParent,
) -> None:
    if Path(os.path.abspath(path.parent)) != parent.path:
        raise DirectoryPublishError("Append-only journal retained parent does not match its path")
    try:
        parent.assert_current()
    except AssetContractError as exc:
        raise DirectoryPublishError("Append-only journal retained parent changed") from exc


def _open_retained_journal(
    path: Path,
    flags: int,
    *,
    retained_parent: PinnedOutputParent | None,
    writable: bool,
    create: bool = False,
    delete: bool = False,
) -> tuple[int, int | None]:
    if retained_parent is None:
        return os.open(path, flags, 0o600), None
    _require_journal_retained_parent(path, retained_parent)
    if retained_parent.parent_fd is not None:
        descriptor = os.open(
            path.name,
            flags,
            0o600,
            dir_fd=retained_parent.parent_fd,
        )
        return descriptor, None
    api = retained_parent.windows_api
    parent_handle = retained_parent.windows_parent_handle
    if api is None or parent_handle is None:
        raise DirectoryPublishError("Append-only journal retained Windows parent is unavailable")
    native = (
        api.create_file(parent_handle, path.name)
        if create
        else api.open_existing_file_strict(
            parent_handle,
            path.name,
            delete=delete,
            write=writable,
        )
    )
    try:
        return api.duplicate_to_descriptor(native, writable=writable), native
    except BaseException:
        api.close(native)
        raise


def _close_retained_journal(
    descriptor: int | None,
    native: int | None,
    retained_parent: PinnedOutputParent | None,
    *,
    context: str,
) -> None:
    errors: list[BaseException] = []
    if descriptor is not None:
        try:
            _close_descriptor(descriptor, context=context)
        except DirectoryPublishError as exc:
            errors.append(exc)
    if native is not None and retained_parent is not None:
        api = retained_parent.windows_api
        if api is not None:
            try:
                api.close(native)
            except AssetContractError as exc:
                errors.append(exc)
    if errors:
        error = errors[0]
        if isinstance(error, DirectoryPublishError):
            raise error
        raise DirectoryPublishError(f"{context}: {error}") from error


def _flush_retained_journal_parent(
    path: Path,
    parent: PinnedOutputParent,
    *,
    context: str,
) -> None:
    _require_journal_retained_parent(path, parent)
    try:
        parent.flush_durable(context=context)
    except (AssetContractError, OSError) as exc:
        raise DirectoryPublishError(f"Could not durably flush {context}: {exc}") from exc
    _require_journal_retained_parent(path, parent)


def _require_retained_parent_binding(path: Path, descriptor: int) -> None:
    """Bind a lexical parent (including /proc/self/fd) to a retained directory."""

    try:
        retained = descriptor_file_stat(descriptor)
        named = os.stat(path, follow_symlinks=True)
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not validate retained append-only journal parent {path}: {exc}"
        ) from exc
    if (
        not stat.S_ISDIR(retained.st_mode)
        or not stat.S_ISDIR(named.st_mode)
        or file_identity(retained) != file_identity(named)
    ):
        raise DirectoryPublishError("Append-only journal retained parent binding changed")


def read_append_only_journal_state(
    path: Path,
    *,
    max_record_bytes: int,
    max_file_bytes: int,
) -> tuple[bytes, DirectoryIdentity, bool] | None:
    """Read the last complete record and classify an exact plausible torn tail."""

    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            return None
        retained = descriptor_file_stat(descriptor)
        identity = file_identity(retained)
        _require_journal_binding(path, descriptor, identity)
        file_payload = _read_descriptor_bytes(descriptor, limit=max_file_bytes)
        _require_journal_binding(path, descriptor, identity)
        recovered, _complete_prefix_size, partial_tail = _validated_journal_prefix(
            file_payload,
            max_record_bytes=max_record_bytes,
        )
        return recovered, identity, partial_tail
    except DirectoryPublishError:
        raise
    except OSError as exc:
        raise DirectoryPublishError(f"Could not read append-only journal {path}: {exc}") from exc
    finally:
        if descriptor is not None:
            _close_descriptors(((descriptor, "append-only journal descriptor cleanup"),))


def read_append_only_journal(
    path: Path,
    *,
    max_record_bytes: int,
    max_file_bytes: int,
) -> tuple[bytes, DirectoryIdentity] | None:
    loaded = read_append_only_journal_state(
        path,
        max_record_bytes=max_record_bytes,
        max_file_bytes=max_file_bytes,
    )
    if loaded is None:
        return None
    return loaded[0], loaded[1]


def read_append_only_journal_history_state(
    path: Path,
    *,
    max_record_bytes: int,
    max_file_bytes: int,
    retained_parent: PinnedOutputParent | None = None,
) -> tuple[tuple[bytes, ...], DirectoryIdentity, bool] | None:
    """Read and bind every complete append-only record plus a torn-tail flag."""

    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    native: int | None = None
    try:
        try:
            descriptor, native = _open_retained_journal(
                path,
                flags,
                retained_parent=retained_parent,
                writable=False,
            )
        except FileNotFoundError:
            return None
        retained = descriptor_file_stat(descriptor)
        identity = file_identity(retained)
        _require_journal_binding(
            path,
            descriptor,
            identity,
            retained_parent=retained_parent,
        )
        file_payload = _read_descriptor_bytes(descriptor, limit=max_file_bytes)
        _require_journal_binding(
            path,
            descriptor,
            identity,
            retained_parent=retained_parent,
        )
        records, _complete_prefix_size, partial_tail = _validated_journal_history(
            file_payload,
            max_record_bytes=max_record_bytes,
        )
        return records, identity, partial_tail
    except DirectoryPublishError:
        raise
    except (AssetContractError, OSError) as exc:
        raise DirectoryPublishError(f"Could not read append-only journal {path}: {exc}") from exc
    finally:
        if descriptor is not None or native is not None:
            _close_retained_journal(
                descriptor,
                native,
                retained_parent,
                context="append-only journal descriptor cleanup",
            )


def truncate_append_only_journal_partial_tail(
    path: Path,
    *,
    expected_identity: DirectoryIdentity,
    expected_payload: bytes,
    expected_history: tuple[bytes, ...],
    max_record_bytes: int,
    max_file_bytes: int,
) -> DirectoryIdentity:
    """Durably discard only a torn append after an exact complete journal prefix."""

    flags = (
        os.O_RDWR
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        _require_journal_binding(path, descriptor, expected_identity)
        before = _read_descriptor_bytes(descriptor, limit=max_file_bytes)
        records, complete_prefix_size, partial_tail = _validated_journal_history(
            before,
            max_record_bytes=max_record_bytes,
        )
        expected_prefix = _journal_history_prefix(expected_history)
        if (
            not partial_tail
            or records != expected_history
            or records[-1] != expected_payload
            or expected_history[-1] != expected_payload
            or before[:complete_prefix_size] != expected_prefix
            or complete_prefix_size != len(expected_prefix)
        ):
            raise DirectoryPublishError("Append-only journal torn tail changed before repair")
        _require_journal_binding(path, descriptor, expected_identity)
        os.ftruncate(descriptor, complete_prefix_size)
        os.fsync(descriptor)
        fsync_directory(path.parent, context="append-only journal tail repair parent")
        _require_journal_binding(path, descriptor, expected_identity)
        repaired = _read_descriptor_bytes(descriptor, limit=max_file_bytes)
        repaired_records, repaired_size, repaired_partial = _validated_journal_history(
            repaired,
            max_record_bytes=max_record_bytes,
        )
        if (
            repaired != expected_prefix
            or repaired_records != expected_history
            or repaired_size != len(repaired)
            or repaired_partial
        ):
            raise DirectoryPublishError("Append-only journal prefix changed during tail repair")
        return expected_identity
    except DirectoryPublishError:
        raise
    except OSError as exc:
        raise DirectoryPublishError(f"Could not repair append-only journal {path}: {exc}") from exc
    finally:
        if descriptor is not None:
            _close_descriptors(((descriptor, "append-only journal tail repair cleanup"),))


def _journal_history_prefix(records: tuple[bytes, ...]) -> bytes:
    if not records:
        raise DirectoryPublishError("Append-only journal history is empty")
    return records[0] + b"".join(_journal_frame(record) for record in records[1:])


def create_append_only_journal(
    path: Path,
    payload: bytes,
    *,
    max_record_bytes: int,
    retained_parent: PinnedOutputParent | None = None,
) -> DirectoryIdentity:
    if not payload or len(payload) > max_record_bytes:
        raise DirectoryPublishError("Append-only journal record exceeds its byte limit")
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    native: int | None = None
    try:
        descriptor, native = _open_retained_journal(
            path,
            flags,
            retained_parent=retained_parent,
            writable=True,
            create=True,
        )
        retained = descriptor_file_stat(descriptor)
        identity = file_identity(retained)
        if (
            is_link_or_reparse(retained)
            or not stat.S_ISREG(retained.st_mode)
            or retained.st_nlink != 1
            or retained.st_size != 0
        ):
            raise DirectoryPublishError("New append-only journal is unsafe")
        _write_descriptor_bytes(descriptor, payload)
        os.fsync(descriptor)
        _require_journal_binding(
            path,
            descriptor,
            identity,
            retained_parent=retained_parent,
        )
        if _read_descriptor_bytes(descriptor, limit=max_record_bytes) != payload:
            raise DirectoryPublishError("New append-only journal bytes changed")
        if retained_parent is not None:
            _flush_retained_journal_parent(
                path,
                retained_parent,
                context="append-only journal parent",
            )
        return identity
    except (DirectoryPublishError, FileExistsError):
        raise
    except (AssetContractError, OSError) as exc:
        raise DirectoryPublishError(f"Could not create append-only journal {path}: {exc}") from exc
    finally:
        if descriptor is not None or native is not None:
            _close_retained_journal(
                descriptor,
                native,
                retained_parent,
                context="new append-only journal descriptor cleanup",
            )


def append_append_only_journal(
    path: Path,
    *,
    expected_identity: DirectoryIdentity,
    expected_payload: bytes,
    expected_history: tuple[bytes, ...] | None = None,
    updated_payload: bytes,
    max_record_bytes: int,
    max_file_bytes: int,
    repair_partial_tail: bool = False,
    retained_parent_fd: int | None = None,
    retained_parent: PinnedOutputParent | None = None,
) -> DirectoryIdentity:
    if not updated_payload or len(updated_payload) > max_record_bytes:
        raise DirectoryPublishError("Append-only journal record exceeds its byte limit")
    flags = (
        os.O_RDWR
        | os.O_APPEND
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    native: int | None = None
    try:
        descriptor, native = _open_retained_journal(
            path,
            flags,
            retained_parent=retained_parent,
            writable=True,
        )
        _require_journal_binding(
            path,
            descriptor,
            expected_identity,
            retained_parent=retained_parent,
        )
        before = _read_descriptor_bytes(descriptor, limit=max_file_bytes)
        records, complete_prefix_size, partial_tail = _validated_journal_history(
            before,
            max_record_bytes=max_record_bytes,
        )
        recovered = records[-1]
        if recovered != expected_payload:
            raise DirectoryPublishError("Append-only journal changed before transition")
        expected_complete_prefix: bytes | None = None
        if expected_history is not None:
            expected_complete_prefix = _journal_history_prefix(expected_history)
            if (
                records != expected_history
                or expected_history[-1] != expected_payload
                or complete_prefix_size != len(expected_complete_prefix)
                or before[:complete_prefix_size] != expected_complete_prefix
            ):
                raise DirectoryPublishError(
                    "Append-only journal complete history changed before transition"
                )
        frame = _journal_frame(updated_payload)
        if partial_tail:
            if not repair_partial_tail:
                raise DirectoryPublishError("Append-only journal changed before transition")
            complete_prefix = before[:complete_prefix_size]
            partial_frame = before[complete_prefix_size:]
            if not frame.startswith(partial_frame):
                raise DirectoryPublishError(
                    "Append-only journal partial tail does not match the expected transition"
                )
            _require_journal_binding(
                path,
                descriptor,
                expected_identity,
                retained_parent=retained_parent,
            )
            os.ftruncate(descriptor, complete_prefix_size)
            os.fsync(descriptor)
            _require_journal_binding(
                path,
                descriptor,
                expected_identity,
                retained_parent=retained_parent,
            )
            if retained_parent is not None:
                _flush_retained_journal_parent(
                    path,
                    retained_parent,
                    context="append-only journal repair parent",
                )
            elif retained_parent_fd is None:
                fsync_directory(path.parent, context="append-only journal repair parent")
            else:
                _require_retained_parent_binding(path.parent, retained_parent_fd)
                os.fsync(retained_parent_fd)
                _require_retained_parent_binding(path.parent, retained_parent_fd)
            _require_journal_binding(
                path,
                descriptor,
                expected_identity,
                retained_parent=retained_parent,
            )
            repaired = _read_descriptor_bytes(descriptor, limit=max_file_bytes)
            if repaired != complete_prefix:
                raise DirectoryPublishError("Append-only journal prefix changed during tail repair")
            repaired_records, repaired_size, repaired_partial = _validated_journal_history(
                repaired,
                max_record_bytes=max_record_bytes,
            )
            if (
                repaired_records[-1] != expected_payload
                or repaired_size != len(complete_prefix)
                or repaired_partial
                or (
                    expected_history is not None
                    and (
                        repaired_records != expected_history or expected_complete_prefix != repaired
                    )
                )
            ):
                raise DirectoryPublishError("Append-only journal prefix changed during tail repair")
            _require_journal_binding(
                path,
                descriptor,
                expected_identity,
                retained_parent=retained_parent,
            )
            before = repaired
        elif expected_history is None and not (
            before == expected_payload or before.endswith(_journal_frame(expected_payload))
        ):
            raise DirectoryPublishError("Append-only journal changed before transition")
        _require_journal_binding(
            path,
            descriptor,
            expected_identity,
            retained_parent=retained_parent,
        )
        if len(before) + len(frame) > max_file_bytes:
            raise DirectoryPublishError("Append-only journal exceeds its byte limit")
        os.lseek(descriptor, 0, os.SEEK_END)
        _write_descriptor_bytes(descriptor, frame)
        os.fsync(descriptor)
        _require_journal_binding(
            path,
            descriptor,
            expected_identity,
            retained_parent=retained_parent,
        )
        after = _read_descriptor_bytes(descriptor, limit=max_file_bytes)
        if expected_history is None:
            if (
                _last_complete_journal_payload(
                    after,
                    max_record_bytes=max_record_bytes,
                )
                != updated_payload
            ):
                raise DirectoryPublishError("Append-only journal transition is incomplete")
        else:
            after_records, after_size, after_partial = _validated_journal_history(
                after,
                max_record_bytes=max_record_bytes,
            )
            expected_after = expected_history + (updated_payload,)
            expected_after_bytes = _journal_history_prefix(expected_after)
            if (
                after_records != expected_after
                or after_partial
                or after_size != len(expected_after_bytes)
                or after != expected_after_bytes
            ):
                raise DirectoryPublishError("Append-only journal transition is incomplete")
        return expected_identity
    except DirectoryPublishError:
        raise
    except (AssetContractError, OSError) as exc:
        raise DirectoryPublishError(f"Could not append journal transition {path}: {exc}") from exc
    finally:
        if descriptor is not None or native is not None:
            _close_retained_journal(
                descriptor,
                native,
                retained_parent,
                context="append-only journal transition cleanup",
            )


def _linux_rename_name_noreplace(
    parent_descriptor: int,
    source_name: str,
    destination_name: str,
) -> None:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (AttributeError, OSError) as exc:
        raise DirectoryPublishError("Linux RENAME_NOREPLACE is unavailable") from exc
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    ctypes.set_errno(0)
    if (
        renameat2(
            parent_descriptor,
            os.fsencode(source_name),
            parent_descriptor,
            os.fsencode(destination_name),
            1,
        )
        == 0
    ):
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise FileExistsError(error, "destination already exists", destination_name)
    if error in {
        errno.ENOSYS,
        errno.EINVAL,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }:
        raise DirectoryPublishError("Linux RENAME_NOREPLACE is unavailable")
    raise DirectoryPublishError(
        error,
        f"Could not claim retained Linux entry: {os.strerror(error)}",
        source_name,
    )


def retained_journal_evidence_path(
    path: Path,
    expected_identity: DirectoryIdentity,
) -> Path:
    """Return the deterministic terminal locator for one retained journal."""

    digest = hashlib.sha256(
        os.fsencode(path.name)
        + b"\0"
        + str(expected_identity[0]).encode("ascii")
        + b"\0"
        + str(expected_identity[1]).encode("ascii")
    ).hexdigest()
    return path.parent / f".worldforge-retained-journal-{digest}.json"


def retained_recovery_evidence(
    *,
    stage_path: Path | None = None,
    stage_identity: DirectoryIdentity | None = None,
    journal_path: Path | None = None,
    journal_identity: DirectoryIdentity | None = None,
) -> dict[str, object]:
    """Describe retained private entries without exposing absolute host paths."""

    evidence: dict[str, object] = {}
    if stage_path is not None:
        evidence["stage"] = {
            "locator": stage_path.name,
            "identity": (
                None if stage_identity is None else [stage_identity[0], stage_identity[1]]
            ),
            "retention": "active",
        }
    if journal_path is not None:
        evidence["journal"] = {
            "locator": journal_path.name,
            "identity": (
                None if journal_identity is None else [journal_identity[0], journal_identity[1]]
            ),
            "retention": "active",
        }
    return evidence


def _linux_archive_retained_journal(
    parent_descriptor: int,
    name: str,
    descriptor: int,
    expected_identity: DirectoryIdentity,
    payload: bytes,
) -> str:
    """Retire one exact Linux journal without pathname-based deletion."""

    def archive_state(info: FileStat) -> tuple[int, int, int, int, int, int]:
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
        )

    claim_name = retained_journal_evidence_path(
        Path(name),
        expected_identity,
    ).name
    retained_before = descriptor_file_stat(descriptor)
    if (
        is_link_or_reparse(retained_before)
        or not stat.S_ISREG(retained_before.st_mode)
        or retained_before.st_nlink != 1
        or file_identity(retained_before) != expected_identity
    ):
        raise DirectoryPublishError("Retained Linux journal identity changed before archival")
    _linux_rename_name_noreplace(
        parent_descriptor,
        name,
        claim_name,
    )
    archived = True
    try:
        retained = descriptor_file_stat(descriptor)
        named = os.stat(
            claim_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            is_link_or_reparse(retained)
            or is_link_or_reparse(named)
            or not stat.S_ISREG(retained.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or retained.st_nlink != 1
            or named.st_nlink != 1
            or file_identity(retained) != expected_identity
            or file_identity(named) != expected_identity
            or archive_state(retained) != archive_state(named)
            or archive_state(retained) != archive_state(retained_before)
            or _read_descriptor_bytes(descriptor, limit=max(len(payload), 1)) != payload
        ):
            raise DirectoryPublishError("Archived Linux journal identity changed")
        try:
            os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise DirectoryPublishError("Active Linux journal name reappeared during archival")
        os.fsync(parent_descriptor)
        retained_after = descriptor_file_stat(descriptor)
        named_after = os.stat(
            claim_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            archive_state(retained_after) != archive_state(retained_before)
            or archive_state(named_after) != archive_state(retained_before)
            or _read_descriptor_bytes(descriptor, limit=max(len(payload), 1)) != payload
        ):
            raise DirectoryPublishError("Archived Linux journal changed after durability flush")
        try:
            os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise DirectoryPublishError("Active Linux journal name reappeared after archival")
        return claim_name
    except BaseException:
        if archived:
            try:
                archived_info = os.stat(
                    claim_name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                try:
                    os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise DirectoryPublishIndeterminateError(
                        "Active Linux journal name reappeared during archival rollback"
                    )
                if (
                    is_link_or_reparse(archived_info)
                    or not stat.S_ISREG(archived_info.st_mode)
                    or file_identity(archived_info) != expected_identity
                    or archive_state(archived_info) != archive_state(retained_before)
                ):
                    raise DirectoryPublishIndeterminateError(
                        "Archived Linux journal changed before rollback"
                    )
                _linux_rename_name_noreplace(
                    parent_descriptor,
                    claim_name,
                    name,
                )
                archived = False
                os.fsync(parent_descriptor)
                restored = os.stat(
                    name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if archive_state(restored) != archive_state(retained_before):
                    raise DirectoryPublishIndeterminateError(
                        "Restored Linux journal identity changed"
                    )
            except BaseException as rollback_error:
                raise DirectoryPublishIndeterminateError(
                    "Retained Linux journal archival could not be restored"
                ) from rollback_error
        raise


def remove_append_only_journal(
    path: Path,
    *,
    expected_identity: DirectoryIdentity,
    expected_payload: bytes,
    expected_history: tuple[bytes, ...] | None = None,
    max_record_bytes: int,
    max_file_bytes: int,
    retained_parent_fd: int | None = None,
    retained_parent: PinnedOutputParent | None = None,
) -> Path | None:
    """Retire one exact journal without deleting an unverified pathname object."""

    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    native: int | None = None
    namespace_mutated = False
    try:
        descriptor, native = _open_retained_journal(
            path,
            flags,
            retained_parent=retained_parent,
            writable=False,
            delete=os.name == "nt",
        )
        _require_journal_binding(
            path,
            descriptor,
            expected_identity,
            retained_parent=retained_parent,
        )
        payload = _read_descriptor_bytes(descriptor, limit=max_file_bytes)
        records, complete_prefix_size, partial_tail = _validated_journal_history(
            payload,
            max_record_bytes=max_record_bytes,
        )
        expected_prefix = (
            None if expected_history is None else _journal_history_prefix(expected_history)
        )
        if (
            records[-1] != expected_payload
            or partial_tail
            or complete_prefix_size != len(payload)
            or (
                expected_history is not None
                and (
                    records != expected_history
                    or expected_history[-1] != expected_payload
                    or expected_prefix != payload
                )
            )
        ):
            raise DirectoryPublishError("Append-only journal changed before removal")
        _require_journal_binding(
            path,
            descriptor,
            expected_identity,
            retained_parent=retained_parent,
        )
        if sys.platform.startswith("linux") and os.name == "posix":
            parent_descriptor = (
                retained_parent.parent_fd
                if retained_parent is not None
                else (
                    os.open(path.parent, _POSIX_DIRECTORY_FLAGS)
                    if retained_parent_fd is None
                    else retained_parent_fd
                )
            )
            if parent_descriptor is None:
                raise DirectoryPublishError(
                    "Append-only journal retained POSIX parent is unavailable"
                )
            try:
                parent_identity = file_identity(descriptor_file_stat(parent_descriptor))
                if retained_parent is not None:
                    _require_journal_retained_parent(path, retained_parent)
                elif retained_parent_fd is None:
                    if (
                        directory_identity(path.parent, context="append-only journal parent")
                        != parent_identity
                    ):
                        raise DirectoryPublishError("Append-only journal parent identity changed")
                else:
                    _require_retained_parent_binding(path.parent, parent_descriptor)
                archived_name = _linux_archive_retained_journal(
                    parent_descriptor,
                    path.name,
                    descriptor,
                    expected_identity,
                    payload,
                )
                namespace_mutated = True
                if retained_parent is not None:
                    _require_journal_retained_parent(path, retained_parent)
                elif retained_parent_fd is None:
                    if (
                        directory_identity(path.parent, context="append-only journal parent")
                        != parent_identity
                    ):
                        raise DirectoryPublishIndeterminateError(
                            "Append-only journal parent identity changed after removal"
                        )
                else:
                    _require_retained_parent_binding(path.parent, parent_descriptor)
            finally:
                if retained_parent is None and retained_parent_fd is None:
                    _close_descriptor(
                        parent_descriptor,
                        context="append-only journal parent cleanup",
                    )
            return path.parent / archived_name
        if os.name == "nt":
            if retained_parent is not None:
                api = retained_parent.windows_api
                if api is None or native is None:
                    raise DirectoryPublishError(
                        "Append-only journal retained Windows deletion is unavailable"
                    )
                _close_descriptor(
                    descriptor,
                    context="append-only journal retained descriptor handoff",
                )
                descriptor = None
                api.mark_delete_on_close(native)
                namespace_mutated = True
                api.close(native)
                native = None
                _flush_retained_journal_parent(
                    path,
                    retained_parent,
                    context="append-only journal parent",
                )
                return None
            _close_descriptor(
                descriptor,
                context="append-only journal retained descriptor handoff",
            )
            descriptor = None
            handle = _windows_open_delete_handle(
                path,
                expected_identity,
                directory=False,
            )
            try:
                _windows_mark_handle_for_deletion(handle, path)
                namespace_mutated = True
            finally:
                _windows_close_cleanup_handle(handle)
            fsync_directory(path.parent, context="append-only journal parent")
            return None
        raise DirectoryPublishError(
            "Identity-bound journal removal is supported only on Linux and Windows"
        )
    except DirectoryPublishIndeterminateError:
        raise
    except BaseException as exc:
        if namespace_mutated:
            raise DirectoryPublishIndeterminateError(
                "Append-only journal removal durability is indeterminate"
            ) from exc
        if isinstance(exc, (DirectoryPublishError, FileNotFoundError)):
            raise
        raise DirectoryPublishError(f"Could not retire append-only journal {path}: {exc}") from exc
    finally:
        if descriptor is not None or native is not None:
            _close_retained_journal(
                descriptor,
                native,
                retained_parent,
                context="append-only journal removal cleanup",
            )


def require_pinned_names_absent(
    parent: PinnedOutputParent,
    names: tuple[str, ...],
    *,
    context: str,
) -> None:
    """Fail indeterminately if any original name exists under a retained parent."""

    try:
        parent.assert_current()
        for name in names:
            if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
                raise DirectoryPublishIndeterminateError(f"{context} original name is invalid")
            if parent.parent_fd is not None:
                try:
                    os.stat(
                        name,
                        dir_fd=parent.parent_fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise DirectoryPublishIndeterminateError(
                        f"{context} original name absence is indeterminate: {name!r}"
                    ) from exc
                raise DirectoryPublishIndeterminateError(
                    f"{context} original name reappeared: {name!r}"
                )
            api = parent.windows_api
            parent_handle = parent.windows_parent_handle
            if api is None or parent_handle is None:
                raise DirectoryPublishIndeterminateError(
                    f"{context} retained Windows parent is unavailable"
                )
            handle: int | None = None
            try:
                handle = api.open_existing_entry(parent_handle, name)
            except FileNotFoundError:
                continue
            except AssetContractError as exc:
                raise DirectoryPublishIndeterminateError(
                    f"{context} original Windows name absence is indeterminate: {name!r}"
                ) from exc
            else:
                raise DirectoryPublishIndeterminateError(
                    f"{context} original Windows name reappeared: {name!r}"
                )
            finally:
                if handle is not None:
                    try:
                        api.close(handle)
                    except AssetContractError as exc:
                        raise DirectoryPublishIndeterminateError(
                            f"{context} Windows reappearance handle cleanup failed"
                        ) from exc
        parent.assert_current()
    except DirectoryPublishIndeterminateError:
        raise
    except (AssetContractError, OSError) as exc:
        raise DirectoryPublishIndeterminateError(
            f"{context} retained parent verification is indeterminate"
        ) from exc


def remove_d3_append_only_journal(
    path: Path,
    *,
    expected_identity: DirectoryIdentity,
    expected_history: tuple[bytes, ...],
    max_record_bytes: int,
    max_file_bytes: int,
    retained_parent: PinnedOutputParent | None = None,
) -> Path | None:
    """Retire a D3 journal and prove its anchored original name stayed absent."""

    expected_payload = expected_history[-1] if expected_history else b""

    def remove_and_check(parent: PinnedOutputParent) -> Path | None:
        if Path(os.path.abspath(path.parent)) != parent.path:
            raise DirectoryPublishError("D3 journal retained parent does not match its path")
        parent.assert_current()
        retained_evidence = remove_append_only_journal(
            path,
            expected_identity=expected_identity,
            expected_payload=expected_payload,
            expected_history=expected_history,
            max_record_bytes=max_record_bytes,
            max_file_bytes=max_file_bytes,
            retained_parent=parent,
        )
        require_pinned_names_absent(
            parent,
            (path.name,),
            context="D3 journal cleanup",
        )
        return retained_evidence

    if retained_parent is not None:
        return remove_and_check(retained_parent)
    try:
        with open_verified_output_parent(path.parent, create=False) as parent:
            return remove_and_check(parent)
    except DirectoryPublishIndeterminateError:
        raise
    except AssetContractError as exc:
        indeterminate = _indeterminate_cause(exc)
        if indeterminate is not None:
            raise indeterminate from exc
        raise DirectoryPublishError(str(exc)) from exc


_AT_REMOVEDIR = 0x200
_AT_EMPTY_PATH = 0x1000


class _FileDispositionInfo(ctypes.Structure):
    _fields_ = [("delete_file", ctypes.c_ubyte)]


class _IoStatusValue(ctypes.Union):
    _fields_ = [
        ("status", ctypes.c_int32),
        ("pointer", ctypes.c_void_p),
    ]


class _IoStatusBlock(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [
        ("value", _IoStatusValue),
        ("information", ctypes.c_size_t),
    ]


class _NtFileRenameInformation(ctypes.Structure):
    _fields_ = [
        ("replace_if_exists", ctypes.c_ubyte),
        ("root_directory", ctypes.c_void_p),
        ("filename_length", ctypes.c_uint32),
        ("filename", ctypes.c_uint16 * 1),
    ]


_WindowsTreeState = tuple[DirectoryIdentity, int, int, int, int]
_WindowsTreeFingerprint = str


@dataclass(frozen=True)
class _WindowsPayloadHandle:
    relative: str
    handle: int
    directory: bool
    expected: _WindowsTreeState


@dataclass
class _WindowsRetainedTree:
    source: Path
    source_identity: DirectoryIdentity
    parent_identity: DirectoryIdentity
    source_handle: int | None
    parent_handle: int
    payload_handles: list[_WindowsPayloadHandle]
    expected_tree: dict[str, _WindowsTreeState]
    expected_root_state: _WindowsTreeState
    expected_fingerprint: _WindowsTreeFingerprint | None
    open_tree_entry: Callable[[Path, bool, bool], int]
    open_published_root: Callable[[Path], int]
    flush_file_buffers: Callable[[ctypes.c_void_p], int]
    close_handle: Callable[[ctypes.c_void_p], int]
    nt_set_information: Callable[[ctypes.c_void_p, object, object, int, int], int]
    nt_status_to_dos_error: Callable[[int], int]
    flush_parent: Callable[[Path, DirectoryIdentity, str], None]
    namespace_mutated: bool = False
    namespace_outcome_ambiguous: bool = False

    def _require_source_handle(self, *, context: str) -> int:
        if self.source_handle is None:
            raise DirectoryPublishError(f"{context} source handle is unavailable")
        return self.source_handle

    def _require_root_handles(self, *, context: str) -> None:
        _require_expected_directory(
            file_stat_module._windows_handle_stat(  # noqa: SLF001
                self._require_source_handle(context=context)
            ),
            self.source_identity,
            context=f"{context} source",
        )
        _require_expected_directory(
            file_stat_module._windows_handle_stat(self.parent_handle),  # noqa: SLF001
            self.parent_identity,
            context=f"{context} parent",
        )

    def _require_payload_handles(self, *, context: str) -> None:
        for retained in self.payload_handles:
            opened = file_stat_module._windows_handle_stat(retained.handle)  # noqa: SLF001
            expected = retained.expected
            if (
                is_link_or_reparse(opened)
                or file_identity(opened) != expected[0]
                or stat.S_IFMT(opened.st_mode) != expected[1]
                or opened.st_nlink != expected[2]
                or opened.st_size != expected[3]
                or opened.st_mtime_ns != expected[4]
            ):
                raise DirectoryPublishError(
                    f"{context} payload identity changed: {retained.relative}"
                )

    def _close_payload_handles(self, *, context: str) -> None:
        primary = sys.exception()
        cleanup_error: DirectoryPublishError | None = None
        retained_failures: list[_WindowsPayloadHandle] = []
        for retained in reversed(self.payload_handles):
            if self.close_handle(ctypes.c_void_p(retained.handle)):
                continue
            retained_failures.append(retained)
            error = ctypes.get_last_error()
            detail = f"{context} for {retained.relative} failed: {_windows_error_detail(error)}"
            if primary is not None:
                primary.add_note(detail)
            elif cleanup_error is not None:
                cleanup_error.add_note(detail)
            else:
                cleanup_error = DirectoryPublishError(detail)
        self.payload_handles = list(reversed(retained_failures))
        if cleanup_error is not None:
            if self.namespace_mutated or self.namespace_outcome_ambiguous:
                raise DirectoryPublishIndeterminateError(
                    "Windows directory publication outcome is indeterminate after "
                    "NtSetInformationFile because retained payload cleanup failed"
                ) from cleanup_error
            raise cleanup_error

    def _flush_handle(self, handle: int, context: str) -> None:
        if not self.flush_file_buffers(ctypes.c_void_p(handle)):
            error = ctypes.get_last_error()
            raise DirectoryPublishError(
                f"Could not durably flush {context}: {_windows_error_detail(error)}"
            )

    def flush_payload_tree(self) -> tuple[str, ...]:
        self._require_root_handles(context="retained Windows publication")
        self._require_payload_handles(context="retained Windows publication")
        if _windows_tree_snapshot(self.source) != self.expected_tree:
            raise DirectoryPublishError(
                "Windows publication payload tree changed before durable flush"
            )
        ordered = sorted(
            self.payload_handles,
            key=lambda item: (
                item.directory,
                -len(PurePath(*item.relative.split("/")).parts) if item.directory else 0,
                item.relative,
            ),
        )
        for retained in ordered:
            self._flush_handle(
                retained.handle,
                "Windows publication "
                f"{'directory' if retained.directory else 'payload'} {retained.relative}",
            )
        self._require_payload_handles(context="durably retained Windows publication")
        if _windows_tree_snapshot(self.source) != self.expected_tree:
            raise DirectoryPublishError(
                "Windows publication payload tree changed during durable flush"
            )
        self._flush_handle(
            self._require_source_handle(context="durable Windows publication stage"),
            "Windows publication stage",
        )
        self.flush_parent(
            self.source.parent,
            self.parent_identity,
            "Windows publication stage parent",
        )
        self._require_root_handles(context="durably retained Windows publication")
        self._require_payload_handles(context="durably retained Windows publication")
        if _windows_tree_snapshot(self.source) != self.expected_tree:
            raise DirectoryPublishError(
                "Windows publication payload tree changed after durable flush"
            )
        fingerprint = _windows_tree_fingerprint(
            self.source,
            expected_root_state=self.expected_root_state,
            expected_tree=self.expected_tree,
        )
        self._require_root_handles(context="fingerprinted Windows publication")
        self._require_payload_handles(context="fingerprinted Windows publication")
        if _windows_tree_snapshot(self.source) != self.expected_tree:
            raise DirectoryPublishError(
                "Windows publication payload tree changed during fingerprinting"
            )
        self.expected_fingerprint = fingerprint
        return tuple(retained.relative for retained in ordered if not retained.directory)

    def _require_published_binding(self, destination: Path, *, context: str) -> None:
        try:
            self._require_root_handles(context=context)
            _require_expected_directory(
                path_file_stat(destination.parent),
                self.parent_identity,
                context=f"{context} lexical parent",
            )
            _require_expected_directory(
                path_file_stat(destination),
                self.source_identity,
                context=f"{context} destination",
            )
            try:
                path_file_stat(self.source)
            except FileNotFoundError:
                pass
            else:
                raise DirectoryPublishError(
                    f"{context} retained the private publication stage name"
                )
        except DirectoryPublishError:
            raise
        except OSError as exc:
            raise DirectoryPublishError(
                f"Could not validate {context} path binding: {exc}"
            ) from exc

    def _retain_published_payload(self, destination: Path) -> None:
        if self.payload_handles:
            raise DirectoryPublishError(
                "Windows publication payload handles were not released before rename"
            )
        published_tree = _windows_tree_snapshot(destination)
        if published_tree != self.expected_tree:
            raise DirectoryPublishError(
                "Published Windows payload inventory or metadata changed during rename"
            )
        retained: list[_WindowsPayloadHandle] = []
        try:
            for relative, expected in sorted(self.expected_tree.items()):
                payload_path = destination / PurePath(*relative.split("/"))
                directory = expected[1] == stat.S_IFDIR
                handle = self.open_tree_entry(payload_path, directory, False)
                item = _WindowsPayloadHandle(
                    relative=relative,
                    handle=handle,
                    directory=directory,
                    expected=expected,
                )
                retained.append(item)
                opened = file_stat_module._windows_handle_stat(handle)  # noqa: SLF001
                if (
                    is_link_or_reparse(opened)
                    or file_identity(opened) != expected[0]
                    or stat.S_IFMT(opened.st_mode) != expected[1]
                    or opened.st_nlink != expected[2]
                    or opened.st_size != expected[3]
                    or opened.st_mtime_ns != expected[4]
                ):
                    raise DirectoryPublishError(
                        f"Published Windows payload identity changed: {relative}"
                    )
        except BaseException:
            self.payload_handles = retained
            raise
        self.payload_handles = retained
        self._require_payload_handles(context="sealed published Windows publication")
        if _windows_tree_snapshot(destination) != self.expected_tree:
            raise DirectoryPublishError(
                "Published Windows payload tree changed while retaining seal handles"
            )

    def _handoff_published_root(self, destination: Path) -> DirectoryIdentity:
        if not self.payload_handles:
            raise DirectoryPublishIndeterminateError(
                "Windows directory publication root handoff requires a retained descendant"
            )
        self._require_published_binding(
            destination,
            context="pre-handoff Windows publication",
        )
        self._require_payload_handles(context="pre-handoff Windows publication")
        if _windows_tree_snapshot(destination) != self.expected_tree:
            raise DirectoryPublishIndeterminateError(
                "Published Windows payload tree changed before root-handle handoff"
            )
        if (
            self.expected_fingerprint is None
            or _windows_tree_fingerprint(
                destination,
                expected_root_state=self.expected_root_state,
                expected_tree=self.expected_tree,
            )
            != self.expected_fingerprint
        ):
            raise DirectoryPublishIndeterminateError(
                "Published Windows payload fingerprint changed before root-handle handoff"
            )

        rename_handle = self._require_source_handle(context="Windows publication root handoff")
        if not self.close_handle(ctypes.c_void_p(rename_handle)):
            error = ctypes.get_last_error()
            raise DirectoryPublishIndeterminateError(
                "Windows directory publication root handoff could not release the "
                f"rename handle: {_windows_error_detail(error)}"
            )
        self.source_handle = None
        try:
            handed_off = self.open_published_root(destination)
        except BaseException as exc:
            raise DirectoryPublishIndeterminateError(
                "Windows directory publication root handoff could not retain the "
                "published destination"
            ) from exc
        self.source_handle = handed_off
        try:
            opened = file_stat_module._windows_handle_stat(handed_off)  # noqa: SLF001
            if _windows_tree_state(opened) != self.expected_root_state:
                raise DirectoryPublishError(
                    "Published Windows root identity changed during handle handoff"
                )
            self._require_published_binding(
                destination,
                context="handed-off Windows publication",
            )
            self._require_payload_handles(context="handed-off Windows publication")
            if _windows_tree_snapshot(destination) != self.expected_tree:
                raise DirectoryPublishError(
                    "Published Windows payload tree changed during root-handle handoff"
                )
            if (
                _windows_tree_fingerprint(
                    destination,
                    expected_root_state=self.expected_root_state,
                    expected_tree=self.expected_tree,
                )
                != self.expected_fingerprint
            ):
                raise DirectoryPublishError(
                    "Published Windows payload fingerprint changed during root-handle handoff"
                )
            return file_identity(opened)
        except BaseException as exc:
            if isinstance(exc, DirectoryPublishIndeterminateError):
                raise
            raise DirectoryPublishIndeterminateError(
                "Windows directory publication root handoff could not prove the exact "
                "published identity"
            ) from exc

    def rename_noreplace(self, destination: Path) -> DirectoryIdentity:
        if self.source.parent != destination.parent:
            raise DirectoryPublishError("Windows directory publication must stay within one parent")
        if not destination.is_absolute():
            raise DirectoryPublishError(
                "Windows directory publication destination must be absolute"
            )
        if not self.expected_tree:
            raise DirectoryPublishError(
                "Windows directory publication requires at least one retained "
                "descendant before rename"
            )
        self.flush_payload_tree()
        if self.expected_fingerprint is None:
            raise DirectoryPublishError(
                "Windows publication payload fingerprint was not established"
            )
        self._require_root_handles(context="pre-rename Windows publication")
        self._require_payload_handles(context="pre-rename Windows publication")
        if _windows_tree_snapshot(self.source) != self.expected_tree:
            raise DirectoryPublishError(
                "Windows publication payload tree changed before handle-bound rename"
            )
        self._close_payload_handles(context="Windows pre-rename payload handle release")
        encoded = destination.name.encode("utf-16-le")
        offset = _NtFileRenameInformation.filename.offset
        buffer = ctypes.create_string_buffer(
            max(ctypes.sizeof(_NtFileRenameInformation), offset + len(encoded))
        )
        information = _NtFileRenameInformation.from_buffer(buffer)
        information.replace_if_exists = False
        information.root_directory = self.parent_handle
        information.filename_length = len(encoded)
        ctypes.memmove(ctypes.addressof(buffer) + offset, encoded, len(encoded))
        io_status = _IoStatusBlock()
        try:
            status = ctypes.c_int32(
                int(
                    self.nt_set_information(
                        ctypes.c_void_p(
                            self._require_source_handle(context="Windows directory rename")
                        ),
                        ctypes.byref(io_status),
                        buffer,
                        len(buffer),
                        10,  # FileRenameInformation
                    )
                )
            ).value
        except BaseException as exc:
            self.namespace_outcome_ambiguous = True
            raise DirectoryPublishIndeterminateError(
                "Windows directory publication outcome is indeterminate because "
                "NtSetInformationFile raised after the rename attempt"
            ) from exc
        if status < 0:
            try:
                error = int(self.nt_status_to_dos_error(status))
            except Exception as exc:
                raise DirectoryPublishError(
                    f"Could not translate Windows directory rename status {status}"
                ) from exc
            if error in {80, 183}:
                raise FileExistsError(
                    error,
                    "destination already exists",
                    destination,
                )
            if error == 5:
                try:
                    path_file_stat(destination)
                except OSError:
                    pass
                else:
                    raise FileExistsError(
                        error,
                        "destination already exists",
                        destination,
                    ) from None
            raise DirectoryPublishError(
                error,
                f"{_windows_error_detail(error)} "
                f"(NTSTATUS 0x{status & 0xFFFFFFFF:08x}, Win32 {error})",
                destination,
            )

        try:
            self.namespace_mutated = True
            self._require_published_binding(
                destination,
                context="renamed Windows publication",
            )
            self._retain_published_payload(destination)
            self._require_published_binding(
                destination,
                context="sealed renamed Windows publication",
            )
            published_fingerprint = _windows_tree_fingerprint(
                destination,
                expected_root_state=self.expected_root_state,
                expected_tree=self.expected_tree,
            )
            if published_fingerprint != self.expected_fingerprint:
                raise DirectoryPublishError(
                    "Published Windows payload fingerprint changed during rename"
                )
            self._require_payload_handles(context="fingerprinted published Windows publication")
            if _windows_tree_snapshot(destination) != self.expected_tree:
                raise DirectoryPublishError(
                    "Published Windows payload tree changed during verification"
                )
            published_info = file_stat_module._windows_handle_stat(  # noqa: SLF001
                self._require_source_handle(context="published Windows directory")
            )
            self._flush_handle(
                self._require_source_handle(context="published Windows directory"),
                "published Windows directory",
            )
            self.flush_parent(
                destination.parent,
                self.parent_identity,
                "Windows publication parent",
            )
            self._require_published_binding(
                destination,
                context="durably published Windows publication",
            )
            self._require_payload_handles(context="durably sealed published Windows publication")
            if _windows_tree_snapshot(destination) != self.expected_tree:
                raise DirectoryPublishError(
                    "Published Windows payload tree changed during handle-bound rename"
                )
            if (
                _windows_tree_fingerprint(
                    destination,
                    expected_root_state=self.expected_root_state,
                    expected_tree=self.expected_tree,
                )
                != self.expected_fingerprint
            ):
                raise DirectoryPublishError(
                    "Published Windows payload fingerprint changed during durable flush"
                )
            published_identity = file_identity(published_info)
            handed_off_identity = self._handoff_published_root(destination)
            if handed_off_identity != published_identity:
                raise DirectoryPublishIndeterminateError(
                    "Published Windows root identity changed during handle handoff"
                )
            return handed_off_identity
        except BaseException as exc:
            if isinstance(exc, DirectoryPublishIndeterminateError):
                raise
            raise DirectoryPublishIndeterminateError(
                "Windows directory publication outcome is indeterminate after "
                "NtSetInformationFile; no rollback was attempted for "
                f"{self.source} and {destination}: {exc}"
            ) from exc

    def require_post_body_unchanged(self, destination: Path) -> None:
        """Revalidate the exact published tree before releasing its seal handles."""

        try:
            if not self.namespace_mutated or self.expected_fingerprint is None:
                raise DirectoryPublishError("Windows publication lease was not fully established")
            self._require_published_binding(
                destination,
                context="post-body Windows publication",
            )
            self._require_payload_handles(context="post-body sealed Windows publication")
            if _windows_tree_snapshot(destination) != self.expected_tree:
                raise DirectoryPublishError(
                    "Published Windows payload tree changed during caller verification"
                )
            if (
                _windows_tree_fingerprint(
                    destination,
                    expected_root_state=self.expected_root_state,
                    expected_tree=self.expected_tree,
                )
                != self.expected_fingerprint
            ):
                raise DirectoryPublishError(
                    "Published Windows payload fingerprint changed during caller verification"
                )
            self._require_published_binding(
                destination,
                context="post-body fingerprinted Windows publication",
            )
            self._require_payload_handles(context="post-body fingerprinted Windows publication")
            if _windows_tree_snapshot(destination) != self.expected_tree:
                raise DirectoryPublishError(
                    "Published Windows payload tree changed after caller verification"
                )
        except DirectoryPublishIndeterminateError:
            raise
        except BaseException as exc:
            raise DirectoryPublishIndeterminateError(
                "Windows directory publication outcome became indeterminate after "
                "caller verification; no rollback was attempted and evidence was "
                f"retained at {destination}: {exc}"
            ) from exc

    def close(self) -> None:
        primary = sys.exception()
        cleanup_error: DirectoryPublishError | None = None
        try:
            self._close_payload_handles(context="Windows publication payload handle cleanup")
        except DirectoryPublishError as exc:
            cleanup_error = exc
        source_handle = self.source_handle
        self.source_handle = None
        for handle, context in (
            (source_handle, "Windows publication source handle cleanup"),
            (self.parent_handle, "Windows publication parent handle cleanup"),
        ):
            if handle is None:
                continue
            if not self.close_handle(ctypes.c_void_p(handle)):
                error = ctypes.get_last_error()
                detail = f"{context} failed: {_windows_error_detail(error)}"
                if primary is not None:
                    primary.add_note(detail)
                elif cleanup_error is not None:
                    cleanup_error.add_note(detail)
                else:
                    cleanup_error = DirectoryPublishError(detail)
        if (
            primary is not None
            and (self.namespace_mutated or self.namespace_outcome_ambiguous)
            and not isinstance(primary, DirectoryPublishIndeterminateError)
        ):
            raise DirectoryPublishIndeterminateError(
                "Windows directory publication outcome is indeterminate after "
                "NtSetInformationFile and retained-handle cleanup"
            ) from primary
        if cleanup_error is not None:
            if self.namespace_mutated or self.namespace_outcome_ambiguous:
                raise DirectoryPublishIndeterminateError(
                    "Windows directory publication outcome is indeterminate after "
                    "NtSetInformationFile because retained-handle cleanup failed"
                ) from cleanup_error
            raise cleanup_error


_POSIX_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_POSIX_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


@dataclass
class RetainedDirectory:
    """One directory and its parent retained by identity-bound descriptors."""

    path: Path
    parent_fd: int
    fd: int
    parent_identity: DirectoryIdentity
    identity: DirectoryIdentity

    def require_binding(self) -> None:
        _require_expected_directory(
            path_file_stat(self.path.parent),
            self.parent_identity,
            context="retained directory parent path",
        )
        _require_expected_directory(
            descriptor_file_stat(self.parent_fd),
            self.parent_identity,
            context="retained directory parent",
        )
        try:
            named = os.stat(
                self.path.name,
                dir_fd=self.parent_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise DirectoryPublishError(
                f"Retained directory path binding changed: {self.path}: {exc}"
            ) from exc
        _require_expected_directory(
            named,
            self.identity,
            context="retained directory path",
        )
        _require_expected_directory(
            descriptor_file_stat(self.fd),
            self.identity,
            context="retained directory descriptor",
        )

    def close(self) -> None:
        _close_descriptors(
            (
                (self.fd, "retained directory descriptor cleanup"),
                (self.parent_fd, "retained directory parent cleanup"),
            )
        )


@dataclass
class DirectoryClaim:
    """An exclusively-created destination retained through its parent and own FD."""

    path: Path
    parent_fd: int
    fd: int
    parent_identity: DirectoryIdentity
    identity: DirectoryIdentity

    def require_binding(self) -> None:
        _require_expected_directory(
            path_file_stat(self.path.parent),
            self.parent_identity,
            context="claimed destination parent path",
        )
        _require_expected_directory(
            descriptor_file_stat(self.parent_fd),
            self.parent_identity,
            context="claimed destination parent",
        )
        try:
            named = os.stat(
                self.path.name,
                dir_fd=self.parent_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise DirectoryPublishError(
                f"Claimed destination path binding changed: {self.path}: {exc}"
            ) from exc
        _require_expected_directory(
            named,
            self.identity,
            context="claimed destination path",
        )
        _require_expected_directory(
            descriptor_file_stat(self.fd),
            self.identity,
            context="claimed destination descriptor",
        )

    def fsync(self) -> None:
        self.require_binding()
        try:
            os.fsync(self.fd)
            os.fsync(self.parent_fd)
        except OSError as exc:
            raise DirectoryPublishError(
                f"Could not durably flush claimed destination {self.path}: {exc}"
            ) from exc
        self.require_binding()

    def close(self) -> None:
        _close_descriptors(
            (
                (self.fd, "claimed destination descriptor cleanup"),
                (self.parent_fd, "claimed destination parent cleanup"),
            )
        )


def _validated_directory_state(path: Path, *, context: str) -> FileStat:
    try:
        info = path_file_stat(path)
    except OSError as exc:
        raise DirectoryPublishError(f"Could not inspect {context} {path}: {exc}") from exc
    if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise DirectoryPublishError(f"{context} must be a real directory: {path}")
    return info


def directory_identity(path: Path, *, context: str) -> DirectoryIdentity:
    return file_identity(_validated_directory_state(path, context=context))


def _require_expected_directory(
    info: FileStat,
    expected_identity: DirectoryIdentity,
    *,
    context: str,
) -> None:
    if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise DirectoryPublishError(f"{context} is no longer a real directory")
    if file_identity(info) != expected_identity:
        raise DirectoryPublishError(f"{context} identity changed unexpectedly")


def _windows_error_detail(error: int) -> str:
    formatter = getattr(ctypes, "FormatError", None)
    return formatter(error) if formatter is not None else f"Windows error {error}"


def _close_descriptors(descriptors: tuple[tuple[int, str], ...]) -> None:
    primary = sys.exception()
    cleanup_error: DirectoryPublishError | None = None
    cleanup_cause: OSError | None = None
    for descriptor, context in descriptors:
        try:
            os.close(descriptor)
        except OSError as exc:
            detail = f"{context} failed: {exc}"
            if primary is not None:
                primary.add_note(detail)
            elif cleanup_error is not None:
                cleanup_error.add_note(detail)
            else:
                cleanup_error = DirectoryPublishError(detail)
                cleanup_cause = exc
    if cleanup_error is not None:
        raise cleanup_error from cleanup_cause


def _close_descriptor(descriptor: int, *, context: str) -> None:
    _close_descriptors(((descriptor, context),))


def _posix_fsync_directory(
    path: Path,
    *,
    expected_identity: DirectoryIdentity,
) -> None:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not open directory for durable metadata flush {path}: {exc}"
        ) from exc
    try:
        _require_expected_directory(
            descriptor_file_stat(descriptor),
            expected_identity,
            context="directory opened for durable metadata flush",
        )
        os.fsync(descriptor)
        _require_expected_directory(
            descriptor_file_stat(descriptor),
            expected_identity,
            context="directory flushed for durable metadata",
        )
    except DirectoryPublishError:
        raise
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not durably flush directory metadata {path}: {exc}"
        ) from exc
    finally:
        _close_descriptor(
            descriptor,
            context="directory durability descriptor cleanup",
        )


def _windows_fsync_directory(
    path: Path,
    *,
    expected_identity: DirectoryIdentity,
) -> None:
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise DirectoryPublishError("Windows directory durability API is unavailable")
    kernel32 = win_dll("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        str(path),
        0x80000000 | 0x40000000,  # GENERIC_READ | GENERIC_WRITE
        0x00000001 | 0x00000002,  # share reads/writes, but never deletion
        None,
        3,  # OPEN_EXISTING
        0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle in {None, invalid_handle}:
        error = ctypes.get_last_error()
        raise DirectoryPublishError(
            "Could not open Windows directory for durable metadata flush "
            f"{path}: {_windows_error_detail(error)}"
        )

    handle_value = int(handle)
    flush_file_buffers = kernel32.FlushFileBuffers
    flush_file_buffers.argtypes = [ctypes.c_void_p]
    flush_file_buffers.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    try:
        _require_expected_directory(
            file_stat_module._windows_handle_stat(handle_value),  # noqa: SLF001
            expected_identity,
            context="Windows directory opened for durable metadata flush",
        )
        if not flush_file_buffers(ctypes.c_void_p(handle_value)):
            error = ctypes.get_last_error()
            raise DirectoryPublishError(
                "Windows filesystem could not durably flush directory metadata "
                f"{path}: {_windows_error_detail(error)}"
            )
        _require_expected_directory(
            file_stat_module._windows_handle_stat(handle_value),  # noqa: SLF001
            expected_identity,
            context="Windows directory flushed for durable metadata",
        )
    except DirectoryPublishError:
        raise
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not validate Windows directory durability handle {path}: {exc}"
        ) from exc
    finally:
        primary = sys.exception()
        if not close_handle(ctypes.c_void_p(handle_value)):
            error = ctypes.get_last_error()
            detail = (
                "Windows directory durability handle cleanup failed: "
                f"{_windows_error_detail(error)}"
            )
            if primary is not None:
                primary.add_note(detail)
            else:
                raise DirectoryPublishError(detail)


def fsync_directory(path: Path, *, context: str) -> None:
    """Durably flush one unchanged real directory on a supported platform."""

    expected_identity = directory_identity(path, context=context)
    if sys.platform.startswith("linux") and os.name == "posix":
        _posix_fsync_directory(path, expected_identity=expected_identity)
    elif os.name == "nt":
        _windows_fsync_directory(path, expected_identity=expected_identity)
    else:
        raise DirectoryPublishError(
            "Durable directory metadata flush is supported only on Linux and Windows"
        )
    if directory_identity(path, context=context) != expected_identity:
        raise DirectoryPublishError(f"{context} identity changed after durable metadata flush")


def _linux_rename_retained_noreplace(
    retained: RetainedDirectory,
    destination: Path,
) -> DirectoryIdentity:
    """Rename one validated stage name with Linux RENAME_NOREPLACE.

    Linux does not provide a source-FD-bound rename primitive here. The retained
    stage descriptor is post-mutation evidence; the source name remains subject
    to a final pathname race that is detected, never described as prevented.
    """

    if retained.path.parent != destination.parent:
        raise DirectoryPublishError("Linux directory publication must stay within one parent")
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (AttributeError, OSError) as exc:
        raise DirectoryPublishError("Linux RENAME_NOREPLACE publication is unavailable") from exc
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int

    retained.require_binding()
    try:
        os.stat(
            destination.name,
            dir_fd=retained.parent_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not inspect publication destination {destination}: {exc}"
        ) from exc
    else:
        raise FileExistsError(errno.EEXIST, "destination already exists", destination)

    ctypes.set_errno(0)
    try:
        rename_result = int(
            renameat2(
                retained.parent_fd,
                os.fsencode(retained.path.name),
                retained.parent_fd,
                os.fsencode(destination.name),
                1,  # RENAME_NOREPLACE
            )
        )
    except BaseException as exc:
        raise DirectoryPublishIndeterminateError(
            "Linux directory publication outcome is indeterminate because "
            "renameat2 raised after the RENAME_NOREPLACE attempt"
        ) from exc
    if rename_result != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError(error, "destination already exists", destination)
        if error in {
            errno.ENOSYS,
            errno.EINVAL,
            getattr(errno, "ENOTSUP", errno.EINVAL),
            getattr(errno, "EOPNOTSUPP", errno.EINVAL),
        }:
            raise DirectoryPublishError("Linux RENAME_NOREPLACE publication is unavailable")
        raise DirectoryPublishError(
            error,
            f"Could not publish retained Linux directory: {os.strerror(error)}",
            destination,
        )

    def require_published_state(*, context: str) -> None:
        _require_expected_directory(
            descriptor_file_stat(retained.fd),
            retained.identity,
            context=f"{context} stage descriptor",
        )
        _require_expected_directory(
            descriptor_file_stat(retained.parent_fd),
            retained.parent_identity,
            context=f"{context} parent descriptor",
        )
        _require_expected_directory(
            path_file_stat(destination.parent),
            retained.parent_identity,
            context=f"{context} lexical parent",
        )
        _require_expected_directory(
            os.stat(
                destination.name,
                dir_fd=retained.parent_fd,
                follow_symlinks=False,
            ),
            retained.identity,
            context=f"{context} destination through retained parent",
        )
        _require_expected_directory(
            path_file_stat(destination),
            retained.identity,
            context=f"{context} logical destination",
        )
        try:
            os.stat(
                retained.path.name,
                dir_fd=retained.parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise DirectoryPublishError(f"{context} retained both stage and destination names")
        try:
            path_file_stat(retained.path)
        except FileNotFoundError:
            pass
        else:
            raise DirectoryPublishError(f"{context} retained the logical stage name")

    try:
        require_published_state(context="published Linux")
        os.fsync(retained.fd)
        os.fsync(retained.parent_fd)
        require_published_state(context="durably published Linux")
    except BaseException as exc:
        raise DirectoryPublishIndeterminateError(
            "Linux directory publication outcome is indeterminate after "
            f"RENAME_NOREPLACE; evidence retained at {retained.path} and {destination}: {exc}"
        ) from exc
    return retained.identity


def _posix_mkdir_noreplace(parent_fd: int, name: str, mode: int) -> None:
    os.mkdir(name, mode=mode, dir_fd=parent_fd)


@contextmanager
def open_expected_directory(
    source: Path,
    expected_identity: DirectoryIdentity,
) -> Iterator[RetainedDirectory]:
    """Open one expected Linux directory without trusting its name afterward."""

    if (
        not sys.platform.startswith("linux")
        or os.name != "posix"
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
    ):
        raise DirectoryPublishError(
            "Identity-bound retained directory access is unavailable on this platform"
        )
    parent_fd: int | None = None
    source_fd: int | None = None
    try:
        try:
            parent_fd = os.open(source.parent, _POSIX_DIRECTORY_FLAGS)
        except OSError as exc:
            raise DirectoryPublishError(
                f"Could not retain publication source parent {source.parent}: {exc}"
            ) from exc
        try:
            parent_info = descriptor_file_stat(parent_fd)
            if is_link_or_reparse(parent_info) or not stat.S_ISDIR(parent_info.st_mode):
                raise DirectoryPublishError("Publication source parent is not a real directory")
            parent_identity = file_identity(parent_info)
            source_fd = os.open(source.name, _POSIX_DIRECTORY_FLAGS, dir_fd=parent_fd)
            _require_expected_directory(
                descriptor_file_stat(source_fd),
                expected_identity,
                context="retained publication source",
            )
            retained = RetainedDirectory(
                path=source,
                parent_fd=parent_fd,
                fd=source_fd,
                parent_identity=parent_identity,
                identity=expected_identity,
            )
            retained.require_binding()
        except DirectoryPublishError:
            raise
        except OSError as exc:
            raise DirectoryPublishError(
                f"Could not retain publication source {source}: {exc}"
            ) from exc
        yield retained
    finally:
        descriptors: list[tuple[int, str]] = []
        if source_fd is not None:
            descriptors.append((source_fd, "retained publication source cleanup"))
        if parent_fd is not None:
            descriptors.append((parent_fd, "retained publication source parent cleanup"))
        _close_descriptors(tuple(descriptors))


@contextmanager
def claim_directory_noreplace(
    destination: Path,
    *,
    expected_parent_identity: DirectoryIdentity | None = None,
) -> Iterator[DirectoryClaim]:
    """Exclusively create and retain one Linux destination directory."""

    if (
        not sys.platform.startswith("linux")
        or os.name != "posix"
        or os.mkdir not in os.supports_dir_fd
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
    ):
        raise DirectoryPublishError(
            "Identity-bound exclusive destination claims are unavailable on this platform"
        )
    if expected_parent_identity is None:
        expected_parent_identity = directory_identity(
            destination.parent,
            context="publication destination parent",
        )
    parent_fd: int | None = None
    destination_fd: int | None = None
    try:
        try:
            parent_fd = os.open(destination.parent, _POSIX_DIRECTORY_FLAGS)
        except OSError as exc:
            raise DirectoryPublishError(
                f"Could not retain publication destination parent {destination.parent}: {exc}"
            ) from exc
        try:
            parent_info = descriptor_file_stat(parent_fd)
            if is_link_or_reparse(parent_info) or not stat.S_ISDIR(parent_info.st_mode):
                raise DirectoryPublishError(
                    "Publication destination parent is not a real directory"
                )
            parent_identity = file_identity(parent_info)
            if parent_identity != expected_parent_identity:
                raise DirectoryPublishError("Publication destination parent identity changed")
            try:
                _posix_mkdir_noreplace(parent_fd, destination.name, 0o700)
            except FileExistsError as exc:
                raise FileExistsError(
                    errno.EEXIST,
                    "destination already exists",
                    destination,
                ) from exc
            destination_fd = os.open(
                destination.name,
                _POSIX_DIRECTORY_FLAGS,
                dir_fd=parent_fd,
            )
            destination_info = descriptor_file_stat(destination_fd)
            if is_link_or_reparse(destination_info) or not stat.S_ISDIR(destination_info.st_mode):
                raise DirectoryPublishError("Claimed destination is not a real directory")
            claim = DirectoryClaim(
                path=destination,
                parent_fd=parent_fd,
                fd=destination_fd,
                parent_identity=parent_identity,
                identity=file_identity(destination_info),
            )
            claim.require_binding()
        except (DirectoryPublishError, FileExistsError):
            raise
        except OSError as exc:
            raise DirectoryPublishError(
                f"Could not claim publication destination {destination}: {exc}"
            ) from exc
        yield claim
    finally:
        descriptors: list[tuple[int, str]] = []
        if destination_fd is not None:
            descriptors.append((destination_fd, "claimed publication destination cleanup"))
        if parent_fd is not None:
            descriptors.append((parent_fd, "claimed publication destination parent cleanup"))
        _close_descriptors(tuple(descriptors))


def _stable_source_state(info: FileStat) -> tuple[int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _copy_regular_file_noreplace(
    source_fd: int,
    destination_fd: int,
    name: str,
    before: FileStat,
) -> None:
    source_file: int | None = None
    destination_file: int | None = None
    try:
        source_file = os.open(name, _POSIX_FILE_FLAGS, dir_fd=source_fd)
        opened = descriptor_file_stat(source_file)
        if (
            is_link_or_reparse(opened)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or file_identity(opened) != file_identity(before)
        ):
            raise DirectoryPublishError(f"Publication source file identity changed: {name!r}")
        destination_file = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            stat.S_IMODE(opened.st_mode),
            dir_fd=destination_fd,
        )
        while True:
            chunk = os.read(source_file, 1024 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(destination_file, view)
                if written <= 0:
                    raise DirectoryPublishError(f"Could not copy publication source file: {name!r}")
                view = view[written:]
        os.fchmod(destination_file, stat.S_IMODE(opened.st_mode))
        os.fsync(destination_file)
        after = descriptor_file_stat(source_file)
        named_after = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        if _stable_source_state(opened) != _stable_source_state(after) or _stable_source_state(
            opened
        ) != _stable_source_state(named_after):
            raise DirectoryPublishError(f"Publication source file changed while copying: {name!r}")
        copied = descriptor_file_stat(destination_file)
        named_copied = os.stat(
            name,
            dir_fd=destination_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(copied.st_mode)
            or copied.st_nlink != 1
            or copied.st_size != opened.st_size
            or stat.S_IMODE(copied.st_mode) != stat.S_IMODE(opened.st_mode)
            or file_identity(named_copied) != file_identity(copied)
            or _stable_source_state(named_copied) != _stable_source_state(copied)
        ):
            raise DirectoryPublishError(
                f"Published file does not match its source metadata: {name!r}"
            )
    except DirectoryPublishError:
        raise
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not copy publication source file {name!r}: {exc}"
        ) from exc
    finally:
        descriptors: list[tuple[int, str]] = []
        if destination_file is not None:
            descriptors.append((destination_file, "published file descriptor cleanup"))
        if source_file is not None:
            descriptors.append((source_file, "publication source file cleanup"))
        _close_descriptors(tuple(descriptors))


def _copy_retained_tree(source_fd: int, destination_fd: int) -> None:
    try:
        with os.scandir(source_fd) as entries:
            names = sorted(entry.name for entry in entries)
    except OSError as exc:
        raise DirectoryPublishError(f"Could not enumerate publication source: {exc}") from exc

    for name in names:
        try:
            before = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        except OSError as exc:
            raise DirectoryPublishError(
                f"Could not inspect publication source entry {name!r}: {exc}"
            ) from exc
        if stat.S_ISLNK(before.st_mode):
            raise DirectoryPublishError(f"Publication source contains a symbolic link: {name!r}")
        if stat.S_ISREG(before.st_mode):
            if before.st_nlink != 1:
                raise DirectoryPublishError(
                    f"Publication source contains a hard-linked file: {name!r}"
                )
            _copy_regular_file_noreplace(
                source_fd,
                destination_fd,
                name,
                before,
            )
            continue
        if not stat.S_ISDIR(before.st_mode):
            raise DirectoryPublishError(f"Publication source contains a special file: {name!r}")

        source_child: int | None = None
        destination_child: int | None = None
        try:
            source_child = os.open(
                name,
                _POSIX_DIRECTORY_FLAGS,
                dir_fd=source_fd,
            )
            opened = descriptor_file_stat(source_child)
            _require_expected_directory(
                opened,
                file_identity(before),
                context=f"publication source directory {name!r}",
            )
            os.mkdir(
                name,
                mode=0o700,
                dir_fd=destination_fd,
            )
            destination_child = os.open(
                name,
                _POSIX_DIRECTORY_FLAGS,
                dir_fd=destination_fd,
            )
            _copy_retained_tree(source_child, destination_child)
            os.fchmod(destination_child, stat.S_IMODE(opened.st_mode))
            os.fsync(destination_child)
            copied_child = descriptor_file_stat(destination_child)
            named_copied_child = os.stat(
                name,
                dir_fd=destination_fd,
                follow_symlinks=False,
            )
            if file_identity(named_copied_child) != file_identity(
                copied_child
            ) or _stable_source_state(named_copied_child) != _stable_source_state(copied_child):
                raise DirectoryPublishError(
                    f"Published directory identity changed while copying: {name!r}"
                )
            after = descriptor_file_stat(source_child)
            named_after = os.stat(
                name,
                dir_fd=source_fd,
                follow_symlinks=False,
            )
            if _stable_source_state(opened) != _stable_source_state(after) or _stable_source_state(
                opened
            ) != _stable_source_state(named_after):
                raise DirectoryPublishError(
                    f"Publication source directory changed while copying: {name!r}"
                )
        except DirectoryPublishError:
            raise
        except OSError as exc:
            raise DirectoryPublishError(
                f"Could not copy publication source directory {name!r}: {exc}"
            ) from exc
        finally:
            descriptors = []
            if destination_child is not None:
                descriptors.append((destination_child, "published directory descriptor cleanup"))
            if source_child is not None:
                descriptors.append((source_child, "publication source directory cleanup"))
            _close_descriptors(tuple(descriptors))


def copy_retained_tree_noreplace(
    source: RetainedDirectory,
    destination: DirectoryClaim,
) -> DirectoryIdentity:
    """Copy an immutable retained tree into an empty claimed destination."""

    destination.require_binding()
    source_info = descriptor_file_stat(source.fd)
    _copy_retained_tree(source.fd, destination.fd)
    os.fchmod(destination.fd, stat.S_IMODE(source_info.st_mode))
    try:
        os.fsync(destination.fd)
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not durably flush published directory {destination.path}: {exc}"
        ) from exc
    if _stable_source_state(source_info) != _stable_source_state(descriptor_file_stat(source.fd)):
        raise DirectoryPublishError("Publication source directory changed while copying")
    destination.fsync()
    return destination.identity


def _windows_tree_state(info: FileStat) -> _WindowsTreeState:
    return (
        file_identity(info),
        stat.S_IFMT(info.st_mode),
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
    )


def _windows_tree_snapshot(root: Path) -> dict[str, _WindowsTreeState]:
    def fail_walk(error: OSError) -> None:
        raise DirectoryPublishError(
            f"Could not inspect Windows publication payload tree: {error}"
        ) from error

    result: dict[str, _WindowsTreeState] = {}
    root_type = type(root)
    for current, directory_names, file_names in os.walk(
        root,
        topdown=True,
        onerror=fail_walk,
        followlinks=False,
    ):
        directory_names.sort()
        file_names.sort()
        current_path = root_type(current)
        for name, directory in (
            *((name, True) for name in directory_names),
            *((name, False) for name in file_names),
        ):
            path = current_path / name
            info = path_file_stat(path)
            expected_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
            if (
                is_link_or_reparse(info)
                or not expected_type
                or (not directory and info.st_nlink != 1)
            ):
                raise DirectoryPublishError(f"Windows publication payload is unsafe: {path}")
            result[path.relative_to(root).as_posix()] = _windows_tree_state(info)
    return result


def _windows_tree_fingerprint(
    root: Path,
    *,
    expected_root_state: _WindowsTreeState,
    expected_tree: dict[str, _WindowsTreeState],
) -> _WindowsTreeFingerprint:
    """Hash one portable tree's metadata and ordinary default-stream bytes.

    Windows alternate data streams are outside this publication contract. All
    runtime-referenced paths remain subject to their existing portable-path
    validation, including rejection of colon-bearing path components.
    """

    root_info = path_file_stat(root)
    if (
        is_link_or_reparse(root_info)
        or not stat.S_ISDIR(root_info.st_mode)
        or _windows_tree_state(root_info) != expected_root_state
    ):
        raise DirectoryPublishError(
            "Windows publication root metadata changed during fingerprinting"
        )
    if _windows_tree_snapshot(root) != expected_tree:
        raise DirectoryPublishError(
            "Windows publication payload tree changed before fingerprinting"
        )

    digest = hashlib.sha256()

    def add_frame(payload: bytes) -> None:
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)

    digest.update(b"worldforge.windows-default-stream-tree.v1\0")
    records = (("", expected_root_state), *sorted(expected_tree.items()))
    for relative, expected in records:
        metadata = "\0".join(
            (
                relative,
                str(expected[0][0]),
                str(expected[0][1]),
                str(expected[1]),
                str(expected[2]),
                str(expected[3]),
                str(expected[4]),
            )
        ).encode("utf-8")
        add_frame(metadata)
        if relative and expected[1] == stat.S_IFREG:
            path = root / PurePath(*relative.split("/"))
            file_digest = hashlib.sha256()
            bytes_read = 0
            try:
                with path.open("rb") as stream:
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        file_digest.update(chunk)
                        bytes_read += len(chunk)
            except OSError as exc:
                raise DirectoryPublishError(
                    f"Could not fingerprint Windows publication payload {relative}: {exc}"
                ) from exc
            if bytes_read != expected[3]:
                raise DirectoryPublishError(
                    f"Windows publication payload size changed during fingerprinting: {relative}"
                )
            add_frame(file_digest.digest())

    if _windows_tree_state(path_file_stat(root)) != expected_root_state:
        raise DirectoryPublishError(
            "Windows publication root metadata changed during fingerprinting"
        )
    if _windows_tree_snapshot(root) != expected_tree:
        raise DirectoryPublishError(
            "Windows publication payload tree changed during fingerprinting"
        )
    return digest.hexdigest()


@contextmanager
def _open_windows_retained_tree(
    source: Path,
    *,
    source_identity: DirectoryIdentity,
    parent_identity: DirectoryIdentity,
    delete_source: bool,
) -> Iterator[_WindowsRetainedTree]:
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise DirectoryPublishError(
            "Identity-bound Windows directory access is unavailable on this system"
        )
    kernel32 = win_dll("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    flush_file_buffers = kernel32.FlushFileBuffers
    flush_file_buffers.argtypes = [ctypes.c_void_p]
    flush_file_buffers.restype = ctypes.c_int
    try:
        ntdll = win_dll("ntdll", use_last_error=True)
        nt_set_information = ntdll.NtSetInformationFile
        nt_status_to_dos_error = ntdll.RtlNtStatusToDosError
    except (AttributeError, OSError) as exc:
        raise DirectoryPublishError(
            "Identity-bound Windows directory rename API is unavailable"
        ) from exc
    nt_set_information.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_IoStatusBlock),
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
    ]
    nt_set_information.restype = ctypes.c_int32
    nt_status_to_dos_error.argtypes = [ctypes.c_int32]
    nt_status_to_dos_error.restype = ctypes.c_uint32
    invalid_handle = ctypes.c_void_p(-1).value

    def open_source_directory(path: Path, *, delete: bool) -> int:
        access = 0x80000000 | 0x40000000 | 0x00100000
        if delete:
            access |= 0x00010000
        handle = create_file(
            str(path),
            access,
            0x00000001,  # FILE_SHARE_READ
            None,
            3,
            0x02000000 | 0x00200000,
            None,
        )
        if handle in {None, invalid_handle}:
            error = ctypes.get_last_error()
            raise DirectoryPublishError(
                f"Could not retain Windows publication directory {path}: "
                f"{_windows_error_detail(error)}"
            )
        return int(handle)

    def open_parent_identity(path: Path) -> int:
        handle = create_file(
            str(path),
            # FILE_LIST_DIRECTORY | FILE_TRAVERSE | FILE_READ_ATTRIBUTES |
            # SYNCHRONIZE. The long-lived parent handle supplies the
            # identity-bound root for the native relative rename.
            0x00000001 | 0x00000020 | 0x00000080 | 0x00100000,
            # The short-lived durability handle must coexist with this
            # identity pin, while omitted delete sharing retains the parent
            # namespace through publication.
            0x00000001 | 0x00000002,
            None,
            3,
            0x02000000 | 0x00200000,
            None,
        )
        if handle in {None, invalid_handle}:
            error = ctypes.get_last_error()
            raise DirectoryPublishError(
                f"Could not retain Windows publication parent {path}: "
                f"{_windows_error_detail(error)}"
            )
        return int(handle)

    def flush_parent(
        path: Path,
        expected_identity: DirectoryIdentity,
        context: str,
    ) -> None:
        handle = create_file(
            str(path),
            # FlushFileBuffers requires write access. Keep this handle
            # short-lived and close it before the native relative rename.
            0x40000000 | 0x00000080 | 0x00100000,
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            3,
            0x02000000 | 0x00200000,
            None,
        )
        if handle in {None, invalid_handle}:
            error = ctypes.get_last_error()
            raise DirectoryPublishError(
                f"Could not open {context} for durable metadata flush: "
                f"{_windows_error_detail(error)}"
            )
        handle_value = int(handle)
        primary: BaseException | None = None
        try:
            _require_expected_directory(
                file_stat_module._windows_handle_stat(handle_value),  # noqa: SLF001
                expected_identity,
                context=f"{context} opened for durable metadata flush",
            )
            if not flush_file_buffers(ctypes.c_void_p(handle_value)):
                error = ctypes.get_last_error()
                raise DirectoryPublishError(
                    f"Could not durably flush {context}: {_windows_error_detail(error)}"
                )
            _require_expected_directory(
                file_stat_module._windows_handle_stat(handle_value),  # noqa: SLF001
                expected_identity,
                context=f"{context} durably flushed",
            )
        except BaseException as exc:
            primary = exc
            raise
        finally:
            if not close_handle(ctypes.c_void_p(handle_value)):
                error = ctypes.get_last_error()
                detail = (
                    f"{context} durability handle cleanup failed: {_windows_error_detail(error)}"
                )
                if primary is not None:
                    primary.add_note(detail)
                else:
                    raise DirectoryPublishError(detail)

    def open_tree_entry(path: Path, directory: bool, writable: bool) -> int:
        flags = 0x00200000
        if directory:
            flags |= 0x02000000
        access = (
            0x80000000 | 0x40000000 | 0x00100000
            if writable
            else 0x00000001 | 0x00000080 | 0x00100000
        )
        handle = create_file(
            str(path),
            access,
            # Descendant handles intentionally deny write and delete sharing.
            # Windows cannot rename a directory with open descendant handles,
            # so the pre-rename set is closed only after its durable
            # fingerprint is complete and a new seal set is acquired
            # immediately after the root-handle-bound rename.
            0x00000001,
            None,
            3,
            flags,
            None,
        )
        if handle in {None, invalid_handle}:
            error = ctypes.get_last_error()
            raise DirectoryPublishError(
                f"Could not retain Windows publication payload {path}: "
                f"{_windows_error_detail(error)}"
            )
        return int(handle)

    def open_published_root(path: Path) -> int:
        handle = create_file(
            str(path),
            # FILE_LIST_DIRECTORY | FILE_TRAVERSE | FILE_READ_ATTRIBUTES |
            # SYNCHRONIZE. The rename handle has already been durably flushed;
            # caller verification receives only this least-privilege root seal.
            0x00000001 | 0x00000020 | 0x00000080 | 0x00100000,
            # Permit read/write consumers while continuing to deny delete
            # sharing so the published root cannot be replaced.
            0x00000001 | 0x00000002,
            None,
            3,
            0x02000000 | 0x00200000,
            None,
        )
        if handle in {None, invalid_handle}:
            error = ctypes.get_last_error()
            raise DirectoryPublishError(
                f"Could not retain published Windows root {path}: {_windows_error_detail(error)}"
            )
        return int(handle)

    source_handle: int | None = None
    parent_handle: int | None = None
    payload_handles: list[_WindowsPayloadHandle] = []
    retained: _WindowsRetainedTree | None = None
    try:
        source_handle = open_source_directory(
            source,
            delete=delete_source,
        )
        _require_expected_directory(
            file_stat_module._windows_handle_stat(source_handle),  # noqa: SLF001
            source_identity,
            context="retained Windows publication source",
        )
        parent_handle = open_parent_identity(source.parent)
        _require_expected_directory(
            file_stat_module._windows_handle_stat(parent_handle),  # noqa: SLF001
            parent_identity,
            context="retained Windows publication parent",
        )
        expected_root_state = _windows_tree_state(path_file_stat(source))
        if expected_root_state[0] != source_identity:
            raise DirectoryPublishError(
                "Windows publication source identity changed before retention"
            )
        expected_tree = _windows_tree_snapshot(source)
        for relative, expected in sorted(expected_tree.items()):
            payload_path = source / PurePath(*relative.split("/"))
            directory = expected[1] == stat.S_IFDIR
            handle = open_tree_entry(payload_path, directory, True)
            payload_handles.append(
                _WindowsPayloadHandle(
                    relative=relative,
                    handle=handle,
                    directory=directory,
                    expected=expected,
                )
            )
            opened = file_stat_module._windows_handle_stat(handle)  # noqa: SLF001
            if (
                is_link_or_reparse(opened)
                or file_identity(opened) != expected[0]
                or stat.S_IFMT(opened.st_mode) != expected[1]
                or opened.st_nlink != expected[2]
                or opened.st_size != expected[3]
                or opened.st_mtime_ns != expected[4]
            ):
                raise DirectoryPublishError(
                    f"Windows publication payload identity changed: {relative}"
                )
        if _windows_tree_snapshot(source) != expected_tree:
            raise DirectoryPublishError(
                "Windows publication payload tree changed while retaining handles"
            )
        retained = _WindowsRetainedTree(
            source=source,
            source_identity=source_identity,
            parent_identity=parent_identity,
            source_handle=source_handle,
            parent_handle=parent_handle,
            payload_handles=payload_handles,
            expected_tree=expected_tree,
            expected_root_state=expected_root_state,
            expected_fingerprint=None,
            open_tree_entry=open_tree_entry,
            open_published_root=open_published_root,
            flush_file_buffers=flush_file_buffers,
            close_handle=close_handle,
            nt_set_information=nt_set_information,
            nt_status_to_dos_error=nt_status_to_dos_error,
            flush_parent=flush_parent,
        )
        yield retained
    finally:
        if retained is not None:
            retained.close()
        else:
            primary = sys.exception()
            cleanup_error: DirectoryPublishError | None = None
            handles = (
                *(
                    (
                        item.handle,
                        f"Windows publication payload handle cleanup for {item.relative}",
                    )
                    for item in reversed(payload_handles)
                ),
                (source_handle, "Windows publication source handle cleanup"),
                (parent_handle, "Windows publication parent handle cleanup"),
            )
            for handle, context in handles:
                if handle is None:
                    continue
                if not close_handle(ctypes.c_void_p(handle)):
                    error = ctypes.get_last_error()
                    detail = f"{context} failed: {_windows_error_detail(error)}"
                    if primary is not None:
                        primary.add_note(detail)
                    elif cleanup_error is not None:
                        cleanup_error.add_note(detail)
                    else:
                        cleanup_error = DirectoryPublishError(detail)
            if cleanup_error is not None:
                raise cleanup_error


def flush_windows_directory_tree(
    source: Path,
    *,
    expected_source_identity: DirectoryIdentity,
) -> tuple[str, ...]:
    """Durably flush one exact Windows payload tree while retaining every handle."""

    if os.name != "nt":
        raise DirectoryPublishError(
            "Identity-bound Windows directory durability is unavailable on this platform"
        )
    parent_identity = directory_identity(
        source.parent,
        context="Windows publication stage parent",
    )
    with _open_windows_retained_tree(
        source,
        source_identity=expected_source_identity,
        parent_identity=parent_identity,
        delete_source=False,
    ) as retained:
        return retained.flush_payload_tree()


@contextmanager
def _windows_rename_noreplace(
    source: Path,
    destination: Path,
    *,
    source_identity: DirectoryIdentity,
    parent_identity: DirectoryIdentity,
) -> Iterator[DirectoryIdentity]:
    with _open_windows_retained_tree(
        source,
        source_identity=source_identity,
        parent_identity=parent_identity,
        delete_source=True,
    ) as retained:
        published_identity = retained.rename_noreplace(destination)
        yield published_identity
        retained.require_post_body_unchanged(destination)


@contextmanager
def publish_directory_noreplace(
    source: Path,
    destination: Path,
    *,
    expected_source_identity: DirectoryIdentity,
    expected_parent_identity: DirectoryIdentity | None = None,
) -> Iterator[DirectoryIdentity]:
    """Publish and retain one directory through immediate caller verification."""

    source_identity = expected_source_identity

    if sys.platform.startswith("linux") and os.name == "posix":
        namespace_mutated = False
        try:
            with open_expected_directory(source, source_identity) as retained:
                parent_identity = retained.parent_identity
                if (
                    expected_parent_identity is not None
                    and parent_identity != expected_parent_identity
                ):
                    raise DirectoryPublishError(
                        "Publication parent differs from the expected identity"
                    )
                published_identity = _linux_rename_retained_noreplace(
                    retained,
                    destination,
                )
                namespace_mutated = True
                if published_identity != source_identity:
                    raise DirectoryPublishError("Published directory identity changed unexpectedly")
                if (
                    directory_identity(destination.parent, context="publication parent")
                    != parent_identity
                ):
                    raise DirectoryPublishError("Publication parent identity changed unexpectedly")
                if (
                    directory_identity(destination, context="published directory")
                    != source_identity
                ):
                    raise DirectoryPublishError("Published directory identity changed unexpectedly")
                yield published_identity
        except DirectoryPublishIndeterminateError:
            raise
        except BaseException as exc:
            if namespace_mutated:
                raise DirectoryPublishIndeterminateError(
                    "Linux directory publication outcome became indeterminate after "
                    f"RENAME_NOREPLACE; evidence retained at {source} and "
                    f"{destination}: {exc}"
                ) from exc
            raise
        return
    if os.name == "nt":
        if source.parent != destination.parent:
            raise DirectoryPublishError("Windows directory publication must stay within one parent")
        parent_identity = directory_identity(source.parent, context="publication parent")
        if expected_parent_identity is not None and parent_identity != expected_parent_identity:
            raise DirectoryPublishError("Publication parent differs from the expected identity")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(errno.EEXIST, "destination already exists", destination)
        with _windows_rename_noreplace(
            source,
            destination,
            source_identity=source_identity,
            parent_identity=parent_identity,
        ) as published_identity:
            try:
                if published_identity != source_identity:
                    raise DirectoryPublishError("Published directory identity changed unexpectedly")
                if (
                    directory_identity(destination.parent, context="publication parent")
                    != parent_identity
                ):
                    raise DirectoryPublishError("Publication parent identity changed unexpectedly")
                if (
                    directory_identity(destination, context="published directory")
                    != source_identity
                ):
                    raise DirectoryPublishError("Published directory identity changed unexpectedly")
                yield published_identity
            except DirectoryPublishIndeterminateError:
                raise
            except BaseException as exc:
                raise DirectoryPublishIndeterminateError(
                    "Windows directory publication outcome became indeterminate after "
                    "NtSetInformationFile; no rollback was attempted for "
                    f"{source} and {destination}: {exc}"
                ) from exc
        return
    raise DirectoryPublishError(
        "Safe exclusive directory publication is supported only on Linux and Windows"
    )


def _posix_unlink_descriptor_raw(descriptor: int, *, directory: bool) -> int | None:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        unlinkat = libc.unlinkat
    except (AttributeError, OSError) as exc:
        raise DirectoryPublishError(
            "Identity-bound POSIX descriptor deletion is unavailable"
        ) from exc
    unlinkat.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    unlinkat.restype = ctypes.c_int
    flags = _AT_EMPTY_PATH | (_AT_REMOVEDIR if directory else 0)
    ctypes.set_errno(0)
    if unlinkat(descriptor, b"", flags) == 0:
        return None
    return ctypes.get_errno()


def _posix_descriptor_deletion_unavailable(error: int) -> bool:
    return error in {
        errno.EINVAL,
        errno.ENOENT,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }


def _raise_posix_descriptor_deletion_error(error: int) -> None:
    if _posix_descriptor_deletion_unavailable(error):
        raise DirectoryPublishError("Identity-bound POSIX descriptor deletion is unavailable")
    detail = os.strerror(error) if error else "unknown POSIX deletion error"
    raise DirectoryPublishError(f"Could not delete the retained POSIX cleanup object: {detail}")


def _verify_posix_descriptor_deleted(descriptor: int) -> None:
    try:
        remaining_links = descriptor_file_stat(descriptor).st_nlink
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not verify retained POSIX cleanup object deletion: {exc}"
        ) from exc
    if remaining_links != 0:
        raise DirectoryPublishError(
            "Identity-bound POSIX descriptor deletion did not remove the retained object"
        )


def _posix_unlink_descriptor(descriptor: int, *, directory: bool) -> None:
    """Delete exactly one open object, or fail without touching its pathname."""

    error = _posix_unlink_descriptor_raw(descriptor, directory=directory)
    if error is not None:
        _raise_posix_descriptor_deletion_error(error)
    _verify_posix_descriptor_deleted(descriptor)


def _posix_preflight_directory_deletion(
    descriptor: int,
    *,
    recursive: bool,
) -> bool:
    """Prove retained-FD deletion support before recursively mutating a tree."""

    directory_error = _posix_unlink_descriptor_raw(descriptor, directory=True)
    if directory_error is None:
        _verify_posix_descriptor_deleted(descriptor)
        return True
    if _posix_descriptor_deletion_unavailable(directory_error):
        _raise_posix_descriptor_deletion_error(directory_error)
    if directory_error not in {errno.ENOTEMPTY, errno.EEXIST}:
        _raise_posix_descriptor_deletion_error(directory_error)
    if recursive:
        raise DirectoryPublishError(
            "Refusing to mutate a non-empty retained directory without a "
            "pre-recorded retained child snapshot"
        )
    raise DirectoryPublishError("Claimed empty directory is no longer empty")


def _posix_remove_retained_directory(
    parent: Path,
    claim_name: str,
    expected_identity: DirectoryIdentity,
    *,
    recursive: bool,
    verify: Callable[[Path], None] | None = None,
    verify_retained: RetainedDirectoryVerifier | None = None,
    retained_parent_fd: int | None = None,
) -> None:
    """Fail closed because Linux cannot unlink an exact open directory identity."""

    required_dir_fd = (os.open, os.stat)
    if (
        not sys.platform.startswith("linux")
        or os.name != "posix"
        or any(operation not in os.supports_dir_fd for operation in required_dir_fd)
        or os.scandir not in os.supports_fd
    ):
        raise DirectoryPublishError("Identity-bound POSIX directory removal is unavailable")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        parent_descriptor = (
            os.open(parent, flags) if retained_parent_fd is None else os.dup(retained_parent_fd)
        )
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not open claimed directory parent {parent}: {exc}"
        ) from exc
    claim_descriptor: int | None = None
    try:
        parent_info = descriptor_file_stat(parent_descriptor)
        if is_link_or_reparse(parent_info) or not stat.S_ISDIR(parent_info.st_mode):
            raise DirectoryPublishError("Claimed directory parent is not a real directory")
        claim_descriptor = os.open(
            claim_name,
            flags,
            dir_fd=parent_descriptor,
        )
        opened = descriptor_file_stat(claim_descriptor)
        named = os.stat(
            claim_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        _require_expected_directory(
            opened,
            expected_identity,
            context="claimed directory",
        )
        _require_expected_directory(
            named,
            expected_identity,
            context="named claimed directory",
        )
        if file_identity(opened) != file_identity(named):
            raise DirectoryPublishError("Claimed directory binding changed")
        _ = recursive, verify, verify_retained
        raise DirectoryPublishRecoveryRequiredError(
            "Exact identity-bound POSIX directory deletion is unavailable; "
            "the retained owned tree was not mutated"
        )
    except DirectoryPublishError:
        raise
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not retain claimed directory {parent / claim_name}: {exc}"
        ) from exc
    finally:
        descriptors = (
            (
                (claim_descriptor, "claimed directory descriptor cleanup"),
                (
                    parent_descriptor,
                    "claimed directory parent descriptor cleanup",
                ),
            )
            if claim_descriptor is not None
            else (
                (
                    parent_descriptor,
                    "claimed directory parent descriptor cleanup",
                ),
            )
        )
        _close_descriptors(descriptors)


def _windows_open_delete_handle(
    path: Path,
    expected_identity: DirectoryIdentity,
    *,
    directory: bool,
) -> int:
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise DirectoryPublishError("Windows identity-bound deletion API is unavailable")
    kernel32 = win_dll("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    flags = 0x00200000  # FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= 0x02000000  # FILE_FLAG_BACKUP_SEMANTICS
    handle = create_file(
        str(path),
        0x00010000 | 0x00000080,  # DELETE | FILE_READ_ATTRIBUTES
        0x00000001 | 0x00000002,  # share reads/writes, but never deletion
        None,
        3,  # OPEN_EXISTING
        flags,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle in {None, invalid_handle}:
        error = ctypes.get_last_error()
        raise DirectoryPublishError(
            f"Could not guard Windows cleanup target {path}: {_windows_error_detail(error)}"
        )
    handle_value = int(handle)
    try:
        info = file_stat_module._windows_handle_stat(handle_value)  # noqa: SLF001
        expected_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if (
            is_link_or_reparse(info)
            or not expected_type
            or (not directory and info.st_nlink != 1)
            or file_identity(info) != expected_identity
        ):
            raise DirectoryPublishError("Windows cleanup target identity changed")
    except BaseException as original:
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        if not close_handle(ctypes.c_void_p(handle_value)):
            error = ctypes.get_last_error()
            original.add_note(
                f"Windows cleanup handle cleanup failed: {_windows_error_detail(error)}"
            )
        raise
    return handle_value


def _windows_mark_handle_for_deletion(handle: int, path: Path) -> None:
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise DirectoryPublishError("Windows identity-bound deletion API is unavailable")
    kernel32 = win_dll("kernel32", use_last_error=True)

    disposition = _FileDispositionInfo(1)
    set_information = kernel32.SetFileInformationByHandle
    set_information.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    set_information.restype = ctypes.c_int
    if not set_information(
        ctypes.c_void_p(handle),
        4,  # FileDispositionInfo
        ctypes.byref(disposition),
        ctypes.sizeof(disposition),
    ):
        error = ctypes.get_last_error()
        raise DirectoryPublishError(
            f"Could not mark Windows cleanup target for deletion {path}: "
            f"{_windows_error_detail(error)}"
        )


def _windows_close_cleanup_handle(handle: int) -> None:
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise DirectoryPublishError("Windows cleanup handle API is unavailable")
    kernel32 = win_dll("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    if not close_handle(ctypes.c_void_p(handle)):
        error = ctypes.get_last_error()
        raise DirectoryPublishError(
            f"Windows cleanup handle cleanup failed: {_windows_error_detail(error)}"
        )


def _windows_remove_claimed_file(path: Path, expected_identity: DirectoryIdentity) -> None:
    handle = _windows_open_delete_handle(
        path,
        expected_identity,
        directory=False,
    )
    try:
        _windows_mark_handle_for_deletion(handle, path)
    finally:
        primary = sys.exception()
        try:
            _windows_close_cleanup_handle(handle)
        except DirectoryPublishError as cleanup_error:
            if primary is not None:
                primary.add_note(str(cleanup_error))
            else:
                raise


@dataclass(slots=True)
class _RetainedWindowsTreeEntry:
    path: Path
    handle: int
    identity: DirectoryIdentity
    directory: bool
    depth: int
    state: tuple[int, int, int, int, int, int, int]


def _register_retained_windows_entry(
    retained: list[_RetainedWindowsTreeEntry],
    entry: _RetainedWindowsTreeEntry,
) -> None:
    retained.append(entry)


def _retain_windows_directory_tree(
    root: Path,
    root_handle: int,
) -> list[_RetainedWindowsTreeEntry]:
    retained: list[_RetainedWindowsTreeEntry] = []
    pending: list[tuple[Path, int, int]] = [(root, root_handle, 0)]
    try:
        while pending:
            directory_path, directory_handle, depth = pending.pop()
            directory_state = file_stat_module._windows_handle_stat(  # noqa: SLF001
                directory_handle
            )
            if is_link_or_reparse(directory_state) or not stat.S_ISDIR(directory_state.st_mode):
                raise DirectoryPublishError("Retained Windows cleanup directory changed")
            try:
                children = sorted(directory_path.iterdir(), key=lambda item: item.name)
            except OSError as exc:
                raise DirectoryPublishError(
                    f"Could not enumerate retained Windows cleanup tree: {exc}"
                ) from exc
            for child in children:
                before = path_file_stat(child)
                if is_link_or_reparse(before):
                    raise DirectoryPublishError(
                        f"Retained Windows cleanup tree contains a reparse point: {child}"
                    )
                directory = stat.S_ISDIR(before.st_mode)
                if not directory and (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1):
                    raise DirectoryPublishError(
                        "Retained Windows cleanup tree contains a special or "
                        f"hard-linked file: {child}"
                    )
                identity = file_identity(before)
                handle = _windows_open_delete_handle(
                    child,
                    identity,
                    directory=directory,
                )
                entry: _RetainedWindowsTreeEntry | None = None
                try:
                    opened = file_stat_module._windows_handle_stat(handle)  # noqa: SLF001
                    if _stable_source_state(opened) != _stable_source_state(before):
                        raise DirectoryPublishError(
                            f"Retained Windows cleanup entry changed while opening: {child}"
                        )
                    entry = _RetainedWindowsTreeEntry(
                        path=child,
                        handle=handle,
                        identity=identity,
                        directory=directory,
                        depth=depth + 1,
                        state=_stable_source_state(opened),
                    )
                    _register_retained_windows_entry(retained, entry)
                except BaseException:
                    if entry is not None and any(item is entry for item in retained):
                        raise
                    primary = sys.exception()
                    try:
                        _windows_close_cleanup_handle(handle)
                    except DirectoryPublishError as cleanup_error:
                        if primary is not None:
                            primary.add_note(str(cleanup_error))
                        else:
                            raise
                    raise
                if len(retained) > 20_000:
                    raise DirectoryPublishError(
                        "Retained Windows cleanup tree exceeds its safe node limit"
                    )
                if directory:
                    pending.append((child, handle, depth + 1))
        return retained
    except BaseException:
        errors: list[DirectoryPublishError] = []
        for entry in reversed(retained):
            try:
                _windows_close_cleanup_handle(entry.handle)
            except DirectoryPublishError as exc:
                errors.append(exc)
        if errors and sys.exception() is not None:
            sys.exception().add_note(str(errors[0]))
        raise


def _require_retained_windows_tree(
    root: Path,
    root_handle: int,
    retained: list[_RetainedWindowsTreeEntry],
) -> None:
    root_state = file_stat_module._windows_handle_stat(root_handle)  # noqa: SLF001
    visible_root = path_file_stat(root)
    if (
        is_link_or_reparse(root_state)
        or is_link_or_reparse(visible_root)
        or not stat.S_ISDIR(root_state.st_mode)
        or not stat.S_ISDIR(visible_root.st_mode)
        or file_identity(root_state) != file_identity(visible_root)
    ):
        raise DirectoryPublishError("Retained Windows cleanup root changed")
    expected_paths = {entry.path for entry in retained}
    current_paths: set[Path] = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        for child in directory.iterdir():
            current_paths.add(child)
            info = path_file_stat(child)
            if stat.S_ISDIR(info.st_mode) and not is_link_or_reparse(info):
                pending.append(child)
    if current_paths != expected_paths:
        raise DirectoryPublishError(
            "Retained Windows cleanup tree namespace changed before deletion"
        )
    for entry in retained:
        opened = file_stat_module._windows_handle_stat(entry.handle)  # noqa: SLF001
        named = path_file_stat(entry.path)
        if (
            _stable_source_state(opened) != entry.state
            or _stable_source_state(named) != entry.state
        ):
            raise DirectoryPublishError(
                f"Retained Windows cleanup entry changed before deletion: {entry.path}"
            )


def _close_retained_windows_entries(
    retained: list[_RetainedWindowsTreeEntry],
) -> None:
    errors: list[DirectoryPublishError] = []
    for entry in reversed(retained):
        if entry.handle == 0:
            continue
        try:
            _windows_close_cleanup_handle(entry.handle)
            entry.handle = 0
        except DirectoryPublishError as exc:
            errors.append(exc)
    if errors:
        raise errors[0]


def _windows_remove_retained_directory(
    path: Path,
    expected_identity: DirectoryIdentity,
    *,
    recursive: bool,
    verify: Callable[[Path], None] | None = None,
    verify_retained: RetainedDirectoryVerifier | None = None,
    retained_parent: PinnedOutputParent | None = None,
) -> None:
    if retained_parent is None:
        handle = _windows_open_delete_handle(
            path,
            expected_identity,
            directory=True,
        )
    else:
        api = retained_parent.windows_api
        parent_handle = retained_parent.windows_parent_handle
        if api is None or parent_handle is None:
            raise DirectoryPublishError("Retained Windows cleanup parent is unavailable")
        try:
            handle = api.open_existing_directory_strict(
                parent_handle,
                path.name,
                delete=True,
            )
            opened = api.strict_directory_info(
                handle,
                context=f"retained cleanup directory {path.name}",
            )
            if file_identity(opened) != expected_identity:
                raise DirectoryPublishError("Windows cleanup target identity changed")
        except BaseException:
            if "handle" in locals():
                try:
                    api.close(handle)
                except AssetContractError as cleanup_error:
                    if sys.exception() is not None:
                        sys.exception().add_note(str(cleanup_error))
            raise
    retained: list[_RetainedWindowsTreeEntry] = []
    namespace_mutated = False
    try:
        retained = _retain_windows_directory_tree(path, handle)
        if retained and not recursive:
            raise DirectoryPublishError("Claimed empty Windows directory is no longer empty")
        if verify_retained is not None:
            verify_retained(path, None)
        elif verify is not None:
            verify(path)
        _require_retained_windows_tree(path, handle, retained)
        for entry in sorted(
            retained,
            key=lambda item: (
                item.directory,
                -item.depth,
                str(item.path),
            ),
        ):
            _windows_mark_handle_for_deletion(entry.handle, entry.path)
            _windows_close_cleanup_handle(entry.handle)
            entry.handle = 0
            namespace_mutated = True
        _require_expected_directory(
            file_stat_module._windows_handle_stat(handle),  # noqa: SLF001
            expected_identity,
            context="guarded Windows cleanup directory",
        )
        _windows_mark_handle_for_deletion(handle, path)
    except DirectoryPublishIndeterminateError:
        raise
    except DirectoryPublishError as exc:
        if namespace_mutated:
            raise DirectoryPublishIndeterminateError(
                "Retained Windows directory cleanup stopped after deleting an exact owned subset"
            ) from exc
        raise
    except OSError as exc:
        if namespace_mutated:
            raise DirectoryPublishIndeterminateError(
                "Retained Windows directory cleanup durability is indeterminate"
            ) from exc
        raise DirectoryPublishError(
            f"Could not remove retained Windows directory {path}: {exc}"
        ) from exc
    finally:
        primary = sys.exception()
        try:
            _close_retained_windows_entries(retained)
        except DirectoryPublishError as cleanup_error:
            if primary is not None:
                primary.add_note(str(cleanup_error))
            else:
                primary = cleanup_error
        try:
            _windows_close_cleanup_handle(handle)
        except DirectoryPublishError as cleanup_error:
            if primary is not None:
                primary.add_note(str(cleanup_error))
            else:
                raise
        if primary is not None and sys.exception() is None:
            raise primary


def _remove_retained_directory(
    parent: Path,
    claim_name: str,
    expected_identity: DirectoryIdentity,
    *,
    recursive: bool,
    verify: Callable[[Path], None] | None = None,
    verify_retained: RetainedDirectoryVerifier | None = None,
    retained_parent: PinnedOutputParent | None = None,
) -> None:
    if sys.platform.startswith("linux") and os.name == "posix":
        _posix_remove_retained_directory(
            parent,
            claim_name,
            expected_identity,
            recursive=recursive,
            verify=verify,
            verify_retained=verify_retained,
            retained_parent_fd=(None if retained_parent is None else retained_parent.parent_fd),
        )
        return
    if os.name == "nt":
        _windows_remove_retained_directory(
            parent / claim_name,
            expected_identity,
            recursive=recursive,
            verify=verify,
            verify_retained=verify_retained,
            retained_parent=retained_parent,
        )
        return
    raise DirectoryPublishError(
        "Identity-bound directory removal is supported only on Linux and Windows"
    )


def quarantine_and_remove_verified_directory(
    path: Path,
    expected_identity: DirectoryIdentity,
    *,
    verify: Callable[[Path], None] | None = None,
    verify_retained: RetainedDirectoryVerifier | None = None,
    retained_parent: PinnedOutputParent | None = None,
) -> None:
    """Remove only a verified owned directory through an identity-bound primitive."""

    if (verify is None) == (verify_retained is None):
        raise DirectoryPublishError("Exactly one retained directory verifier is required")

    if directory_identity(path, context="rollback directory") != expected_identity:
        raise DirectoryPublishError("Rollback directory identity no longer matches its journal")

    def remove_and_check(parent: PinnedOutputParent) -> None:
        if Path(os.path.abspath(path.parent)) != parent.path:
            raise DirectoryPublishError("D3 retained parent does not match its stage path")
        parent.assert_current()
        _remove_retained_directory(
            path.parent,
            path.name,
            expected_identity,
            recursive=True,
            verify=verify,
            verify_retained=verify_retained,
            retained_parent=parent,
        )
        require_pinned_names_absent(
            parent,
            (path.name,),
            context="D3 verified stage cleanup",
        )

    if retained_parent is not None:
        remove_and_check(retained_parent)
        return
    try:
        with open_verified_output_parent(path.parent, create=False) as parent:
            remove_and_check(parent)
    except DirectoryPublishIndeterminateError:
        raise
    except AssetContractError as exc:
        raise DirectoryPublishIndeterminateError(
            "D3 verified stage cleanup parent outcome is indeterminate"
        ) from exc


def remove_verified_empty_directory(
    path: Path,
    expected_identity: DirectoryIdentity,
    *,
    retained_parent: PinnedOutputParent | None = None,
) -> None:
    """Claim and remove an empty owned directory without deleting a replacement."""

    try:
        info = path_file_stat(path)
    except FileNotFoundError as exc:
        raise DirectoryPublishError(
            "Created directory disappeared before identity-bound removal"
        ) from exc
    except OSError as exc:
        raise DirectoryPublishError(f"Could not inspect created directory {path}: {exc}") from exc
    if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise DirectoryPublishError("Created directory is no longer a real directory")
    if file_identity(info) != expected_identity:
        raise DirectoryPublishError("Created directory identity no longer matches its journal")
    try:
        next(path.iterdir())
    except StopIteration:
        pass
    except FileNotFoundError as exc:
        raise DirectoryPublishError(
            "Created directory disappeared during empty-directory verification"
        ) from exc
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not inspect created directory contents {path}: {exc}"
        ) from exc
    else:
        raise DirectoryPublishError("Created directory is no longer empty")

    def remove_and_check(parent: PinnedOutputParent) -> None:
        if Path(os.path.abspath(path.parent)) != parent.path:
            raise DirectoryPublishError("D3 retained parent does not match its stage path")
        parent.assert_current()
        _remove_retained_directory(
            path.parent,
            path.name,
            expected_identity,
            recursive=False,
            retained_parent=parent,
        )
        require_pinned_names_absent(
            parent,
            (path.name,),
            context="D3 empty stage cleanup",
        )

    if retained_parent is not None:
        remove_and_check(retained_parent)
        return
    try:
        with open_verified_output_parent(path.parent, create=False) as parent:
            remove_and_check(parent)
    except DirectoryPublishIndeterminateError:
        raise
    except AssetContractError as exc:
        raise DirectoryPublishIndeterminateError(
            "D3 empty stage cleanup parent outcome is indeterminate"
        ) from exc


def _posix_remove_claimed_directory(
    parent: Path,
    claim_name: str,
    expected_identity: DirectoryIdentity,
    *,
    recursive: bool,
) -> None:
    """Preserve the legacy fail-closed descriptor-unlink cleanup contract."""

    required_dir_fd = (os.open, os.stat)
    if (
        not sys.platform.startswith("linux")
        or os.name != "posix"
        or any(operation not in os.supports_dir_fd for operation in required_dir_fd)
        or os.scandir not in os.supports_fd
    ):
        raise DirectoryPublishError("Identity-bound POSIX directory removal is unavailable")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        parent_descriptor = os.open(parent, flags)
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not open claimed directory parent {parent}: {exc}"
        ) from exc
    claim_descriptor: int | None = None
    try:
        parent_info = descriptor_file_stat(parent_descriptor)
        if is_link_or_reparse(parent_info) or not stat.S_ISDIR(parent_info.st_mode):
            raise DirectoryPublishError("Claimed directory parent is not a real directory")
        claim_descriptor = os.open(
            claim_name,
            flags,
            dir_fd=parent_descriptor,
        )
        opened = descriptor_file_stat(claim_descriptor)
        _require_expected_directory(
            opened,
            expected_identity,
            context="claimed directory",
        )
        if _posix_preflight_directory_deletion(
            claim_descriptor,
            recursive=recursive,
        ):
            return
        _posix_unlink_descriptor(claim_descriptor, directory=True)
    except DirectoryPublishError:
        raise
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not remove claimed directory {parent / claim_name}: {exc}"
        ) from exc
    finally:
        descriptors = (
            (
                (claim_descriptor, "claimed directory descriptor cleanup"),
                (
                    parent_descriptor,
                    "claimed directory parent descriptor cleanup",
                ),
            )
            if claim_descriptor is not None
            else (
                (
                    parent_descriptor,
                    "claimed directory parent descriptor cleanup",
                ),
            )
        )
        _close_descriptors(descriptors)


def _windows_remove_claimed_directory(
    path: Path,
    expected_identity: DirectoryIdentity,
    *,
    recursive: bool,
) -> None:
    """Preserve the legacy empty-only Windows cleanup contract."""

    handle = _windows_open_delete_handle(
        path,
        expected_identity,
        directory=True,
    )
    try:
        try:
            has_children = next(path.iterdir(), None) is not None
        except OSError as exc:
            raise DirectoryPublishError(
                f"Could not inspect claimed Windows directory {path}: {exc}"
            ) from exc
        if has_children:
            if recursive:
                raise DirectoryPublishError(
                    "Refusing to mutate a non-empty retained directory without a "
                    "pre-recorded retained child snapshot"
                )
            raise DirectoryPublishError("Claimed empty Windows directory is no longer empty")
        _require_expected_directory(
            file_stat_module._windows_handle_stat(handle),  # noqa: SLF001
            expected_identity,
            context="guarded Windows cleanup directory",
        )
        _windows_mark_handle_for_deletion(handle, path)
    finally:
        primary = sys.exception()
        try:
            _windows_close_cleanup_handle(handle)
        except DirectoryPublishError as cleanup_error:
            if primary is not None:
                primary.add_note(str(cleanup_error))
            else:
                raise


def _remove_claimed_directory(
    parent: Path,
    claim_name: str,
    expected_identity: DirectoryIdentity,
    *,
    recursive: bool,
) -> None:
    if sys.platform.startswith("linux") and os.name == "posix":
        _posix_remove_claimed_directory(
            parent,
            claim_name,
            expected_identity,
            recursive=recursive,
        )
        return
    if os.name == "nt":
        _windows_remove_claimed_directory(
            parent / claim_name,
            expected_identity,
            recursive=recursive,
        )
        return
    raise DirectoryPublishError(
        "Identity-bound directory removal is supported only on Linux and Windows"
    )


def quarantine_and_remove_owned_directory(
    path: Path,
    expected_identity: DirectoryIdentity,
    *,
    verify: Callable[[Path], None],
) -> None:
    """Use the unchanged legacy cleanup boundary for existing bundle formats."""

    if directory_identity(path, context="rollback directory") != expected_identity:
        raise DirectoryPublishError("Rollback directory identity no longer matches its journal")
    verify(path)
    if sys.platform.startswith("linux") and os.name == "posix":
        _remove_claimed_directory(
            path.parent,
            path.name,
            expected_identity,
            recursive=True,
        )
        return
    if os.name != "nt":
        raise DirectoryPublishError(
            "Identity-bound directory removal is supported only on Linux and Windows"
        )
    _remove_claimed_directory(
        path.parent,
        path.name,
        expected_identity,
        recursive=True,
    )


def remove_owned_empty_directory(path: Path, expected_identity: DirectoryIdentity) -> None:
    """Use the unchanged legacy empty-directory cleanup boundary."""

    try:
        info = path_file_stat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise DirectoryPublishError(f"Could not inspect created directory {path}: {exc}") from exc
    if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise DirectoryPublishError("Created directory is no longer a real directory")
    if file_identity(info) != expected_identity:
        raise DirectoryPublishError("Created directory identity no longer matches its journal")
    try:
        next(path.iterdir())
    except StopIteration:
        pass
    except FileNotFoundError:
        return
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not inspect created directory contents {path}: {exc}"
        ) from exc
    else:
        return

    if sys.platform.startswith("linux") and os.name == "posix":
        _remove_claimed_directory(
            path.parent,
            path.name,
            expected_identity,
            recursive=False,
        )
        return
    if os.name != "nt":
        raise DirectoryPublishError(
            "Identity-bound directory removal is supported only on Linux and Windows"
        )
    try:
        _remove_claimed_directory(
            path.parent,
            path.name,
            expected_identity,
            recursive=False,
        )
    except DirectoryPublishError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            return
        raise
