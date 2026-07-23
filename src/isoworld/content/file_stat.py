from __future__ import annotations

import ctypes
import os
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WindowsFileStat:
    """Stable Windows file state obtained from one non-following kernel handle."""

    st_mode: int
    st_dev: int
    st_ino: int
    st_nlink: int
    st_size: int
    st_mtime_ns: int
    st_ctime_ns: int
    st_file_attributes: int


FileStat = os.stat_result | WindowsFileStat

_FILE_ATTRIBUTE_READONLY = 0x00000001
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_READ_ATTRIBUTES = 0x00000080
_FILE_SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004
_OPEN_EXISTING = 3
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_WINDOWS_EPOCH_100NS = 116_444_736_000_000_000


def _platform_name() -> str:
    return os.name


def _windows_error(error: int, path: Path | None = None) -> OSError:
    mapped = ctypes.WinError(error)
    if path is None:
        return mapped
    return OSError(mapped.errno, mapped.strerror, str(path), error)


def _windows_handle_stat(handle: int) -> WindowsFileStat:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _FileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", ctypes.c_uint32),
            ("creation_time", _FileTime),
            ("last_access_time", _FileTime),
            ("last_write_time", _FileTime),
            ("volume_serial_number", ctypes.c_uint32),
            ("file_size_high", ctypes.c_uint32),
            ("file_size_low", ctypes.c_uint32),
            ("number_of_links", ctypes.c_uint32),
            ("file_index_high", ctypes.c_uint32),
            ("file_index_low", ctypes.c_uint32),
        ]

    class _FileBasicInformation(ctypes.Structure):
        _fields_ = [
            ("creation_time", ctypes.c_int64),
            ("last_access_time", ctypes.c_int64),
            ("last_write_time", ctypes.c_int64),
            ("change_time", ctypes.c_int64),
            ("attributes", ctypes.c_uint32),
        ]

    class _FileId128(ctypes.Structure):
        _fields_ = [("identifier", ctypes.c_ubyte * 16)]

    class _FileIdInformation(ctypes.Structure):
        _fields_ = [
            ("volume_serial_number", ctypes.c_uint64),
            ("file_id", _FileId128),
        ]

    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ByHandleFileInformation)]
    get_information.restype = ctypes.c_int
    legacy = _ByHandleFileInformation()
    if not get_information(ctypes.c_void_p(handle), ctypes.byref(legacy)):
        raise _windows_error(ctypes.get_last_error())

    get_extended_information = kernel32.GetFileInformationByHandleEx
    get_extended_information.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    get_extended_information.restype = ctypes.c_int

    basic = _FileBasicInformation()
    if get_extended_information(
        ctypes.c_void_p(handle),
        0,  # FileBasicInfo
        ctypes.byref(basic),
        ctypes.sizeof(basic),
    ):
        attributes = int(basic.attributes)
        modified_100ns = int(basic.last_write_time)
        changed_100ns = int(basic.change_time)
    else:
        attributes = int(legacy.attributes)
        modified_100ns = (int(legacy.last_write_time.high) << 32) | int(legacy.last_write_time.low)
        changed_100ns = (int(legacy.creation_time.high) << 32) | int(legacy.creation_time.low)

    file_id = _FileIdInformation()
    if get_extended_information(
        ctypes.c_void_p(handle),
        18,  # FileIdInfo
        ctypes.byref(file_id),
        ctypes.sizeof(file_id),
    ):
        device = int(file_id.volume_serial_number)
        inode = int.from_bytes(bytes(file_id.file_id.identifier), "little")
    else:
        device = int(legacy.volume_serial_number)
        inode = (int(legacy.file_index_high) << 32) | int(legacy.file_index_low)

    permissions = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
    if not attributes & _FILE_ATTRIBUTE_READONLY:
        permissions |= stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
    file_type = stat.S_IFDIR if attributes & _FILE_ATTRIBUTE_DIRECTORY else stat.S_IFREG
    size = (int(legacy.file_size_high) << 32) | int(legacy.file_size_low)
    return WindowsFileStat(
        st_mode=file_type | permissions,
        st_dev=device,
        st_ino=inode,
        st_nlink=int(legacy.number_of_links),
        st_size=size,
        st_mtime_ns=(modified_100ns - _WINDOWS_EPOCH_100NS) * 100,
        st_ctime_ns=(changed_100ns - _WINDOWS_EPOCH_100NS) * 100,
        st_file_attributes=attributes,
    )


def _windows_path_file_stat(path: Path) -> WindowsFileStat:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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
    handle = create_file(
        str(path),
        _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_ALL,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle in {None, invalid_handle}:
        raise _windows_error(ctypes.get_last_error(), path)

    validation_error: BaseException | None = None
    result: WindowsFileStat | None = None
    try:
        result = _windows_handle_stat(int(handle))
    except BaseException as exc:
        validation_error = exc
    if not close_handle(ctypes.c_void_p(handle)):
        close_error = _windows_error(ctypes.get_last_error(), path)
        if validation_error is not None:
            raise OSError(
                f"{validation_error}; additionally could not close the Windows file handle: "
                f"{close_error}"
            ) from validation_error
        raise close_error
    if validation_error is not None:
        raise validation_error
    assert result is not None
    return result


def _windows_descriptor_file_stat(descriptor: int) -> WindowsFileStat:
    import msvcrt

    handle = msvcrt.get_osfhandle(descriptor)
    if handle == -1:
        raise OSError("Could not obtain the Windows handle for a file descriptor")
    return _windows_handle_stat(handle)


def path_file_stat(path: str | Path) -> FileStat:
    """Inspect a path without following its final reparse point or symbolic link."""

    source = Path(path)
    if _platform_name() == "nt":
        return _windows_path_file_stat(source)
    return os.stat(source, follow_symlinks=False)


def descriptor_file_stat(descriptor: int) -> FileStat:
    """Inspect an open descriptor using the same identity contract as ``path_file_stat``."""

    if _platform_name() == "nt":
        return _windows_descriptor_file_stat(descriptor)
    return os.fstat(descriptor)


def file_identity(info: FileStat) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def is_link_or_reparse(info: FileStat) -> bool:
    """Return whether one non-following file state denotes a link-like object."""

    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", _FILE_ATTRIBUTE_REPARSE_POINT)
    )


__all__ = [
    "FileStat",
    "WindowsFileStat",
    "descriptor_file_stat",
    "file_identity",
    "is_link_or_reparse",
    "path_file_stat",
]
