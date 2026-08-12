from __future__ import annotations

import os
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from isoworld.content.file_stat import descriptor_file_stat, path_file_stat
from worldforge.asset_io import AssetContractError, _WindowsPublicationApi
from worldforge.windows_project_migration import windows_migration_support_reason


def _directory_identity(descriptor: int, *, context: str) -> tuple[int, int]:
    info = descriptor_file_stat(descriptor)
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"{context} is not a retained directory")
    return info.st_dev, info.st_ino


def _retained_directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _open_retained_directory_ancestry(
    path: Path,
) -> tuple[list[int], tuple[tuple[int, int], ...]]:
    absolute = Path(os.path.abspath(path))
    descriptors: list[int] = []
    identities: list[tuple[int, int]] = []
    flags = _retained_directory_flags()
    try:
        descriptor = os.open(absolute.anchor, flags)
        descriptors.append(descriptor)
        identities.append(_directory_identity(descriptor, context=absolute.anchor))
        for component in absolute.parts[1:]:
            descriptor = os.open(
                component,
                flags,
                dir_fd=descriptors[-1],
            )
            descriptors.append(descriptor)
            identities.append(
                _directory_identity(
                    descriptor,
                    context=f"world project ancestor {component}",
                )
            )
        return descriptors, tuple(identities)
    except BaseException as primary:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError as exc:
                primary.add_note(f"World project ancestry cleanup failed: {exc}")
        raise


def retained_world_lifecycle_supported() -> bool:
    """Report whether a retained lifecycle implementation exists on this platform."""

    if os.name == "posix":
        return Path("/proc/self/fd").is_dir()
    if os.name == "nt":
        return getattr(__import__("ctypes"), "WinDLL", None) is not None
    return False


def world_project_migration_apply_capability(
    project_root: str | Path,
) -> tuple[bool, str | None]:
    """Probe apply support without creating locks, journals, or migration files."""

    if os.name == "posix":
        if retained_world_lifecycle_supported() and sys.platform.startswith("linux"):
            return True, None
        return False, "identity_exchange_unavailable"
    if os.name != "nt":
        return False, "retained_lifecycle_unavailable"
    root = Path(os.path.abspath(Path(project_root)))
    api: _WindowsPublicationApi | None = None
    handles: list[int] = []
    try:
        api = _WindowsPublicationApi()
        ancestry, _ignored = api.open_ancestry(root.parent, create=False)
        handles.extend(ancestry)
        root_handle = api.open_relative_directory(
            ancestry[-1],
            root.name,
            create=False,
            writable=True,
        )
        handles.append(root_handle)
        capabilities = api.migration_volume_capabilities(root_handle, root)
        reason = windows_migration_support_reason(capabilities)
        return reason is None, reason
    except (AssetContractError, OSError) as exc:
        return False, f"windows_capability_probe_failed:{exc}"
    finally:
        if api is not None:
            try:
                api.close_many(handles)
            except AssetContractError:
                pass


@dataclass(slots=True)
class RetainedWorldLifecycle:
    """Writer lease bound to one root, control directory, and source directory."""

    root: Path
    parent_path: Path
    parent_fd: int
    root_fd: int
    control_fd: int
    source_fd: int
    root_name: str
    parent_ancestry_identities: tuple[tuple[int, int], ...]
    root_identity: tuple[int, int]
    control_identity: tuple[int, int]
    source_identity: tuple[int, int]
    error_type: type[ValueError]

    @property
    def control_path(self) -> Path:
        return Path(f"/proc/self/fd/{self.control_fd}")

    def assert_current(self) -> None:
        verification: list[int] = []
        try:
            verification, visible_ancestry = _open_retained_directory_ancestry(self.parent_path)
            if visible_ancestry != self.parent_ancestry_identities:
                raise self.error_type("World project root ancestry changed")
            named_root = os.stat(
                self.root_name,
                dir_fd=self.parent_fd,
                follow_symlinks=False,
            )
            named_control = os.stat(
                ".worldforge",
                dir_fd=self.root_fd,
                follow_symlinks=False,
            )
            named_source = os.stat(
                "source",
                dir_fd=self.root_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise self.error_type(f"World project root or control ancestry changed: {exc}") from exc
        finally:
            primary = sys.exception()
            cleanup_errors: list[OSError] = []
            for descriptor in reversed(verification):
                try:
                    os.close(descriptor)
                except OSError as exc:
                    cleanup_errors.append(exc)
            if cleanup_errors:
                if primary is not None:
                    for exc in cleanup_errors:
                        primary.add_note(
                            f"World project ancestry verification cleanup failed: {exc}"
                        )
                else:
                    raise self.error_type(
                        f"World project ancestry verification cleanup failed: {cleanup_errors[0]}"
                    ) from cleanup_errors[0]
        if (
            not stat.S_ISDIR(named_root.st_mode)
            or not stat.S_ISDIR(named_control.st_mode)
            or not stat.S_ISDIR(named_source.st_mode)
            or (named_root.st_dev, named_root.st_ino) != self.root_identity
            or (named_control.st_dev, named_control.st_ino) != self.control_identity
            or (named_source.st_dev, named_source.st_ino) != self.source_identity
        ):
            raise self.error_type("World project root or control ancestry changed")


@dataclass(slots=True)
class WindowsRetainedWorldLifecycle:
    """Retained non-reparse Windows ancestry and control handles."""

    root: Path
    api: _WindowsPublicationApi
    ancestry_handles: tuple[int, ...]
    ancestry_identities: tuple[tuple[int, int], ...]
    root_handle: int
    control_handle: int
    source_handle: int
    root_identity: tuple[int, int]
    control_identity: tuple[int, int]
    source_identity: tuple[int, int]
    error_type: type[ValueError]

    @property
    def control_path(self) -> Path:
        return self.root / ".worldforge"

    def assert_current(self) -> None:
        verification: list[int] = []
        try:
            verification, _ignored = self.api.open_ancestry(
                self.root.parent,
                create=False,
            )
            visible_ancestry = tuple(
                (
                    info.st_dev,
                    info.st_ino,
                )
                for handle in verification
                for info in (
                    self.api.strict_directory_info(
                        handle,
                        context="Windows world-project ancestry",
                    ),
                )
            )
            if visible_ancestry != self.ancestry_identities:
                raise self.error_type("World project root ancestry changed")
            root_handle = self.api.open_relative_directory(
                verification[-1],
                self.root.name,
                create=False,
            )
            verification.append(root_handle)
            control_handle = self.api.open_relative_directory(
                root_handle,
                ".worldforge",
                create=False,
            )
            verification.append(control_handle)
            source_handle = self.api.open_relative_directory(
                root_handle,
                "source",
                create=False,
            )
            verification.append(source_handle)
            visible = (
                self.api.strict_directory_info(
                    root_handle,
                    context="Windows world-project root",
                ),
                self.api.strict_directory_info(
                    control_handle,
                    context="Windows world-project control root",
                ),
                self.api.strict_directory_info(
                    source_handle,
                    context="Windows world-project source root",
                ),
            )
            identities = tuple((item.st_dev, item.st_ino) for item in visible)
            if identities != (
                self.root_identity,
                self.control_identity,
                self.source_identity,
            ):
                raise self.error_type("World project root or control ancestry changed")
        except self.error_type:
            raise
        except (AssetContractError, OSError) as exc:
            raise self.error_type(f"World project root or control ancestry changed: {exc}") from exc
        finally:
            try:
                self.api.close_many(verification)
            except AssetContractError as exc:
                primary = sys.exception()
                if primary is not None:
                    primary.add_note(f"Windows ancestry verification cleanup failed: {exc}")
                else:
                    raise self.error_type(
                        f"Windows ancestry verification cleanup failed: {exc}"
                    ) from exc

    def flush_control(self) -> None:
        self.assert_current()
        self.api.flush_handle(
            self.control_handle,
            context="Windows world-project control directory",
        )
        self.assert_current()


@contextmanager
def _exclusive_windows_retained_world_lifecycle(
    project_root: str | Path,
    *,
    error_type: type[ValueError],
    require_migration_capabilities: bool,
) -> Iterator[WindowsRetainedWorldLifecycle]:
    root = Path(os.path.abspath(Path(project_root)))
    if not root.name or root.name in {".", ".."}:
        raise error_type("The world project root is invalid")
    handles: list[int] = []
    api: _WindowsPublicationApi | None = None
    lock_handle: int | None = None
    lock_overlapped: object | None = None
    lease: WindowsRetainedWorldLifecycle | None = None
    body_entered = False
    try:
        api = _WindowsPublicationApi()
        ancestry, _ignored = api.open_ancestry(root.parent, create=False)
        handles.extend(ancestry)
        ancestry_identities = tuple(
            (info.st_dev, info.st_ino)
            for handle in ancestry
            for info in (
                api.strict_directory_info(
                    handle,
                    context="Windows world-project ancestry",
                ),
            )
        )
        root_handle = api.open_relative_directory(
            ancestry[-1],
            root.name,
            create=False,
            writable=True,
        )
        handles.append(root_handle)
        control_handle = api.open_relative_directory(
            root_handle,
            ".worldforge",
            create=False,
            writable=True,
        )
        handles.append(control_handle)
        source_handle = api.open_relative_directory(root_handle, "source", create=False)
        handles.append(source_handle)
        root_info = api.strict_directory_info(
            root_handle,
            context="Windows world-project root",
        )
        control_info = api.strict_directory_info(
            control_handle,
            context="Windows world-project control root",
        )
        source_info = api.strict_directory_info(
            source_handle,
            context="Windows world-project source root",
        )
        if require_migration_capabilities:
            capabilities = api.migration_volume_capabilities(root_handle, root)
            reason = windows_migration_support_reason(capabilities)
            if reason is not None:
                raise error_type(f"Retained world lifecycle primitives are unavailable: {reason}")
        lease = WindowsRetainedWorldLifecycle(
            root=root,
            api=api,
            ancestry_handles=tuple(ancestry),
            ancestry_identities=ancestry_identities,
            root_handle=root_handle,
            control_handle=control_handle,
            source_handle=source_handle,
            root_identity=(root_info.st_dev, root_info.st_ino),
            control_identity=(control_info.st_dev, control_info.st_ino),
            source_identity=(source_info.st_dev, source_info.st_ino),
            error_type=error_type,
        )
        lease.assert_current()
        lock_handle = api.open_lock(control_handle, "lifecycle.lock")
        lock_info = api.strict_entry_info(
            lock_handle,
            context="Windows world lifecycle lock",
        )
        if lock_info.st_nlink != 1:
            raise error_type("The world lifecycle lock is not a standalone regular file")
        try:
            lock_overlapped = api.acquire_lock(lock_handle)
        except BlockingIOError as exc:
            raise error_type("Another world lifecycle operation is already in progress") from exc
        api.normalize_lock_byte(lock_handle)
        api.flush_handle(control_handle, context="Windows world-project control directory")
        lease.assert_current()
        body_entered = True
        yield lease
        body_entered = False
        lease.assert_current()
    except error_type:
        raise
    except (AssetContractError, OSError) as exc:
        if body_entered:
            raise
        raise error_type(f"Could not acquire or retain the world lifecycle lock: {exc}") from exc
    finally:
        primary = sys.exception()
        cleanup: list[str] = []
        if api is not None and lock_handle is not None and lock_overlapped is not None:
            try:
                api.release_lock(lock_handle, lock_overlapped)
            except AssetContractError as exc:
                cleanup.append(f"Windows lifecycle lock release failed: {exc}")
        if api is not None and lock_handle is not None:
            try:
                api.close(lock_handle)
            except AssetContractError as exc:
                cleanup.append(f"Windows lifecycle lock close failed: {exc}")
        if api is not None:
            try:
                api.close_many(handles)
            except AssetContractError as exc:
                cleanup.append(f"Windows retained-handle cleanup failed: {exc}")
        if cleanup:
            if primary is not None:
                for detail in cleanup:
                    primary.add_note(detail)
            else:
                raise error_type(cleanup[0])


@contextmanager
def exclusive_retained_world_lifecycle(
    project_root: str | Path,
    *,
    error_type: type[ValueError] = ValueError,
) -> Iterator[RetainedWorldLifecycle | WindowsRetainedWorldLifecycle]:
    """Retain a POSIX world tree and clean its lock only through retained handles."""

    if os.name == "nt":
        with _exclusive_windows_retained_world_lifecycle(
            project_root,
            error_type=error_type,
            require_migration_capabilities=True,
        ) as lease:
            yield lease
        return
    if not retained_world_lifecycle_supported():
        raise error_type("Retained world lifecycle primitives are unavailable on this platform")
    root = Path(os.path.abspath(Path(project_root)))
    root_name = root.name
    if not root_name or root_name in {".", ".."}:
        raise error_type("The world project root is invalid")
    directory_flags = _retained_directory_flags()
    descriptors: list[int] = []
    lock_descriptor: int | None = None
    lease: RetainedWorldLifecycle | None = None
    lock_acquired = False
    body_entered = False
    try:
        parent_ancestry, parent_ancestry_identities = _open_retained_directory_ancestry(root.parent)
        descriptors.extend(parent_ancestry)
        parent_fd = parent_ancestry[-1]
        root_fd = os.open(root_name, directory_flags, dir_fd=parent_fd)
        descriptors.append(root_fd)
        control_fd = os.open(".worldforge", directory_flags, dir_fd=root_fd)
        descriptors.append(control_fd)
        source_fd = os.open("source", directory_flags, dir_fd=root_fd)
        descriptors.append(source_fd)
        lease = RetainedWorldLifecycle(
            root=root,
            parent_path=root.parent,
            parent_fd=parent_fd,
            root_fd=root_fd,
            control_fd=control_fd,
            source_fd=source_fd,
            root_name=root_name,
            parent_ancestry_identities=parent_ancestry_identities,
            root_identity=_directory_identity(root_fd, context="world project root"),
            control_identity=_directory_identity(control_fd, context="world control root"),
            source_identity=_directory_identity(source_fd, context="world source root"),
            error_type=error_type,
        )
        lease.assert_current()
        lock_flags = (
            os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        lock_descriptor = os.open(
            "lifecycle.lock",
            lock_flags,
            0o600,
            dir_fd=control_fd,
        )
        lock_info = descriptor_file_stat(lock_descriptor)
        if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 1:
            raise error_type("The world lifecycle lock is not a standalone regular file")
        lock_identity = lock_info.st_dev, lock_info.st_ino
        try:
            import fcntl

            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            raise error_type("Another world lifecycle operation is already in progress") from exc
        lock_acquired = True
        named_lock = os.stat("lifecycle.lock", dir_fd=control_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(named_lock.st_mode)
            or named_lock.st_nlink != 1
            or (named_lock.st_dev, named_lock.st_ino) != lock_identity
        ):
            raise error_type("The world lifecycle lock identity changed during acquisition")
        os.ftruncate(lock_descriptor, 1)
        os.lseek(lock_descriptor, 0, os.SEEK_SET)
        os.write(lock_descriptor, bytes((os.getpid() & 0xFF,)))
        os.fsync(lock_descriptor)
        os.fsync(control_fd)
        lease.assert_current()
        body_entered = True
        yield lease
        body_entered = False
        lease.assert_current()
    except error_type:
        raise
    except OSError as exc:
        if body_entered:
            raise
        raise error_type(f"Could not acquire or retain the world lifecycle lock: {exc}") from exc
    finally:
        primary = sys.exception()
        cleanup_details: list[str] = []
        if lock_descriptor is not None and lock_acquired:
            try:
                import fcntl

                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            except (ImportError, OSError) as exc:
                cleanup_details.append(f"World lifecycle lock release failed: {exc}")
        if lock_descriptor is not None:
            try:
                os.close(lock_descriptor)
            except OSError as exc:
                cleanup_details.append(f"World lifecycle lock close failed: {exc}")
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError as exc:
                cleanup_details.append(f"World lifecycle retained descriptor cleanup failed: {exc}")
        if cleanup_details:
            if primary is not None:
                for detail in cleanup_details:
                    primary.add_note(detail)
            else:
                raise error_type(cleanup_details[0])


@contextmanager
def _exclusive_path_world_lifecycle(
    project_root: str | Path,
    *,
    error_type: type[ValueError],
) -> Iterator[Path]:
    """Retain the existing Windows-compatible lock behavior for non-migration writers."""

    root_input = Path(project_root)
    if root_input.is_symlink():
        raise error_type("The world project root cannot be a symbolic link")
    root = root_input.resolve()
    if not root.is_dir():
        raise error_type(f"The world project does not exist: {root}")
    control_root = root / ".worldforge"
    if control_root.is_symlink() or not control_root.is_dir():
        raise error_type("The world project has no safe .worldforge control directory")
    lock_path = control_root / "lifecycle.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise error_type("Another world lifecycle operation is already in progress") from exc
    except OSError as exc:
        raise error_type(f"Could not acquire the world lifecycle lock: {exc}") from exc
    identity = descriptor_file_stat(descriptor)
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        yield root
    finally:
        os.close(descriptor)
        try:
            current = path_file_stat(lock_path)
        except FileNotFoundError:
            current = None
        if current is not None and (current.st_dev, current.st_ino) == (
            identity.st_dev,
            identity.st_ino,
        ):
            lock_path.unlink()


@contextmanager
def exclusive_world_lifecycle(
    project_root: str | Path,
    *,
    error_type: type[ValueError] = ValueError,
) -> Iterator[Path]:
    """Own one writer snapshot without deleting a replacement lock."""

    if os.name == "posix":
        with exclusive_retained_world_lifecycle(
            project_root,
            error_type=error_type,
        ) as lease:
            yield lease.root
        return
    if os.name == "nt":
        with _exclusive_windows_retained_world_lifecycle(
            project_root,
            error_type=error_type,
            require_migration_capabilities=False,
        ) as lease:
            yield lease.root
        return
    with _exclusive_path_world_lifecycle(project_root, error_type=error_type) as root:
        yield root
