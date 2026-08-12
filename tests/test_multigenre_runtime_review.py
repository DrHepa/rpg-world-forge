from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import inspect
import io
import json
import os
import tempfile
import unittest
from collections.abc import Callable, Mapping
from pathlib import Path
from unittest import mock

from scripts.generate_generic_runtime_schemas import (
    build_schemas,
    build_studio_runtime_policy_module,
    build_studio_runtime_trusted_files_module,
)
from worldforge import generic_runtime
from worldforge.__main__ import main
from worldforge.creation_contracts import canonical_creation_hash
from worldforge.generic_runtime import (
    RUNTIME_EXECUTION_SEMANTICS_POLICY,
    RuntimeContractError,
    build_builtin_runtime_adapters,
    build_game_runtime_composition,
    build_game_runtime_snapshot,
    build_runtime_adapter_registry,
    build_runtime_evidence,
    build_runtime_support_report,
    resolve_required_feature_support,
    resolve_runtime_adapter,
    resolve_runtime_compatibility,
    validate_game_runtime_composition,
    validate_game_runtime_composition_document,
    validate_runtime_adapter_document,
    validate_runtime_adapter_registry_document,
    validate_runtime_evidence_document,
    validate_runtime_snapshot_document,
    validate_runtime_support_report,
    validate_runtime_support_report_document,
)

ROOT = Path(__file__).resolve().parents[1]


class _ExplosiveDict(dict[object, object]):
    def _explode(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("hostile dict callback executed")

    __copy__ = _explode
    __deepcopy__ = _explode
    __getitem__ = _explode
    __iter__ = _explode
    __len__ = _explode
    get = _explode
    items = _explode
    keys = _explode
    values = _explode


class _ExplosiveList(list[object]):
    def _explode(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("hostile list callback executed")

    __copy__ = _explode
    __deepcopy__ = _explode
    __getitem__ = _explode
    __iter__ = _explode
    __len__ = _explode


class _ExplosiveString(str):
    def _explode(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("hostile string callback executed")

    __eq__ = _explode
    __hash__ = str.__hash__
    __iter__ = _explode
    casefold = _explode
    encode = _explode


class _ExplosiveInteger(int):
    def _explode(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("hostile integer callback executed")

    __eq__ = _explode
    __index__ = _explode
    __int__ = _explode
    __le__ = _explode
    __lt__ = _explode


class _ExplosiveMapping(Mapping[object, object]):
    def _explode(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("hostile mapping callback executed")

    __getitem__ = _explode
    __iter__ = _explode
    __len__ = _explode
    get = _explode
    items = _explode


def _hostile_value(value: object) -> object | None:
    if type(value) is dict:
        hostile = _ExplosiveDict()
        for key, item in value.items():
            dict.__setitem__(hostile, key, item)
        return hostile
    if type(value) is list:
        hostile_list = _ExplosiveList()
        for item in value:
            list.append(hostile_list, item)
        return hostile_list
    if type(value) is str:
        return _ExplosiveString(value)
    if type(value) is int:
        return _ExplosiveInteger(value)
    return None


def _json_value_paths(value: object) -> list[tuple[object, ...]]:
    paths: list[tuple[object, ...]] = []
    pending: list[tuple[tuple[object, ...], object]] = [((), value)]
    while pending:
        path, current = pending.pop()
        paths.append(path)
        if type(current) is dict:
            pending.extend((path + (key,), item) for key, item in reversed(list(current.items())))
        elif type(current) is list:
            pending.extend(
                (path + (index,), item) for index, item in reversed(list(enumerate(current)))
            )
    return paths


def _replace_json_value(
    document: dict[str, object],
    path: tuple[object, ...],
    replacement: object,
) -> object:
    if not path:
        return replacement
    changed: object = copy.deepcopy(document)
    current = changed
    for segment in path[:-1]:
        if type(current) is dict:
            current = current[segment]
        else:
            assert type(current) is list and type(segment) is int
            current = current[segment]
    final = path[-1]
    if type(current) is dict:
        current[final] = replacement
    else:
        assert type(current) is list and type(final) is int
        current[final] = replacement
    return changed


def _fixture(relative: str) -> dict[str, object]:
    value = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{relative} must contain an object")
    return value


def _reseal(document: dict[str, object]) -> dict[str, object]:
    format_name = document["format"]
    if format_name == "world-forge.game_runtime_snapshot":
        document["tree_hash"] = canonical_creation_hash({"files": document["files"]})
        document["snapshot_id"] = (
            "runtime_snapshot_"
            + canonical_creation_hash(
                {
                    "runtime_api": document["runtime_api"],
                    "adapter_descriptors": document["adapter_descriptors"],
                    "files": document["files"],
                    "tree_hash": document["tree_hash"],
                }
            )[:40]
        )
    elif format_name == "world-forge.runtime_adapter_registry":
        document["registry_id"] = (
            "runtime_registry_"
            + canonical_creation_hash(
                {
                    "runtime_snapshot": document["runtime_snapshot"],
                    "adapters": document["adapters"],
                }
            )[:40]
        )
    elif format_name == "world-forge.game_runtime_composition":
        document["composition_id"] = (
            "runtime_composition_"
            + canonical_creation_hash(
                {
                    field: document[field]
                    for field in (
                        "gamepack",
                        "asset_inventory",
                        "assetpack",
                        "adapter",
                        "registry",
                        "runtime_snapshot",
                        "platforms",
                        "bindings",
                    )
                }
            )[:40]
        )
    elif format_name == "world-forge.runtime_evidence":
        document["evidence_id"] = (
            "runtime_evidence_"
            + canonical_creation_hash(
                {
                    field: document[field]
                    for field in (
                        "composition",
                        "adapter",
                        "platform",
                        "execution_status",
                        "packaging_status",
                        "checks",
                    )
                }
            )[:40]
        )
    elif format_name == "world-forge.runtime_support_report":
        document["report_id"] = (
            "runtime_support_"
            + canonical_creation_hash(
                {
                    field: document[field]
                    for field in (
                        "gamepack",
                        "composition",
                        "adapter",
                        "evidence",
                        "dimensions",
                        "compatibility_status",
                        "mechanics",
                        "features",
                        "missing_capabilities",
                        "reason_codes",
                        "supported",
                    )
                }
            )[:40]
        )
    document["content_hash"] = canonical_creation_hash(document)
    return document


def _checks(prefix: str) -> list[dict[str, object]]:
    return [
        {
            "check_id": "check:headless_determinism",
            "kind": "headless",
            "status": "passed",
            "evidence_id": f"{prefix}_headless",
            "content_hash": "1" * 64,
        },
        {
            "check_id": "check:native_raylib",
            "kind": "native",
            "status": "passed",
            "evidence_id": f"{prefix}_native",
            "content_hash": "2" * 64,
        },
        {
            "check_id": "check:package_verification",
            "kind": "packaging",
            "status": "passed",
            "evidence_id": f"{prefix}_package",
            "content_hash": "3" * 64,
        },
        {
            "check_id": "check:save_replay",
            "kind": "save_replay",
            "status": "passed",
            "evidence_id": f"{prefix}_replay",
            "content_hash": "4" * 64,
        },
    ]


class GenericRuntimeReviewTests(unittest.TestCase):
    def _registry(self) -> tuple[dict[str, object], dict[str, object]]:
        adapters = build_builtin_runtime_adapters()
        snapshot = build_game_runtime_snapshot(
            ROOT / "src" / "gamepack_runtime",
            adapter_runtime_root=ROOT / "src" / "gamepack_raylib_2d",
            adapters=adapters,
        )
        registry = build_runtime_adapter_registry(
            adapters=adapters,
            snapshot=snapshot,
        )
        return snapshot, registry

    def _puzzle_inputs(
        self,
    ) -> tuple[
        dict[str, object],
        dict[str, object],
        dict[str, object],
        dict[str, object],
    ]:
        snapshot, registry = self._registry()
        return (
            _fixture(
                "examples/multigenre-contracts/abstract-puzzle/"
                "artifacts/abstract-puzzle.gamepack.json"
            ),
            _fixture("examples/multigenre-contracts/abstract-puzzle/runtime/composition.json"),
            snapshot,
            registry,
        )

    def test_execution_policy_is_exact_and_shared_by_adapter_and_schema(self) -> None:
        self.assertEqual(
            RUNTIME_EXECUTION_SEMANTICS_POLICY,
            {
                "content_hash": (
                    "f43fa43e4c54a2910ae8a99fbbfc0b2556359f95c1c88abef59a2508c9ea5983"
                ),
                "version": 1,
            },
        )
        adapter = build_builtin_runtime_adapters()[0]
        self.assertEqual(
            adapter["execution_semantics"],
            RUNTIME_EXECUTION_SEMANTICS_POLICY,
        )

        tampered = copy.deepcopy(adapter)
        tampered["execution_semantics"]["content_hash"] = "f" * 64
        _reseal(tampered)
        with self.assertRaisesRegex(
            RuntimeContractError,
            "runtime_semantics_unsupported",
        ):
            validate_runtime_adapter_document(tampered)

        _snapshot, registry = self._registry()
        nested = copy.deepcopy(registry)
        nested_adapter = nested["adapters"][0]
        nested_adapter["execution_semantics"]["content_hash"] = "e" * 64
        _reseal(nested_adapter)
        _reseal(nested)
        with self.assertRaisesRegex(
            RuntimeContractError,
            "runtime_semantics_unsupported",
        ):
            validate_runtime_adapter_registry_document(nested)

        schemas = build_schemas()
        adapter_schema = schemas["generic-runtime-adapter.schema.json"]
        self.assertEqual(
            adapter_schema["properties"]["execution_semantics"],
            {"const": RUNTIME_EXECUTION_SEMANTICS_POLICY},
        )
        platform_variants = adapter_schema["$defs"]["platform"]["oneOf"]
        self.assertEqual(
            [
                (
                    variant["properties"]["platform_id"]["const"],
                    variant["properties"]["platform_family"]["const"],
                    variant["properties"]["architecture"]["const"],
                    variant["properties"]["backend"]["const"],
                )
                for variant in platform_variants
            ],
            [
                (
                    "platform:linux_x86_64",
                    "platform:linux",
                    "architecture:x86_64",
                    "backend:raylib",
                ),
                (
                    "platform:windows_x86_64",
                    "platform:windows",
                    "architecture:x86_64",
                    "backend:raylib",
                ),
            ],
        )
        support_schema = schemas["generic-runtime-support-report.schema.json"]
        self.assertIn("evidence", support_schema["properties"])
        self.assertEqual(
            support_schema["$defs"]["execution"]["oneOf"][0]["properties"]["status"],
            {"const": "untested"},
        )
        self.assertEqual(
            support_schema["$defs"]["feature"]["oneOf"][0]["properties"]["status"],
            {
                "enum": [
                    "supported_current",
                    "game_extension_verified",
                ]
            },
        )
        self.assertEqual(
            build_studio_runtime_policy_module(),
            (
                b"/* AUTO-GENERATED from the neutral Python execution policy. */\n"
                b"export const GENERIC_RUNTIME_EXECUTION_POLICY = "
                b'Object.freeze({"content_hash":'
                b'"f43fa43e4c54a2910ae8a99fbbfc0b2556359f95c1c88abef59a2508c9ea5983",'
                b'"version":1});\n'
            ),
        )
        trusted_module = build_studio_runtime_trusted_files_module()
        session_payload = (ROOT / "src/gamepack_runtime/session.py").read_bytes()
        self.assertIn(b"gamepack_runtime/session.py", trusted_module)
        self.assertIn(base64.b64encode(session_payload), trusted_module)
        self.assertEqual(
            (ROOT / "apps/studio/scripts/generic-runtime-trusted-files.mjs").read_bytes(),
            trusted_module,
        )

    def test_resolver_recomputes_the_exact_installed_kernel_and_registry(self) -> None:
        self.assertNotIn(
            "kernel_root",
            inspect.signature(resolve_runtime_adapter).parameters,
        )
        gamepack, _composition, snapshot, registry = self._puzzle_inputs()
        self.assertEqual(
            resolve_runtime_adapter(
                gamepack,
                registry=registry,
                snapshot=snapshot,
            )["adapter_id"],
            "gamepack_raylib_2d_puzzle",
        )

        resealed_snapshot = copy.deepcopy(snapshot)
        kernel_entry = next(
            item
            for item in resealed_snapshot["files"]
            if item["path"] == "gamepack_runtime/__init__.py"
        )
        kernel_entry["sha256"] = "f" * 64
        _reseal(resealed_snapshot)
        resealed_registry = copy.deepcopy(registry)
        resealed_registry["runtime_snapshot"] = {
            "format": resealed_snapshot["format"],
            "format_version": resealed_snapshot["format_version"],
            "id": resealed_snapshot["snapshot_id"],
            "content_hash": resealed_snapshot["content_hash"],
        }
        _reseal(resealed_registry)
        with self.assertRaisesRegex(
            RuntimeContractError,
            "runtime_snapshot_untrusted",
        ):
            resolve_runtime_adapter(
                gamepack,
                registry=resealed_registry,
                snapshot=resealed_snapshot,
            )

        for case_id, mutate, expected_reason in (
            (
                "missing",
                lambda document: document["files"].pop(
                    next(
                        index
                        for index, item in enumerate(document["files"])
                        if item["path"].startswith("gamepack_runtime/")
                    )
                ),
                "runtime_snapshot_untrusted",
            ),
            (
                "extra",
                lambda document: document["files"].append(
                    {
                        "path": "gamepack_runtime/unregistered.py",
                        "sha256": "a" * 64,
                        "size_bytes": 1,
                    }
                ),
                "runtime_snapshot_untrusted",
            ),
            (
                "reordered",
                lambda document: document["files"].reverse(),
                "runtime_contract_noncanonical",
            ),
        ):
            with self.subTest(case=case_id):
                changed_snapshot = copy.deepcopy(snapshot)
                mutate(changed_snapshot)
                if case_id == "extra":
                    changed_snapshot["files"].sort(key=lambda item: item["path"].encode("utf-8"))
                _reseal(changed_snapshot)
                changed_registry = copy.deepcopy(registry)
                changed_registry["runtime_snapshot"] = {
                    "format": changed_snapshot["format"],
                    "format_version": changed_snapshot["format_version"],
                    "id": changed_snapshot["snapshot_id"],
                    "content_hash": changed_snapshot["content_hash"],
                }
                _reseal(changed_registry)
                with self.assertRaisesRegex(RuntimeContractError, expected_reason):
                    resolve_runtime_adapter(
                        gamepack,
                        registry=changed_registry,
                        snapshot=changed_snapshot,
                    )

    def test_trusted_kernel_rejects_same_length_replacement_links_and_hardlinks(
        self,
    ) -> None:
        gamepack = _fixture(
            "examples/multigenre-contracts/abstract-puzzle/artifacts/abstract-puzzle.gamepack.json"
        )
        adapters = build_builtin_runtime_adapters()
        with tempfile.TemporaryDirectory(prefix="wf-runtime-trust-") as temporary:
            root = Path(temporary)
            source = root / "__init__.py"
            source.write_bytes(b"VALUE = 1\n")
            snapshot = build_game_runtime_snapshot(root, adapters=adapters)
            registry = build_runtime_adapter_registry(
                adapters=adapters,
                snapshot=snapshot,
            )

            replacement = root / "replacement.py"
            replacement.write_bytes(b"VALUE = 2\n")
            os.replace(replacement, source)
            with (
                mock.patch.object(
                    generic_runtime,
                    "_installed_runtime_kernel_root",
                    return_value=root,
                ),
                self.assertRaisesRegex(
                    RuntimeContractError,
                    "runtime_snapshot_untrusted",
                ),
            ):
                resolve_runtime_adapter(
                    gamepack,
                    registry=registry,
                    snapshot=snapshot,
                )

            source.unlink()
            target = root / "target.py"
            target.write_bytes(b"VALUE = 1\n")
            os.link(target, source)
            with self.assertRaisesRegex(
                RuntimeContractError,
                "runtime_snapshot_tree_unsafe",
            ):
                build_game_runtime_snapshot(root, adapters=adapters)

            source.unlink()
            source.symlink_to(target.name)
            with self.assertRaisesRegex(
                RuntimeContractError,
                "runtime_snapshot_tree_unsafe",
            ):
                build_game_runtime_snapshot(root, adapters=adapters)

    @unittest.skipUnless(os.name == "posix", "POSIX retained descriptors required")
    def test_retained_runtime_tree_reads_original_bytes_across_root_subdir_and_file_aba(
        self,
    ) -> None:
        scenarios = ("root", "subdir", "file")
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                with tempfile.TemporaryDirectory(prefix=f"wf-runtime-{scenario}-aba-") as temporary:
                    parent = Path(temporary)
                    root = parent / "gamepack_runtime"
                    root.mkdir()
                    source_parent = root
                    relative = "kernel.py"
                    if scenario == "subdir":
                        source_parent = root / "kernel"
                        source_parent.mkdir()
                        relative = "kernel/kernel.py"
                    source = source_parent / "kernel.py"
                    original = b"ORIGINAL = 1\n"
                    malicious = b"MALICIOUS = 2\n"
                    source.write_bytes(original)

                    swapped = False
                    original_aside = parent / "gamepack_runtime.original"
                    malicious_root = parent / "gamepack_runtime.malicious"
                    malicious_root.mkdir()
                    (malicious_root / "kernel.py").write_bytes(malicious)
                    original_subdir = parent / "kernel.original"
                    malicious_subdir = parent / "kernel.malicious"
                    if scenario == "subdir":
                        malicious_subdir.mkdir()
                        (malicious_subdir / "kernel.py").write_bytes(malicious)
                    original_file = parent / "kernel.original.py"
                    malicious_file = parent / "kernel.malicious.py"
                    if scenario == "file":
                        malicious_file.write_bytes(malicious)

                    def hook(
                        event: str,
                        retained_relative: str | None,
                        scenario: str = scenario,
                        root: Path = root,
                        original_aside: Path = original_aside,
                        malicious_root: Path = malicious_root,
                        original_subdir: Path = original_subdir,
                        malicious_subdir: Path = malicious_subdir,
                        source: Path = source,
                        original_file: Path = original_file,
                        malicious_file: Path = malicious_file,
                    ) -> None:
                        nonlocal swapped
                        if scenario == "root" and event == "after_root_retained":
                            root.rename(original_aside)
                            malicious_root.rename(root)
                            swapped = True
                        elif (
                            scenario == "subdir"
                            and event == "after_directory_retained"
                            and retained_relative == "kernel"
                        ):
                            (root / "kernel").rename(original_subdir)
                            malicious_subdir.rename(root / "kernel")
                            swapped = True
                        elif (
                            scenario == "file"
                            and event == "after_file_retained"
                            and retained_relative == "kernel.py"
                        ):
                            source.rename(original_file)
                            malicious_file.rename(source)
                            swapped = True
                        elif event == "before_final_verification" and swapped:
                            if scenario == "root":
                                root.rename(malicious_root)
                                original_aside.rename(root)
                            elif scenario == "subdir":
                                (root / "kernel").rename(malicious_subdir)
                                original_subdir.rename(root / "kernel")
                            else:
                                source.rename(malicious_file)
                                original_file.rename(source)
                            swapped = False

                    try:
                        if scenario == "file":
                            with self.assertRaisesRegex(
                                RuntimeContractError,
                                "runtime_snapshot_changed",
                            ):
                                generic_runtime._capture_runtime_files(  # noqa: SLF001
                                    root,
                                    _verification_hook=hook,
                                )
                            captured = None
                        else:
                            captured = generic_runtime._capture_runtime_files(  # noqa: SLF001
                                root,
                                _verification_hook=hook,
                            )
                    finally:
                        if swapped:
                            if scenario == "root":
                                root.rename(malicious_root)
                                original_aside.rename(root)
                            elif scenario == "subdir":
                                (root / "kernel").rename(malicious_subdir)
                                original_subdir.rename(root / "kernel")
                            else:
                                source.rename(malicious_file)
                                original_file.rename(source)
                    if captured is not None:
                        self.assertEqual(
                            captured[f"gamepack_runtime/{relative}"],
                            original,
                        )
                        self.assertNotEqual(
                            hashlib.sha256(captured[f"gamepack_runtime/{relative}"]).digest(),
                            hashlib.sha256(malicious).digest(),
                        )

    @unittest.skipUnless(os.name == "posix", "POSIX retained descriptors required")
    def test_retained_runtime_tree_rejects_same_inode_mutation_and_inventory_drift(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-runtime-retained-mutation-") as temporary:
            root = Path(temporary) / "gamepack_runtime"
            root.mkdir()
            source = root / "kernel.py"
            original = b"VALUE = 1\n"
            source.write_bytes(original)

            def mutation_hook(event: str, relative: str | None) -> None:
                if event == "after_file_retained" and relative == "kernel.py":
                    source.write_bytes(b"VALUE = 2\n")
                elif event == "after_file_read" and relative == "kernel.py":
                    source.write_bytes(original)

            with self.assertRaisesRegex(
                RuntimeContractError,
                "runtime_snapshot_changed",
            ):
                generic_runtime._capture_runtime_files(  # noqa: SLF001
                    root,
                    _verification_hook=mutation_hook,
                )

            def extra_hook(event: str, _relative: str | None) -> None:
                if event == "before_final_verification":
                    (root / "extra.py").write_bytes(b"EXTRA = 1\n")

            with self.assertRaisesRegex(
                RuntimeContractError,
                "runtime_snapshot_changed",
            ):
                generic_runtime._capture_runtime_files(  # noqa: SLF001
                    root,
                    _verification_hook=extra_hook,
                )

    @unittest.skipUnless(os.name == "posix", "POSIX portable-name probes required")
    def test_retained_runtime_tree_maps_nonportable_names_and_casefold_collisions(
        self,
    ) -> None:
        cases = (
            ("nonportable", ("invalid:name.py",)),
            ("casefold-collision", ("Kernel.py", "kernel.py")),
        )
        for case_id, names in cases:
            with self.subTest(case_id=case_id):
                with tempfile.TemporaryDirectory(prefix=f"wf-runtime-{case_id}-") as temporary:
                    root = Path(temporary) / "gamepack_runtime"
                    root.mkdir()
                    for name in names:
                        (root / name).write_bytes(b"VALUE = 1\n")
                    try:
                        generic_runtime._capture_runtime_files(root)  # noqa: SLF001
                    except Exception as exc:  # noqa: BLE001 - raw exception audit.
                        self.assertIs(type(exc), RuntimeContractError)
                        self.assertEqual(
                            exc.reason_code,
                            "runtime_snapshot_tree_unsafe",
                        )
                    else:
                        self.fail(f"{case_id} runtime tree was accepted")

    @unittest.skipUnless(os.name == "nt", "native Windows retained handles required")
    def test_windows_native_runtime_tree_retains_root_and_file_bindings(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-runtime-windows-aba-") as temporary:
            parent = Path(temporary)
            root = parent / "gamepack_runtime"
            root.mkdir()
            source = root / "kernel.py"
            original = b"ORIGINAL = 1\n"
            source.write_bytes(original)
            malicious_root = parent / "gamepack_runtime.malicious"
            malicious_root.mkdir()
            (malicious_root / "kernel.py").write_bytes(b"MALICIOUS = 2\n")
            original_aside = parent / "gamepack_runtime.original"
            rename_blocked = False
            swapped = False
            swap_completed = False

            def hook(event: str, _relative: str | None) -> None:
                nonlocal rename_blocked, swap_completed, swapped
                if event == "after_root_retained":
                    try:
                        root.rename(original_aside)
                    except OSError:
                        rename_blocked = True
                    else:
                        try:
                            malicious_root.rename(root)
                        except OSError:
                            original_aside.rename(root)
                            rename_blocked = True
                        else:
                            swapped = True
                            swap_completed = True
                elif event == "before_final_verification" and swapped:
                    root.rename(malicious_root)
                    original_aside.rename(root)
                    swapped = False

            try:
                captured = generic_runtime._capture_runtime_files(  # noqa: SLF001
                    root,
                    _verification_hook=hook,
                )
            finally:
                if swapped:
                    root.rename(malicious_root)
                    original_aside.rename(root)
            self.assertEqual(captured["gamepack_runtime/kernel.py"], original)
            self.assertTrue(
                rename_blocked or swap_completed,
                "Windows retention neither blocked nor safely completed the ABA attempt",
            )

    @unittest.skipUnless(os.name == "nt", "native Windows reparse checks required")
    def test_windows_native_runtime_tree_rejects_hardlinks_and_reparse_points(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-runtime-windows-links-") as temporary:
            root = Path(temporary) / "gamepack_runtime"
            root.mkdir()
            target = root / "target.py"
            target.write_bytes(b"VALUE = 1\n")
            linked = root / "linked.py"
            os.link(target, linked)
            with self.assertRaisesRegex(
                RuntimeContractError,
                "runtime_snapshot_tree_unsafe",
            ):
                generic_runtime._capture_runtime_files(root)  # noqa: SLF001

            linked.unlink()
            try:
                linked.symlink_to(target.name)
            except OSError as exc:
                self.skipTest(f"native Windows symlink creation unavailable: {exc}")
            with self.assertRaisesRegex(
                RuntimeContractError,
                "runtime_snapshot_tree_unsafe",
            ):
                generic_runtime._capture_runtime_files(root)  # noqa: SLF001

    def test_runtime_public_documents_own_exact_plain_json_before_any_callback(
        self,
    ) -> None:
        corpus = _fixture("tests/fixtures/generic-runtime/parity-corpus.json")
        validators = {
            "game-runtime-composition": validate_game_runtime_composition_document,
            "game-runtime-snapshot": validate_runtime_snapshot_document,
            "generic-runtime-adapter": validate_runtime_adapter_document,
            "generic-runtime-adapter-registry": validate_runtime_adapter_registry_document,
            "generic-runtime-evidence": validate_runtime_evidence_document,
            "generic-runtime-support-report": validate_runtime_support_report_document,
        }
        valid_by_kind = {case["kind"]: case["document"] for case in corpus["valid"]}
        self.assertEqual(set(validators), set(valid_by_kind))

        cases_exercised = 0

        def assert_boundary_rejection(
            validator: object,
            value: object,
            *,
            case_id: str,
        ) -> None:
            nonlocal cases_exercised
            cases_exercised += 1
            try:
                validator(value)
            except Exception as exc:  # noqa: BLE001 - raw exception audit is the assertion.
                self.assertIs(
                    type(exc),
                    RuntimeContractError,
                    msg=f"{case_id} leaked {type(exc).__name__}: {exc}",
                )
                self.assertEqual(
                    exc.reason_code,
                    "runtime_json_invalid",
                    msg=case_id,
                )
            else:
                self.fail(f"{case_id} accepted hostile JSON")

        for kind, validator in validators.items():
            document = valid_by_kind[kind]
            root_hostile = _hostile_value(document)
            assert root_hostile is not None
            assert_boundary_rejection(
                validator,
                root_hostile,
                case_id=f"{kind}:root-subclass",
            )
            assert_boundary_rejection(
                validator,
                _ExplosiveMapping(),
                case_id=f"{kind}:custom-mapping",
            )

            for path in _json_value_paths(document)[1:]:
                current: object = document
                for segment in path:
                    current = current[segment]
                hostile = _hostile_value(current)
                if hostile is None:
                    continue
                assert_boundary_rejection(
                    validator,
                    _replace_json_value(document, path, hostile),
                    case_id=f"{kind}:nested:{path!r}",
                )

            keys = list(document)
            cyclic = copy.deepcopy(document)
            cyclic[keys[-1]] = cyclic
            assert_boundary_rejection(
                validator,
                cyclic,
                case_id=f"{kind}:cycle",
            )

            aliased = copy.deepcopy(document)
            shared: list[object] = []
            aliased[keys[-1]] = shared
            aliased[keys[-2]] = shared
            assert_boundary_rejection(
                validator,
                aliased,
                case_id=f"{kind}:alias",
            )
            for case_id, hostile_scalar in (
                ("float", 1.5),
                ("nonfinite", float("nan")),
                ("surrogate", "\ud800"),
                (
                    "oversize",
                    "x" * (generic_runtime.MAX_RUNTIME_CONTRACT_BYTES + 1),
                ),
            ):
                changed = copy.deepcopy(document)
                changed[keys[-1]] = hostile_scalar
                assert_boundary_rejection(
                    validator,
                    changed,
                    case_id=f"{kind}:{case_id}",
                )

        self.assertGreater(cases_exercised, 200)

    def test_runtime_builders_and_resolvers_own_inputs_before_any_callback(
        self,
    ) -> None:
        gamepack, composition, snapshot, registry = self._puzzle_inputs()
        inventory = _fixture("examples/multigenre-contracts/abstract-puzzle/assets/inventory.json")
        corpus = _fixture("tests/fixtures/generic-runtime/parity-corpus.json")
        evidence = next(
            case["document"]
            for case in corpus["valid"]
            if case["kind"] == "generic-runtime-evidence"
        )
        support_report = next(
            case["document"]
            for case in corpus["valid"]
            if case["kind"] == "generic-runtime-support-report"
        )
        adapter = build_builtin_runtime_adapters()[0]

        def assert_boundary_rejection(
            case_id: str,
            operation: Callable[[], object],
        ) -> None:
            try:
                operation()
            except Exception as exc:  # noqa: BLE001 - raw exception audit is the assertion.
                self.assertIs(
                    type(exc),
                    RuntimeContractError,
                    msg=f"{case_id} leaked {type(exc).__name__}: {exc}",
                )
                self.assertEqual(exc.reason_code, "runtime_json_invalid", msg=case_id)
            else:
                self.fail(f"{case_id} accepted hostile JSON")

        hostile_dict = _ExplosiveDict()
        hostile_list = _ExplosiveList()
        hostile_string = _ExplosiveString("hostile")
        operations: tuple[tuple[str, Callable[[], object]], ...] = (
            (
                "snapshot-builder:adapters",
                lambda: build_game_runtime_snapshot(
                    ROOT / "src" / "gamepack_runtime",
                    adapters=hostile_list,
                ),
            ),
            (
                "registry-builder:snapshot",
                lambda: build_runtime_adapter_registry(
                    snapshot=hostile_dict,
                ),
            ),
            (
                "registry-builder:adapters",
                lambda: build_runtime_adapter_registry(
                    snapshot=snapshot,
                    adapters=hostile_list,
                ),
            ),
            (
                "registry-integral:snapshot",
                lambda: validate_runtime_adapter_registry_document(
                    registry,
                    snapshot=hostile_dict,
                ),
            ),
            (
                "adapter-resolver:gamepack",
                lambda: resolve_runtime_adapter(
                    hostile_dict,
                    registry=registry,
                    snapshot=snapshot,
                ),
            ),
            (
                "adapter-resolver:registry",
                lambda: resolve_runtime_adapter(
                    gamepack,
                    registry=hostile_dict,
                    snapshot=snapshot,
                ),
            ),
            (
                "adapter-resolver:snapshot",
                lambda: resolve_runtime_adapter(
                    gamepack,
                    registry=registry,
                    snapshot=hostile_dict,
                ),
            ),
            (
                "composition-builder:gamepack",
                lambda: build_game_runtime_composition(
                    hostile_dict,
                    inventory,
                    ROOT,
                    registry=registry,
                    snapshot=snapshot,
                ),
            ),
            (
                "composition-builder:inventory",
                lambda: build_game_runtime_composition(
                    gamepack,
                    hostile_dict,
                    ROOT,
                    registry=registry,
                    snapshot=snapshot,
                ),
            ),
            (
                "composition-builder:registry",
                lambda: build_game_runtime_composition(
                    gamepack,
                    inventory,
                    ROOT,
                    registry=hostile_dict,
                    snapshot=snapshot,
                ),
            ),
            (
                "composition-builder:snapshot",
                lambda: build_game_runtime_composition(
                    gamepack,
                    inventory,
                    ROOT,
                    registry=registry,
                    snapshot=hostile_dict,
                ),
            ),
            (
                "composition-integral:value",
                lambda: validate_game_runtime_composition(
                    hostile_dict,
                    gamepack=gamepack,
                    inventory=inventory,
                    assetpack_root=ROOT,
                    registry=registry,
                    snapshot=snapshot,
                ),
            ),
            (
                "composition-integral:gamepack",
                lambda: validate_game_runtime_composition(
                    composition,
                    gamepack=hostile_dict,
                    inventory=inventory,
                    assetpack_root=ROOT,
                    registry=registry,
                    snapshot=snapshot,
                ),
            ),
            (
                "composition-integral:inventory",
                lambda: validate_game_runtime_composition(
                    composition,
                    gamepack=gamepack,
                    inventory=hostile_dict,
                    assetpack_root=ROOT,
                    registry=registry,
                    snapshot=snapshot,
                ),
            ),
            (
                "composition-integral:registry",
                lambda: validate_game_runtime_composition(
                    composition,
                    gamepack=gamepack,
                    inventory=inventory,
                    assetpack_root=ROOT,
                    registry=hostile_dict,
                    snapshot=snapshot,
                ),
            ),
            (
                "composition-integral:snapshot",
                lambda: validate_game_runtime_composition(
                    composition,
                    gamepack=gamepack,
                    inventory=inventory,
                    assetpack_root=ROOT,
                    registry=registry,
                    snapshot=hostile_dict,
                ),
            ),
            (
                "evidence-builder:composition",
                lambda: build_runtime_evidence(
                    hostile_dict,
                    platform_id="platform:linux_x86_64",
                    execution_status="native_verified",
                    packaging_status="verified",
                    checks=_checks("hostile"),
                ),
            ),
            (
                "evidence-builder:platform",
                lambda: build_runtime_evidence(
                    composition,
                    platform_id=hostile_string,
                    execution_status="native_verified",
                    packaging_status="verified",
                    checks=_checks("hostile"),
                ),
            ),
            (
                "evidence-builder:execution-status",
                lambda: build_runtime_evidence(
                    composition,
                    platform_id="platform:linux_x86_64",
                    execution_status=hostile_string,
                    packaging_status="verified",
                    checks=_checks("hostile"),
                ),
            ),
            (
                "evidence-builder:packaging-status",
                lambda: build_runtime_evidence(
                    composition,
                    platform_id="platform:linux_x86_64",
                    execution_status="native_verified",
                    packaging_status=hostile_string,
                    checks=_checks("hostile"),
                ),
            ),
            (
                "evidence-builder:checks",
                lambda: build_runtime_evidence(
                    composition,
                    platform_id="platform:linux_x86_64",
                    execution_status="native_verified",
                    packaging_status="verified",
                    checks=hostile_list,
                ),
            ),
            (
                "evidence-integral:composition",
                lambda: validate_runtime_evidence_document(
                    evidence,
                    composition=hostile_dict,
                ),
            ),
            (
                "feature-resolver:required",
                lambda: resolve_required_feature_support(
                    hostile_list,
                    adapter,
                ),
            ),
            (
                "feature-resolver:adapter",
                lambda: resolve_required_feature_support(
                    [],
                    hostile_dict,
                ),
            ),
            (
                "support-builder:composition",
                lambda: build_runtime_support_report(
                    hostile_dict,
                    gamepack=gamepack,
                    registry=registry,
                    snapshot=snapshot,
                    evidence=[],
                ),
            ),
            (
                "support-builder:gamepack",
                lambda: build_runtime_support_report(
                    composition,
                    gamepack=hostile_dict,
                    registry=registry,
                    snapshot=snapshot,
                    evidence=[],
                ),
            ),
            (
                "support-builder:registry",
                lambda: build_runtime_support_report(
                    composition,
                    gamepack=gamepack,
                    registry=hostile_dict,
                    snapshot=snapshot,
                    evidence=[],
                ),
            ),
            (
                "support-builder:snapshot",
                lambda: build_runtime_support_report(
                    composition,
                    gamepack=gamepack,
                    registry=registry,
                    snapshot=hostile_dict,
                    evidence=[],
                ),
            ),
            (
                "support-builder:evidence",
                lambda: build_runtime_support_report(
                    composition,
                    gamepack=gamepack,
                    registry=registry,
                    snapshot=snapshot,
                    evidence=hostile_list,
                ),
            ),
            (
                "support-integral:value",
                lambda: validate_runtime_support_report(
                    hostile_dict,
                    composition=composition,
                    gamepack=gamepack,
                    registry=registry,
                    snapshot=snapshot,
                    evidence=[],
                ),
            ),
            (
                "support-integral:composition",
                lambda: validate_runtime_support_report(
                    support_report,
                    composition=hostile_dict,
                    gamepack=gamepack,
                    registry=registry,
                    snapshot=snapshot,
                    evidence=[],
                ),
            ),
            (
                "support-integral:gamepack",
                lambda: validate_runtime_support_report(
                    support_report,
                    composition=composition,
                    gamepack=hostile_dict,
                    registry=registry,
                    snapshot=snapshot,
                    evidence=[],
                ),
            ),
            (
                "support-integral:registry",
                lambda: validate_runtime_support_report(
                    support_report,
                    composition=composition,
                    gamepack=gamepack,
                    registry=hostile_dict,
                    snapshot=snapshot,
                    evidence=[],
                ),
            ),
            (
                "support-integral:snapshot",
                lambda: validate_runtime_support_report(
                    support_report,
                    composition=composition,
                    gamepack=gamepack,
                    registry=registry,
                    snapshot=hostile_dict,
                    evidence=[],
                ),
            ),
            (
                "support-integral:evidence",
                lambda: validate_runtime_support_report(
                    support_report,
                    composition=composition,
                    gamepack=gamepack,
                    registry=registry,
                    snapshot=snapshot,
                    evidence=hostile_list,
                ),
            ),
            (
                "compatibility-resolver:gamepack",
                lambda: resolve_runtime_compatibility(
                    hostile_dict,
                    inventory,
                    ROOT,
                    registry=registry,
                    snapshot=snapshot,
                    evidence=[],
                ),
            ),
            (
                "compatibility-resolver:inventory",
                lambda: resolve_runtime_compatibility(
                    gamepack,
                    hostile_dict,
                    ROOT,
                    registry=registry,
                    snapshot=snapshot,
                    evidence=[],
                ),
            ),
            (
                "compatibility-resolver:registry",
                lambda: resolve_runtime_compatibility(
                    gamepack,
                    inventory,
                    ROOT,
                    registry=hostile_dict,
                    snapshot=snapshot,
                    evidence=[],
                ),
            ),
            (
                "compatibility-resolver:snapshot",
                lambda: resolve_runtime_compatibility(
                    gamepack,
                    inventory,
                    ROOT,
                    registry=registry,
                    snapshot=hostile_dict,
                    evidence=[],
                ),
            ),
            (
                "compatibility-resolver:evidence",
                lambda: resolve_runtime_compatibility(
                    gamepack,
                    inventory,
                    ROOT,
                    registry=registry,
                    snapshot=snapshot,
                    evidence=hostile_list,
                ),
            ),
        )
        for case_id, operation in operations:
            with self.subTest(case_id=case_id):
                assert_boundary_rejection(case_id, operation)

    def test_cli_rejects_a_resealed_untrusted_kernel_before_asset_loading(self) -> None:
        _gamepack, _composition, snapshot, registry = self._puzzle_inputs()
        changed_snapshot = copy.deepcopy(snapshot)
        kernel_entry = next(
            item
            for item in changed_snapshot["files"]
            if item["path"] == "gamepack_runtime/__init__.py"
        )
        kernel_entry["sha256"] = "b" * 64
        _reseal(changed_snapshot)
        changed_registry = copy.deepcopy(registry)
        changed_registry["runtime_snapshot"] = {
            "format": changed_snapshot["format"],
            "format_version": changed_snapshot["format_version"],
            "id": changed_snapshot["snapshot_id"],
            "content_hash": changed_snapshot["content_hash"],
        }
        _reseal(changed_registry)
        with tempfile.TemporaryDirectory(prefix="wf-runtime-cli-trust-") as temporary:
            root = Path(temporary)
            snapshot_path = root / "snapshot.json"
            registry_path = root / "registry.json"
            snapshot_path.write_text(
                json.dumps(changed_snapshot),
                encoding="utf-8",
            )
            registry_path.write_text(
                json.dumps(changed_registry),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            arguments = [
                "worldforge",
                "inspect-game-runtime",
                str(
                    ROOT / "examples/multigenre-contracts/abstract-puzzle/"
                    "artifacts/abstract-puzzle.gamepack.json"
                ),
                str(ROOT / "examples/multigenre-contracts/abstract-puzzle/assets/inventory.json"),
                str(root / "untrusted-assetpack"),
                "--registry",
                str(registry_path),
                "--snapshot",
                str(snapshot_path),
            ]
            with (
                mock.patch("sys.argv", arguments),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                self.assertEqual(main(), 1)
            self.assertEqual(stdout.getvalue(), "")
            error = json.loads(stderr.getvalue())
            self.assertEqual(error["reason_code"], "runtime_snapshot_untrusted")

    def test_platform_projection_is_closed_in_every_runtime_contract(self) -> None:
        _gamepack, composition, _snapshot, _registry = self._puzzle_inputs()
        mutations = []
        bad_composition = copy.deepcopy(composition)
        bad_composition["platforms"][0]["platform_family"] = "platform:windows"
        mutations.append(
            (
                validate_game_runtime_composition_document,
                _reseal(bad_composition),
            )
        )

        evidence = build_runtime_evidence(
            composition,
            platform_id="platform:linux_x86_64",
            execution_status="native_verified",
            packaging_status="verified",
            checks=_checks("closed_projection"),
        )
        bad_evidence = copy.deepcopy(evidence)
        bad_evidence["platform"]["platform_family"] = "platform:windows"
        mutations.append((validate_runtime_evidence_document, _reseal(bad_evidence)))

        report = _fixture(
            "examples/multigenre-contracts/abstract-puzzle/runtime/support-report.json"
        )
        bad_report = copy.deepcopy(report)
        bad_report["dimensions"]["execution"][0]["platform"]["platform_family"] = "platform:windows"
        mutations.append((validate_runtime_support_report_document, _reseal(bad_report)))
        for validator, document in mutations:
            with self.subTest(format=document["format"]):
                with self.assertRaisesRegex(
                    RuntimeContractError,
                    "runtime_platform_invalid",
                ):
                    validator(document)

    def test_structural_limits_cover_snapshot_files_tree_and_composition_bindings(
        self,
    ) -> None:
        _gamepack, composition, snapshot, _registry = self._puzzle_inputs()
        oversized_file = copy.deepcopy(snapshot)
        oversized_file["files"][0]["size_bytes"] = 4 * 1024 * 1024 + 1
        with self.assertRaisesRegex(
            RuntimeContractError,
            "runtime_snapshot_limit",
        ):
            validate_runtime_snapshot_document(_reseal(oversized_file))

        oversized_tree = copy.deepcopy(snapshot)
        for index in range(9):
            oversized_tree["files"].append(
                {
                    "path": f"gamepack_runtime/oversized-{index:02d}.py",
                    "sha256": f"{index + 1:064x}",
                    "size_bytes": 4 * 1024 * 1024,
                }
            )
        oversized_tree["files"].sort(key=lambda item: item["path"].encode("utf-8"))
        with self.assertRaisesRegex(
            RuntimeContractError,
            "runtime_snapshot_limit",
        ):
            validate_runtime_snapshot_document(_reseal(oversized_tree))

        oversized_binding = copy.deepcopy(composition)
        oversized_binding["bindings"][0]["size_bytes"] = 16 * 1024 * 1024 + 1
        with self.assertRaisesRegex(
            RuntimeContractError,
            "runtime_composition_limit",
        ):
            validate_game_runtime_composition_document(_reseal(oversized_binding))

    def test_external_evidence_ids_are_globally_unique(self) -> None:
        gamepack, composition, snapshot, registry = self._puzzle_inputs()
        shared_checks = _checks("shared_external")
        linux = build_runtime_evidence(
            composition,
            platform_id="platform:linux_x86_64",
            execution_status="native_verified",
            packaging_status="verified",
            checks=shared_checks,
        )
        windows = build_runtime_evidence(
            composition,
            platform_id="platform:windows_x86_64",
            execution_status="native_verified",
            packaging_status="verified",
            checks=shared_checks,
        )
        with self.assertRaisesRegex(
            RuntimeContractError,
            "runtime_support_evidence_collision",
        ):
            build_runtime_support_report(
                composition,
                gamepack=gamepack,
                registry=registry,
                snapshot=snapshot,
                evidence=[linux, windows],
            )

    def test_positive_support_claims_require_exact_nonempty_evidence_references(
        self,
    ) -> None:
        report = _fixture(
            "examples/multigenre-contracts/abstract-puzzle/runtime/support-report.json"
        )
        overclaim = copy.deepcopy(report)
        overclaim["evidence"] = []
        overclaim["dimensions"]["adapter"] = "verified"
        overclaim["dimensions"]["packaging"] = "verified"
        overclaim["dimensions"]["release"] = "ready"
        for execution in overclaim["dimensions"]["execution"]:
            execution["status"] = "native_verified"
            execution["evidence_ids"] = []
        for mechanic in overclaim["mechanics"]:
            mechanic["status"] = "supported_current"
            mechanic["reason_codes"] = []
            mechanic["test_evidence"] = []
            mechanic["native_evidence"] = []
        for feature in overclaim["features"]:
            feature["status"] = "supported_current"
            feature["reason_codes"] = []
            feature["evidence_ids"] = []
        overclaim["compatibility_status"] = "supported"
        overclaim["missing_capabilities"] = []
        overclaim["reason_codes"] = []
        overclaim["supported"] = True
        with self.assertRaisesRegex(
            RuntimeContractError,
            "runtime_support_(evidence|overclaim)",
        ):
            validate_runtime_support_report_document(_reseal(overclaim))

        contradiction = copy.deepcopy(report)
        contradiction["mechanics"][0]["reason_codes"] = []
        with self.assertRaisesRegex(
            RuntimeContractError,
            "runtime_support_contradiction",
        ):
            validate_runtime_support_report_document(_reseal(contradiction))

    def test_integral_support_validation_resolves_exact_evidence_objects(self) -> None:
        gamepack, composition, snapshot, registry = self._puzzle_inputs()
        linux = build_runtime_evidence(
            composition,
            platform_id="platform:linux_x86_64",
            execution_status="native_verified",
            packaging_status="verified",
            checks=_checks("integral_linux"),
        )
        report = build_runtime_support_report(
            composition,
            gamepack=gamepack,
            registry=registry,
            snapshot=snapshot,
            evidence=[linux],
        )
        self.assertEqual(
            report,
            validate_runtime_support_report(
                report,
                composition=composition,
                gamepack=gamepack,
                registry=registry,
                snapshot=snapshot,
                evidence=[linux],
            ),
        )
        self.assertEqual(
            report["evidence"],
            [
                {
                    "format": "world-forge.runtime_evidence",
                    "format_version": 1,
                    "id": linux["evidence_id"],
                    "content_hash": linux["content_hash"],
                    "platform": linux["platform"],
                    "execution_status": "native_verified",
                    "packaging_status": "verified",
                    "passed_check_kinds": [
                        "headless",
                        "native",
                        "packaging",
                        "save_replay",
                    ],
                }
            ],
        )
        with self.assertRaisesRegex(
            RuntimeContractError,
            "runtime_support_evidence_mismatch",
        ):
            validate_runtime_support_report(
                report,
                composition=composition,
                gamepack=gamepack,
                registry=registry,
                snapshot=snapshot,
                evidence=[],
            )

        crossed = copy.deepcopy(linux)
        crossed["checks"][0]["content_hash"] = "a" * 64
        _reseal(crossed)
        with self.assertRaisesRegex(
            RuntimeContractError,
            "runtime_support_evidence_mismatch",
        ):
            validate_runtime_support_report(
                report,
                composition=composition,
                gamepack=gamepack,
                registry=registry,
                snapshot=snapshot,
                evidence=[crossed],
            )

    def test_shared_reseal_parity_corpus_closes_every_review_case(self) -> None:
        corpus = _fixture("tests/fixtures/generic-runtime/parity-corpus.json")
        self.assertEqual(corpus["format"], "world-forge.runtime_parity_corpus")
        self.assertEqual(corpus["format_version"], 1)
        self.assertEqual(
            corpus["execution_semantics"],
            RUNTIME_EXECUTION_SEMANTICS_POLICY,
        )
        validators = {
            "game-runtime-composition": validate_game_runtime_composition_document,
            "game-runtime-snapshot": validate_runtime_snapshot_document,
            "generic-runtime-adapter": validate_runtime_adapter_document,
            "generic-runtime-adapter-registry": (validate_runtime_adapter_registry_document),
            "generic-runtime-evidence": validate_runtime_evidence_document,
            "generic-runtime-support-report": (validate_runtime_support_report_document),
        }
        valid = corpus["valid"]
        invalid = corpus["invalid"]
        self.assertGreaterEqual(len(valid), 7)
        self.assertGreaterEqual(len(invalid), 21)
        all_case_ids = [item["case_id"] for item in [*valid, *invalid]]
        self.assertEqual(len(all_case_ids), len(set(all_case_ids)))
        for case in valid:
            with self.subTest(case=case["case_id"]):
                self.assertEqual(
                    validators[case["kind"]](case["document"]),
                    case["document"],
                )
        for case in invalid:
            with self.subTest(case=case["case_id"]):
                with self.assertRaises(RuntimeContractError):
                    validators[case["kind"]](case["document"])


if __name__ == "__main__":
    unittest.main()
