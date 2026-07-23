from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import worldforge.studio.assets as studio_assets
from worldforge.integrity import canonical_json_bytes, canonical_payload_hash
from worldforge.repository_boundary import FORGE_ROOT
from worldforge.scaffold import create_world_project
from worldforge.studio.assets import MAX_ASSET_INLINE_BYTES, AssetCatalogManager
from worldforge.studio.contracts import (
    EXACT_ASSET_CATALOG_METHODS,
    LEGACY_METHODS,
    METHODS,
    StudioContractError,
    validate_studio_protocol_envelope,
)
from worldforge.studio.errors import StudioError
from worldforge.studio.service import StudioService
from worldforge.studio.storage import StudioStore
from worldforge.workflow import PHASES


class StudioAssetCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.world = self.root / "world"
        self.asset_root = self.world / "assets/renderpack"
        self._install_fixture("renderpack")
        self.store = StudioStore(self.root / "studio-data")
        self.addCleanup(self.store.close)
        self.service = StudioService(self.store)
        self.service.workspaces.register(
            {
                "workspace_id": "workspace_01",
                "forge_root": str(FORGE_ROOT),
                "world_root": str(self.world),
            }
        )
        self.catalog = AssetCatalogManager(self.service.workspaces)

    def _service_call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        return self.service.handle(
            {
                "protocol": "rpg-world-forge.studio_protocol",
                "protocol_version": 1,
                "kind": "request",
                "request_id": "asset-request",
                "method": method,
                "params": params,
            }
        )["result"]

    def _install_fixture(self, pack: str) -> None:
        create_world_project(
            self.world,
            world_id="foundation_slice",
            title="Studio Assets",
            language="en",
        )
        fixture_root = FORGE_ROOT / "examples/m5-neutral" / pack
        self.asset_root = self.world / "assets" / pack
        shutil.copytree(fixture_root, self.asset_root)
        worldpack_source = FORGE_ROOT / "content/compiled/foundation.worldpack.json"
        worldpack = self.world / "content/compiled/foundation.worldpack.json"
        worldpack.parent.mkdir(parents=True)
        shutil.copyfile(worldpack_source, worldpack)

        prefix = self.asset_root.relative_to(self.world).as_posix()
        status_path = self.world / ".worldforge/status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status.update(
            {
                "completed_phases": [phase.id for phase in PHASES[:13]],
                "current_phase": PHASES[13].id,
                "revision": 13,
                "canon_locked": True,
                "worldpack_hash": json.loads(worldpack.read_text(encoding="utf-8"))["content_hash"],
                "worldpack_path": worldpack.relative_to(self.world).as_posix(),
                "asset_target": f"{prefix}/target.json",
                "visual_bible": f"{prefix}/bibles/visual.json",
                "audio_bible": f"{prefix}/bibles/audio.json",
                "asset_inventory": f"{prefix}/inventory/assets.json",
                "asset_manifest": f"{prefix}/manifest.json",
            }
        )
        status_path.write_bytes(canonical_json_bytes(status))

    def _all_entries(self) -> tuple[str, list[dict[str, object]]]:
        first = self.catalog.list("workspace_01", offset=0, limit=64)
        revision = first["manifest_revision"]
        entries = list(first["entries"])
        offset = first["next_offset"]
        while offset is not None:
            page = self.catalog.list(
                "workspace_01",
                offset=offset,
                limit=64,
                expected_manifest_revision=revision,
            )
            entries.extend(page["entries"])
            offset = page["next_offset"]
        return revision, entries

    def _entry(
        self,
        entries: list[dict[str, object]],
        category: str,
        *,
        asset_id: str | None = None,
        media_type: str | None = None,
    ) -> dict[str, object]:
        matches = [
            entry
            for entry in entries
            if entry["category"] == category
            and (asset_id is None or entry["asset_id"] == asset_id)
            and (media_type is None or entry["media_type"] == media_type)
        ]
        self.assertEqual(1, len(matches), (category, asset_id, media_type, matches))
        return matches[0]

    def test_list_is_paginated_revision_bound_and_contains_no_total_or_discovery(self) -> None:
        unreferenced = self.asset_root / "private.png"
        unreferenced.write_bytes(b"not authorized")

        first = self.catalog.list("workspace_01", offset=0, limit=2)
        self.assertEqual(0, first["offset"])
        self.assertEqual(2, first["limit"])
        self.assertEqual(2, len(first["entries"]))
        self.assertIsInstance(first["next_offset"], int)
        self.assertNotIn("total", first)
        self.assertRegex(first["manifest_revision"], r"^[0-9a-f]{64}$")
        with self.assertRaisesRegex(StudioError, "expected_manifest_revision"):
            self.catalog.list("workspace_01", offset=2, limit=2)

        second = self.catalog.list(
            "workspace_01",
            offset=2,
            limit=2,
            expected_manifest_revision=first["manifest_revision"],
        )
        self.assertEqual(2, second["offset"])
        self.assertEqual(first["manifest_revision"], second["manifest_revision"])
        revision, entries = self._all_entries()
        self.assertEqual(first["manifest_revision"], revision)
        self.assertNotIn(
            unreferenced.relative_to(self.world).as_posix(),
            {entry["path"] for entry in entries},
        )
        production_outputs = [
            entry for entry in entries if entry["category"] == "production_output"
        ]
        self.assertTrue(production_outputs)
        self.assertTrue(all(entry["selected"] for entry in production_outputs))
        self.assertNotIn("selected_candidate", {entry["category"] for entry in entries})
        self.assertNotIn(str(self.world), json.dumps(entries))
        self.assertTrue(all(str(entry["entry_id"]).startswith("asset_") for entry in entries))
        self.assertEqual(len(entries), len({entry["entry_id"] for entry in entries}))
        self.assertLess(
            len(json.dumps(self.catalog.list("workspace_01"), separators=(",", ":"))),
            1024 * 1024,
        )

    def test_ids_are_page_stable_and_inspection_rejects_crafted_or_stale_authority(self) -> None:
        revision, entries = self._all_entries()
        paged: list[dict[str, object]] = []
        offset: int | None = 0
        while offset is not None:
            page = self.catalog.list(
                "workspace_01",
                offset=offset,
                limit=3,
                expected_manifest_revision=None if offset == 0 else revision,
            )
            paged.extend(page["entries"])
            offset = page["next_offset"]
        self.assertEqual(
            [entry["entry_id"] for entry in entries],
            [entry["entry_id"] for entry in paged],
        )

        with self.assertRaises(StudioError) as raised:
            self.catalog.inspect(
                "workspace_01",
                entry_id="asset_" + ("0" * 64),
                expected_manifest_revision=revision,
            )
        self.assertEqual("not_found", raised.exception.code)

        status_path = self.world / ".worldforge/status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["revision"] += 1
        status_path.write_bytes(canonical_json_bytes(status))
        with self.assertRaises(StudioError) as raised:
            self.catalog.inspect(
                "workspace_01",
                entry_id=str(entries[0]["entry_id"]),
                expected_manifest_revision=revision,
            )
        self.assertEqual("conflict", raised.exception.code)
        new_revision, new_entries = self._all_entries()
        self.assertNotEqual(revision, new_revision)
        self.assertEqual(
            [entry["entry_id"] for entry in entries],
            [entry["entry_id"] for entry in new_entries],
        )

    def test_inspection_returns_exact_text_or_metadata_and_never_binary_bytes(self) -> None:
        revision, entries = self._all_entries()
        cases = (
            ("target", None, None, "json"),
            ("runtime_output", "neutral_fragment_shader", "text/x-glsl", "glsl"),
            ("runtime_output", "neutral_sheet", "image/png", "png"),
            ("runtime_output", "neutral_sfx", "audio/wav", "wav"),
            ("runtime_output", "neutral_font", "font/ttf", "font"),
        )
        for category, asset_id, media_type, expected_kind in cases:
            with self.subTest(category=category, asset_id=asset_id, media_type=media_type):
                entry = self._entry(
                    entries,
                    category,
                    asset_id=asset_id,
                    media_type=media_type,
                )
                result = self.catalog.inspect(
                    "workspace_01",
                    entry_id=str(entry["entry_id"]),
                    expected_manifest_revision=revision,
                )
                self.assertEqual(revision, result["manifest_revision"])
                self.assertEqual(entry, result["entry"])
                self.assertEqual(expected_kind, result["inspection"]["kind"])
                encoded = json.dumps(result, separators=(",", ":"))
                self.assertNotIn("base64", encoded)
                self.assertNotIn('"bytes"', encoded)
                self.assertNotIn(str(self.world), encoded)

        target = self._entry(entries, "target")
        inspected = self.catalog.inspect(
            "workspace_01",
            entry_id=str(target["entry_id"]),
            expected_manifest_revision=revision,
        )["inspection"]
        self.assertIsInstance(inspected["value"], dict)
        self.assertEqual((self.asset_root / "target.json").read_text(), inspected["content"])

    def test_glb_inspection_is_metadata_only(self) -> None:
        self.store.close()
        shutil.rmtree(self.world)
        self._install_fixture("assetpack")
        self.store = StudioStore(self.root / "studio-data-glb")
        self.addCleanup(self.store.close)
        self.service = StudioService(self.store)
        self.service.workspaces.register(
            {
                "workspace_id": "workspace_glb",
                "forge_root": str(FORGE_ROOT),
                "world_root": str(self.world),
            }
        )
        self.catalog = AssetCatalogManager(self.service.workspaces)

        first = self.catalog.list("workspace_glb")
        entry = self._entry(
            first["entries"],
            "runtime_output",
            asset_id="neutral_actor_3d",
            media_type="model/gltf-binary",
        )
        result = self.catalog.inspect(
            "workspace_glb",
            entry_id=str(entry["entry_id"]),
            expected_manifest_revision=first["manifest_revision"],
        )
        self.assertEqual("glb", result["inspection"]["kind"])
        self.assertGreater(result["inspection"]["byte_length"], 0)
        self.assertNotIn("payload", result["inspection"])
        self.assertNotIn("bytes", result["inspection"])

    def test_v1_recipe_is_identity_only_while_v2_recipe_is_inspectable(self) -> None:
        revision, entries = self._all_entries()
        recipe = self._entry(entries, "processing_recipe", asset_id="neutral_font")
        self.assertTrue(recipe["inspectable"])
        self.assertEqual(
            "json",
            self.catalog.inspect(
                "workspace_01",
                entry_id=str(recipe["entry_id"]),
                expected_manifest_revision=revision,
            )["inspection"]["kind"],
        )

        receipt_path = self.asset_root / "processed/neutral_font/processing.receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        recipe_ref = receipt.pop("recipe_ref")
        receipt["format_version"] = 1
        receipt["recipe"] = {
            "content_hash": recipe_ref["content_hash"],
            "sha256": recipe_ref["sha256"],
        }
        receipt["content_hash"] = canonical_payload_hash(receipt)
        receipt_path.write_bytes(canonical_json_bytes(receipt))
        manifest_path = self.asset_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        asset = next(item for item in manifest["assets"] if item["id"] == "neutral_font")
        asset["processing_receipt"]["sha256"] = hashlib.sha256(
            receipt_path.read_bytes()
        ).hexdigest()
        manifest["content_hash"] = canonical_payload_hash(manifest)
        manifest_path.write_bytes(canonical_json_bytes(manifest))

        revision, entries = self._all_entries()
        recipe = self._entry(entries, "processing_recipe", asset_id="neutral_font")
        self.assertIsNone(recipe["path"])
        self.assertFalse(recipe["inspectable"])
        self.assertEqual(
            {"kind": "unavailable", "reason": "identity_only"},
            self.catalog.inspect(
                "workspace_01",
                entry_id=str(recipe["entry_id"]),
                expected_manifest_revision=revision,
            )["inspection"],
        )

    def test_planned_assets_expose_specs_without_inventing_production_artifacts(self) -> None:
        manifest_path = self.asset_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        asset = next(item for item in manifest["assets"] if item["id"] == "neutral_font")
        asset["status"] = "planned"
        asset["production_receipts"] = []
        asset["outputs"] = []
        for field in ("selected_candidates", "processing_receipt", "license", "qa"):
            asset.pop(field)
        manifest["content_hash"] = canonical_payload_hash(manifest)
        manifest_path.write_bytes(canonical_json_bytes(manifest))

        _, entries = self._all_entries()
        font_entries = [entry for entry in entries if entry["asset_id"] == "neutral_font"]
        self.assertEqual(["specification"], [entry["category"] for entry in font_entries])

    def test_tamper_and_registered_root_replacement_fail_closed(self) -> None:
        self.catalog.list("workspace_01")
        target = self.asset_root / "target.json"
        target.write_bytes(target.read_bytes() + b" ")
        with self.assertRaises(StudioError) as raised:
            self.catalog.list("workspace_01")
        self.assertEqual("conflict", raised.exception.code)

        target.write_bytes(target.read_bytes()[:-1])
        replaced = self.root / "replaced-world"
        self.world.rename(replaced)
        shutil.copytree(replaced, self.world)
        with self.assertRaises(StudioError) as raised:
            self.catalog.list("workspace_01")
        self.assertEqual("conflict", raised.exception.code)

    def test_manifest_validation_uses_the_exact_captured_object_during_swap_restore(
        self,
    ) -> None:
        manifest_path = self.asset_root / "manifest.json"
        valid_payload = manifest_path.read_bytes()
        invalid_manifest = json.loads(valid_payload)
        invalid_manifest["unvalidated_private"] = {"secret": "must-not-escape"}
        invalid_payload = canonical_json_bytes(invalid_manifest)
        manifest_path.write_bytes(invalid_payload)

        exact_validator = studio_assets.validate_asset_manifest_object
        validated_captured_object = False

        def validate_during_swap(
            raw: dict[str, object],
            *,
            root: Path,
            profile: str,
            worldpack_path: Path,
        ):
            nonlocal validated_captured_object
            validated_captured_object = raw.get("unvalidated_private") == {
                "secret": "must-not-escape"
            }
            manifest_path.write_bytes(valid_payload)
            try:
                return exact_validator(
                    raw,
                    root=root,
                    profile=profile,
                    worldpack_path=worldpack_path,
                )
            finally:
                manifest_path.write_bytes(invalid_payload)

        with (
            mock.patch.object(
                studio_assets,
                "validate_asset_manifest_object",
                side_effect=validate_during_swap,
            ),
            self.assertRaises(StudioError) as raised,
        ):
            self.catalog.list("workspace_01")
        self.assertTrue(validated_captured_object)
        self.assertEqual("conflict", raised.exception.code)
        self.assertEqual(invalid_payload, manifest_path.read_bytes())

    def test_inline_json_cap_and_post_inspection_status_race_fail_closed(self) -> None:
        request_path = self.asset_root / "requests/neutral_font.json"
        request = json.loads(request_path.read_text(encoding="utf-8"))
        request["parameters"]["padding"] = "x" * (MAX_ASSET_INLINE_BYTES + 1)
        request["content_hash"] = canonical_payload_hash(request)
        request_path.write_bytes(canonical_json_bytes(request))

        receipt_path = self.asset_root / "receipts/neutral_font.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["request"]["sha256"] = hashlib.sha256(request_path.read_bytes()).hexdigest()
        receipt["content_hash"] = canonical_payload_hash(receipt)
        receipt_path.write_bytes(canonical_json_bytes(receipt))

        manifest_path = self.asset_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        asset = next(item for item in manifest["assets"] if item["id"] == "neutral_font")
        asset["production_receipts"][0]["sha256"] = hashlib.sha256(
            receipt_path.read_bytes()
        ).hexdigest()
        manifest["content_hash"] = canonical_payload_hash(manifest)
        manifest_path.write_bytes(canonical_json_bytes(manifest))

        revision, entries = self._all_entries()
        request_entry = self._entry(
            entries,
            "production_request",
            asset_id="neutral_font",
        )
        with self.assertRaises(StudioError) as raised:
            self.catalog.inspect(
                "workspace_01",
                entry_id=str(request_entry["entry_id"]),
                expected_manifest_revision=revision,
            )
        self.assertEqual("invalid_request", raised.exception.code)

        target_entry = self._entry(entries, "target")
        real_read = studio_assets.read_validated_resource

        def mutate_status(*args: object, **kwargs: object):
            resource = real_read(*args, **kwargs)
            status_path = self.world / ".worldforge/status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["revision"] += 1
            status_path.write_bytes(canonical_json_bytes(status))
            return resource

        with (
            mock.patch.object(
                studio_assets,
                "read_validated_resource",
                side_effect=mutate_status,
            ),
            self.assertRaises(StudioError) as raised,
        ):
            self.catalog.inspect(
                "workspace_01",
                entry_id=str(target_entry["entry_id"]),
                expected_manifest_revision=revision,
            )
        self.assertEqual("conflict", raised.exception.code)

    def test_unsupported_authorized_media_is_visible_but_never_returns_bytes(self) -> None:
        authority = self.catalog._authority("workspace_01")
        original = next(
            entry
            for entry in self.catalog._entries(authority)
            if entry.category == "runtime_output" and entry.media_type == "image/png"
        )
        unsupported = replace(
            original,
            media_type="application/octet-stream",
            inspectable=False,
        )
        with mock.patch.object(
            self.catalog,
            "_entries",
            return_value=iter((unsupported,)),
        ):
            result = self.catalog.inspect(
                "workspace_01",
                entry_id=unsupported.entry_id,
                expected_manifest_revision=authority.manifest_revision,
            )
        self.assertFalse(result["entry"]["inspectable"])
        self.assertEqual(
            {"kind": "unavailable", "reason": "unsupported_media_type"},
            result["inspection"],
        )
        self.assertNotIn("bytes", json.dumps(result))

    def test_service_exposes_only_the_two_closed_asset_catalog_methods(self) -> None:
        self.assertEqual(
            {"asset.catalog.list", "asset.catalog.inspect"},
            set(EXACT_ASSET_CATALOG_METHODS),
        )
        self.assertTrue(EXACT_ASSET_CATALOG_METHODS <= METHODS)
        self.assertTrue(EXACT_ASSET_CATALOG_METHODS.isdisjoint(LEGACY_METHODS))
        initialized = self._service_call("service.initialize", {})
        self.assertTrue(initialized["capabilities"]["asset_catalog_inspection"])
        first = self._service_call(
            "asset.catalog.list",
            {"workspace_id": "workspace_01", "limit": 2},
        )
        inspected = self._service_call(
            "asset.catalog.inspect",
            {
                "workspace_id": "workspace_01",
                "entry_id": first["entries"][0]["entry_id"],
                "expected_manifest_revision": first["manifest_revision"],
            },
        )
        self.assertEqual(first["manifest_revision"], inspected["manifest_revision"])
        with self.assertRaises(StudioError):
            self._service_call(
                "asset.catalog.list",
                {"workspace_id": "workspace_01", "path": "assets/renderpack/manifest.json"},
            )

    def test_python_protocol_closes_asset_catalog_requests_and_responses(self) -> None:
        first = self.catalog.list("workspace_01", limit=2)
        requests = (
            {
                "method": "asset.catalog.list",
                "params": {"workspace_id": "workspace_01"},
            },
            {
                "method": "asset.catalog.inspect",
                "params": {
                    "workspace_id": "workspace_01",
                    "entry_id": first["entries"][0]["entry_id"],
                    "expected_manifest_revision": first["manifest_revision"],
                },
            },
        )
        for index, item in enumerate(requests):
            envelope = {
                "protocol": "rpg-world-forge.studio_protocol",
                "protocol_version": 1,
                "kind": "request",
                "request_id": f"request-{index}",
                **item,
            }
            self.assertEqual(envelope, validate_studio_protocol_envelope(envelope))
        response = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 1,
            "kind": "response",
            "request_id": "response-1",
            "method": "asset.catalog.list",
            "result": first,
        }
        self.assertEqual(response, validate_studio_protocol_envelope(response))
        for invalid in (
            {
                **requests[0],
                "params": {"workspace_id": "workspace_01", "path": "manifest.json"},
            },
            {
                **requests[0],
                "params": {"workspace_id": "workspace_01", "offset": 1},
            },
            {
                **requests[1],
                "params": {
                    **requests[1]["params"],
                    "entry_id": "assets/renderpack/manifest.json",
                },
            },
        ):
            with self.subTest(invalid=invalid), self.assertRaises(StudioContractError):
                validate_studio_protocol_envelope(
                    {
                        "protocol": "rpg-world-forge.studio_protocol",
                        "protocol_version": 1,
                        "kind": "request",
                        "request_id": "invalid",
                        **invalid,
                    }
                )
        with self.assertRaises(StudioContractError):
            validate_studio_protocol_envelope(
                {
                    **response,
                    "result": {**first, "total": 100},
                }
            )

    def test_python_protocol_enforces_asset_paths_and_utf8_inline_limits(self) -> None:
        first = self.catalog.list("workspace_01", limit=1)
        entry = first["entries"][0]
        list_response = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 1,
            "kind": "response",
            "request_id": "asset-path",
            "method": "asset.catalog.list",
            "result": first,
        }
        for invalid_path in (
            "assets/../private.png",
            "/".join(f"part-{index}" for index in range(33)),
            "assets/cafe\u0301.png",
        ):
            with self.subTest(path=invalid_path), self.assertRaises(StudioContractError):
                validate_studio_protocol_envelope(
                    {
                        **list_response,
                        "result": {
                            **first,
                            "entries": [{**entry, "path": invalid_path}],
                        },
                    }
                )

        oversized_value = {"text": "é" * 200_000}
        inspections = (
            {
                "kind": "json",
                "encoding": "utf-8",
                "content": json.dumps(oversized_value, ensure_ascii=False),
                "value": oversized_value,
            },
            {
                "kind": "glsl",
                "encoding": "utf-8",
                "content": "é" * 200_000,
            },
        )
        for inspection in inspections:
            with (
                self.subTest(kind=inspection["kind"]),
                self.assertRaises(StudioContractError),
            ):
                validate_studio_protocol_envelope(
                    {
                        **list_response,
                        "request_id": f"asset-{inspection['kind']}",
                        "method": "asset.catalog.inspect",
                        "result": {
                            "manifest_revision": first["manifest_revision"],
                            "entry": entry,
                            "inspection": inspection,
                        },
                    }
                )


if __name__ == "__main__":
    unittest.main()
