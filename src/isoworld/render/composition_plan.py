"""Pure deterministic ordering for composed presentation slots."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

_WORLD_PLANES = {"world_base": 0, "world_overlay": 1, "ui_overlay": 2}


class CompositionPlanError(ValueError):
    """Raised when a composition slot plan is ambiguous."""


@dataclass(frozen=True, slots=True)
class CompositionDraw:
    plane: str
    representation: str
    slot: str
    asset_id: str
    pack: str


@dataclass(frozen=True, slots=True)
class CompositionAudio:
    slot: str
    asset_id: str
    pack: str


@dataclass(frozen=True, slots=True)
class CompositionPlan:
    draws: tuple[CompositionDraw, ...]
    audio: tuple[CompositionAudio, ...]


@dataclass(frozen=True, slots=True)
class PackSlotBinding:
    """One binding proven by an integrally loaded content-pack snapshot."""

    pack: str
    slot: str
    asset_id: str
    kind: str
    representation: str | None


def validate_composition_slot_ownership(
    profile_layers: Iterable[str],
    slot_owners: Iterable[Mapping[str, object]],
    pack_bindings: Iterable[PackSlotBinding],
) -> None:
    """Correlate composition owners with exact bindings from verified packs."""

    layers = tuple(profile_layers)
    owners = tuple(slot_owners)
    bindings = tuple(pack_bindings)
    bindings_by_source: dict[tuple[str, str], PackSlotBinding] = {}
    bindings_by_slot: dict[str, list[PackSlotBinding]] = {}
    for binding in bindings:
        key = (binding.pack, binding.slot)
        if key in bindings_by_source:
            raise CompositionPlanError(
                f"pack contains a duplicated slot binding: {binding.pack}/{binding.slot}"
            )
        bindings_by_source[key] = binding
        bindings_by_slot.setdefault(binding.slot, []).append(binding)

    owner_slots: set[str] = set()
    represented_world_planes: set[tuple[str, str]] = set()
    for index, owner in enumerate(owners):
        try:
            pack = owner["pack"]
            slot = owner["slot"]
            asset_id = owner["asset_id"]
            representation = owner["representation"]
            plane = owner["plane"]
        except KeyError as exc:
            raise CompositionPlanError(f"slot owner {index} is incomplete") from exc
        values = (pack, slot, asset_id, representation, plane)
        if any(type(value) is not str or not value for value in values):
            raise CompositionPlanError(f"slot owner {index} has invalid fields")
        pack = str(pack)
        slot = str(slot)
        asset_id = str(asset_id)
        representation = str(representation)
        plane = str(plane)
        if slot in owner_slots:
            raise CompositionPlanError(f"composition slot owner is duplicated: {slot}")
        owner_slots.add(slot)

        binding = bindings_by_source.get((pack, slot))
        if binding is None:
            alternate_sources = bindings_by_slot.get(slot, ())
            if alternate_sources:
                sources = ", ".join(sorted(item.pack for item in alternate_sources))
                raise CompositionPlanError(
                    f"composition slot owner selects {pack}/{slot}, but the exact "
                    f"binding belongs to: {sources}"
                )
            raise CompositionPlanError(
                f"composition slot owner has no exact pack binding: {pack}/{slot}"
            )
        if binding.asset_id != asset_id:
            raise CompositionPlanError(f"composition slot owner asset disagrees with {pack}/{slot}")
        if binding.representation is not None:
            if binding.representation != representation:
                raise CompositionPlanError(
                    f"composition slot owner representation disagrees with {pack}/{slot}"
                )
        elif (binding.kind in {"music", "sfx"}) != (representation == "audio"):
            raise CompositionPlanError(
                f"composition slot owner representation disagrees with "
                f"{pack}/{slot} kind {binding.kind}"
            )
        if plane in {"world_base", "world_overlay"}:
            represented_world_planes.add((plane, representation))

    expected_world_planes = tuple(
        (("world_base", "world_overlay")[index], layer) for index, layer in enumerate(layers[:2])
    )
    missing = [
        f"{plane}:{representation}"
        for plane, representation in expected_world_planes
        if (plane, representation) not in represented_world_planes
    ]
    if missing:
        raise CompositionPlanError(
            "composition is missing required world slot owners: " + ", ".join(missing)
        )


def build_composition_plan(
    profile_layers: Iterable[str],
    slot_owners: Iterable[Mapping[str, object]],
) -> CompositionPlan:
    """Return an immutable plan without importing a renderer or mutating state."""

    layers = tuple(profile_layers)
    if len(layers) != len(set(layers)):
        raise CompositionPlanError("profile layers must be unique")
    layer_order = {layer: index for index, layer in enumerate(layers)}
    draws: list[CompositionDraw] = []
    audio: list[CompositionAudio] = []
    seen: set[tuple[str, str]] = set()
    for index, owner in enumerate(slot_owners):
        try:
            plane = owner["plane"]
            representation = owner["representation"]
            slot = owner["slot"]
            asset_id = owner["asset_id"]
            pack = owner["pack"]
        except KeyError as exc:
            raise CompositionPlanError(f"slot owner {index} is incomplete") from exc
        values = (plane, representation, slot, asset_id, pack)
        if any(type(value) is not str or not value for value in values):
            raise CompositionPlanError(f"slot owner {index} has invalid fields")
        key = (str(plane), str(slot))
        if key in seen:
            raise CompositionPlanError(f"slot owner is duplicated: {key!r}")
        seen.add(key)
        if plane == "audio":
            if representation != "audio":
                raise CompositionPlanError("audio slots require the audio representation")
            audio.append(CompositionAudio(str(slot), str(asset_id), str(pack)))
            continue
        if plane not in _WORLD_PLANES:
            raise CompositionPlanError(f"unsupported composition plane: {plane}")
        if representation not in layer_order:
            raise CompositionPlanError(
                f"slot representation is absent from profile layers: {representation}"
            )
        draws.append(
            CompositionDraw(
                str(plane),
                str(representation),
                str(slot),
                str(asset_id),
                str(pack),
            )
        )
    draws.sort(
        key=lambda item: (
            _WORLD_PLANES[item.plane],
            layer_order[item.representation],
            item.slot,
            item.asset_id,
        )
    )
    audio.sort(key=lambda item: (item.slot, item.asset_id))
    return CompositionPlan(tuple(draws), tuple(audio))


__all__ = [
    "CompositionAudio",
    "CompositionDraw",
    "CompositionPlan",
    "CompositionPlanError",
    "PackSlotBinding",
    "build_composition_plan",
    "validate_composition_slot_ownership",
]
