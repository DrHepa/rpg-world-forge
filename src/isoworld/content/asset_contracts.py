from __future__ import annotations

from dataclasses import dataclass

REPRESENTATIONS = frozenset({"2d", "2_5d", "3d", "audio"})
ASSET_KINDS = frozenset(
    {
        "animation_3d",
        "character_3d",
        "collision_3d",
        "environment_3d",
        "font",
        "material_set",
        "model_3d",
        "music",
        "portrait",
        "rig",
        "sfx",
        "shader",
        "sprite",
        "spritesheet",
        "tileset",
        "ui",
        "vfx",
        "vfx_3d",
    }
)
AUDIO_ASSET_KINDS = frozenset({"music", "sfx"})
THREE_D_ASSET_KINDS = frozenset(
    {
        "animation_3d",
        "character_3d",
        "collision_3d",
        "environment_3d",
        "material_set",
        "model_3d",
        "rig",
        "vfx_3d",
    }
)
TWO_D_ASSET_KINDS = ASSET_KINDS - AUDIO_ASSET_KINDS - THREE_D_ASSET_KINDS
KIND_REPRESENTATIONS = {
    **{kind: frozenset({"audio"}) for kind in AUDIO_ASSET_KINDS},
    **{kind: frozenset({"3d"}) for kind in THREE_D_ASSET_KINDS},
    **{kind: frozenset({"2d", "2_5d"}) for kind in TWO_D_ASSET_KINDS},
}
OUTPUT_ROLE_MEDIA = {
    "animation": frozenset({"model/gltf-binary"}),
    "audio": frozenset({"audio/mpeg", "audio/ogg", "audio/wav"}),
    "clipset": frozenset({"application/json"}),
    "collision": frozenset({"model/gltf-binary"}),
    "font": frozenset({"font/otf", "font/ttf"}),
    "fragment_shader": frozenset({"text/x-glsl"}),
    "material_metadata": frozenset({"application/json"}),
    "model": frozenset({"model/gltf-binary"}),
    "model_metadata": frozenset({"application/json"}),
    "preview": frozenset({"image/jpeg", "image/png", "image/webp"}),
    "skeleton": frozenset({"model/gltf-binary"}),
    "texture": frozenset({"image/jpeg", "image/png", "image/webp"}),
    "vertex_shader": frozenset({"text/x-glsl"}),
}
GLB_OUTPUT_ROLES = frozenset({"animation", "collision", "model", "skeleton"})


@dataclass(frozen=True, slots=True)
class AssetRuntimeOutputContract:
    representations: frozenset[str]
    required_roles: frozenset[str]
    allowed_roles: frozenset[str]
    min_outputs: int
    max_outputs: int


def _runtime_contract(
    kind: str,
    required_roles: set[str],
    allowed_roles: set[str] | None = None,
    *,
    min_outputs: int | None = None,
) -> AssetRuntimeOutputContract:
    allowed = frozenset(allowed_roles or required_roles)
    required = frozenset(required_roles)
    return AssetRuntimeOutputContract(
        representations=KIND_REPRESENTATIONS[kind],
        required_roles=required,
        allowed_roles=allowed,
        min_outputs=len(required) if min_outputs is None else min_outputs,
        max_outputs=len(allowed),
    )


ASSET_RUNTIME_OUTPUT_CONTRACTS = {
    **{kind: _runtime_contract(kind, {"audio"}) for kind in AUDIO_ASSET_KINDS},
    "font": _runtime_contract("font", {"font"}),
    "shader": _runtime_contract(
        "shader",
        set(),
        {"fragment_shader", "vertex_shader"},
        min_outputs=1,
    ),
    **{kind: _runtime_contract(kind, {"texture"}) for kind in {"portrait", "sprite", "ui", "vfx"}},
    **{
        kind: _runtime_contract(kind, {"clipset", "texture"}) for kind in {"spritesheet", "tileset"}
    },
    **{
        kind: _runtime_contract(
            kind,
            {
                {
                    "animation_3d": "animation",
                    "collision_3d": "collision",
                    "rig": "skeleton",
                }.get(kind, "model")
            },
            set(GLB_OUTPUT_ROLES),
        )
        for kind in THREE_D_ASSET_KINDS
    },
}


def runtime_output_contract_issue(
    kind: object,
    representation: object,
    roles: list[str],
) -> str | None:
    if not isinstance(kind, str) or kind not in ASSET_RUNTIME_OUTPUT_CONTRACTS:
        return "asset kind has no runtime output contract"
    contract = ASSET_RUNTIME_OUTPUT_CONTRACTS[kind]
    if not isinstance(representation, str) or representation not in contract.representations:
        allowed = ", ".join(sorted(contract.representations))
        return f"{kind} requires representation in: {allowed}"
    if not contract.min_outputs <= len(roles) <= contract.max_outputs:
        return (
            f"{kind} requires between {contract.min_outputs} and "
            f"{contract.max_outputs} runtime outputs"
        )
    if len(roles) != len(set(roles)):
        return f"{kind} runtime output roles must be unique"
    unknown = set(roles) - contract.allowed_roles
    if unknown:
        return f"{kind} has forbidden runtime output roles: {', '.join(sorted(unknown))}"
    missing = contract.required_roles - set(roles)
    if missing:
        return f"{kind} is missing runtime output roles: {', '.join(sorted(missing))}"
    return None


__all__ = [
    "ASSET_KINDS",
    "ASSET_RUNTIME_OUTPUT_CONTRACTS",
    "AUDIO_ASSET_KINDS",
    "GLB_OUTPUT_ROLES",
    "KIND_REPRESENTATIONS",
    "OUTPUT_ROLE_MEDIA",
    "REPRESENTATIONS",
    "THREE_D_ASSET_KINDS",
    "TWO_D_ASSET_KINDS",
    "AssetRuntimeOutputContract",
    "runtime_output_contract_issue",
]
