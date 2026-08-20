from __future__ import annotations

import re

from worldforge.creation_contracts import MAX_SAFE_INTEGER

_LEGACY_FIELDS = frozenset({"device", "inode"})
_WINDOWS_FIELDS = frozenset({"volume_serial", "file_id"})
_VOLUME_SERIAL_RE = re.compile(r"[0-9a-f]{16}")
_FILE_ID_RE = re.compile(r"[0-9a-f]{32}")
_MAX_VOLUME_SERIAL = 2**64 - 1
_MAX_FILE_ID = 2**128 - 1

PublicationIdentity = tuple[int, int]
PublicationIdentityDocument = dict[str, int | str]


class PublicationIdentityCodecError(ValueError):
    """Raised when a private publication identity is not exact and portable."""


def _checked_identity(
    identity: object,
    *,
    maximum_device: int,
    maximum_inode: int,
) -> PublicationIdentity:
    if (
        not isinstance(identity, tuple)
        or len(identity) != 2
        or type(identity[0]) is not int
        or type(identity[1]) is not int
        or not 0 <= identity[0] <= maximum_device
        or not 0 <= identity[1] <= maximum_inode
    ):
        raise PublicationIdentityCodecError("publication filesystem identity is out of range")
    return identity


def encode_publication_identity(
    identity: object,
    *,
    windows: bool,
) -> PublicationIdentityDocument:
    """Encode a filesystem identity without cross-language integer loss."""

    if windows:
        device, inode = _checked_identity(
            identity,
            maximum_device=_MAX_VOLUME_SERIAL,
            maximum_inode=_MAX_FILE_ID,
        )
        return {
            "volume_serial": f"{device:016x}",
            "file_id": f"{inode:032x}",
        }
    device, inode = _checked_identity(
        identity,
        maximum_device=MAX_SAFE_INTEGER,
        maximum_inode=MAX_SAFE_INTEGER,
    )
    return {"device": device, "inode": inode}


def decode_publication_identity(
    value: object,
    *,
    context: str = "publication filesystem identity",
) -> PublicationIdentity:
    """Decode the current Windows or legacy JavaScript-safe identity shape."""

    if type(value) is not dict:
        raise PublicationIdentityCodecError(f"{context} must be an object")
    fields = set(value)
    if fields == _LEGACY_FIELDS:
        device = value["device"]
        inode = value["inode"]
        if (
            type(device) is not int
            or type(inode) is not int
            or not 0 <= device <= MAX_SAFE_INTEGER
            or not 0 <= inode <= MAX_SAFE_INTEGER
        ):
            raise PublicationIdentityCodecError(
                f"{context} numeric values must be JavaScript-safe non-negative integers"
            )
        return device, inode
    if fields == _WINDOWS_FIELDS:
        volume_serial = value["volume_serial"]
        file_id = value["file_id"]
        if (
            not isinstance(volume_serial, str)
            or _VOLUME_SERIAL_RE.fullmatch(volume_serial) is None
            or not isinstance(file_id, str)
            or _FILE_ID_RE.fullmatch(file_id) is None
        ):
            raise PublicationIdentityCodecError(
                f"{context} Windows values must be fixed-width lowercase hexadecimal strings"
            )
        return int(volume_serial, 16), int(file_id, 16)
    raise PublicationIdentityCodecError(f"{context} must use exactly one supported identity shape")


__all__ = [
    "PublicationIdentityCodecError",
    "decode_publication_identity",
    "encode_publication_identity",
]
