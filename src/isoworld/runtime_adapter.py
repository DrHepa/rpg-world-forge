from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Generic, TypeVar, final

T = TypeVar("T")


class RuntimeAdapterRegistryError(LookupError):
    """Raised when a static runtime adapter registry cannot satisfy an exact key."""


@dataclass(frozen=True, order=True, slots=True)
class RuntimeAdapterKey:
    id: str
    version: str
    content_hash: str


def _primitive_key(key: RuntimeAdapterKey) -> tuple[str, str, str]:
    if type(key) is not RuntimeAdapterKey:
        raise RuntimeAdapterRegistryError(
            "runtime adapter registry keys must be exact RuntimeAdapterKey values"
        )
    values = (key.id, key.version, key.content_hash)
    if any(type(value) is not str or not value for value in values):
        raise RuntimeAdapterRegistryError(
            "runtime adapter registry key fields must be non-empty built-in strings"
        )
    return values


@final
@dataclass(frozen=True, slots=True, init=False, repr=False, eq=False)
class StaticRuntimeAdapterRegistry(Generic[T]):
    """Immutable exact-key registry for opaque, code-owned runtime adapters."""

    _entries: Mapping[tuple[str, str, str], T]

    def __init__(
        self,
        entries: (Mapping[RuntimeAdapterKey, T] | Iterable[tuple[RuntimeAdapterKey, T]]) = (),
    ) -> None:
        source = entries.items() if isinstance(entries, Mapping) else entries
        copied: dict[tuple[str, str, str], T] = {}
        try:
            for key, value in source:
                primitive = _primitive_key(key)
                if primitive in copied:
                    raise RuntimeAdapterRegistryError(
                        "runtime adapter registry contains a duplicate exact key"
                    )
                copied[primitive] = value
        except RuntimeAdapterRegistryError:
            raise
        except (TypeError, ValueError) as exc:
            raise RuntimeAdapterRegistryError(
                "runtime adapter registry entries must be key/value pairs"
            ) from exc
        object.__setattr__(self, "_entries", MappingProxyType(copied))

    def __init_subclass__(cls, **_kwargs: object) -> None:
        raise TypeError("StaticRuntimeAdapterRegistry cannot be subclassed")

    def __copy__(self) -> StaticRuntimeAdapterRegistry[T]:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> StaticRuntimeAdapterRegistry[T]:
        memo[id(self)] = self
        return self

    def resolve(self, key: RuntimeAdapterKey) -> T:
        """Return the opaque value registered for exactly ``key``."""

        primitive = _primitive_key(key)
        try:
            return self._entries[primitive]
        except KeyError as exc:
            raise RuntimeAdapterRegistryError(
                "no runtime adapter is registered for the exact declaration key"
            ) from exc


__all__ = [
    "RuntimeAdapterKey",
    "RuntimeAdapterRegistryError",
    "StaticRuntimeAdapterRegistry",
]
