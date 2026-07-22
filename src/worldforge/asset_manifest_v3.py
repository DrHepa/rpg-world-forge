from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from isoworld.content.media import media_signature_matches
from worldforge.asset_contracts import (
    ASSET_KINDS,
    EXECUTORS,
    KIND_REPRESENTATIONS,
    OUTPUT_ROLE_MEDIA,
    ROUTES,
    THREE_D_ASSET_KINDS,
    ContractIssue,
    _issue,
    runtime_output_contract_issue,
    validate_asset_bibles,
    validate_asset_license_record,
    validate_asset_qa_report,
    validate_asset_spec,
    validate_asset_target,
)
from worldforge.asset_inventory import (
    create_asset_target,
    validate_asset_inventory,
)
from worldforge.asset_io import (
    AssetContractError,
    artifact_reference,
    bind_content_hash,
    prepare_output_path,
    read_json_object,
    require_content_hash,
    resolve_artifact,
    sha256_file,
    verify_artifact_reference,
    write_json_atomic,
)
from worldforge.asset_production import validate_production_receipt
from worldforge.validation import ID_PATTERN, PLACEHOLDER_PATTERN

ASSET_PHASES = {"art_direction", "production", "release"}
ASSET_STATUSES = {"planned", "generated", "approved", "processed"}
THREE_D_KINDS = THREE_D_ASSET_KINDS
OUTPUT_ROLES = set(OUTPUT_ROLE_MEDIA)
ROLE_MEDIA_TYPES = OUTPUT_ROLE_MEDIA


def _walk_strings(value: Any, path: str):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_strings(child, f"{path}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_strings(child, f"{path}/{index}")


def _field_issues(value: dict[str, Any], expected: set[str], context: str) -> list[ContractIssue]:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    issues: list[ContractIssue] = []
    if missing:
        issues.append(_issue(context, f"missing fields: {', '.join(missing)}"))
    if unknown:
        issues.append(_issue(context, f"unknown fields: {', '.join(unknown)}"))
    return issues


def init_asset_manifest_v3(
    worldpack_path: str | Path,
    output_path: str | Path,
    *,
    target_id: str,
    dimension: str,
    enable_modly: bool = False,
) -> dict[str, Any]:
    if not isinstance(enable_modly, bool):
        raise AssetContractError("enable_modly must be an explicit boolean")
    output = prepare_output_path(output_path)
    try:
        output.lstat()
    except FileNotFoundError:
        pass
    else:
        raise AssetContractError(f"The asset manifest already exists: {output}")
    for directory in (
        "bibles",
        "generated",
        "inventory",
        "licenses",
        "processed",
        "qa",
        "receipts",
        "recipes",
        "references",
        "requests",
        "specs",
        "work",
    ):
        prepare_output_path(output.parent / directory / ".directory-probe")
    target_path = output.parent / "target.json"
    target = create_asset_target(
        worldpack_path,
        target_path,
        target_id=target_id,
        dimension=dimension,
    )
    executors = ["human", "openai_image", "procedural"]
    if dimension == "3d":
        executors.insert(0, "blender_mcp")
    enabled_routes = ["openai"]
    if enable_modly:
        enabled_routes.insert(0, "modly")
        executors.append("modly_cli_mcp")
    manifest = bind_content_hash(
        {
            "format": "rpg-world-forge.asset_manifest",
            "format_version": 3,
            "world_id": target["world_id"],
            "world_content_hash": target["world_content_hash"],
            "target": artifact_reference(output.parent, "target.json"),
            "phase": "art_direction",
            "generation_policy": {
                "orchestrator": "gpt",
                "enabled_routes": enabled_routes,
                "local_model_route": "modly",
                "executors": sorted(executors),
            },
            "bibles": {"visual": None, "audio": None},
            "inventory": None,
            "assets": [],
            "bindings": [],
        }
    )
    write_json_atomic(output, manifest)
    return manifest


def _relative_inside(root: Path, path: str | Path) -> str:
    candidate = Path(path).resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise AssetContractError(f"Artifact must live under {root}: {candidate}") from exc
    if not relative.parts:
        raise AssetContractError("Artifact reference cannot point to the asset root")
    # resolve_artifact performs portability/link/count checks.
    resolve_artifact(root, relative.as_posix())
    return relative.as_posix()


def bind_asset_plan(
    manifest_path: str | Path,
    *,
    visual_bible_path: str | Path,
    audio_bible_path: str | Path,
    inventory_path: str | Path,
    expected_manifest_hash: str,
) -> dict[str, Any]:
    """Bind approved P11/P12 contracts and exact specs using optimistic locking."""

    manifest_file = Path(manifest_path)
    root = manifest_file.parent.resolve()
    manifest = read_json_object(manifest_file)
    if (
        manifest.get("format") != "rpg-world-forge.asset_manifest"
        or manifest.get("format_version") != 3
    ):
        raise AssetContractError("bind_asset_plan requires asset manifest version 3")
    require_content_hash(manifest, context="asset manifest")
    if manifest.get("content_hash") != expected_manifest_hash:
        raise AssetContractError("Asset manifest changed; reload before binding the plan")
    target_path = verify_artifact_reference(root, manifest.get("target"), context="target")
    visual_relative = _relative_inside(root, visual_bible_path)
    audio_relative = _relative_inside(root, audio_bible_path)
    inventory_relative = _relative_inside(root, inventory_path)
    bible_issues = validate_asset_bibles(
        root / visual_relative,
        root / audio_relative,
        target_path,
    )
    if bible_issues:
        raise AssetContractError("; ".join(str(issue) for issue in bible_issues))
    inventory_issues = validate_asset_inventory(root / inventory_relative)
    if inventory_issues:
        raise AssetContractError("; ".join(inventory_issues))
    target = read_json_object(target_path)
    inventory = read_json_object(root / inventory_relative)
    if inventory.get("world_id") != manifest.get("world_id") or inventory.get(
        "world_content_hash"
    ) != manifest.get("world_content_hash"):
        raise AssetContractError("Inventory does not match the asset manifest world")
    if inventory.get("target_id") != target.get("id") or inventory.get("target_hash") != target.get(
        "content_hash"
    ):
        raise AssetContractError("Inventory does not match the asset target")
    visual = read_json_object(root / visual_relative)
    audio = read_json_object(root / audio_relative)
    if inventory.get("visual_bible_hash") != visual.get("content_hash"):
        raise AssetContractError("Inventory is stale for the approved visual bible")
    if inventory.get("audio_bible_hash") != audio.get("content_hash"):
        raise AssetContractError("Inventory is stale for the approved audio bible")
    requirements = [*inventory["requirements"], *inventory.get("manual_additions", [])]
    assets: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    for requirement in sorted(requirements, key=lambda item: item["id"]):
        specification_file = f"specs/{requirement['id']}.json"
        specification = resolve_artifact(root, specification_file)
        assert specification is not None
        spec_issues = validate_asset_spec(
            specification,
            expected_id=requirement["id"],
            expected_kind=requirement["kind"],
            target_hash=target["content_hash"],
        )
        if spec_issues:
            raise AssetContractError("; ".join(str(issue) for issue in spec_issues))
        spec = read_json_object(specification)
        if spec.get("representation") != requirement.get("representation"):
            raise AssetContractError(
                f"Specification {requirement['id']} representation disagrees with inventory"
            )
        lineage = {
            "inventory_hash": inventory["content_hash"],
            "visual_bible_hash": visual["content_hash"],
            "audio_bible_hash": audio["content_hash"],
        }
        for field, expected in lineage.items():
            if spec.get(field) != expected:
                raise AssetContractError(f"Specification {requirement['id']} {field} is stale")
        if spec.get("purpose") != requirement.get("purpose"):
            raise AssetContractError(
                f"Specification {requirement['id']} purpose disagrees with inventory"
            )
        if spec.get("canonical_sources") != requirement.get("canonical_sources"):
            raise AssetContractError(
                f"Specification {requirement['id']} canonical sources disagree with inventory"
            )
        if spec.get("semantic_slots") != requirement.get("semantic_slots"):
            raise AssetContractError(
                f"Specification {requirement['id']} semantic slots disagree with inventory"
            )
        assets.append(
            {
                "id": requirement["id"],
                "kind": requirement["kind"],
                "representation": requirement["representation"],
                "required": requirement["required"],
                "status": "planned",
                "specification": artifact_reference(root, specification_file),
                "production_receipts": [],
                "outputs": [],
            }
        )
        bindings.extend(
            {
                "slot": slot,
                "asset_id": requirement["id"],
                "representation": requirement["representation"],
            }
            for slot in requirement["semantic_slots"]
        )
    updated = dict(manifest)
    updated.update(
        {
            "phase": "production",
            "bibles": {
                "visual": artifact_reference(root, visual_relative),
                "audio": artifact_reference(root, audio_relative),
            },
            "inventory": artifact_reference(root, inventory_relative),
            "assets": assets,
            "bindings": sorted(bindings, key=lambda item: item["slot"]),
        }
    )
    updated = bind_content_hash(updated)
    write_json_atomic(
        manifest_file,
        updated,
        overwrite=True,
        expected_content_hash=expected_manifest_hash,
    )
    return updated


def finalize_asset_release(
    manifest_path: str | Path,
    deliverable_path: str | Path,
    worldpack_path: str | Path,
    *,
    expected_manifest_hash: str,
) -> dict[str, Any]:
    """Seal a built renderpack/assetpack into manifest v3 with optimistic locking."""

    manifest_file = Path(manifest_path)
    root = manifest_file.parent.resolve()
    manifest = read_json_object(manifest_file)
    require_content_hash(manifest, context="asset manifest")
    if (
        manifest.get("format") != "rpg-world-forge.asset_manifest"
        or manifest.get("format_version") != 3
    ):
        raise AssetContractError("finalize_asset_release requires asset manifest version 3")
    if manifest.get("content_hash") != expected_manifest_hash:
        raise AssetContractError("Asset manifest changed; reload before finalizing the release")
    build_issues = validate_asset_manifest_v3(
        manifest_file,
        profile="build",
        worldpack_path=worldpack_path,
    )
    if build_issues:
        raise AssetContractError("; ".join(str(issue) for issue in build_issues))

    target_file = verify_artifact_reference(root, manifest.get("target"), context="target")
    target = read_json_object(target_file)
    deliverable_relative = _relative_inside(root, deliverable_path)
    deliverable_file = resolve_artifact(root, deliverable_relative)
    assert deliverable_file is not None
    if target.get("delivery_profile") == "assetpack_v1":
        from worldforge.assetpack import verify_assetpack

        payload = verify_assetpack(deliverable_file, worldpack_path)
        expected_format = "rpg-world-forge.assetpack"
    elif target.get("delivery_profile") == "renderpack_v1":
        from isoworld.content.loader import load_worldpack
        from isoworld.content.renderpack import load_renderpack

        with load_renderpack(deliverable_file, load_worldpack(worldpack_path)) as loaded:
            loaded_world_id = loaded.world_id
        payload = read_json_object(deliverable_file, limit=64 * 1024 * 1024)
        if loaded_world_id != manifest.get("world_id"):
            raise AssetContractError("Renderpack world does not match the asset manifest")
        expected_format = "isoworld.renderpack"
    else:
        raise AssetContractError("Asset target has an unsupported delivery profile")
    if payload.get("format") != expected_format:
        raise AssetContractError("Deliverable format does not match the asset target")

    updated = dict(manifest)
    updated["phase"] = "release"
    updated["deliverable"] = {
        "format": expected_format,
        "file": deliverable_relative,
        "sha256": sha256_file(deliverable_file),
        "content_hash": payload["content_hash"],
    }
    updated = bind_content_hash(updated)

    descriptor, candidate_name = tempfile.mkstemp(
        prefix=f".{manifest_file.name}.release-",
        suffix=".json",
        dir=root,
    )
    os.close(descriptor)
    candidate = Path(candidate_name)
    candidate.unlink()
    try:
        write_json_atomic(candidate, updated)
        release_issues = validate_asset_manifest_v3(
            candidate,
            profile="release",
            worldpack_path=worldpack_path,
        )
        if release_issues:
            raise AssetContractError("; ".join(str(issue) for issue in release_issues))
    finally:
        candidate.unlink(missing_ok=True)
    write_json_atomic(
        manifest_file,
        updated,
        overwrite=True,
        expected_content_hash=expected_manifest_hash,
    )
    return updated


def _validate_output(
    root: Path,
    asset: dict[str, Any],
    output: object,
    *,
    index: int,
    technical: dict[str, Any],
) -> list[ContractIssue]:
    context = f"outputs/{index}"
    if not isinstance(output, dict):
        return [_issue(context, "must be an object")]
    issues: list[ContractIssue] = []
    allowed_fields = {"role", "runtime_file", "sha256", "media_type", "size"}
    missing = sorted({"role", "runtime_file", "sha256", "media_type"} - set(output))
    unknown = sorted(set(output) - allowed_fields)
    if missing:
        issues.append(_issue(context, f"missing fields: {', '.join(missing)}"))
    if unknown:
        issues.append(_issue(context, f"unknown fields: {', '.join(unknown)}"))
    role = output.get("role")
    media_type = output.get("media_type")
    if not isinstance(role, str) or role not in OUTPUT_ROLES:
        issues.append(_issue(f"{context}/role", "unknown output role"))
    elif not isinstance(media_type, str) or media_type not in ROLE_MEDIA_TYPES[role]:
        issues.append(_issue(f"{context}/media_type", "is incompatible with the output role"))
    runtime_file = output.get("runtime_file")
    reference = {
        "file": runtime_file,
        "sha256": output.get("sha256"),
    }
    try:
        path = verify_artifact_reference(root, reference, context=context)
    except AssetContractError as exc:
        issues.append(_issue(context, str(exc)))
        path = None
    if path is not None and "size" in output:
        size = output.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size != path.stat().st_size:
            issues.append(_issue(f"{context}/size", "does not match the runtime file"))
    if path is not None and isinstance(media_type, str):
        if media_type == "model/gltf-binary":
            try:
                from worldforge.asset_formats.gltf import inspect_glb

                inspect_glb(path, allow_external_uris=False)
            except ValueError as exc:
                issues.append(_issue(f"{context}/media_type", f"invalid GLB: {exc}"))
        elif not media_signature_matches(path, media_type):
            issues.append(_issue(f"{context}/media_type", "does not match runtime file bytes"))
    if (
        asset.get("representation") == "3d"
        and isinstance(role, str)
        and role in {"model", "collision", "animation", "skeleton"}
    ):
        if media_type != "model/gltf-binary":
            issues.append(_issue(f"{context}/media_type", "3d runtime geometry must be GLB"))
        if isinstance(runtime_file, str) and runtime_file.casefold().endswith(".blend"):
            issues.append(_issue(f"{context}/runtime_file", ".blend is authoring evidence only"))
    if role == "texture" and media_type == "image/png" and path is not None:
        from worldforge.assets import _png_dimensions

        expected = (technical.get("width"), technical.get("height"))
        dimensions = _png_dimensions(path)
        if all(isinstance(value, int) for value in expected) and dimensions != expected:
            issues.append(
                _issue(
                    f"{context}/runtime_file",
                    f"PNG dimensions {dimensions} do not match {expected}",
                )
            )
    if role == "audio" and media_type == "audio/wav" and path is not None:
        import wave

        try:
            with wave.open(str(path), "rb") as source:
                actual_audio = (source.getframerate(), source.getnchannels())
        except (EOFError, OSError, wave.Error) as exc:
            issues.append(_issue(f"{context}/runtime_file", f"invalid WAV: {exc}"))
        else:
            expected_audio = (technical.get("sample_rate"), technical.get("channels"))
            if actual_audio != expected_audio:
                issues.append(
                    _issue(
                        f"{context}/runtime_file",
                        f"WAV properties {actual_audio} do not match {expected_audio}",
                    )
                )
    return issues


def _binding_compatible(slot: str, kind: str, representation: str) -> bool:
    category = slot.split(":", 1)[0]
    if representation == "3d":
        if category == "actor":
            return kind == "character_3d"
        if category in {"tile_type", "construction"}:
            return kind in {"environment_3d", "model_3d"}
        if category == "ability":
            return kind in {"vfx_3d", "animation_3d", "model_3d"}
    if category == "event":
        return kind == "sfx"
    if category == "music":
        return kind == "music"
    if category == "ui" and slot == "ui:font":
        return kind == "font"
    if category == "portrait":
        return kind in {"portrait", "sprite"}
    if category == "scene":
        return kind in {"portrait", "sprite", "ui"}
    if category == "ability":
        return kind in {"vfx", "sprite", "spritesheet"}
    if category == "actor":
        return kind in {"sprite", "spritesheet"}
    if category == "tile_type":
        return kind in {"sprite", "spritesheet", "tileset"}
    if category == "construction":
        return kind in {"sprite", "spritesheet", "tileset"}
    return False


def _receipt_lineage_has_cycle(receipt_parents: dict[str, list[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(receipt_hash: str) -> bool:
        if receipt_hash in visited:
            return False
        if receipt_hash in visiting:
            return True
        visiting.add(receipt_hash)
        if any(
            parent in receipt_parents and visit(parent)
            for parent in receipt_parents.get(receipt_hash, [])
        ):
            return True
        visiting.remove(receipt_hash)
        visited.add(receipt_hash)
        return False

    return any(visit(receipt_hash) for receipt_hash in sorted(receipt_parents))


def validate_asset_manifest_v3(
    manifest_path: str | Path,
    *,
    profile: str,
    worldpack_path: str | Path | None,
) -> list[ContractIssue]:
    if profile not in {"draft", "build", "release"}:
        return [_issue("profile", "must be draft, build, or release")]
    path = Path(manifest_path)
    root = path.parent.resolve()
    try:
        raw = read_json_object(path)
    except AssetContractError as exc:
        return [_issue("manifest", str(exc))]
    issues: list[ContractIssue] = []
    release_like = profile in {"build", "release"}
    if raw.get("format") != "rpg-world-forge.asset_manifest" or raw.get("format_version") != 3:
        return [_issue("format", "not an asset manifest v3")]
    manifest_fields = {
        "format",
        "format_version",
        "world_id",
        "world_content_hash",
        "target",
        "phase",
        "generation_policy",
        "bibles",
        "inventory",
        "assets",
        "bindings",
        "content_hash",
    }
    if raw.get("phase") == "release":
        manifest_fields.add("deliverable")
    issues.extend(_field_issues(raw, manifest_fields, "manifest"))
    try:
        require_content_hash(raw, context="asset manifest")
    except AssetContractError as exc:
        issues.append(_issue("content_hash", str(exc)))
    phase = raw.get("phase")
    if not isinstance(phase, str) or phase not in ASSET_PHASES:
        issues.append(_issue("phase", "unknown asset-production phase"))
    if profile == "build" and phase != "production":
        issues.append(_issue("phase", "build validation requires the production phase"))
    if profile == "release" and phase != "release":
        issues.append(_issue("phase", "release validation requires the release phase"))
    try:
        target_path = verify_artifact_reference(root, raw.get("target"), context="target")
    except AssetContractError as exc:
        issues.append(_issue("target", str(exc)))
        target: dict[str, Any] = {}
    else:
        issues.extend(
            _issue(f"target/{item.path}", item.message)
            for item in validate_asset_target(target_path)
        )
        target = read_json_object(target_path)
        if target.get("world_id") != raw.get("world_id"):
            issues.append(_issue("target/world_id", "does not match the manifest"))
        if target.get("world_content_hash") != raw.get("world_content_hash"):
            issues.append(_issue("target/world_content_hash", "does not match the manifest"))
    policy = raw.get("generation_policy")
    enabled_routes: set[str] = set()
    enabled_executors: set[str] = set()
    if not isinstance(policy, dict):
        issues.append(_issue("generation_policy", "must be an object"))
    else:
        issues.extend(
            _field_issues(
                policy,
                {"orchestrator", "enabled_routes", "local_model_route", "executors"},
                "generation_policy",
            )
        )
        if policy.get("orchestrator") != "gpt":
            issues.append(_issue("generation_policy/orchestrator", "must remain gpt"))
        routes = policy.get("enabled_routes")
        executors = policy.get("executors")
        routes_valid = (
            isinstance(routes, list)
            and bool(routes)
            and all(isinstance(route, str) for route in routes)
            and routes == sorted(set(routes))
            and all(route in ROUTES for route in routes)
        )
        if not routes_valid:
            issues.append(_issue("generation_policy/enabled_routes", "invalid routes"))
        else:
            enabled_routes = set(routes)
        executors_valid = (
            isinstance(executors, list)
            and bool(executors)
            and all(isinstance(executor, str) for executor in executors)
            and executors == sorted(set(executors))
            and all(executor in EXECUTORS for executor in executors)
        )
        if not executors_valid:
            issues.append(_issue("generation_policy/executors", "invalid executors"))
        else:
            enabled_executors = set(executors)
        if policy.get("local_model_route") != "modly":
            issues.append(_issue("generation_policy/local_model_route", "must be modly"))
        if "modly_cli_mcp" in enabled_executors and "modly" not in enabled_routes:
            issues.append(_issue("generation_policy", "modly_cli_mcp requires the modly route"))
    bibles = raw.get("bibles")
    inventory_reference = raw.get("inventory")
    inventory: dict[str, Any] | None = None
    visual_contract: dict[str, Any] | None = None
    audio_contract: dict[str, Any] | None = None
    if phase == "art_direction" and profile == "draft":
        if bibles not in ({"visual": None, "audio": None}, None) and not isinstance(bibles, dict):
            issues.append(_issue("bibles", "must contain visual/audio references or nulls"))
    else:
        if not isinstance(bibles, dict):
            issues.append(_issue("bibles", "approved visual/audio bibles are required"))
        else:
            try:
                visual_path = verify_artifact_reference(
                    root, bibles.get("visual"), context="bibles/visual"
                )
                audio_path = verify_artifact_reference(
                    root, bibles.get("audio"), context="bibles/audio"
                )
            except AssetContractError as exc:
                issues.append(_issue("bibles", str(exc)))
            else:
                visual_contract = read_json_object(visual_path)
                audio_contract = read_json_object(audio_path)
                if target:
                    issues.extend(validate_asset_bibles(visual_path, audio_path, target_path))
        try:
            inventory_path = verify_artifact_reference(
                root, inventory_reference, context="inventory"
            )
        except AssetContractError as exc:
            issues.append(_issue("inventory", str(exc)))
        else:
            inventory_issues = validate_asset_inventory(
                inventory_path,
                worldpack_path=worldpack_path,
            )
            issues.extend(_issue("inventory", message) for message in inventory_issues)
            inventory = read_json_object(inventory_path)
            if target and (
                inventory.get("target_id") != target.get("id")
                or inventory.get("target_hash") != target.get("content_hash")
            ):
                issues.append(_issue("inventory", "does not match the target"))
            if visual_contract is not None and inventory.get(
                "visual_bible_hash"
            ) != visual_contract.get("content_hash"):
                issues.append(_issue("inventory", "is stale for the approved visual bible"))
            if audio_contract is not None and inventory.get(
                "audio_bible_hash"
            ) != audio_contract.get("content_hash"):
                issues.append(_issue("inventory", "is stale for the approved audio bible"))
    assets = raw.get("assets")
    if not isinstance(assets, list):
        return [*issues, _issue("assets", "must be a list")]
    if release_like and not assets:
        issues.append(_issue("assets", "a release must contain assets"))
    seen_ids: set[str] = set()
    assets_by_id: dict[str, dict[str, Any]] = {}
    specs_by_id: dict[str, dict[str, Any]] = {}
    for index, asset in enumerate(assets):
        context = f"assets/{index}"
        if not isinstance(asset, dict):
            issues.append(_issue(context, "must be an object"))
            continue
        asset_fields = {
            "id",
            "kind",
            "representation",
            "required",
            "status",
            "specification",
            "production_receipts",
            "outputs",
        }
        if isinstance(asset.get("status"), str) and asset.get("status") in {
            "approved",
            "processed",
        }:
            asset_fields.add("selected_candidates")
        if asset.get("status") == "processed":
            asset_fields.update({"processing_receipt", "license", "qa"})
        issues.extend(_field_issues(asset, asset_fields, context))
        asset_id = asset.get("id")
        if not isinstance(asset_id, str) or not ID_PATTERN.fullmatch(asset_id):
            issues.append(_issue(f"{context}/id", "invalid ID"))
        elif asset_id in seen_ids:
            issues.append(_issue(f"{context}/id", "duplicate ID"))
        else:
            seen_ids.add(asset_id)
            assets_by_id[asset_id] = asset
        kind = asset.get("kind")
        representation = asset.get("representation")
        status = asset.get("status")
        if not isinstance(kind, str) or kind not in ASSET_KINDS:
            issues.append(_issue(f"{context}/kind", "unknown asset kind"))
        if not isinstance(representation, str) or representation not in {
            "2d",
            "2_5d",
            "3d",
            "audio",
        }:
            issues.append(_issue(f"{context}/representation", "unknown representation"))
        elif isinstance(kind, str) and kind in KIND_REPRESENTATIONS:
            if representation not in KIND_REPRESENTATIONS[kind]:
                allowed = ", ".join(sorted(KIND_REPRESENTATIONS[kind]))
                issues.append(
                    _issue(
                        f"{context}/representation",
                        f"{kind} assets require {allowed} representation",
                    )
                )
        if not isinstance(asset.get("required"), bool):
            issues.append(_issue(f"{context}/required", "must be boolean"))
        if not isinstance(status, str) or status not in ASSET_STATUSES:
            issues.append(_issue(f"{context}/status", "unknown status"))
        if release_like and asset.get("required", True) and status != "processed":
            issues.append(_issue(f"{context}/status", "required release assets must be processed"))
        technical: dict[str, Any] = {}
        spec: dict[str, Any] = {}
        try:
            specification_path = verify_artifact_reference(
                root,
                asset.get("specification"),
                context=f"{context}/specification",
            )
        except AssetContractError as exc:
            issues.append(_issue(f"{context}/specification", str(exc)))
        else:
            spec_issues = validate_asset_spec(
                specification_path,
                expected_id=asset_id if isinstance(asset_id, str) else None,
                expected_kind=kind if isinstance(kind, str) else None,
                target_hash=target.get("content_hash") if target else None,
            )
            issues.extend(
                _issue(f"{context}/specification/{item.path}", item.message) for item in spec_issues
            )
            spec = read_json_object(specification_path)
            if isinstance(asset_id, str):
                specs_by_id[asset_id] = spec
            technical = spec.get("technical") if isinstance(spec.get("technical"), dict) else {}
            if spec.get("representation") != representation:
                issues.append(
                    _issue(f"{context}/representation", "does not match the specification")
                )
            if inventory is not None:
                expected_lineage = {
                    "inventory_hash": inventory.get("content_hash"),
                    "visual_bible_hash": inventory.get("visual_bible_hash"),
                    "audio_bible_hash": inventory.get("audio_bible_hash"),
                }
                for field, expected in expected_lineage.items():
                    if spec.get(field) != expected:
                        issues.append(
                            _issue(
                                f"{context}/specification/{field}",
                                "is stale for the bound asset plan",
                            )
                        )
        receipts = asset.get("production_receipts")
        if not isinstance(receipts, list):
            issues.append(_issue(f"{context}/production_receipts", "must be a list"))
            receipts = []
        if (
            isinstance(status, str)
            and status in {"generated", "approved", "processed"}
            and not receipts
        ):
            issues.append(
                _issue(f"{context}/production_receipts", "production lineage is required")
            )
        receipt_parents: dict[str, list[str]] = {}
        production_candidate_keys: set[tuple[str, str]] = set()
        for receipt_index, reference in enumerate(receipts):
            try:
                receipt_path = verify_artifact_reference(
                    root,
                    reference,
                    context=f"{context}/production_receipts/{receipt_index}",
                )
            except AssetContractError as exc:
                issues.append(_issue(f"{context}/production_receipts/{receipt_index}", str(exc)))
                continue
            receipt_issues = validate_production_receipt(receipt_path, asset_root=root)
            issues.extend(
                _issue(f"{context}/production_receipts/{receipt_index}/{item.path}", item.message)
                for item in receipt_issues
            )
            receipt = read_json_object(receipt_path)
            receipt_hash = receipt.get("content_hash")
            if isinstance(receipt_hash, str):
                if receipt_hash in receipt_parents:
                    issues.append(
                        _issue(
                            f"{context}/production_receipts/{receipt_index}",
                            "duplicate receipt content hash",
                        )
                    )
                receipt_parents[receipt_hash] = [
                    value
                    for value in receipt.get("parent_receipt_hashes", [])
                    if isinstance(value, str)
                ]
            for output in receipt.get("outputs", []):
                if not isinstance(output, dict):
                    continue
                file_value = output.get("file")
                hash_value = output.get("sha256")
                if isinstance(file_value, str) and isinstance(hash_value, str):
                    production_candidate_keys.add((file_value, hash_value))
            if receipt.get("asset_id") != asset_id:
                issues.append(
                    _issue(f"{context}/production_receipts/{receipt_index}", "asset ID mismatch")
                )
            if (
                not isinstance(receipt.get("route"), str)
                or receipt.get("route") not in enabled_routes
            ):
                issues.append(
                    _issue(f"{context}/production_receipts/{receipt_index}", "route is disabled")
                )
            if (
                not isinstance(receipt.get("executor"), str)
                or receipt.get("executor") not in enabled_executors
            ):
                issues.append(
                    _issue(f"{context}/production_receipts/{receipt_index}", "executor is disabled")
                )
        for receipt_hash, parents in receipt_parents.items():
            for parent in parents:
                if parent not in receipt_parents:
                    issues.append(
                        _issue(
                            f"{context}/production_receipts",
                            f"receipt {receipt_hash} has unknown parent {parent}",
                        )
                    )

        if _receipt_lineage_has_cycle(receipt_parents):
            issues.append(
                _issue(
                    f"{context}/production_receipts",
                    "receipt lineage has a cycle",
                )
            )
        if isinstance(status, str) and status in {"approved", "processed"}:
            selected_candidates = asset.get("selected_candidates")
            if not isinstance(selected_candidates, list) or not selected_candidates:
                issues.append(_issue(f"{context}/selected_candidates", "must be a non-empty list"))
                selected_candidates = []
            selected_keys: list[tuple[str, str]] = []
            for selected_index, selected in enumerate(selected_candidates):
                selected_context = f"{context}/selected_candidates/{selected_index}"
                try:
                    verify_artifact_reference(
                        root,
                        selected,
                        context=selected_context,
                        allowed_extra=frozenset({"approved_by"}),
                    )
                except AssetContractError as exc:
                    issues.append(_issue(selected_context, str(exc)))
                if (
                    not isinstance(selected, dict)
                    or not isinstance(selected.get("approved_by"), str)
                    or not selected["approved_by"].strip()
                ):
                    issues.append(_issue(f"{selected_context}/approved_by", "is required"))
                    continue
                file_value = selected.get("file")
                hash_value = selected.get("sha256")
                if (
                    not isinstance(file_value, str)
                    or not isinstance(hash_value, str)
                    or (file_value, hash_value) not in production_candidate_keys
                ):
                    issues.append(
                        _issue(
                            selected_context,
                            "must be an exact output of a bound production receipt",
                        )
                    )
                    continue
                selected_keys.append((file_value, hash_value))
            if selected_keys != sorted(set(selected_keys)):
                issues.append(
                    _issue(
                        f"{context}/selected_candidates",
                        "must be sorted and unique by file and SHA-256",
                    )
                )
        processing_input_keys: set[tuple[str, str]] = set()
        processing_output_hashes: set[str] = set()
        if status == "processed":
            declared_output_hashes = {
                output.get("sha256")
                for output in asset.get("outputs", [])
                if isinstance(output, dict) and isinstance(output.get("sha256"), str)
            }
            processing = asset.get("processing_receipt")
            try:
                processing_path = verify_artifact_reference(
                    root,
                    processing,
                    context=f"{context}/processing_receipt",
                )
            except AssetContractError as exc:
                issues.append(_issue(f"{context}/processing_receipt", str(exc)))
            else:
                try:
                    processing_raw = read_json_object(processing_path)
                    require_content_hash(processing_raw, context="processing receipt")
                    from worldforge.asset_processing import verify_processing_receipt

                    verify_processing_receipt(processing_path)
                except AssetContractError as exc:
                    issues.append(_issue(f"{context}/processing_receipt", str(exc)))
                else:
                    if processing_raw.get("format") != "rpg-world-forge.asset_processing_receipt":
                        issues.append(_issue(f"{context}/processing_receipt", "unknown format"))
                    processing_input_keys = {
                        (item["artifact"]["file"], item["artifact"]["sha256"])
                        for item in processing_raw.get("inputs", [])
                        if isinstance(item, dict)
                        and isinstance(item.get("artifact"), dict)
                        and isinstance(item["artifact"].get("file"), str)
                        and isinstance(item["artifact"].get("sha256"), str)
                    }
                    processing_output_hashes = {
                        item["artifact"]["sha256"]
                        for item in processing_raw.get("outputs", [])
                        if isinstance(item, dict)
                        and isinstance(item.get("artifact"), dict)
                        and isinstance(item["artifact"].get("sha256"), str)
                    }
            try:
                license_path = verify_artifact_reference(
                    root,
                    asset.get("license"),
                    context=f"{context}/license",
                )
            except AssetContractError as exc:
                issues.append(_issue(f"{context}/license", str(exc)))
            else:
                issues.extend(
                    _issue(f"{context}/license/{item.path}", item.message)
                    for item in validate_asset_license_record(
                        license_path,
                        root=root,
                        expected_asset_id=asset_id if isinstance(asset_id, str) else None,
                        expected_output_hashes=declared_output_hashes,
                    )
                )
            try:
                qa_path = verify_artifact_reference(root, asset.get("qa"), context=f"{context}/qa")
            except AssetContractError as exc:
                issues.append(_issue(f"{context}/qa", str(exc)))
            else:
                issues.extend(
                    _issue(f"{context}/qa/{item.path}", item.message)
                    for item in validate_asset_qa_report(
                        qa_path,
                        root=root,
                        expected_asset_id=asset_id if isinstance(asset_id, str) else None,
                        expected_target_hash=target.get("content_hash") if target else None,
                        expected_output_hashes=declared_output_hashes,
                        expected_checks={
                            criterion
                            for criterion in spec.get("acceptance_criteria", [])
                            if isinstance(criterion, str)
                        },
                    )
                )
        outputs = asset.get("outputs")
        if not isinstance(outputs, list):
            issues.append(_issue(f"{context}/outputs", "must be a list"))
            outputs = []
        if status == "processed" and not outputs:
            issues.append(_issue(f"{context}/outputs", "processed assets require outputs"))
        roles: list[str] = []
        for output_index, output in enumerate(outputs):
            output_issues = _validate_output(
                root,
                asset,
                output,
                index=output_index,
                technical=technical,
            )
            issues.extend(_issue(f"{context}/{item.path}", item.message) for item in output_issues)
            if isinstance(output, dict) and isinstance(output.get("role"), str):
                roles.append(output["role"])
        if status == "processed":
            total_output_bytes = 0
            for output_index, output in enumerate(outputs):
                if not isinstance(output, dict):
                    continue
                try:
                    output_path = verify_artifact_reference(
                        root,
                        {
                            "file": output.get("runtime_file"),
                            "sha256": output.get("sha256"),
                        },
                        context=f"{context}/outputs/{output_index}",
                    )
                except AssetContractError:
                    # _validate_output already reports the precise artifact issue.
                    continue
                total_output_bytes += output_path.stat().st_size
            memory_budget = technical.get("memory_budget_bytes")
            if (
                isinstance(memory_budget, int)
                and not isinstance(memory_budget, bool)
                and total_output_bytes > memory_budget
            ):
                issues.append(
                    _issue(
                        f"{context}/outputs",
                        (
                            f"use {total_output_bytes} bytes and exceed the "
                            f"{memory_budget}-byte budget"
                        ),
                    )
                )
            expected_pairs = sorted(
                (item["role"], item["media_type"])
                for item in spec.get("expected_outputs", [])
                if isinstance(item, dict)
                and isinstance(item.get("role"), str)
                and isinstance(item.get("media_type"), str)
            )
            actual_pairs = sorted(
                (item["role"], item["media_type"])
                for item in outputs
                if isinstance(item, dict)
                and isinstance(item.get("role"), str)
                and isinstance(item.get("media_type"), str)
            )
            if actual_pairs != expected_pairs:
                issues.append(_issue(f"{context}/outputs", "do not match specification outputs"))
            role_issue = runtime_output_contract_issue(kind, representation, roles)
            if role_issue is not None:
                issues.append(_issue(f"{context}/outputs", role_issue))
            selected_candidate_keys = {
                (selected["file"], selected["sha256"])
                for selected in asset.get("selected_candidates", [])
                if isinstance(selected, dict)
                and isinstance(selected.get("file"), str)
                and isinstance(selected.get("sha256"), str)
            }
            if processing_input_keys != selected_candidate_keys:
                issues.append(
                    _issue(
                        f"{context}/processing_receipt",
                        "must consume every approved candidate and no unapproved candidate",
                    )
                )
            if not processing_input_keys <= production_candidate_keys:
                issues.append(
                    _issue(
                        f"{context}/processing_receipt",
                        "contains inputs absent from production receipt outputs",
                    )
                )
            manifest_output_hashes = {
                output["sha256"]
                for output in outputs
                if isinstance(output, dict) and isinstance(output.get("sha256"), str)
            }
            if manifest_output_hashes != processing_output_hashes:
                issues.append(
                    _issue(
                        f"{context}/processing_receipt",
                        "processed output hashes do not match the manifest",
                    )
                )
        for value_path, value in _walk_strings(asset, context):
            if PLACEHOLDER_PATTERN.search(value):
                issues.append(_issue(value_path, "unresolved placeholder"))
    if inventory is not None:
        entries = [*inventory.get("requirements", []), *inventory.get("manual_additions", [])]
        inventory_by_id = {
            item["id"]: item for item in entries if isinstance(item, dict) and "id" in item
        }
        for requirement in inventory.get("requirements", []):
            if not isinstance(requirement, dict):
                continue
            if requirement.get("required") is True and requirement.get("id") not in assets_by_id:
                issues.append(
                    _issue("assets", f"missing required inventory asset {requirement.get('id')}")
                )
        for asset_id, asset in assets_by_id.items():
            item = inventory_by_id.get(asset_id)
            if item is None:
                issues.append(_issue("assets", f"asset {asset_id} is absent from inventory"))
                continue
            if asset.get("required") != item.get("required"):
                issues.append(_issue(f"assets/{asset_id}/required", "does not match inventory"))
            spec = specs_by_id.get(asset_id, {})
            for field in ("purpose", "canonical_sources", "semantic_slots"):
                if spec.get(field) != item.get(field):
                    issues.append(
                        _issue(
                            f"assets/{asset_id}/specification/{field}",
                            "does not match inventory",
                        )
                    )
    bindings = raw.get("bindings")
    if not isinstance(bindings, list):
        issues.append(_issue("bindings", "must be a list"))
    else:
        seen_slots: set[str] = set()
        bound_slots: set[str] = set()
        for index, binding in enumerate(bindings):
            context = f"bindings/{index}"
            if not isinstance(binding, dict):
                issues.append(_issue(context, "must be an object"))
                continue
            binding_fields = {"slot", "asset_id", "representation"}
            if binding.get("representation") == "3d":
                if release_like or "presentation" in binding:
                    binding_fields.add("presentation")
            else:
                binding_fields.update({"clip", "moving_clip", "scale", "layer"} & set(binding))
            issues.extend(_field_issues(binding, binding_fields, context))
            slot = binding.get("slot")
            asset_id = binding.get("asset_id")
            if not isinstance(slot, str) or not slot or slot in seen_slots:
                issues.append(_issue(f"{context}/slot", "must be a unique semantic slot"))
                continue
            seen_slots.add(slot)
            bound_slots.add(slot)
            asset = assets_by_id.get(asset_id) if isinstance(asset_id, str) else None
            if asset is None:
                issues.append(_issue(f"{context}/asset_id", "unknown asset"))
                continue
            if binding.get("representation") != asset.get("representation"):
                issues.append(_issue(f"{context}/representation", "does not match the asset"))
            if (
                not isinstance(asset.get("kind"), str)
                or not isinstance(asset.get("representation"), str)
                or not _binding_compatible(slot, asset["kind"], asset["representation"])
            ):
                issues.append(_issue(f"{context}/asset_id", "asset kind is incompatible"))
            if (
                release_like
                and isinstance(asset.get("representation"), str)
                and asset.get("representation") in {"2d", "2_5d"}
                and slot.split(":", 1)[0]
                in {
                    "actor",
                    "tile_type",
                    "construction",
                }
                and not binding.get("clip")
            ):
                issues.append(_issue(f"{context}/clip", "2d release binding requires a clip"))
        if inventory is not None and release_like:
            required_slots = {
                slot
                for item in inventory.get("requirements", [])
                if isinstance(item, dict) and item.get("required") is True
                for slot in item.get("semantic_slots", [])
                if isinstance(slot, str)
            }
            for slot in sorted(required_slots - bound_slots):
                issues.append(_issue("bindings", f"release is missing required slot {slot}"))
    deliverable = raw.get("deliverable")
    if profile == "build" and deliverable is not None:
        issues.append(_issue("deliverable", "must be absent until the build is hash-bound"))
    if profile == "release":
        if not isinstance(deliverable, dict):
            issues.append(_issue("deliverable", "a hash-bound runtime deliverable is required"))
        else:
            expected_format = (
                "rpg-world-forge.assetpack"
                if target.get("delivery_profile") == "assetpack_v1"
                else "isoworld.renderpack"
            )
            if deliverable.get("format") != expected_format:
                issues.append(_issue("deliverable/format", "does not match the asset target"))
            try:
                deliverable_path = verify_artifact_reference(
                    root,
                    deliverable,
                    context="deliverable",
                    allowed_extra=frozenset({"format", "content_hash"}),
                )
                deliverable_raw = read_json_object(deliverable_path, limit=64 * 1024 * 1024)
                require_content_hash(deliverable_raw, context="asset deliverable")
            except AssetContractError as exc:
                issues.append(_issue("deliverable", str(exc)))
            else:
                if deliverable_raw.get("format") != deliverable.get("format"):
                    issues.append(_issue("deliverable/format", "does not match the file"))
                if deliverable_raw.get("content_hash") != deliverable.get("content_hash"):
                    issues.append(_issue("deliverable/content_hash", "does not match the file"))
                if deliverable_raw.get("world_id") != raw.get("world_id"):
                    issues.append(_issue("deliverable/world_id", "does not match the manifest"))
                if deliverable_raw.get("world_content_hash") != raw.get("world_content_hash"):
                    issues.append(
                        _issue("deliverable/world_content_hash", "does not match the manifest")
                    )
                try:
                    if deliverable.get("format") == "rpg-world-forge.assetpack":
                        from worldforge.assetpack import verify_assetpack

                        verify_assetpack(deliverable_path, worldpack_path)
                    elif deliverable.get("format") == "isoworld.renderpack":
                        from isoworld.content.loader import load_worldpack
                        from isoworld.content.renderpack import load_renderpack

                        if worldpack_path is None:
                            raise AssetContractError(
                                "worldpack is required to verify a release renderpack"
                            )
                        with load_renderpack(
                            deliverable_path,
                            load_worldpack(worldpack_path),
                        ):
                            pass
                except (AssetContractError, ValueError) as exc:
                    issues.append(_issue("deliverable", f"runtime verification failed: {exc}"))
    if worldpack_path is not None:
        try:
            from worldforge.assets import _verified_worldpack

            worldpack = _verified_worldpack(Path(worldpack_path))
        except ValueError as exc:
            issues.append(_issue("worldpack", str(exc)))
        else:
            if worldpack["world"]["id"] != raw.get("world_id"):
                issues.append(_issue("world_id", "does not match the worldpack"))
            if worldpack["content_hash"] != raw.get("world_content_hash"):
                issues.append(
                    _issue("world_content_hash", "canon changed; restart or migrate the asset plan")
                )
    return issues
