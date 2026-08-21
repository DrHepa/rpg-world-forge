from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import worldforge.runtime_implementation as runtime_implementation_module
from scripts.generate_materialization_contract_schemas import (
    build_game_materialization_bundle_schema,
    build_runtime_implementation_schema,
    build_runtime_platform_lock_schema,
    build_standalone_game_lock_schema,
    build_standalone_game_schema,
    build_standalone_platform_schema,
)
from worldforge.__main__ import _resolve_generic_assetpack_cli_source, main
from worldforge.creation_contracts import canonical_creation_hash
from worldforge.game_materialization_bundle import (
    GAME_MATERIALIZATION_BUNDLE_FORMAT,
    GAME_MATERIALIZATION_BUNDLE_MANIFEST,
    GameMaterializationBundleError,
    build_game_materialization_bundle,
    require_game_materialization_bundle,
    validate_game_materialization_bundle_document,
    verify_game_materialization_bundle,
)
from worldforge.game_runtime_bundle import (
    build_game_runtime_bundle_from_objects,
    verify_game_runtime_bundle,
)
from worldforge.generic_assetpack import seal_generic_assetpack
from worldforge.generic_runtime import (
    build_builtin_runtime_adapters,
    build_game_runtime_composition,
    build_game_runtime_snapshot,
    build_runtime_adapter_registry,
    build_runtime_support_report,
)
from worldforge.integrity import canonical_json_bytes
from worldforge.runtime_implementation import (
    RUNTIME_IMPLEMENTATION_FORMAT,
    RuntimeImplementationError,
    build_runtime_implementation,
    validate_runtime_implementation_document,
)
from worldforge.runtime_platform_lock import (
    RUNTIME_PLATFORM_LOCK_FORMAT,
    RuntimePlatformLockError,
    build_builtin_runtime_platform_locks,
    validate_runtime_platform_lock_document,
)

ROOT = Path(__file__).resolve().parents[1]


def _fixture(name: str, relative: str) -> Path:
    return ROOT / "examples" / "multigenre-contracts" / name / relative


def _runtime_document(name: str) -> dict[str, object]:
    return json.loads(
        (ROOT / "examples" / "multigenre-contracts" / "runtime" / name).read_text(encoding="utf-8")
    )


def _fixture_document(name: str, relative: str) -> dict[str, object]:
    return json.loads(_fixture(name, relative).read_text(encoding="utf-8"))


@contextmanager
def _runtime_bundle(name: str, root: Path):
    adapters = build_builtin_runtime_adapters()
    snapshot = build_game_runtime_snapshot(
        ROOT / "src/gamepack_runtime",
        adapter_runtime_root=ROOT / "src/gamepack_raylib_2d",
        adapters=adapters,
    )
    registry = build_runtime_adapter_registry(adapters=adapters, snapshot=snapshot)
    trusted_snapshot_identity = {
        "snapshot_id": snapshot["snapshot_id"],
        "content_hash": snapshot["content_hash"],
        "tree_hash": snapshot["tree_hash"],
    }
    trusted_adapter_hashes = {
        adapter["adapter_id"]: adapter["content_hash"] for adapter in adapters
    }
    trusted_package_hashes: dict[str, str] = {}
    for package in ("gamepack_raylib_2d", "gamepack_runtime"):
        prefix = f"{package}/"
        records = [
            {
                "path": record["path"][len(prefix) :],
                "sha256": record["sha256"],
                "size_bytes": record["size_bytes"],
            }
            for record in snapshot["files"]
            if record["path"].startswith(prefix)
        ]
        trusted_package_hashes[package] = canonical_creation_hash({"files": records})
    gamepack = _fixture_document(name, f"artifacts/{name}.gamepack.json")
    inventory = _fixture_document(name, "assets/inventory.json")
    source = _resolve_generic_assetpack_cli_source(_fixture(name, "assets/manifest.json"))
    assetpack = seal_generic_assetpack(root / f"{name}-assetpack", **source)
    try:
        composition = build_game_runtime_composition(
            gamepack,
            inventory,
            assetpack.root,
            registry=registry,
            snapshot=snapshot,
        )
        support = build_runtime_support_report(
            composition,
            gamepack=gamepack,
            registry=registry,
            snapshot=snapshot,
            evidence=[],
        )
        bundle = build_game_runtime_bundle_from_objects(
            root / f"{name}-runtime-bundle",
            gamepack=gamepack,
            inventory=inventory,
            assetpack=assetpack.manifest,
            assetpack_root=assetpack.root,
            snapshot=snapshot,
            registry=registry,
            composition=composition,
            support_report=support,
        )
        try:
            with (
                mock.patch.object(
                    runtime_implementation_module,
                    "_TRUSTED_SNAPSHOT_IDENTITY",
                    trusted_snapshot_identity,
                ),
                mock.patch.object(
                    runtime_implementation_module,
                    "_TRUSTED_ADAPTER_HASHES",
                    trusted_adapter_hashes,
                ),
                mock.patch.object(
                    runtime_implementation_module,
                    "_TRUSTED_PACKAGE_TREE_HASHES",
                    trusted_package_hashes,
                ),
            ):
                yield bundle
        finally:
            bundle.close()
    finally:
        assetpack.close()


def _reseal(
    document: dict[str, object],
    *,
    id_field: str,
    prefix: str,
    digest_chars: int,
) -> None:
    seed = {key: value for key, value in document.items() if key not in {id_field, "content_hash"}}
    document[id_field] = prefix + canonical_creation_hash(seed)[:digest_chars]
    document["content_hash"] = canonical_creation_hash(document)


class MaterializationContractTests(unittest.TestCase):
    def test_schemas_are_generated_closed_and_additive(self) -> None:
        cases = (
            (
                "runtime-implementation.schema.json",
                build_runtime_implementation_schema(),
                RUNTIME_IMPLEMENTATION_FORMAT,
            ),
            (
                "runtime-platform-lock.schema.json",
                build_runtime_platform_lock_schema(),
                RUNTIME_PLATFORM_LOCK_FORMAT,
            ),
            (
                "game-materialization-bundle.schema.json",
                build_game_materialization_bundle_schema(),
                GAME_MATERIALIZATION_BUNDLE_FORMAT,
            ),
            (
                "standalone-game.schema.json",
                build_standalone_game_schema(),
                "world-forge.standalone_game",
            ),
            (
                "standalone-game-lock.schema.json",
                build_standalone_game_lock_schema(),
                "world-forge.standalone_game_lock",
            ),
            (
                "standalone-platform.schema.json",
                build_standalone_platform_schema(),
                "world-forge.standalone_platform",
            ),
        )
        for name, schema, format_name in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8")),
                    schema,
                )
                self.assertEqual(schema["properties"]["format"]["const"], format_name)
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(schema["properties"]["format_version"]["const"], 1)

    def test_builtin_platform_locks_pin_exact_official_wheels(self) -> None:
        locks = build_builtin_runtime_platform_locks()
        self.assertEqual(len(locks), 4)
        observed = {
            (
                lock["platform"]["os"],
                lock["python"]["minor"],
            ): (
                lock["dependency"]["artifact"]["filename"],
                lock["dependency"]["artifact"]["size_bytes"],
                lock["dependency"]["artifact"]["sha256"],
            )
            for lock in locks
        }
        self.assertEqual(
            observed,
            {
                (
                    "linux",
                    "3.11",
                ): (
                    "raylib-6.0.1.0-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
                    2302782,
                    "6b126a8b9e9a0d36dc796fb0ae1bd7473464a4b126315e332079e5eca7215116",
                ),
                (
                    "windows",
                    "3.11",
                ): (
                    "raylib-6.0.1.0-cp311-cp311-win_amd64.whl",
                    2297998,
                    "a665bd824128396f70435f959399d76c2bb460ce1867fb9d19b41490b70a0d2a",
                ),
                (
                    "linux",
                    "3.12",
                ): (
                    "raylib-6.0.1.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
                    2320911,
                    "bcd224e184c5d64fb6d57bbdabc07124a6f64455ec711d748a0c148b3b26b914",
                ),
                (
                    "windows",
                    "3.12",
                ): (
                    "raylib-6.0.1.0-cp312-cp312-win_amd64.whl",
                    2300464,
                    "64ee5407b3e222045a2b4e6c41ede77a7be05c90335e0679c4765d0e5bcf3ba6",
                ),
            },
        )
        for lock in locks:
            self.assertEqual(
                validate_runtime_platform_lock_document(lock),
                lock,
            )
            self.assertEqual(lock["python"]["requires_python"], ">=3.11,<3.13")
            self.assertEqual(lock["dependency"]["pin"], "raylib==6.0.1.0")
            self.assertEqual(lock["dependency"]["distribution"], "raylib")
            self.assertEqual(lock["dependency"]["import_module"], "pyray")
            self.assertEqual(lock["dependency"]["native_api"], "raylib-5.5")

    def test_platform_lock_rejects_resealed_artifact_abi_and_platform_tamper(self) -> None:
        source = build_builtin_runtime_platform_locks()[0]
        mutations = (
            ("artifact", ("dependency", "artifact", "sha256"), "0" * 64),
            ("abi", ("python", "abi"), "cp312"),
            ("platform", ("platform", "os"), "windows"),
        )
        for label, path, value in mutations:
            with self.subTest(label=label):
                document = copy.deepcopy(source)
                cursor = document
                for part in path[:-1]:
                    cursor = cursor[part]
                cursor[path[-1]] = value
                _reseal(
                    document,
                    id_field="lock_id",
                    prefix="runtime_platform_lock_",
                    digest_chars=40,
                )
                with self.assertRaises(RuntimePlatformLockError):
                    validate_runtime_platform_lock_document(document)

    def test_runtime_implementation_projects_exact_snapshot_packages_and_entrypoints(
        self,
    ) -> None:
        snapshot = _runtime_document("snapshot.json")
        registry = _runtime_document("registry.json")
        adapter = registry["adapters"][0]
        locks = build_builtin_runtime_platform_locks()
        implementation = build_runtime_implementation(
            adapter=adapter,
            snapshot=snapshot,
            platform_locks=locks,
        )
        self.assertEqual(
            validate_runtime_implementation_document(
                implementation,
                adapter=adapter,
                snapshot=snapshot,
                platform_locks=locks,
            ),
            implementation,
        )
        self.assertEqual(
            [package["source_prefix"] for package in implementation["packages"]],
            ["gamepack_raylib_2d", "gamepack_runtime"],
        )
        self.assertEqual(
            {
                item["role"]: (item["module"], item["symbol"])
                for item in implementation["entry_points"]
            },
            {
                "application_factory": (
                    "gamepack_raylib_2d.app",
                    "RuntimeApp.from_bundle",
                ),
                "backend_factory": (
                    "gamepack_raylib_2d.backend",
                    "PyrayBackend",
                ),
                "bundle_loader": (
                    "gamepack_raylib_2d.resources",
                    "load_runtime_bundle",
                ),
                "native_smoke": (
                    "gamepack_raylib_2d.native_smoke",
                    "native_smoke",
                ),
            },
        )
        snapshot_paths = {item["path"]: item for item in snapshot["files"]}
        for package in implementation["packages"]:
            for item in package["files"]:
                self.assertEqual(
                    snapshot_paths[f"{package['source_prefix']}/{item['path']}"],
                    {
                        "path": f"{package['source_prefix']}/{item['path']}",
                        "sha256": item["sha256"],
                        "size_bytes": item["size_bytes"],
                    },
                )

    def test_runtime_implementation_rejects_package_entrypoint_and_crossed_adapter(
        self,
    ) -> None:
        snapshot = _runtime_document("snapshot.json")
        registry = _runtime_document("registry.json")
        locks = build_builtin_runtime_platform_locks()
        puzzle = build_runtime_implementation(
            adapter=registry["adapters"][0],
            snapshot=snapshot,
            platform_locks=locks,
        )
        mutations = []
        package = copy.deepcopy(puzzle)
        package["packages"][0]["files"][0]["sha256"] = "0" * 64
        package["packages"][0]["tree_hash"] = canonical_creation_hash(
            {"files": package["packages"][0]["files"]}
        )
        _reseal(
            package,
            id_field="implementation_id",
            prefix="runtime_implementation_",
            digest_chars=40,
        )
        mutations.append(("package", package, registry["adapters"][0]))
        entrypoint = copy.deepcopy(puzzle)
        entrypoint["entry_points"][0]["symbol"] = "eval"
        _reseal(
            entrypoint,
            id_field="implementation_id",
            prefix="runtime_implementation_",
            digest_chars=40,
        )
        mutations.append(("entrypoint", entrypoint, registry["adapters"][0]))
        mutations.append(("crossed", puzzle, registry["adapters"][1]))
        for label, document, adapter in mutations:
            with self.subTest(label=label):
                with self.assertRaises(RuntimeImplementationError):
                    validate_runtime_implementation_document(
                        document,
                        adapter=adapter,
                        snapshot=snapshot,
                        platform_locks=locks,
                    )

    def test_uncontextual_implementation_rejects_self_resealed_identity_drift(
        self,
    ) -> None:
        snapshot = _runtime_document("snapshot.json")
        registry = _runtime_document("registry.json")
        forged = build_runtime_implementation(
            adapter=registry["adapters"][0],
            snapshot=snapshot,
            platform_locks=build_builtin_runtime_platform_locks(),
        )
        forged["adapter"]["content_hash"] = "0" * 64
        forged["snapshot"] = {
            "snapshot_id": "runtime_snapshot_forged",
            "content_hash": "1" * 64,
            "tree_hash": "2" * 64,
        }
        forged["packages"][0]["files"][0]["sha256"] = "3" * 64
        forged["packages"][0]["tree_hash"] = canonical_creation_hash(
            {"files": forged["packages"][0]["files"]}
        )
        forged["platform_locks"][0]["content_hash"] = "4" * 64
        _reseal(
            forged,
            id_field="implementation_id",
            prefix="runtime_implementation_",
            digest_chars=40,
        )
        with self.assertRaises(RuntimeImplementationError):
            validate_runtime_implementation_document(forged)

    def test_outer_envelope_is_integral_and_materialization_ready(self) -> None:
        locks = build_builtin_runtime_platform_locks()
        with tempfile.TemporaryDirectory(prefix="wf-materialization-") as temporary:
            root = Path(temporary)
            with _runtime_bundle("abstract-puzzle", root) as runtime_bundle:
                verified = build_game_materialization_bundle(
                    root / "materialization-bundle",
                    runtime_bundle_root=runtime_bundle.root,
                    platform_locks=locks,
                )
                try:
                    manifest = verified.manifest
                    self.assertEqual(
                        validate_game_materialization_bundle_document(manifest),
                        manifest,
                    )
                    self.assertEqual(manifest["state"], "materialization_ready")
                    self.assertTrue(manifest["materialization_ready"])
                    self.assertEqual(manifest["missing_launcher_roles"], [])
                    nested = (
                        verified.root
                        / manifest["runtime_bundle"]["root"]
                        / "game-runtime-bundle.json"
                    ).read_bytes()
                    self.assertEqual(
                        nested,
                        (runtime_bundle.root / "game-runtime-bundle.json").read_bytes(),
                    )
                    self.assertEqual(
                        verified.evidence,
                        {
                            "integrity": "valid",
                            "state": "materialization_ready",
                            "materialization_ready": True,
                            "release": "blocked",
                            "supported": False,
                            "bundle_id": manifest["materialization_bundle_id"],
                            "content_hash": manifest["content_hash"],
                        },
                    )
                    second = build_game_materialization_bundle(
                        root / "materialization-bundle-second",
                        runtime_bundle_root=runtime_bundle.root,
                        platform_locks=locks,
                    )
                    try:
                        self.assertEqual(second.manifest, manifest)
                        self.assertEqual(second.files, verified.files)
                    finally:
                        second.close()
                finally:
                    verified.close()

    def test_outer_envelope_rejects_crossed_implementation_and_bare_runtime_bundle(
        self,
    ) -> None:
        snapshot = _runtime_document("snapshot.json")
        registry = _runtime_document("registry.json")
        locks = build_builtin_runtime_platform_locks()
        text_implementation = build_runtime_implementation(
            adapter=registry["adapters"][1],
            snapshot=snapshot,
            platform_locks=locks,
        )
        with tempfile.TemporaryDirectory(prefix="wf-materialization-crossed-") as temporary:
            root = Path(temporary)
            with _runtime_bundle("abstract-puzzle", root) as runtime_bundle:
                with self.assertRaises(GameMaterializationBundleError) as crossed:
                    build_game_materialization_bundle(
                        root / "crossed",
                        runtime_bundle_root=runtime_bundle.root,
                        runtime_implementation=text_implementation,
                        platform_locks=locks,
                    )
                self.assertEqual(
                    crossed.exception.reason_code,
                    "runtime_implementation_adapter_mismatch",
                )
                with self.assertRaises(GameMaterializationBundleError) as bare:
                    require_game_materialization_bundle(runtime_bundle.root)
                self.assertEqual(
                    bare.exception.reason_code,
                    "runtime_implementation_identity_missing",
                )

    def test_builder_rejects_destination_inside_runtime_bundle_before_mutation(
        self,
    ) -> None:
        snapshot = _runtime_document("snapshot.json")
        registry = _runtime_document("registry.json")
        locks = build_builtin_runtime_platform_locks()
        implementation = build_runtime_implementation(
            adapter=registry["adapters"][0],
            snapshot=snapshot,
            platform_locks=locks,
        )
        with tempfile.TemporaryDirectory(prefix="wf-materialization-overlap-") as temporary:
            root = Path(temporary)
            with _runtime_bundle("abstract-puzzle", root) as runtime_bundle:
                destination = runtime_bundle.root / "materialization"
                with self.assertRaises(GameMaterializationBundleError) as overlap:
                    build_game_materialization_bundle(
                        destination,
                        runtime_bundle_root=runtime_bundle.root,
                        runtime_implementation=implementation,
                        platform_locks=locks,
                    )
                self.assertEqual(
                    overlap.exception.reason_code,
                    "game_materialization_bundle_path_overlap",
                )
                self.assertFalse(destination.exists())
                still_valid = verify_game_runtime_bundle(runtime_bundle.root)
                try:
                    self.assertEqual(
                        still_valid.manifest,
                        runtime_bundle.manifest,
                    )
                finally:
                    still_valid.close()

    def test_outer_envelope_rejects_extra_nested_tamper_and_outer_self_reseal(
        self,
    ) -> None:
        snapshot = _runtime_document("snapshot.json")
        registry = _runtime_document("registry.json")
        locks = build_builtin_runtime_platform_locks()
        implementation = build_runtime_implementation(
            adapter=registry["adapters"][0],
            snapshot=snapshot,
            platform_locks=locks,
        )
        with tempfile.TemporaryDirectory(prefix="wf-materialization-tamper-") as temporary:
            root = Path(temporary)
            with _runtime_bundle("abstract-puzzle", root) as runtime_bundle:
                verified = build_game_materialization_bundle(
                    root / "materialization",
                    runtime_bundle_root=runtime_bundle.root,
                    runtime_implementation=implementation,
                    platform_locks=locks,
                )
                verified.close()
                (verified.root / "extra.txt").write_text("extra", encoding="utf-8")
                with self.assertRaises(GameMaterializationBundleError):
                    verify_game_materialization_bundle(verified.root)
                (verified.root / "extra.txt").unlink()

                source = verified.root / "licenses/world-forge-mit.txt"
                for kind in ("symlink", "hardlink"):
                    with self.subTest(kind=kind):
                        path = verified.root / f"extra-{kind}"
                        try:
                            if kind == "symlink":
                                path.symlink_to(source)
                            else:
                                os.link(source, path)
                        except OSError:
                            continue
                        try:
                            with self.assertRaises(GameMaterializationBundleError):
                                verify_game_materialization_bundle(verified.root)
                        finally:
                            path.unlink()

                empty = verified.root / "empty"
                empty.mkdir()
                with self.assertRaises(GameMaterializationBundleError):
                    verify_game_materialization_bundle(verified.root)
                empty.rmdir()

                nested_runtime = (
                    verified.root
                    / "runtime-bundle/runtime/snapshot-tree/gamepack_runtime/session.py"
                )
                nested_payload = nested_runtime.read_bytes()
                nested_runtime.write_bytes(nested_payload + b"\0")
                with self.assertRaises(GameMaterializationBundleError):
                    verify_game_materialization_bundle(verified.root)
                nested_runtime.write_bytes(nested_payload)

                manifest_path = verified.root / GAME_MATERIALIZATION_BUNDLE_MANIFEST
                pristine_manifest = manifest_path.read_bytes()
                manifest = json.loads(pristine_manifest)
                launcher_record = manifest["launchers"]["inventory"][0]
                launcher_record["sha256"] = "0" * 64
                launcher_record["size_bytes"] = 1
                manifest["launchers"]["tree_hash"] = canonical_creation_hash(
                    {
                        "files": [
                            {
                                "path": launcher_record["path"],
                                "sha256": launcher_record["sha256"],
                                "size_bytes": launcher_record["size_bytes"],
                            }
                        ]
                    }
                )
                _reseal(
                    manifest,
                    id_field="materialization_bundle_id",
                    prefix="game_materialization_bundle_",
                    digest_chars=36,
                )
                manifest_path.write_bytes(canonical_json_bytes(manifest))
                with self.assertRaises(GameMaterializationBundleError):
                    verify_game_materialization_bundle(verified.root)

                manifest_path.write_bytes(pristine_manifest)
                manifest = json.loads(pristine_manifest)
                license_file = next(
                    item
                    for item in manifest["files"]
                    if item["path"] == "licenses/world-forge-mit.txt"
                )
                license_file["sha256"] = "0" * 64
                license_file["size_bytes"] = 1
                manifest["tree_hash"] = canonical_creation_hash({"files": manifest["files"]})
                _reseal(
                    manifest,
                    id_field="materialization_bundle_id",
                    prefix="game_materialization_bundle_",
                    digest_chars=36,
                )
                with self.assertRaises(GameMaterializationBundleError):
                    validate_game_materialization_bundle_document(manifest)

                manifest = json.loads(pristine_manifest)
                manifest["runtime_implementation"]["content_hash"] = "0" * 64
                _reseal(
                    manifest,
                    id_field="materialization_bundle_id",
                    prefix="game_materialization_bundle_",
                    digest_chars=36,
                )
                manifest_path.write_bytes(canonical_json_bytes(manifest))
                with self.assertRaises(GameMaterializationBundleError):
                    verify_game_materialization_bundle(verified.root)

    def test_contract_fixtures_are_canonical_and_bind_both_verticals(self) -> None:
        locks = build_builtin_runtime_platform_locks()
        for lock in locks:
            path = (
                ROOT
                / "examples/multigenre-contracts/runtime/platform-locks"
                / f"{lock['lock_id']}.json"
            )
            self.assertEqual(path.read_bytes(), canonical_json_bytes(lock))
        snapshot = _runtime_document("snapshot.json")
        registry = _runtime_document("registry.json")
        expected_hashes = {}
        for name, adapter in (
            ("abstract-puzzle", registry["adapters"][0]),
            ("branching-narrative", registry["adapters"][1]),
        ):
            implementation = build_runtime_implementation(
                adapter=adapter,
                snapshot=snapshot,
                platform_locks=locks,
            )
            path = _fixture(name, "runtime/runtime-implementation.json")
            self.assertEqual(path.read_bytes(), canonical_json_bytes(implementation))
            expected_hashes[name] = implementation["content_hash"]
        self.assertNotEqual(
            expected_hashes["abstract-puzzle"],
            expected_hashes["branching-narrative"],
        )

    def test_cli_builds_ready_envelope_but_release_remains_blocked(self) -> None:
        lock_paths = sorted(
            (ROOT / "examples/multigenre-contracts/runtime/platform-locks").glob("*.json")
        )
        implementation_path = _fixture(
            "abstract-puzzle",
            "runtime/runtime-implementation.json",
        )
        with tempfile.TemporaryDirectory(prefix="wf-materialization-cli-") as temporary:
            root = Path(temporary)
            with _runtime_bundle("abstract-puzzle", root) as runtime_bundle:
                output = root / "materialization"
                build_args = [
                    "build-game-materialization-bundle",
                    str(runtime_bundle.root),
                    str(implementation_path),
                    *sum(
                        (["--platform-lock", str(path)] for path in lock_paths),
                        [],
                    ),
                    "--output",
                    str(output),
                ]
                stdout = io.StringIO()
                with (
                    mock.patch("sys.argv", ["worldforge", *build_args]),
                    contextlib.redirect_stdout(stdout),
                ):
                    self.assertEqual(main(), 0)
                built = json.loads(stdout.getvalue())
                self.assertEqual(built["state"], "materialization_ready")
                self.assertTrue(built["materialization_ready"])
                self.assertEqual(built["release"], "blocked")
                stdout = io.StringIO()
                with (
                    mock.patch(
                        "sys.argv",
                        [
                            "worldforge",
                            "verify-game-materialization-bundle",
                            str(output),
                            "--expected-hash",
                            built["content_hash"],
                        ],
                    ),
                    contextlib.redirect_stdout(stdout),
                ):
                    self.assertEqual(
                        main(),
                        0,
                    )
                self.assertEqual(json.loads(stdout.getvalue())["integrity"], "valid")

        stdout = io.StringIO()
        with (
            mock.patch(
                "sys.argv",
                ["worldforge", "inspect-runtime-platform-lock", str(lock_paths[0])],
            ),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(
                main(),
                0,
            )
        self.assertEqual(json.loads(stdout.getvalue())["status"], "audited")

        stdout = io.StringIO()
        with (
            mock.patch(
                "sys.argv",
                [
                    "worldforge",
                    "inspect-runtime-implementation",
                    str(implementation_path),
                ],
            ),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(
                main(),
                0,
            )
        inspected = json.loads(stdout.getvalue())
        self.assertEqual(inspected["adapter_id"], "gamepack_raylib_2d_puzzle")
        self.assertFalse(inspected["materialization_ready"])

    def test_runtime_snapshot_rotates_for_neutral_distribution_validator(self) -> None:
        expected = {
            "snapshot.json": (
                5889,
                "08ce14e290b8eb1c963724d5f1ff0e37199bf2dd80b85439cba7a7ded555fdf4",
            ),
            "registry.json": (
                8178,
                "9485e28a864328cca5a4a3bc21fe8569ae160459130917175b25caf119b87570",
            ),
        }
        for relative, (size_bytes, content_hash) in expected.items():
            with self.subTest(relative=relative):
                payload = (ROOT / "examples/multigenre-contracts/runtime" / relative).read_bytes()
                self.assertEqual(len(payload), size_bytes)
                document = json.loads(payload)
                self.assertEqual(document["content_hash"], content_hash)
                self.assertEqual(
                    hashlib.sha256(payload).hexdigest(),
                    hashlib.sha256(canonical_json_bytes(document)).hexdigest(),
                )
        snapshot = _runtime_document("snapshot.json")
        self.assertIn(
            "gamepack_runtime/distribution.py",
            {item["path"] for item in snapshot["files"]},
        )
        self.assertIn(
            "gamepack_runtime/distribution_names.py",
            {item["path"] for item in snapshot["files"]},
        )
        self.assertIn(
            "gamepack_runtime/game_package.py",
            {item["path"] for item in snapshot["files"]},
        )
