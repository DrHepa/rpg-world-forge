from __future__ import annotations

import hashlib
import secrets
import string
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from isoworld.content.media import MAX_MEDIA_BYTES
from isoworld.content.resource_snapshot import (
    ResourceSnapshotError,
    ResourceSnapshotOwner,
    ResourceSnapshotReader,
)
from worldforge.studio.assets import (
    AssetCatalogManager,
    ResolvedPreviewAuthority,
)
from worldforge.studio.errors import (
    StudioError,
    conflict,
    invalid_request,
    invalid_state,
    not_found,
)

_CHUNK_BYTES = 64 * 1024
_MAX_SEQUENCE = 8191
_HANDLE_LENGTH = 43
_HANDLE_ALPHABET = frozenset(string.ascii_letters + string.digits + "_-")


@dataclass(frozen=True, slots=True)
class _AssetPreviewPolicy:
    max_artifact_bytes: int = MAX_MEDIA_BYTES
    max_workspace_handles: int = 4
    max_workspace_bytes: int = 512 * 1024 * 1024
    max_global_handles: int = 16
    max_global_bytes: int = 1024 * 1024 * 1024
    idle_seconds: float = 60.0
    lifetime_seconds: float = 300.0
    reaper_seconds: float = 5.0
    shutdown_wait_seconds: float = 5.0

    def __post_init__(self) -> None:
        bounded_integers = (
            self.max_artifact_bytes,
            self.max_workspace_handles,
            self.max_workspace_bytes,
            self.max_global_handles,
            self.max_global_bytes,
        )
        durations = (
            self.idle_seconds,
            self.lifetime_seconds,
            self.reaper_seconds,
            self.shutdown_wait_seconds,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in bounded_integers):
            raise ValueError("Asset preview quota values must be integers")
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) for value in durations
        ):
            raise ValueError("Asset preview durations must be numeric")
        if any(value <= 0 for value in (*bounded_integers, *durations)):
            raise ValueError("Asset preview policy values must be positive")
        if self.max_artifact_bytes > MAX_MEDIA_BYTES:
            raise ValueError("Asset preview artifacts cannot exceed the media boundary")


@dataclass(frozen=True, slots=True)
class _CachedRead:
    handle: str
    sequence: int
    payload: bytes
    cumulative_bytes: int
    cumulative_sha256: str
    eof: bool

    def public(self) -> dict[str, object]:
        return {
            "handle": self.handle,
            "sequence": self.sequence,
            "payload": self.payload,
            "cumulative_bytes": self.cumulative_bytes,
            "cumulative_sha256": self.cumulative_sha256,
            "eof": self.eof,
        }


@dataclass(slots=True)
class _Lease:
    handle: str
    workspace_id: str
    authority: ResolvedPreviewAuthority
    reserved_bytes: int
    created_at: float
    last_access: float
    state: str = "opening"
    owner: ResourceSnapshotOwner | Any | None = None
    reader: ResourceSnapshotReader | Any | None = None
    next_sequence: int = 0
    cumulative_bytes: int = 0
    digest: Any = field(default_factory=hashlib.sha256)
    previous: _CachedRead | None = None
    in_flight: bool = True
    cleanup_in_progress: bool = False
    reader_close_attempted: bool = False


def _random_handle() -> str:
    return secrets.token_urlsafe(32)


def _valid_handle(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _HANDLE_LENGTH
        and all(character in _HANDLE_ALPHABET for character in value)
    )


class AssetPreviewManager:
    """Own bounded, revision-guarded preview snapshots without exposing paths."""

    def __init__(
        self,
        catalog: AssetCatalogManager,
        *,
        _policy: _AssetPreviewPolicy | None = None,
        _clock: Callable[[], float] = time.monotonic,
        _owner_factory: Callable[[], ResourceSnapshotOwner] = ResourceSnapshotOwner,
        _token_factory: Callable[[], str] = _random_handle,
        _start_reaper: bool = True,
    ) -> None:
        self._catalog = catalog
        self._policy = _policy or _AssetPreviewPolicy()
        self._clock = _clock
        self._owner_factory = _owner_factory
        self._token_factory = _token_factory
        self._condition = threading.Condition(threading.RLock())
        self._leases: dict[str, _Lease] = {}
        self._shutdown = False
        self._stop = threading.Event()
        self._reaper: threading.Thread | None = None
        if _start_reaper:
            self._reaper = threading.Thread(
                target=self._reaper_loop,
                name="asset-preview-reaper",
                daemon=True,
            )
            try:
                self._reaper.start()
            except BaseException:
                self._shutdown = True
                self._stop.set()
                try:
                    if self._reaper.is_alive() and self._reaper is not threading.current_thread():
                        self._reaper.join(timeout=self._policy.shutdown_wait_seconds)
                except BaseException:
                    pass
                raise

    def open(
        self,
        workspace_id: object,
        manifest_revision: object,
        entry_id: object,
    ) -> dict[str, object]:
        with self._condition:
            self._require_running()
        authority = self._catalog.resolve_preview_authority(
            workspace_id,
            manifest_revision,
            entry_id,
        )
        self._validate_authority(authority)
        lease = self._reserve(authority)
        try:
            owner = self._owner_factory()
            with self._condition:
                lease.owner = owner
            captured = owner.materialize(
                authority.world_root,
                authority.relative,
                authority.media_type,
                limit=authority.byte_length,
            )
            if captured.sha256 != authority.sha256:
                raise ResourceSnapshotError("materialized preview SHA-256 changed")
            reader = owner.open_reader(authority.relative)
            with self._condition:
                lease.reader = reader
            if reader.size != authority.byte_length or reader.sha256 != authority.sha256:
                raise ResourceSnapshotError("materialized preview identity changed")
            self._catalog.assert_current(authority.guard)
            with self._condition:
                if self._shutdown or lease.state != "opening":
                    raise invalid_state("Asset preview manager is shut down")
                lease.state = "active"
                lease.in_flight = False
                lease.last_access = self._clock()
                self._condition.notify_all()
            return {
                "handle": lease.handle,
                "entry_id": authority.entry_id,
                "manifest_revision": authority.guard.manifest_revision,
                "media_type": authority.media_type,
                "byte_length": authority.byte_length,
                "sha256": authority.sha256,
            }
        except StudioError:
            self._abort(lease)
            raise
        except Exception as exc:
            self._abort(lease)
            raise conflict("Asset preview changed or failed while opening") from exc
        except BaseException:
            self._abort(lease)
            raise

    def read(self, handle: object, sequence: object) -> dict[str, object]:
        normalized_sequence = self._sequence(sequence)
        cleanup_expired = False
        with self._condition:
            self._require_running()
            lease = self._available_lease(handle)
            now = self._clock()
            if self._expired(lease, now):
                lease.state = "closing"
                cleanup_expired = True
            elif lease.in_flight:
                raise conflict("Asset preview read is already in progress")
            if cleanup_expired:
                pass
            elif lease.previous is not None and normalized_sequence == lease.previous.sequence:
                replay = True
            elif lease.state == "active" and normalized_sequence == lease.next_sequence:
                replay = False
            else:
                raise conflict("Asset preview sequence conflict")
            if not cleanup_expired:
                lease.in_flight = True

        if cleanup_expired:
            self._cleanup(lease.handle)
            raise not_found("Asset preview handle is unavailable")

        try:
            self._catalog.assert_current(lease.authority.guard)
            if replay:
                cached = lease.previous
                assert cached is not None
                pending = cached
                pending_digest = None
            else:
                reader = lease.reader
                if reader is None:
                    raise ResourceSnapshotError("preview reader is unavailable")
                chunk = reader.read_next()
                if not isinstance(chunk.payload, bytes):
                    raise ResourceSnapshotError("preview chunk payload is invalid")
                pending_digest = lease.digest.copy()
                pending_digest.update(chunk.payload)
                self._validate_chunk(
                    lease,
                    normalized_sequence,
                    chunk.sequence,
                    chunk.payload,
                    chunk.cumulative_bytes,
                    chunk.cumulative_sha256,
                    chunk.eof,
                    pending_digest.hexdigest(),
                )
                pending = _CachedRead(
                    handle=lease.handle,
                    sequence=chunk.sequence,
                    payload=chunk.payload,
                    cumulative_bytes=chunk.cumulative_bytes,
                    cumulative_sha256=chunk.cumulative_sha256,
                    eof=chunk.eof,
                )
            self._catalog.assert_current(lease.authority.guard)

            cleanup_cancelled = False
            with self._condition:
                now = self._clock()
                if lease.state not in {"active", "eof"} or self._expired(lease, now):
                    lease.state = "closing"
                    cleanup_cancelled = True
                elif replay:
                    if lease.previous != pending:
                        lease.state = "closing"
                        cleanup_cancelled = True
                elif normalized_sequence != lease.next_sequence:
                    lease.state = "closing"
                    cleanup_cancelled = True
                else:
                    assert pending_digest is not None
                    lease.digest = pending_digest
                    lease.cumulative_bytes = pending.cumulative_bytes
                    lease.previous = pending
                    lease.next_sequence += 1
                    lease.state = "eof" if pending.eof else "active"
                lease.last_access = now
                lease.in_flight = False
                self._condition.notify_all()
            if cleanup_cancelled:
                self._cleanup(lease.handle)
                raise not_found("Asset preview handle is unavailable")
            return pending.public()
        except StudioError:
            self._abort(lease)
            raise
        except Exception as exc:
            self._abort(lease)
            raise conflict("Asset preview read failed") from exc
        except BaseException:
            self._abort(lease)
            raise

    def close(self, handle: object) -> None:
        if not _valid_handle(handle):
            return
        assert isinstance(handle, str)
        with self._condition:
            lease = self._leases.get(handle)
            if lease is None:
                return
            if lease.state != "closed":
                lease.state = "closing"
            if lease.in_flight:
                return
        self._cleanup(handle)

    def shutdown(self) -> None:
        deadline = time.monotonic() + self._policy.shutdown_wait_seconds
        with self._condition:
            self._shutdown = True
            self._stop.set()
            for lease in self._leases.values():
                if lease.state != "closed":
                    lease.state = "closing"
            reaper = self._reaper
        if reaper is not None and reaper.is_alive() and reaper is not threading.current_thread():
            reaper.join(timeout=max(0.0, deadline - time.monotonic()))

        with self._condition:
            self._condition.wait_for(
                lambda: (
                    not any(
                        lease.in_flight or lease.cleanup_in_progress
                        for lease in self._leases.values()
                    )
                ),
                timeout=max(0.0, deadline - time.monotonic()),
            )
            handles = tuple(self._leases)
        for handle in handles:
            self._cleanup(handle)
        with self._condition:
            self._condition.wait_for(
                lambda: (
                    not self._leases
                    or not any(
                        lease.in_flight or lease.cleanup_in_progress
                        for lease in self._leases.values()
                    )
                ),
                timeout=max(0.0, deadline - time.monotonic()),
            )
            if self._leases or (
                reaper is not None
                and reaper is not threading.current_thread()
                and reaper.is_alive()
            ):
                raise StudioError(
                    "internal_error",
                    "Asset preview shutdown cleanup failed",
                )

    def _reserve(self, authority: ResolvedPreviewAuthority) -> _Lease:
        now = self._clock()
        with self._condition:
            self._require_running()
            workspace_leases = [
                lease
                for lease in self._leases.values()
                if lease.workspace_id == authority.guard.workspace_id
            ]
            if len(workspace_leases) >= self._policy.max_workspace_handles:
                raise invalid_state("workspace preview quota exceeded")
            if (
                sum(lease.reserved_bytes for lease in workspace_leases) + authority.byte_length
                > self._policy.max_workspace_bytes
            ):
                raise invalid_state("workspace preview quota exceeded")
            if len(self._leases) >= self._policy.max_global_handles:
                raise invalid_state("global preview quota exceeded")
            if (
                sum(lease.reserved_bytes for lease in self._leases.values()) + authority.byte_length
                > self._policy.max_global_bytes
            ):
                raise invalid_state("global preview quota exceeded")

            handle = ""
            for _ in range(128):
                candidate = self._token_factory()
                if not _valid_handle(candidate):
                    raise StudioError(
                        "internal_error",
                        "Asset preview handle generation failed",
                    )
                if candidate not in self._leases:
                    handle = candidate
                    break
            if not handle:
                raise StudioError(
                    "internal_error",
                    "Asset preview handle generation failed",
                )
            lease = _Lease(
                handle=handle,
                workspace_id=authority.guard.workspace_id,
                authority=authority,
                reserved_bytes=authority.byte_length,
                created_at=now,
                last_access=now,
            )
            self._leases[handle] = lease
            return lease

    def _cleanup(self, handle: str) -> bool:
        with self._condition:
            lease = self._leases.get(handle)
            if lease is None or lease.in_flight or lease.cleanup_in_progress:
                return lease is None
            lease.cleanup_in_progress = True
            lease.state = "closing"
            reader = lease.reader
            owner = lease.owner

        if reader is not None and not lease.reader_close_attempted and not reader.closed:
            lease.reader_close_attempted = True
            try:
                reader.close()
            except Exception:
                with self._condition:
                    lease.cleanup_in_progress = False
                    lease.state = "quarantined"
                    self._condition.notify_all()
                return False

        if owner is not None and not owner.closed:
            try:
                owner.close()
            except Exception:
                if owner.closed:
                    pass
                else:
                    with self._condition:
                        lease.cleanup_in_progress = False
                        lease.state = "quarantined"
                        self._condition.notify_all()
                    return False
        if owner is not None and not owner.closed:
            with self._condition:
                lease.cleanup_in_progress = False
                lease.state = "quarantined"
                self._condition.notify_all()
            return False

        with self._condition:
            current = self._leases.get(handle)
            if current is lease:
                lease.cleanup_in_progress = False
                lease.state = "closed"
                self._leases.pop(handle)
            self._condition.notify_all()
        return True

    def _abort(self, lease: _Lease) -> None:
        with self._condition:
            lease.state = "closing"
            lease.in_flight = False
            self._condition.notify_all()
        self._cleanup(lease.handle)

    def _available_lease(self, handle: object) -> _Lease:
        if not _valid_handle(handle):
            raise not_found("Asset preview handle is unavailable")
        assert isinstance(handle, str)
        lease = self._leases.get(handle)
        if lease is None or lease.state not in {"active", "eof"}:
            raise not_found("Asset preview handle is unavailable")
        return lease

    @staticmethod
    def _sequence(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_SEQUENCE:
            raise invalid_request(
                f"Asset preview sequence must be an integer from 0 to {_MAX_SEQUENCE}"
            )
        return value

    def _expired(self, lease: _Lease, now: float) -> bool:
        return (
            now - lease.last_access >= self._policy.idle_seconds
            or now - lease.created_at >= self._policy.lifetime_seconds
        )

    def _validate_authority(self, authority: ResolvedPreviewAuthority) -> None:
        if (
            not isinstance(authority.byte_length, int)
            or isinstance(authority.byte_length, bool)
            or not 1 <= authority.byte_length <= self._policy.max_artifact_bytes
        ):
            raise invalid_request("artifact preview quota exceeded")
        if authority.media_type not in {"audio/wav", "image/png"}:
            raise invalid_request("Asset catalog entry is not previewable")
        if not isinstance(authority.relative, PurePosixPath):
            raise StudioError("internal_error", "Asset preview authority is invalid")

    @staticmethod
    def _validate_chunk(
        lease: _Lease,
        requested_sequence: int,
        actual_sequence: int,
        payload: bytes,
        cumulative_bytes: int,
        cumulative_sha256: str,
        eof: bool,
        computed_sha256: str,
    ) -> None:
        expected_cumulative = lease.cumulative_bytes + len(payload)
        expected_payload_bytes = min(
            _CHUNK_BYTES,
            lease.authority.byte_length - lease.cumulative_bytes,
        )
        if (
            type(actual_sequence) is not int
            or type(cumulative_bytes) is not int
            or not isinstance(cumulative_sha256, str)
            or not isinstance(eof, bool)
            or actual_sequence != requested_sequence
            or actual_sequence != lease.next_sequence
            or not isinstance(payload, bytes)
            or len(payload) != expected_payload_bytes
            or cumulative_bytes != expected_cumulative
            or cumulative_sha256 != computed_sha256
            or cumulative_bytes > lease.authority.byte_length
        ):
            raise ResourceSnapshotError("preview chunk integrity changed")
        if eof:
            if (
                cumulative_bytes != lease.authority.byte_length
                or cumulative_sha256 != lease.authority.sha256
            ):
                raise ResourceSnapshotError("preview EOF integrity changed")
        elif not payload or cumulative_bytes >= lease.authority.byte_length:
            raise ResourceSnapshotError("preview chunk ended at an unauthorized boundary")

    def _reap_once(self) -> None:
        now = self._clock()
        with self._condition:
            handles: list[str] = []
            for lease in self._leases.values():
                if (
                    lease.state == "opening"
                    and now - lease.created_at >= self._policy.lifetime_seconds
                ):
                    lease.state = "closing"
                if lease.state in {"active", "eof"} and self._expired(lease, now):
                    lease.state = "closing"
                if lease.state in {"closing", "quarantined"} and not lease.in_flight:
                    handles.append(lease.handle)
        for handle in handles:
            self._cleanup(handle)

    def _reaper_loop(self) -> None:
        while not self._stop.wait(self._policy.reaper_seconds):
            self._reap_once()

    def _require_running(self) -> None:
        if self._shutdown:
            raise invalid_state("Asset preview manager is shut down")
