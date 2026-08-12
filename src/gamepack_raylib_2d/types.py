"""Small immutable value types shared by the bounded raylib 2D adapters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SemanticIntent:
    kind: str
    value: int | None = None
    authoritative: bool = True

    def __post_init__(self) -> None:
        if type(self.kind) is not str or not self.kind:
            raise ValueError("intent kind must be a non-empty exact string")
        if self.value is not None and type(self.value) is not int:
            raise ValueError("intent value must be an exact integer or null")
        if type(self.authoritative) is not bool:
            raise ValueError("intent authoritative flag must be an exact boolean")


@dataclass(frozen=True, slots=True)
class InputFrame:
    keys_pressed: tuple[str, ...] = ()
    pointer_pressed: bool = False
    pointer_x: float = 0.0
    pointer_y: float = 0.0

    def __post_init__(self) -> None:
        if (
            type(self.keys_pressed) is not tuple
            or any(type(key) is not str or not key for key in self.keys_pressed)
            or len(set(self.keys_pressed)) != len(self.keys_pressed)
        ):
            raise ValueError("keys_pressed must be a unique exact string tuple")
        if type(self.pointer_pressed) is not bool:
            raise ValueError("pointer_pressed must be an exact boolean")
        if type(self.pointer_x) not in {int, float} or type(self.pointer_y) not in {
            int,
            float,
        }:
            raise ValueError("pointer coordinates must be finite numbers")
        if not (-1_000_000.0 <= float(self.pointer_x) <= 1_000_000.0) or not (
            -1_000_000.0 <= float(self.pointer_y) <= 1_000_000.0
        ):
            raise ValueError("pointer coordinates are outside the bounded viewport domain")


@dataclass(frozen=True, slots=True)
class TextureHandle:
    identity: str
    width: int
    height: int
    native: object | None = None


@dataclass(frozen=True, slots=True)
class FontHandle:
    identity: str
    size: int
    native: object | None = None


__all__ = [
    "FontHandle",
    "InputFrame",
    "SemanticIntent",
    "TextureHandle",
]
