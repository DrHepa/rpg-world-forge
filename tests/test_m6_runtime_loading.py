from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
import unicodedata
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from isoworld.content.loader import load_worldpack
from isoworld.content.renderpack import RenderPackError, load_renderpack
from isoworld.runtime_adapter import (
    RuntimeAdapterKey,
    RuntimeAdapterRegistryError,
    StaticRuntimeAdapterRegistry,
)
from worldforge.integrity import canonical_json_bytes, canonical_payload_hash
from worldforge.renderpack import build_renderpack
from worldforge.runtime_composition import (
    RuntimeCompositionDocuments,
    RuntimeCompositionError,
    load_registered_runtime_composition,
    load_runtime_composition_documents,
    verify_runtime_composition_files,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "examples/m6-contracts"
WORLDPACK = ROOT / "content/compiled/foundation.worldpack.json"


def _read_fixture(relative: str) -> dict[str, object]:
    value = json.loads((FIXTURES / relative).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _reseal(value: dict[str, object]) -> dict[str, object]:
    result = copy.deepcopy(value)
    result["content_hash"] = canonical_payload_hash(result)
    return result


class RuntimeCompositionLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.contracts = self.root / "contracts"
        self.contracts.mkdir()
        self.paths = {
            "capability_catalog_path": "contracts/capability-catalog.json",
            "presentation_profile_path": "contracts/profile.json",
            "runtime_adapter_path": "contracts/adapter.json",
            "composition_path": "contracts/composition.json",
        }
        fixtures = {
            "capability_catalog_path": "capability-catalog.json",
            "presentation_profile_path": "profiles/profile_2d.json",
            "runtime_adapter_path": "adapter.declared.json",
            "composition_path": "composition.json",
        }
        for name, fixture in fixtures.items():
            destination = self.root / self.paths[name]
            destination.write_bytes((FIXTURES / fixture).read_bytes())
        worldpack = self.root / "content/compiled/foundation.worldpack.json"
        worldpack.parent.mkdir(parents=True)
        worldpack.write_bytes(WORLDPACK.read_bytes())

    def _file_arguments(self) -> dict[str, object]:
        return {"root": self.root, **self.paths}

    def _write_document(self, name: str, value: dict[str, object]) -> None:
        (self.root / self.paths[name]).write_bytes(canonical_json_bytes(value))

    def _make_compatible(self) -> RuntimeAdapterKey:
        packs = self.root / "packs"
        packs.mkdir()
        worldpack_path = packs / "worldpack.json"
        worldpack_path.write_bytes(WORLDPACK.read_bytes())
        renderpack_path = packs / "renderpack.json"
        built = build_renderpack(
            ROOT / "examples/m5-neutral/renderpack/manifest.json",
            worldpack_path,
            renderpack_path,
        )

        adapter = _read_fixture("adapter.declared.json")
        adapter["state"] = "verified"
        adapter = _reseal(adapter)
        composition = _read_fixture("composition.json")
        composition["adapter"]["content_hash"] = adapter["content_hash"]
        composition["packs"] = {
            "renderpack": {
                "content_hash": built["content_hash"],
                "format": "isoworld.renderpack",
                "format_version": 1,
                "path": "packs/renderpack.json",
            },
            "worldpack": {
                "content_hash": composition["world_content_hash"],
                "format": "isoworld.worldpack",
                "format_version": 5,
                "path": "packs/worldpack.json",
            },
        }
        composition["slot_owners"] = [
            {
                "asset_id": "neutral_sheet",
                "pack": "renderpack",
                "plane": "world_base",
                "representation": "2d",
                "slot": "actor:neutral",
            }
        ]
        composition = _reseal(composition)
        self._write_document("runtime_adapter_path", adapter)
        self._write_document("composition_path", composition)
        return RuntimeAdapterKey(
            id=adapter["id"],
            version=adapter["version"],
            content_hash=adapter["content_hash"],
        )

    def test_loads_exact_four_documents_once_into_detached_canonical_snapshots(self) -> None:
        before = {
            name: (self.root / relative).read_bytes() for name, relative in self.paths.items()
        }
        from isoworld.runtime_io import decode_json_object as strict_decoder

        with patch(
            "worldforge.runtime_composition.decode_json_object",
            wraps=strict_decoder,
        ) as decoder:
            documents = load_runtime_composition_documents(**self._file_arguments())

        self.assertIsInstance(documents, RuntimeCompositionDocuments)
        self.assertEqual(4, decoder.call_count)
        self.assertEqual(
            _read_fixture("capability-catalog.json"),
            documents.capability_catalog,
        )
        detached = documents.runtime_adapter
        detached["state"] = "verified"
        self.assertEqual("declared", documents.runtime_adapter["state"])
        (self.root / self.paths["runtime_adapter_path"]).write_text(
            "{}\n",
            encoding="utf-8",
        )
        self.assertEqual("declared", documents.runtime_adapter["state"])
        for name, payload in before.items():
            if name != "runtime_adapter_path":
                self.assertEqual(payload, (self.root / self.paths[name]).read_bytes())
        with self.assertRaises(FrozenInstanceError):
            documents._runtime_adapter_bytes = b"{}"  # type: ignore[misc]

    def test_real_integral_m5_verification_returns_compatible_report(self) -> None:
        self._make_compatible()

        result = verify_runtime_composition_files(
            **self._file_arguments(),
            platform="linux_x86_64",
            runtime_api_version="0.5.0",
        )

        self.assertTrue(result.compatible)
        self.assertEqual((), result.issues)
        self.assertTrue(result.report["compatible"])
        loaded_world = load_worldpack(self.root / "packs/worldpack.json")
        with load_renderpack(self.root / "packs/renderpack.json", loaded_world) as loaded:
            self.assertEqual(
                result.report["pack_hashes"]["renderpack"],
                loaded.content_hash,
            )

    def test_valid_incompatible_files_return_recomputed_report_and_ignore_stale_report(
        self,
    ) -> None:
        stale = _read_fixture("compatibility-report.json")
        stale["compatible"] = True
        (self.contracts / "stale-report.json").write_text(
            json.dumps(stale),
            encoding="utf-8",
        )

        result = verify_runtime_composition_files(
            **self._file_arguments(),
            platform="linux_x86_64",
            runtime_api_version="0.5.0",
        )

        self.assertFalse(result.compatible)
        self.assertEqual(
            {"adapter_not_verified", "pack_unverified"},
            {issue.code for issue in result.issues},
        )
        self.assertFalse(result.report["compatible"])
        with self.assertRaises(TypeError):
            verify_runtime_composition_files(  # type: ignore[call-arg]
                **self._file_arguments(),
                platform="linux_x86_64",
                compatibility_report_path="contracts/stale-report.json",
            )

    def test_declared_adapter_is_rejected_before_registry_lookup(self) -> None:
        registry: StaticRuntimeAdapterRegistry[object] = StaticRuntimeAdapterRegistry()

        with (
            patch.object(
                StaticRuntimeAdapterRegistry,
                "resolve",
                side_effect=AssertionError("registry lookup must not occur"),
            ) as resolver,
            self.assertRaisesRegex(RuntimeCompositionError, "statically incompatible"),
        ):
            load_registered_runtime_composition(
                **self._file_arguments(),
                platform="linux_x86_64",
                registry=registry,
            )

        resolver.assert_not_called()

    def test_verified_adapter_requires_exact_registration_without_invocation(self) -> None:
        key = self._make_compatible()

        class OpaqueAdapter:
            def __init__(self) -> None:
                self.calls = 0

            def __call__(self) -> None:
                self.calls += 1

        opaque = OpaqueAdapter()
        source = {key: opaque}
        registry = StaticRuntimeAdapterRegistry(source)
        source.clear()

        loaded = load_registered_runtime_composition(
            **self._file_arguments(),
            platform="windows_x86_64",
            registry=registry,
        )

        self.assertTrue(loaded.verification.compatible)
        self.assertEqual(key, loaded.adapter_key)
        self.assertIs(opaque, loaded.adapter_value)
        self.assertEqual(0, opaque.calls)
        loaded.documents.composition["world_id"] = "mutated"
        self.assertEqual(
            "foundation_slice",
            loaded.documents.composition["world_id"],
        )
        with self.assertRaises(FrozenInstanceError):
            loaded.adapter_key = key  # type: ignore[misc]

    def test_registry_is_frozen_and_copies_source_collections(self) -> None:
        key = RuntimeAdapterKey("adapter", "1.2.3", "a" * 64)
        near_key = RuntimeAdapterKey("adapter", "1.2.3", "b" * 64)

        class OpaqueValue:
            def __deepcopy__(self, _memo: object) -> object:
                raise AssertionError("opaque registry values must not be copied")

        value = OpaqueValue()
        source_mapping = {key: value}
        mapping_registry = StaticRuntimeAdapterRegistry(source_mapping)
        source_mapping.clear()
        source_mapping[near_key] = OpaqueValue()
        self.assertIs(value, mapping_registry.resolve(key))
        with self.assertRaises(RuntimeAdapterRegistryError):
            mapping_registry.resolve(near_key)

        source_entries = [(key, value)]
        entries_registry = StaticRuntimeAdapterRegistry(source_entries)
        source_entries.clear()
        source_entries.append((near_key, OpaqueValue()))
        self.assertIs(value, entries_registry.resolve(key))
        with self.assertRaises(RuntimeAdapterRegistryError):
            entries_registry.resolve(near_key)

        self.assertIs(mapping_registry, copy.copy(mapping_registry))
        self.assertIs(mapping_registry, copy.deepcopy(mapping_registry))
        with self.assertRaises(FrozenInstanceError):
            mapping_registry._entries = {}  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            del mapping_registry._entries
        with self.assertRaises(TypeError):
            mapping_registry._entries[near_key] = value  # type: ignore[index]

    def test_registry_is_empty_safe_exact_and_duplicate_rejecting(self) -> None:
        key = RuntimeAdapterKey("adapter", "1.2.3", "a" * 64)
        value = object()
        empty: StaticRuntimeAdapterRegistry[object] = StaticRuntimeAdapterRegistry()
        with self.assertRaises(RuntimeAdapterRegistryError):
            empty.resolve(key)
        registry = StaticRuntimeAdapterRegistry([(key, value)])
        self.assertIs(value, registry.resolve(key))
        for wrong in (
            RuntimeAdapterKey("other", key.version, key.content_hash),
            RuntimeAdapterKey(key.id, "1.2.4", key.content_hash),
            RuntimeAdapterKey(key.id, key.version, "b" * 64),
        ):
            with self.subTest(wrong=wrong), self.assertRaises(RuntimeAdapterRegistryError):
                registry.resolve(wrong)
        with self.assertRaisesRegex(RuntimeAdapterRegistryError, "duplicate"):
            StaticRuntimeAdapterRegistry([(key, value), (key, object())])
        with self.assertRaises(RuntimeAdapterRegistryError):
            StaticRuntimeAdapterRegistry([("not-a-key", value)])  # type: ignore[list-item]
        self.assertFalse(hasattr(registry, "register"))

    def test_registry_cannot_be_subclassed_or_replaced_by_a_duck_type(self) -> None:
        with self.assertRaisesRegex(TypeError, "cannot be subclassed"):

            class MaliciousRegistry(StaticRuntimeAdapterRegistry[object]):
                def resolve(self, _key: RuntimeAdapterKey) -> object:
                    return object()

        key = self._make_compatible()
        near_key = RuntimeAdapterKey(key.id, key.version, "f" * 64)
        near_registry = StaticRuntimeAdapterRegistry([(near_key, object())])
        with self.assertRaisesRegex(RuntimeCompositionError, "exact code-owned"):
            load_registered_runtime_composition(
                **self._file_arguments(),
                platform="linux_x86_64",
                registry=near_registry,
            )

        class DuckRegistry:
            def resolve(self, _key: RuntimeAdapterKey) -> object:
                return object()

        with self.assertRaisesRegex(TypeError, "StaticRuntimeAdapterRegistry"):
            load_registered_runtime_composition(
                **self._file_arguments(),
                platform="linux_x86_64",
                registry=DuckRegistry(),  # type: ignore[arg-type]
            )

    def test_registry_never_hashes_or_compares_caller_controlled_key_objects(self) -> None:
        exact_key = self._make_compatible()
        sentinel = object()

        class MaliciousKey(RuntimeAdapterKey):
            def __hash__(self) -> int:
                return hash(exact_key)

            def __eq__(self, _other: object) -> bool:
                return True

        malicious = MaliciousKey("wrong_adapter", "9.9.9", "f" * 64)
        rejected = False
        try:
            registry = StaticRuntimeAdapterRegistry([(malicious, sentinel)])
        except RuntimeAdapterRegistryError:
            rejected = True
            registry = StaticRuntimeAdapterRegistry()
        self.assertTrue(rejected)
        with self.assertRaises(RuntimeAdapterRegistryError):
            registry.resolve(malicious)
        with self.assertRaisesRegex(RuntimeCompositionError, "exact code-owned"):
            load_registered_runtime_composition(
                **self._file_arguments(),
                platform="linux_x86_64",
                registry=registry,
            )

        class StringSubclass(str):
            pass

        malformed = (
            RuntimeAdapterKey(
                StringSubclass(exact_key.id),
                exact_key.version,
                exact_key.content_hash,
            ),
            RuntimeAdapterKey(exact_key.id, None, exact_key.content_hash),  # type: ignore[arg-type]
            RuntimeAdapterKey(exact_key.id, exact_key.version, 7),  # type: ignore[arg-type]
            RuntimeAdapterKey("", exact_key.version, exact_key.content_hash),
        )
        valid_registry = StaticRuntimeAdapterRegistry([(exact_key, object())])
        for invalid in malformed:
            with self.subTest(key=invalid):
                with self.assertRaises(RuntimeAdapterRegistryError):
                    StaticRuntimeAdapterRegistry([(invalid, sentinel)])
                with self.assertRaises(RuntimeAdapterRegistryError):
                    valid_registry.resolve(invalid)

    def test_registered_loading_rejects_wrong_id_version_hash_and_empty_registry(self) -> None:
        key = self._make_compatible()
        wrong_keys = (
            RuntimeAdapterKey("wrong_adapter", key.version, key.content_hash),
            RuntimeAdapterKey(key.id, "9.9.9", key.content_hash),
            RuntimeAdapterKey(key.id, key.version, "f" * 64),
            None,
        )
        for wrong in wrong_keys:
            entries = [] if wrong is None else [(wrong, object())]
            with (
                self.subTest(key=wrong),
                self.assertRaisesRegex(RuntimeCompositionError, "exact code-owned"),
            ):
                load_registered_runtime_composition(
                    **self._file_arguments(),
                    platform="linux_x86_64",
                    registry=StaticRuntimeAdapterRegistry(entries),
                )

    def test_rejects_nonportable_missing_and_out_of_root_paths(self) -> None:
        decomposed = unicodedata.normalize("NFD", "café.json")
        invalid = (
            ("../outside.json", "portable relative"),
            ("contracts\\adapter.json", "portable relative"),
            (str(self.root / "contracts/adapter.json"), "portable relative"),
            (f"contracts/{decomposed}", "portable relative"),
            ("contracts/missing.json", "JSON_MISSING"),
        )
        for value, message in invalid:
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(
                    RuntimeCompositionError,
                    message,
                ),
            ):
                load_runtime_composition_documents(
                    **{
                        **self._file_arguments(),
                        "runtime_adapter_path": value,
                    }
                )

        outside = self.root.parent / f"{self.root.name}-outside.json"
        outside.write_bytes((FIXTURES / "adapter.declared.json").read_bytes())
        self.addCleanup(outside.unlink)
        with self.assertRaisesRegex(RuntimeCompositionError, "portable relative"):
            load_runtime_composition_documents(
                **{
                    **self._file_arguments(),
                    "runtime_adapter_path": f"../{outside.name}",
                }
            )

    def test_rejects_cross_document_nfc_casefold_ambiguity(self) -> None:
        with self.assertRaisesRegex(RuntimeCompositionError, "NFC/casefold collision"):
            load_runtime_composition_documents(
                **{
                    **self._file_arguments(),
                    "presentation_profile_path": "contracts/Capability-Catalog.json",
                }
            )

    def test_nested_document_paths_use_pinned_component_traversal(self) -> None:
        nested = self.contracts / "nested/deeper"
        nested.mkdir(parents=True)
        adapter = self.root / self.paths["runtime_adapter_path"]
        adapter.rename(nested / "adapter.json")
        self.paths["runtime_adapter_path"] = "contracts/nested/deeper/adapter.json"

        documents = load_runtime_composition_documents(**self._file_arguments())

        self.assertEqual("neutral_contract_adapter", documents.runtime_adapter["id"])

    @unittest.skipUnless(os.name == "posix", "POSIX directory descriptors required")
    def test_intermediate_parent_swap_cannot_redirect_document_read(self) -> None:
        import worldforge.runtime_composition as runtime_composition_module

        nested = self.contracts / "nested"
        nested.mkdir()
        adapter = self.root / self.paths["runtime_adapter_path"]
        adapter.rename(nested / "adapter.json")
        self.paths["runtime_adapter_path"] = "contracts/nested/adapter.json"
        held = self.contracts / "nested-held"
        swapped = False
        real_open = os.open

        with tempfile.TemporaryDirectory() as outside_directory:
            outside = Path(outside_directory)
            outside_adapter = _read_fixture("adapter.declared.json")
            outside_adapter["id"] = "outside_adapter"
            outside_adapter = _reseal(outside_adapter)
            (outside / "adapter.json").write_bytes(canonical_json_bytes(outside_adapter))

            def swapping_open(
                path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal swapped
                if not swapped and Path(path).name == "adapter.json":
                    swapped = True
                    nested.rename(held)
                    nested.symlink_to(outside, target_is_directory=True)
                    try:
                        return real_open(path, flags, mode, dir_fd=dir_fd)
                    finally:
                        nested.unlink()
                        held.rename(nested)
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with patch.object(runtime_composition_module.os, "open", side_effect=swapping_open):
                documents = load_runtime_composition_documents(**self._file_arguments())

        self.assertTrue(swapped)
        self.assertEqual("neutral_contract_adapter", documents.runtime_adapter["id"])

    @unittest.skipUnless(os.name == "posix", "POSIX directory descriptors required")
    def test_root_replacement_is_rejected_and_pinned_descriptor_is_closed(self) -> None:
        import worldforge.runtime_composition as runtime_composition_module

        held = self.root.parent / f"{self.root.name}-held"
        real_open = os.open
        real_close = os.close
        replaced_descriptor: int | None = None
        closed: set[int] = set()

        def replacing_open(
            path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            nonlocal replaced_descriptor
            if replaced_descriptor is None and dir_fd is None and Path(path) == self.root:
                self.root.rename(held)
                self.root.mkdir()
                try:
                    replaced_descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
                    return replaced_descriptor
                finally:
                    self.root.rmdir()
                    held.rename(self.root)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        def tracking_close(descriptor: int) -> None:
            closed.add(descriptor)
            real_close(descriptor)

        with (
            patch.object(runtime_composition_module.os, "open", side_effect=replacing_open),
            patch.object(runtime_composition_module.os, "close", side_effect=tracking_close),
            self.assertRaisesRegex(RuntimeCompositionError, "root identity changed"),
        ):
            load_runtime_composition_documents(**self._file_arguments())

        self.assertIsNotNone(replaced_descriptor)
        self.assertIn(replaced_descriptor, closed)

    @unittest.skipUnless(os.name == "posix", "POSIX directory descriptors required")
    def test_all_descriptors_close_after_nested_document_decode_failure(self) -> None:
        import worldforge.runtime_composition as runtime_composition_module

        nested = self.contracts / "nested"
        nested.mkdir()
        adapter = self.root / self.paths["runtime_adapter_path"]
        adapter.rename(nested / "adapter.json")
        (nested / "adapter.json").write_bytes(b'{"id": 1, "id": 2}\n')
        self.paths["runtime_adapter_path"] = "contracts/nested/adapter.json"
        real_open = os.open
        opened: list[int] = []

        def tracking_open(
            path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            opened.append(descriptor)
            return descriptor

        with (
            patch.object(runtime_composition_module.os, "open", side_effect=tracking_open),
            self.assertRaisesRegex(RuntimeCompositionError, "JSON_DUPLICATE_KEY"),
        ):
            load_runtime_composition_documents(**self._file_arguments())

        self.assertGreaterEqual(len(opened), 3)
        for descriptor in opened:
            with self.subTest(descriptor=descriptor), self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_rejects_reparse_intermediate_parent(self) -> None:
        import worldforge.runtime_composition as runtime_composition_module

        if os.name != "posix":
            self.skipTest("POSIX entry-stat seam required")
        real_entry_stat = runtime_composition_module._posix_entry_stat

        def reparse_parent(parent_descriptor: int, name: str) -> object:
            info = real_entry_stat(parent_descriptor, name)
            if name == "contracts":
                return SimpleNamespace(
                    st_dev=info.st_dev,
                    st_ino=info.st_ino,
                    st_mode=info.st_mode,
                    st_nlink=info.st_nlink,
                    st_size=info.st_size,
                    st_mtime_ns=info.st_mtime_ns,
                    st_ctime_ns=info.st_ctime_ns,
                    st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
                )
            return info

        with (
            patch.object(
                runtime_composition_module,
                "_posix_entry_stat",
                side_effect=reparse_parent,
            ),
            self.assertRaisesRegex(RuntimeCompositionError, "not a plain directory"),
        ):
            load_runtime_composition_documents(**self._file_arguments())

    def test_rejects_nonregular_document(self) -> None:
        adapter_path = self.root / self.paths["runtime_adapter_path"]
        adapter_path.unlink()
        adapter_path.mkdir()
        with self.assertRaisesRegex(RuntimeCompositionError, "JSON_NOT_REGULAR"):
            load_runtime_composition_documents(**self._file_arguments())

    def test_rejects_hardlinked_document(self) -> None:
        adapter_path = self.root / self.paths["runtime_adapter_path"]
        hardlink = self.contracts / "adapter-hardlink.json"
        try:
            os.link(adapter_path, hardlink)
        except OSError as exc:
            self.skipTest(f"hardlinks unavailable: {exc}")
        with self.assertRaisesRegex(RuntimeCompositionError, "JSON_HARDLINK"):
            load_runtime_composition_documents(
                **{
                    **self._file_arguments(),
                    "runtime_adapter_path": "contracts/adapter-hardlink.json",
                }
            )

    def test_rejects_linked_document_and_unsafe_parent(self) -> None:
        adapter_path = self.root / self.paths["runtime_adapter_path"]
        original = adapter_path.read_bytes()
        outside = self.root / "outside-adapter.json"
        outside.write_bytes(original)
        adapter_path.unlink()
        try:
            adapter_path.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaisesRegex(RuntimeCompositionError, "JSON_SYMLINK"):
            load_runtime_composition_documents(**self._file_arguments())
        adapter_path.unlink()
        adapter_path.write_bytes(original)

        moved_contracts = self.root / "real-contracts"
        self.contracts.rename(moved_contracts)
        try:
            self.contracts.symlink_to(moved_contracts, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")
        with self.assertRaisesRegex(RuntimeCompositionError, "parent.*plain directory"):
            load_runtime_composition_documents(**self._file_arguments())

    def test_rejects_linked_root(self) -> None:
        alias = self.root.parent / f"{self.root.name}-alias"
        try:
            alias.symlink_to(self.root, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")
        self.addCleanup(alias.unlink)
        with self.assertRaisesRegex(RuntimeCompositionError, "root is unsafe"):
            load_runtime_composition_documents(
                **{
                    **self._file_arguments(),
                    "root": alias,
                }
            )

    def test_rejects_reparse_root(self) -> None:
        from worldforge import game_boundary_policy

        real_stat = game_boundary_policy._non_following_stat

        def reparse_root(candidate: Path) -> object:
            info = real_stat(candidate)
            if candidate == self.root:
                return SimpleNamespace(
                    st_mode=info.st_mode,
                    st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
                )
            return info

        with (
            patch.object(
                game_boundary_policy,
                "_non_following_stat",
                side_effect=reparse_root,
            ),
            self.assertRaisesRegex(RuntimeCompositionError, "FS_SYMLINK"),
        ):
            load_runtime_composition_documents(**self._file_arguments())

    def test_rejects_root_identity_change_between_document_reads(self) -> None:
        from isoworld.content.file_stat import path_file_stat as real_stat

        root_stats = 0

        def changing_root(candidate: Path) -> object:
            nonlocal root_stats
            info = real_stat(candidate)
            if candidate == self.root:
                root_stats += 1
                if root_stats >= 3:
                    return SimpleNamespace(
                        st_dev=info.st_dev,
                        st_ino=info.st_ino + 1,
                        st_mode=info.st_mode,
                        st_nlink=info.st_nlink,
                        st_size=info.st_size,
                        st_mtime_ns=info.st_mtime_ns,
                        st_ctime_ns=info.st_ctime_ns,
                        st_file_attributes=getattr(info, "st_file_attributes", 0),
                    )
            return info

        with (
            patch(
                "worldforge.runtime_composition.path_file_stat",
                side_effect=changing_root,
            ),
            self.assertRaisesRegex(RuntimeCompositionError, "root identity changed"),
        ):
            load_runtime_composition_documents(**self._file_arguments())

    def test_strict_json_and_contract_identity_fail_closed(self) -> None:
        catalog_path = self.root / self.paths["capability_catalog_path"]
        invalid_payloads = (
            (b'{"format": 1, "format": 2}\n', "JSON_DUPLICATE_KEY"),
            (b'{"value": NaN}\n', "JSON_NONFINITE"),
            (b'{"value": 1e9999}\n', "JSON_NUMBER_OVERFLOW"),
            (b"\xff", "JSON_NOT_UTF8"),
            (b"[]\n", "JSON_NOT_OBJECT"),
        )
        original = catalog_path.read_bytes()
        for payload, message in invalid_payloads:
            with self.subTest(message=message):
                catalog_path.write_bytes(payload)
                with self.assertRaisesRegex(RuntimeCompositionError, message):
                    load_runtime_composition_documents(**self._file_arguments())
        catalog_path.write_bytes(original)

        catalog = _read_fixture("capability-catalog.json")
        catalog["format"] = "wrong.format"
        catalog_path.write_bytes(canonical_json_bytes(_reseal(catalog)))
        with self.assertRaisesRegex(RuntimeCompositionError, "format or format_version"):
            load_runtime_composition_documents(**self._file_arguments())
        catalog["format"] = "rpg-world-forge.runtime_capability_catalog"
        catalog = _reseal(catalog)
        catalog["content_hash"] = "0" * 64
        catalog_path.write_bytes(canonical_json_bytes(catalog))
        with self.assertRaisesRegex(RuntimeCompositionError, "content hash"):
            load_runtime_composition_documents(**self._file_arguments())

    def test_supported_platforms_and_runtime_api_are_explicit(self) -> None:
        for platform in ("linux_x86_64", "windows_x86_64"):
            with self.subTest(platform=platform):
                result = verify_runtime_composition_files(
                    **self._file_arguments(),
                    platform=platform,
                    runtime_api_version="0.5.0",
                )
                self.assertNotIn(
                    "platform_unsupported",
                    {issue.code for issue in result.issues},
                )
        with self.assertRaisesRegex(RuntimeCompositionError, "platform is unsupported"):
            verify_runtime_composition_files(
                **self._file_arguments(),
                platform="macos_arm64",
            )
        incompatible_api = verify_runtime_composition_files(
            **self._file_arguments(),
            platform="linux_x86_64",
            runtime_api_version="1.0.0",
        )
        self.assertIn(
            "runtime_api_incompatible",
            {issue.code for issue in incompatible_api.issues},
        )
        with self.assertRaisesRegex(RuntimeCompositionError, "strict MAJOR.MINOR.PATCH"):
            verify_runtime_composition_files(
                **self._file_arguments(),
                platform="linux_x86_64",
                runtime_api_version="latest",
            )

    def test_document_io_fails_closed_without_safe_platform_primitives(self) -> None:
        import worldforge.runtime_composition as runtime_composition_module

        with (
            patch.object(runtime_composition_module, "_platform_name", return_value="unsupported"),
            self.assertRaisesRegex(RuntimeCompositionError, "I/O is unsupported"),
        ):
            load_runtime_composition_documents(**self._file_arguments())

        with (
            patch.object(runtime_composition_module, "_platform_name", return_value="posix"),
            patch.object(runtime_composition_module, "_SAFE_POSIX_DOCUMENT_IO", False),
            self.assertRaisesRegex(RuntimeCompositionError, "POSIX.*unavailable"),
        ):
            load_runtime_composition_documents(**self._file_arguments())

        with (
            patch.object(runtime_composition_module, "_platform_name", return_value="nt"),
            patch.object(
                runtime_composition_module,
                "_windows_open_directory_handle",
                side_effect=OSError("unavailable"),
            ),
            self.assertRaisesRegex(RuntimeCompositionError, "root could not be pinned"),
        ):
            load_runtime_composition_documents(**self._file_arguments())

    def test_hash_correlations_are_recomputed_as_incompatibility(self) -> None:
        composition = _read_fixture("composition.json")
        composition["adapter"]["id"] = "other_adapter"
        composition = _reseal(composition)
        self._write_document("composition_path", composition)

        result = verify_runtime_composition_files(
            **self._file_arguments(),
            platform="linux_x86_64",
        )

        self.assertFalse(result.compatible)
        self.assertIn("adapter_not_verified", {issue.code for issue in result.issues})
        self.assertEqual(
            composition["content_hash"],
            result.report["composition_hash"],
        )

    def test_renderpack_snapshot_is_closed_when_integral_verification_fails(self) -> None:
        key = self._make_compatible()
        self.assertIsInstance(key, RuntimeAdapterKey)

        class FailingRenderPack:
            entered = False
            exited = False

            def __enter__(self) -> FailingRenderPack:
                self.entered = True
                return self

            def __exit__(self, *_args: object) -> None:
                self.exited = True

            @property
            def assets(self) -> object:
                raise RenderPackError("synthetic post-open failure")

        failing = FailingRenderPack()
        with patch(
            "worldforge.runtime_composition.load_renderpack",
            return_value=failing,
        ):
            result = verify_runtime_composition_files(
                **self._file_arguments(),
                platform="linux_x86_64",
            )

        self.assertTrue(failing.entered)
        self.assertTrue(failing.exited)
        self.assertFalse(result.compatible)
        self.assertIn("pack_unverified", {issue.code for issue in result.issues})

    def test_runtime_registry_keeps_stdlib_boundary_and_m5_bytes_unchanged(self) -> None:
        tracked = (
            ROOT / "schemas/worldpack.schema.json",
            ROOT / "schemas/renderpack.schema.json",
            ROOT / "schemas/assetpack.schema.json",
            ROOT / "schemas/runtime-bundle.schema.json",
            WORLDPACK,
        )
        before = {path: path.read_bytes() for path in tracked}
        source = (ROOT / "src/isoworld/runtime_adapter.py").read_text(encoding="utf-8")
        for forbidden in (
            "worldforge",
            "importlib",
            "entry_points",
            "__import__",
            "os.environ",
            "PATH",
        ):
            self.assertNotIn(forbidden, source)

        self._make_compatible()
        verify_runtime_composition_files(
            **self._file_arguments(),
            platform="linux_x86_64",
        )

        self.assertEqual(before, {path: path.read_bytes() for path in tracked})


if __name__ == "__main__":
    unittest.main()
