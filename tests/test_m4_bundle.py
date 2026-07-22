from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from isoworld.content.loader import load_worldpack
from worldforge.bundle import (
    BUNDLE_FORMAT,
    CATALOG_FORMAT,
    BundleError,
    export_runtime_bundle,
    import_runtime_bundle,
    verify_runtime_bundle,
)
from worldforge.compiler import build_worldpack
from worldforge.game_scaffold import create_game_project
from worldforge.integrity import canonical_payload_hash
from worldforge.project import load_source_project
from worldforge.scaffold import create_world_project

ROOT = Path(__file__).resolve().parents[1]
COMPILED = ROOT / "content/compiled/foundation.worldpack.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(22050)
        target.writeframes(b"\x00\x00" * 64)


def _write_worldpack(root: Path, world_id: str = "modly_foundation") -> Path:
    raw = json.loads(COMPILED.read_text(encoding="utf-8"))
    raw["world"]["id"] = world_id
    raw["content_hash"] = canonical_payload_hash(raw)
    path = root / f"{world_id}.worldpack.json"
    path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_renderpack(root: Path, worldpack_path: Path) -> Path:
    pack = load_worldpack(worldpack_path)
    audio = root / "mutable-build/provider-shaped-name.wav"
    _write_wav(audio)
    raw = {
        "format": "isoworld.renderpack",
        "format_version": 1,
        "world_id": pack.world_id,
        "world_content_hash": pack.content_hash,
        "assets": [
            {
                "id": "neutral_sfx",
                "kind": "sfx",
                "files": [
                    {
                        "role": "audio",
                        "path": "mutable-build/provider-shaped-name.wav",
                        "sha256": _sha256(audio),
                        "media_type": "audio/wav",
                    }
                ],
            }
        ],
        "bindings": [],
    }
    raw["content_hash"] = canonical_payload_hash(raw)
    path = root / "renderpack.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def _fixture(root: Path, world_id: str = "modly_foundation") -> tuple[Path, Path, Path]:
    source = root / "source"
    source.mkdir(parents=True)
    worldpack = _write_worldpack(source, world_id)
    renderpack = _write_renderpack(source, worldpack)
    licenses = root / "runtime-licenses"
    licenses.mkdir()
    (licenses / "CONTENT-LICENSE.txt").write_text("Fixture content: CC0-1.0\n", encoding="utf-8")
    return worldpack, renderpack, licenses


def _export(root: Path, name: str, release_id: str = "1.0.0", world_id: str = "modly_foundation"):
    worldpack, renderpack, licenses = _fixture(root / f"input-{name}", world_id)
    return export_runtime_bundle(
        worldpack,
        renderpack,
        root / name,
        release_id=release_id,
        licenses_directory=licenses,
    )


def _rewrite_manifest(bundle: Path, mutate) -> dict:
    path = bundle / "bundle.manifest.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    mutate(raw)
    raw["bundle_hash"] = canonical_payload_hash(raw, hash_field="bundle_hash")
    path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return raw


def _game(root: Path, name: str = "game") -> Path:
    game = root / name
    create_game_project(game, game_id=name.replace("-", "_"), title=name.title())
    return game


class _ImportVerified:
    def __init__(self, close_error: BaseException | None = None) -> None:
        self.close_error = close_error
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class RuntimeBundleTests(unittest.TestCase):
    def test_import_runtime_bundle_preserves_body_error_and_still_closes(self) -> None:
        verified = _ImportVerified()
        primary = BundleError("import body failed")

        with (
            patch("worldforge.bundle.verify_runtime_bundle", return_value=verified),
            patch(
                "worldforge.bundle._import_runtime_bundle_from_verified",
                side_effect=primary,
            ),
            self.assertRaises(BundleError) as caught,
        ):
            import_runtime_bundle(
                "bundle",
                "game",
                expected_bundle_hash="0" * 64,
            )

        self.assertIs(primary, caught.exception)
        self.assertEqual(1, verified.close_calls)

    def test_import_runtime_bundle_surfaces_close_only_failure(self) -> None:
        cleanup = RuntimeError("verified bundle close failed")
        verified = _ImportVerified(cleanup)

        with (
            patch("worldforge.bundle.verify_runtime_bundle", return_value=verified),
            patch(
                "worldforge.bundle._import_runtime_bundle_from_verified",
                return_value=Path("installed"),
            ),
            self.assertRaises(RuntimeError) as caught,
        ):
            import_runtime_bundle(
                "bundle",
                "game",
                expected_bundle_hash="0" * 64,
            )

        self.assertIs(cleanup, caught.exception)
        self.assertEqual(1, verified.close_calls)

    def test_import_runtime_bundle_keeps_body_primary_when_close_also_fails(self) -> None:
        primary = BundleError("import body failed")
        cleanup = RuntimeError("verified bundle close failed")
        verified = _ImportVerified(cleanup)

        with (
            patch("worldforge.bundle.verify_runtime_bundle", return_value=verified),
            patch(
                "worldforge.bundle._import_runtime_bundle_from_verified",
                side_effect=primary,
            ),
            self.assertRaises(BundleError) as caught,
        ):
            import_runtime_bundle(
                "bundle",
                "game",
                expected_bundle_hash="0" * 64,
            )

        self.assertIs(primary, caught.exception)
        self.assertIs(cleanup, caught.exception.__cause__)
        self.assertTrue(
            any("verified bundle close failed" in note for note in caught.exception.__notes__)
        )
        self.assertEqual(1, verified.close_calls)

    def test_bundle_export_and_import_require_independent_repository_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worldpack, renderpack, licenses = _fixture(root / "fixture")
            game = _game(root)
            world = root / "world"
            create_world_project(
                world,
                world_id="boundary_world",
                title="Boundary World",
                language="en",
            )

            forbidden_targets = (
                ROOT / "m4_forbidden_bundle_target",
                game / "nested-bundle",
                world / "nested-bundle",
            )
            for target in forbidden_targets:
                self.assertFalse(target.exists())
                with self.subTest(target=target):
                    with self.assertRaisesRegex(BundleError, "outside the Forge|nested"):
                        export_runtime_bundle(
                            worldpack,
                            renderpack,
                            target,
                            release_id="1.0.0",
                            licenses_directory=licenses,
                        )
                self.assertFalse(target.exists())

            bundle = export_runtime_bundle(
                worldpack,
                renderpack,
                root / "external-bundle",
                release_id="1.0.0",
                licenses_directory=licenses,
            )
            nested_world_bundle = world / "copied-bundle"
            shutil.copytree(bundle.root, nested_world_bundle)
            with self.assertRaisesRegex(BundleError, "nested inside a world repository"):
                import_runtime_bundle(
                    nested_world_bundle,
                    game,
                    expected_bundle_hash=bundle.bundle_hash,
                )
            nested_game_bundle = game / "copied-bundle"
            shutil.copytree(bundle.root, nested_game_bundle)
            with self.assertRaisesRegex(BundleError, "nested inside a game repository"):
                import_runtime_bundle(
                    nested_game_bundle,
                    game,
                    expected_bundle_hash=bundle.bundle_hash,
                )
            shutil.rmtree(nested_game_bundle)

            arbitrary = root / "arbitrary"
            arbitrary.mkdir()
            for invalid_game in (arbitrary, world, ROOT):
                with self.subTest(invalid_game=invalid_game):
                    with self.assertRaisesRegex(BundleError, "recognizable standalone game"):
                        import_runtime_bundle(
                            bundle.root,
                            invalid_game,
                            expected_bundle_hash=bundle.bundle_hash,
                        )

    def test_export_is_runtime_only_reproducible_and_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _export(root, "bundle-a")
            second = _export(root, "bundle-b")

            self.assertEqual(first.bundle_hash, second.bundle_hash)
            self.assertEqual(BUNDLE_FORMAT, first.manifest["format"])
            self.assertEqual("modly_foundation", first.world_id)
            self.assertEqual("1.0.0", first.release_id)
            paths = [item["path"] for item in first.manifest["files"]]
            self.assertEqual(sorted(paths), paths)
            self.assertEqual(
                {
                    "worldpack.json",
                    "renderpack.json",
                    "assets/neutral_sfx/00_audio.wav",
                    "licenses/CONTENT-LICENSE.txt",
                },
                set(paths),
            )
            self.assertEqual(
                [item for item in first.manifest["files"] if item["path"].startswith("licenses/")],
                first.manifest["licenses"],
            )
            renderpack = json.loads((first.root / "renderpack.json").read_text(encoding="utf-8"))
            self.assertEqual(
                "assets/neutral_sfx/00_audio.wav",
                renderpack["assets"][0]["files"][0]["path"],
            )
            serialized = json.dumps(first.manifest) + json.dumps(renderpack)
            self.assertNotIn("mutable-build", serialized)
            self.assertNotIn("provider-shaped-name", serialized)
            self.assertEqual(first.bundle_hash, verify_runtime_bundle(first.root).bundle_hash)
            with self.assertRaisesRegex(BundleError, "expected immutable hash"):
                verify_runtime_bundle(first.root, expected_bundle_hash="0" * 64)

    def test_export_returns_exact_renderpack_with_live_snapshot_after_stage_publish(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot_parent = root / "snapshot-temp"
            snapshot_parent.mkdir()
            with patch.object(tempfile, "tempdir", str(snapshot_parent)):
                verified = _export(root, "published-bundle")
                returned_renderpack = verified.renderpack
                snapshot_root = returned_renderpack.root

                self.assertEqual(root / "published-bundle", verified.root)
                self.assertTrue(snapshot_root.is_dir())
                self.assertEqual([snapshot_root], list(snapshot_parent.iterdir()))
                for asset in returned_renderpack.assets:
                    for item in asset.files:
                        resolved = returned_renderpack.resolve_file(item)
                        self.assertTrue(resolved.is_file())
                        self.assertEqual(item.sha256, _sha256(resolved))

                verified.close()
                self.assertFalse(snapshot_root.exists())
                self.assertEqual([], list(snapshot_parent.iterdir()))
                self.assertTrue((verified.root / "renderpack.json").is_file())

    def test_export_uses_v5_runtime_requirements_as_bundle_features(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            payload = build_worldpack(
                load_source_project(ROOT / "examples/foundation/source/manifest.json")
            )
            worldpack = source / "v5.worldpack.json"
            worldpack.write_text(json.dumps(payload), encoding="utf-8")
            renderpack = _write_renderpack(source, worldpack)
            licenses = root / "licenses"
            licenses.mkdir()
            (licenses / "LICENSE.txt").write_text("CC0-1.0\n", encoding="utf-8")

            bundle = export_runtime_bundle(
                worldpack,
                renderpack,
                root / "bundle-v5",
                release_id="1.0.0",
                licenses_directory=licenses,
            )

            self.assertEqual(5, bundle.worldpack.format_version)
            self.assertEqual(
                sorted(payload["runtime_requirements"]["required_features"]),
                bundle.manifest["required_runtime_features"],
            )

    def test_export_rejects_mutable_release_authoring_metadata_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worldpack, renderpack, licenses = _fixture(root)
            with self.assertRaisesRegex(BundleError, "immutable"):
                export_runtime_bundle(
                    worldpack,
                    renderpack,
                    root / "latest-bundle",
                    release_id="latest",
                    licenses_directory=licenses,
                )
            for invalid in ("1.0", "01.0.0", "1.0.0-dev"):
                with self.subTest(release_id=invalid):
                    with self.assertRaisesRegex(BundleError, "MAJOR.MINOR.PATCH"):
                        export_runtime_bundle(
                            worldpack,
                            renderpack,
                            root / f"invalid-{invalid}",
                            release_id=invalid,
                            licenses_directory=licenses,
                        )

            raw = json.loads(renderpack.read_text(encoding="utf-8"))
            raw["provider"] = "not-runtime-safe"
            raw["content_hash"] = canonical_payload_hash(raw)
            renderpack.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(BundleError, "provider metadata"):
                export_runtime_bundle(
                    worldpack,
                    renderpack,
                    root / "provider-bundle",
                    release_id="1.0.0",
                    licenses_directory=licenses,
                )

            raw.pop("provider")
            raw["content_hash"] = canonical_payload_hash(raw)
            renderpack.write_text(json.dumps(raw), encoding="utf-8")
            (licenses / "unsafe-link.txt").symlink_to(licenses / "CONTENT-LICENSE.txt")
            with self.assertRaisesRegex(BundleError, "symlink"):
                export_runtime_bundle(
                    worldpack,
                    renderpack,
                    root / "symlink-bundle",
                    release_id="1.0.0",
                    licenses_directory=licenses,
                )

    def test_license_payloads_are_text_only_and_reserved_controls_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worldpack, renderpack, licenses = _fixture(root / "fixture")
            binary = licenses / "NOTICE.dll"
            binary.write_bytes(b"MZ\x00")
            with self.assertRaisesRegex(BundleError, "license.*extension"):
                export_runtime_bundle(
                    worldpack,
                    renderpack,
                    root / "binary-license",
                    release_id="1.0.0",
                    licenses_directory=licenses,
                )
            binary.unlink()
            reserved = licenses / "AGENTS.md"
            reserved.write_text("agent instructions\n", encoding="utf-8")
            with self.assertRaisesRegex(BundleError, "authoring-only path"):
                export_runtime_bundle(
                    worldpack,
                    renderpack,
                    root / "reserved-license",
                    release_id="1.0.0",
                    licenses_directory=licenses,
                )
            reserved.unlink()

            bundle = export_runtime_bundle(
                worldpack,
                renderpack,
                root / "valid-bundle",
                release_id="1.0.0",
                licenses_directory=licenses,
            )
            malicious = bundle.root / "licenses/NOTICE.dll"
            malicious.write_bytes(b"MZ\x00")

            def reseal(manifest: dict) -> None:
                record = {
                    "path": "licenses/NOTICE.dll",
                    "sha256": _sha256(malicious),
                    "size": malicious.stat().st_size,
                    "media_type": "application/octet-stream",
                }
                manifest["files"].append(record)
                manifest["files"].sort(key=lambda item: item["path"])
                manifest["licenses"].append(record)
                manifest["licenses"].sort(key=lambda item: item["path"])

            _rewrite_manifest(bundle.root, reseal)
            with self.assertRaisesRegex(BundleError, "approved license notice"):
                verify_runtime_bundle(bundle.root)

    def test_strict_verifier_rejects_tampering_extras_paths_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tampered = _export(root, "tampered")
            asset = tampered.root / "assets/neutral_sfx/00_audio.wav"
            asset.write_bytes(asset.read_bytes() + b"tamper")
            with self.assertRaisesRegex(BundleError, "size mismatch"):
                verify_runtime_bundle(tampered.root)

            extra = _export(root, "extra")
            (extra.root / "unexpected.txt").write_text("extra", encoding="utf-8")
            with self.assertRaisesRegex(BundleError, "tree mismatch"):
                verify_runtime_bundle(extra.root)

            traversal = _export(root, "traversal")
            _rewrite_manifest(
                traversal.root,
                lambda raw: raw["files"][0].update({"path": "assets/../escape.wav"}),
            )
            with self.assertRaisesRegex(BundleError, "normalized contained"):
                verify_runtime_bundle(traversal.root)

            linked = _export(root, "linked")
            target = linked.root / "licenses/CONTENT-LICENSE.txt"
            target.unlink()
            target.symlink_to(linked.root / "worldpack.json")
            with self.assertRaisesRegex(BundleError, "symlink"):
                verify_runtime_bundle(linked.root)

            prefix_collision = _export(root, "prefix-collision")

            def collide_prefixes(manifest: dict) -> None:
                records = [
                    {
                        "path": "licenses/Foo/a.txt",
                        "sha256": "0" * 64,
                        "size": 1,
                        "media_type": "text/plain",
                    },
                    {
                        "path": "licenses/foo/b.txt",
                        "sha256": "1" * 64,
                        "size": 1,
                        "media_type": "text/plain",
                    },
                ]
                manifest["files"].extend(records)
                manifest["files"].sort(key=lambda item: item["path"])
                manifest["licenses"].extend(records)
                manifest["licenses"].sort(key=lambda item: item["path"])

            _rewrite_manifest(prefix_collision.root, collide_prefixes)
            with self.assertRaisesRegex(BundleError, "prefix collision"):
                verify_runtime_bundle(prefix_collision.root)

    def test_verifier_rejects_provider_metadata_even_if_all_hashes_are_resealed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = _export(root, "bundle")
            renderpack_path = bundle.root / "renderpack.json"
            renderpack = json.loads(renderpack_path.read_text(encoding="utf-8"))
            renderpack["model_id"] = "leaked-model"
            renderpack["content_hash"] = canonical_payload_hash(renderpack)
            renderpack_path.write_text(
                json.dumps(renderpack, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )

            def reseal(manifest: dict) -> None:
                record = next(
                    item for item in manifest["files"] if item["path"] == "renderpack.json"
                )
                record["sha256"] = _sha256(renderpack_path)
                record["size"] = renderpack_path.stat().st_size
                manifest["renderpack"]["content_hash"] = renderpack["content_hash"]

            _rewrite_manifest(bundle.root, reseal)
            with self.assertRaisesRegex(BundleError, "provider metadata"):
                verify_runtime_bundle(bundle.root)

    def test_export_and_resealed_verifier_reject_false_media_types(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worldpack, renderpack, licenses = _fixture(root / "source-fixture")
            renderpack_raw = json.loads(renderpack.read_text(encoding="utf-8"))
            source_asset = renderpack.parent / renderpack_raw["assets"][0]["files"][0]["path"]
            source_asset.write_text("this is not wave audio\n", encoding="utf-8")
            renderpack_raw["assets"][0]["files"][0]["sha256"] = _sha256(source_asset)
            renderpack_raw["content_hash"] = canonical_payload_hash(renderpack_raw)
            renderpack.write_text(json.dumps(renderpack_raw), encoding="utf-8")
            with self.assertRaisesRegex(BundleError, "declared media type"):
                export_runtime_bundle(
                    worldpack,
                    renderpack,
                    root / "false-export",
                    release_id="1.0.0",
                    licenses_directory=licenses,
                )

            bundle = _export(root, "valid-bundle")
            asset = bundle.root / "assets/neutral_sfx/00_audio.wav"
            asset.write_text("this is not wave audio\n", encoding="utf-8")
            bundled_renderpack = bundle.root / "renderpack.json"
            bundled_raw = json.loads(bundled_renderpack.read_text(encoding="utf-8"))
            bundled_raw["assets"][0]["files"][0]["sha256"] = _sha256(asset)
            bundled_raw["content_hash"] = canonical_payload_hash(bundled_raw)
            bundled_renderpack.write_text(
                json.dumps(bundled_raw, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            def reseal(manifest: dict) -> None:
                by_path = {item["path"]: item for item in manifest["files"]}
                for relative, path in (
                    ("assets/neutral_sfx/00_audio.wav", asset),
                    ("renderpack.json", bundled_renderpack),
                ):
                    by_path[relative]["sha256"] = _sha256(path)
                    by_path[relative]["size"] = path.stat().st_size
                manifest["renderpack"]["content_hash"] = bundled_raw["content_hash"]

            _rewrite_manifest(bundle.root, reseal)
            with self.assertRaisesRegex(BundleError, "declared media type"):
                verify_runtime_bundle(bundle.root)

    def test_imports_multiple_worlds_and_releases_into_a_sorted_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = _game(root)
            later = _export(root, "later", release_id="2.0.0")
            other = _export(root, "other", release_id="1.0.0", world_id="second_world")
            earlier = _export(root, "earlier", release_id="1.0.0")

            for bundle in (other, later, earlier):
                imported = import_runtime_bundle(
                    bundle.root,
                    game,
                    expected_bundle_hash=bundle.bundle_hash,
                )
                self.assertTrue(imported.is_dir())
                self.assertEqual(bundle.bundle_hash, verify_runtime_bundle(imported).bundle_hash)

            catalog_path = game / "game_data/worlds.lock.json"
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            self.assertEqual(CATALOG_FORMAT, catalog["format"])
            keys = [(item["world_id"], item["release_id"]) for item in catalog["releases"]]
            self.assertEqual(
                [
                    ("modly_foundation", "1.0.0"),
                    ("modly_foundation", "2.0.0"),
                    ("second_world", "1.0.0"),
                ],
                keys,
            )
            self.assertNotIn("worldforge", catalog_path.read_text(encoding="utf-8"))
            with self.assertRaisesRegex(BundleError, "already imported"):
                import_runtime_bundle(
                    earlier.root,
                    game,
                    expected_bundle_hash=earlier.bundle_hash,
                )

    def test_import_requires_a_pinned_hash_and_preserves_catalog_on_incompatibility(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = _export(root, "bundle")
            game = _game(root)
            with self.assertRaisesRegex(BundleError, "lowercase SHA-256"):
                import_runtime_bundle(
                    bundle.root,
                    game,
                    expected_bundle_hash=None,  # type: ignore[arg-type]
                )

            worldpack, renderpack, licenses = _fixture(
                root / "incompatible-fixture",
                world_id="future_world",
            )
            worldpack_raw = json.loads(worldpack.read_text(encoding="utf-8"))
            worldpack_raw["runtime_requirements"]["required_features"].append("unsupported_feature")
            worldpack_raw["runtime_requirements"]["required_features"].sort()
            worldpack_raw["content_hash"] = canonical_payload_hash(worldpack_raw)
            worldpack.write_text(
                json.dumps(worldpack_raw, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            renderpack_raw = json.loads(renderpack.read_text(encoding="utf-8"))
            renderpack_raw["world_content_hash"] = worldpack_raw["content_hash"]
            renderpack_raw["content_hash"] = canonical_payload_hash(renderpack_raw)
            renderpack.write_text(json.dumps(renderpack_raw), encoding="utf-8")
            incompatible = export_runtime_bundle(
                worldpack,
                renderpack,
                root / "incompatible-bundle",
                release_id="1.0.0",
                licenses_directory=licenses,
            )
            catalog = game / "game_data/worlds.lock.json"
            before = catalog.read_bytes()
            with self.assertRaisesRegex(BundleError, "missing_features"):
                import_runtime_bundle(
                    incompatible.root,
                    game,
                    expected_bundle_hash=incompatible.bundle_hash,
                )
            self.assertEqual(before, catalog.read_bytes())
            self.assertFalse((game / "game_data/worlds").exists())

    def test_import_rejects_tampered_catalog_storage_and_unmanaged_live_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _export(root, "first", release_id="1.0.0")
            second = _export(root, "second", release_id="2.0.0")
            game = _game(root)
            installed = import_runtime_bundle(
                first.root,
                game,
                expected_bundle_hash=first.bundle_hash,
            )
            asset = installed / "assets/neutral_sfx/00_audio.wav"
            asset.write_bytes(asset.read_bytes() + b"tampered")
            with self.assertRaisesRegex(BundleError, "mismatch"):
                import_runtime_bundle(
                    second.root,
                    game,
                    expected_bundle_hash=second.bundle_hash,
                )

            unmanaged = _game(root, "unmanaged-game")
            (unmanaged / "game_data/worlds/foreign_world/1.0.0").mkdir(parents=True)
            with self.assertRaisesRegex(BundleError, "Unmanaged world|allowlist"):
                import_runtime_bundle(
                    second.root,
                    unmanaged,
                    expected_bundle_hash=second.bundle_hash,
                )

    def test_failed_catalog_write_rolls_back_import(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = _export(root, "bundle")
            game = _game(root)
            catalog_before = (game / "game_data/worlds.lock.json").read_bytes()
            with patch("worldforge.bundle._write_catalog_atomic", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    import_runtime_bundle(
                        bundle.root,
                        game,
                        expected_bundle_hash=bundle.bundle_hash,
                    )
            self.assertEqual(
                catalog_before,
                (game / "game_data/worlds.lock.json").read_bytes(),
            )
            self.assertFalse(game.joinpath("game_data/worlds").exists())
            self.assertFalse(game.joinpath(".isoworld-mutation.lock").exists())

    def test_import_refuses_a_concurrent_or_stale_exclusive_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = _export(root, "bundle")
            game = _game(root)
            lock = game / ".isoworld-mutation.lock"
            lock.write_text("owned by another importer\n", encoding="utf-8")

            with self.assertRaisesRegex(BundleError, "already in progress"):
                import_runtime_bundle(
                    bundle.root,
                    game,
                    expected_bundle_hash=bundle.bundle_hash,
                )

            self.assertEqual("owned by another importer\n", lock.read_text(encoding="utf-8"))
            self.assertTrue((game / "game_data/worlds.lock.json").is_file())

    def test_schema_documents_are_valid_json_and_runtime_neutral(self) -> None:
        for name in ("runtime-bundle.schema.json", "world-catalog.schema.json"):
            raw = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
            self.assertEqual("object", raw["type"])
            serialized = json.dumps(raw)
            self.assertNotIn("rpg-world-forge.", serialized)


if __name__ == "__main__":
    unittest.main()
