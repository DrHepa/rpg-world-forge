from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest import mock

from scripts import studio_runtime_assembly as assembly
from scripts.studio_runtime_sources import REQUIRED_BLOCKERS

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DATE_EPOCH = 1_700_000_000


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_tree_metrics(value: object) -> tuple[int, int]:
    nodes = 0
    maximum_depth = 0
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        node, depth = stack.pop()
        nodes += 1
        maximum_depth = max(maximum_depth, depth)
        if isinstance(node, dict):
            stack.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, list):
            stack.extend((child, depth + 1) for child in node)
    return nodes, maximum_depth


def _write(path: Path, payload: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(mode)


def _forge_source(root: Path) -> Path:
    _write(root / "src/isoworld/__init__.py", b'"""Synthetic isoworld."""\n')
    _write(root / "src/isoworld/runtime_io.py", b"VALUE = 1\n")
    _write(root / "src/worldforge/__init__.py", b'"""Synthetic worldforge."""\n')
    _write(root / "src/worldforge/studio/__init__.py", b"")
    _write(root / "src/worldforge/studio/__main__.py", b"def main(): return 0\n")
    _write(root / "src/worldforge/studio/mcp_server.py", b"def main(): return 0\n")
    _write(root / "src/worldforge/templates/pyray_game/readme.tmpl", b"template\n")
    _write(root / "schemas/example.schema.json", b'{"type":"object"}\n')
    _write(root / "contracts/catalog.json", b'{"contracts":[]}\n')
    _write(root / "contracts/README.md", b"# Contracts\n")
    _write(
        root / "apps/studio/protocol/codex-app-server-0.144.6/manifest.json",
        b'{"format":"synthetic"}\n',
    )
    _write(
        root / "apps/studio/protocol/codex-app-server-0.144.6/types/example.ts",
        b"export type Example = string;\n",
    )
    _write(root / "LICENSE", b"MIT synthetic fixture\n")
    _write(root / "THIRD_PARTY_NOTICES.md", b"synthetic only\n")
    return root


def _tar_bytes(
    entries: list[tuple[str, bytes, int, bytes | None]],
) -> bytes:
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for name, payload, type_flag, link in entries:
                info = tarfile.TarInfo(name)
                info.mtime = 0
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                info.mode = (
                    0o755
                    if name.endswith(("codex", "codex.exe", "python3", "python3.12", "python.exe"))
                    else 0o644
                )
                info.type = type_flag
                if type_flag in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
                    info.linkname = (link or b"target").decode("ascii")
                    info.size = 0
                    archive.addfile(info)
                elif type_flag == tarfile.FIFOTYPE:
                    info.size = 0
                    archive.addfile(info)
                else:
                    info.size = len(payload)
                    archive.addfile(info, io.BytesIO(payload))
    return raw.getvalue()


def _archive(
    path: Path,
    *,
    component: str,
    payload_root: str,
    entrypoint: str,
    files: dict[str, bytes],
    extra_entries: list[tuple[str, bytes, int, bytes | None]] | None = None,
    inventory: bool = True,
) -> assembly.ArchiveSpec:
    entries = [
        (f"{payload_root}/{name}", payload, tarfile.REGTYPE, None)
        for name, payload in files.items()
    ]
    entries.extend(extra_entries or [])
    payload = _tar_bytes(entries)
    _write(path, payload)
    pins = (
        tuple(
            sorted(
                (
                    assembly.FilePin(name, len(value), _sha256(value))
                    for name, value in files.items()
                ),
                key=lambda item: item.path.encode("utf-8"),
            )
        )
        if inventory
        else None
    )
    return assembly.ArchiveSpec(
        component=component,
        path=path,
        filename=path.name,
        size=len(payload),
        sha256=_sha256(payload),
        payload_root=payload_root,
        entrypoint=f"{payload_root}/{entrypoint}",
        expected_inventory=pins,
    )


def _rewrite_zip(
    source: Path,
    destination: Path,
    mutation: str,
) -> None:
    with zipfile.ZipFile(source, "r") as archive:
        rows = [(info, archive.read(info)) for info in archive.infolist()]
    if mutation == "order":
        rows.reverse()
    with zipfile.ZipFile(
        destination,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        if mutation == "archive-comment":
            archive.comment = b"noncanonical"
        for index, (original, payload) in enumerate(rows):
            timestamp = original.date_time
            if mutation == "timestamp" and index == 0:
                timestamp = (2024, 1, 2, 3, 4, 6)
            info = zipfile.ZipInfo(original.filename, timestamp)
            info.create_system = original.create_system
            info.external_attr = original.external_attr
            info.internal_attr = original.internal_attr
            info.compress_type = original.compress_type
            if mutation == "entry-comment" and index == 0:
                info.comment = b"noncanonical"
            if mutation == "entry-extra" and index == 0:
                info.extra = b"\xfe\xca\x01\x00x"
            if mutation == "attributes" and index == 0:
                info.create_system = 0
                info.external_attr = 0
            if mutation == "compression" and index == 0:
                info.compress_type = zipfile.ZIP_STORED
            archive.writestr(
                info,
                payload,
                compress_type=info.compress_type,
                compresslevel=9 if info.compress_type == zipfile.ZIP_DEFLATED else None,
            )


def _set_first_zip_flag(source: Path, destination: Path) -> None:
    payload = bytearray(source.read_bytes())
    local = payload.find(b"PK\x03\x04")
    central = payload.find(b"PK\x01\x02")
    if local < 0 or central < 0:
        raise AssertionError("synthetic ZIP lacks required headers")
    local_flags = int.from_bytes(payload[local + 6 : local + 8], "little") | 0x0002
    central_flags = int.from_bytes(payload[central + 8 : central + 10], "little") | 0x0002
    payload[local + 6 : local + 8] = local_flags.to_bytes(2, "little")
    payload[central + 8 : central + 10] = central_flags.to_bytes(2, "little")
    destination.write_bytes(payload)


class StudioRuntimeAssemblyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="rwf-studio-assembly-")
        self.root = Path(self.temporary.name)
        self.source = _forge_source(self.root / "forge")

    def tearDown(self) -> None:
        assembly._READ_TEST_HOOK = None
        assembly._WRITE_TEST_HOOK = None
        self.temporary.cleanup()

    def _plan(
        self,
        target_id: str = "linux-x64",
        *,
        source: Path | None = None,
        archives: Path | None = None,
    ) -> assembly.AssemblyPlan:
        archive_root = archives or self.root / f"archives-{target_id}"
        archive_root.mkdir(parents=True, exist_ok=True)
        if target_id == "linux-x64":
            codex_root = "package/vendor/x86_64-unknown-linux-musl"
            codex_entry = "bin/codex"
            codex_files = {
                "bin/codex": b"synthetic-linux-codex",
                "bin/codex-code-mode-host": b"mode-host",
                "codex-package.json": b"{}",
                "codex-path/rg": b"rg",
                "codex-resources/bwrap": b"bwrap",
                "codex-resources/zsh/bin/zsh": b"zsh",
            }
            python_entry = "bin/python3"
            python_files = {
                "bin/python3": b"synthetic-linux-python",
                "lib/python3.12/os.py": b"# stdlib fixture\n",
            }
        else:
            codex_root = "package/vendor/x86_64-pc-windows-msvc"
            codex_entry = "bin/codex.exe"
            codex_files = {
                "bin/codex.exe": b"synthetic-windows-codex",
                "bin/codex-code-mode-host.exe": b"mode-host",
                "codex-package.json": b"{}",
                "codex-path/rg.exe": b"rg",
                "codex-resources/codex-command-runner.exe": b"runner",
                "codex-resources/codex-windows-sandbox-setup.exe": b"sandbox",
            }
            python_entry = "python.exe"
            python_files = {
                "python.exe": b"synthetic-windows-python",
                "Lib/os.py": b"# stdlib fixture\n",
            }
        codex = _archive(
            archive_root / "codex.tgz",
            component="codex",
            payload_root=codex_root,
            entrypoint=codex_entry,
            files=codex_files,
        )
        python = _archive(
            archive_root / "python.tar.gz",
            component="python",
            payload_root="python",
            entrypoint=python_entry,
            files=python_files,
        )
        return assembly.AssemblyPlan(
            target_id=target_id,
            assembly_kind="synthetic_test_fixture",
            runtime_sources_sha256=_sha256(b"synthetic runtime sources"),
            source_date_epoch=SOURCE_DATE_EPOCH,
            codex=codex,
            python=python,
            forge_source_root=source or self.source,
            open_blocker_codes=tuple(REQUIRED_BLOCKERS),
        )

    def _assemble(self, target_id: str = "linux-x64", name: str = "output") -> Path:
        output = self.root / name
        assembly.assemble_runtime_resources(self._plan(target_id), output)
        return output

    def _canonical_linux_pbs_manifest(self) -> tuple[dict[str, object], bytes]:
        plan = self._plan("linux-x64")
        manifest = assembly._package_manifest(plan, assembly._output_files(plan))
        receipt_bytes = assembly.DEFAULT_ARCHIVE_NORMALIZATION.read_bytes()
        receipt = assembly._parse_archive_normalization(receipt_bytes)
        inventory = [entry for entry in manifest["inventory"] if entry["component"] != "python"]
        inventory.extend(
            {
                "component": "python",
                "mode": item.mode,
                "path": f"runtime/python/linux-x64/{item.source}",
                "sha256": item.sha256,
                "size": item.size,
            }
            for item in receipt.files
        )
        inventory.append(
            {
                "component": "control",
                "mode": 0o644,
                "path": assembly.NORMALIZATION_PACKAGE_PATH,
                "sha256": assembly.NORMALIZATION_SHA256,
                "size": assembly.NORMALIZATION_SIZE,
            }
        )
        manifest["inventory"] = sorted(
            inventory,
            key=lambda item: item["path"].encode("utf-8"),
        )
        manifest["sources"]["python"] = {
            "archive": {
                "entrypoint": "python/bin/python3",
                "filename": assembly.LINUX_PBS_FILENAME,
                "payload_root": "python",
                "sha256": assembly.LINUX_PBS_SHA256,
                "size": assembly.LINUX_PBS_SIZE,
            },
            "normalization": assembly._normalization_identity(),
            "version": assembly.PYTHON_VERSION,
        }
        return manifest, receipt_bytes

    def _verified_manifest(
        self,
        target_id: str,
    ) -> tuple[dict[str, object], bytes | None, bytes]:
        if target_id == "linux-x64":
            manifest, receipt_bytes = self._canonical_linux_pbs_manifest()
        else:
            plan = self._plan(target_id)
            manifest = assembly._package_manifest(plan, assembly._output_files(plan))
            receipt_bytes = None
        runtime_sources = assembly._read_runtime_sources_bytes()
        document = assembly._parse_runtime_sources_control(runtime_sources)
        codex_identity, python_identity, blocker_codes = (
            assembly._runtime_source_archive_identities(document, target_id)
        )
        manifest["assembly_kind"] = "verified_development_runtime"
        manifest["open_blocker_codes"] = list(blocker_codes)
        manifest["sources"]["codex"]["archive"] = codex_identity
        manifest["sources"]["python"]["archive"] = python_identity
        manifest["sources"]["runtime_sources"] = assembly._runtime_sources_identity()
        manifest["sources"]["runtime_sources_sha256"] = assembly.RUNTIME_SOURCES_SHA256
        manifest["inventory"].append(
            {
                "component": "control",
                "mode": 0o644,
                "path": assembly.RUNTIME_SOURCES_PACKAGE_PATH,
                "sha256": assembly.RUNTIME_SOURCES_SHA256,
                "size": assembly.RUNTIME_SOURCES_SIZE,
            }
        )
        manifest["inventory"].sort(key=lambda item: item["path"].encode("utf-8"))
        return manifest, receipt_bytes, runtime_sources

    def _manifest_with_inventory_count(self, count: int) -> dict[str, object]:
        plan = self._plan("linux-x64")
        manifest = assembly._package_manifest(plan, assembly._output_files(plan))
        inventory = list(manifest["inventory"])
        if count < len(inventory):
            raise AssertionError("requested inventory is smaller than the required fixture")
        for index in range(count - len(inventory)):
            inventory.append(
                {
                    "component": "forge",
                    "mode": 0o644,
                    "path": (f"runtime/python/linux-x64/lib/package-budget/filler-{index:05d}.py"),
                    "sha256": _sha256(b""),
                    "size": 0,
                }
            )
        inventory.sort(key=lambda item: item["path"].encode("utf-8"))
        manifest["inventory"] = inventory
        forge_inventory = [entry for entry in inventory if entry["component"] == "forge"]
        manifest["sources"]["forge"]["inventory_sha256"] = _sha256(
            assembly._canonical_json_bytes(forge_inventory)
        )
        return manifest

    def test_real_cli_fails_before_cache_or_output_mutation_with_all_blockers(self) -> None:
        cache = self.root / "must-not-create-cache"
        output = self.root / "must-not-create-output"
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/studio_runtime_assembly.py",
                "assemble",
                "--target",
                "linux-x64",
                "--cache-dir",
                os.fspath(cache),
                "--output-dir",
                os.fspath(output),
                "--source-date-epoch",
                str(SOURCE_DATE_EPOCH),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        payload = json.loads(completed.stderr)
        self.assertFalse(payload["valid"])
        self.assertEqual(payload["error"]["code"], "redistribution_blocked")
        self.assertEqual(
            payload["error"]["open_blocker_codes"],
            list(REQUIRED_BLOCKERS),
        )
        self.assertFalse(cache.exists())
        self.assertFalse(output.exists())

    def test_committed_production_plans_use_rooted_entrypoints_and_exact_receipt(
        self,
    ) -> None:
        source_bytes = assembly.DEFAULT_SOURCE.read_bytes()
        document = assembly.load_strict_json_bytes(source_bytes)
        for target_id in ("linux-x64", "win32-x64"):
            with self.subTest(target_id=target_id):
                plan = assembly._production_plan(
                    document,
                    source_bytes,
                    target_id=target_id,
                    cache_dir=self.root / "cache",
                    source_date_epoch=SOURCE_DATE_EPOCH,
                )
                assembly._validate_plan(plan)
                self.assertTrue(plan.codex.entrypoint.startswith(f"{plan.codex.payload_root}/"))
                self.assertTrue(plan.python.entrypoint.startswith(f"{plan.python.payload_root}/"))
                if target_id == "linux-x64":
                    receipt = plan.python.normalization
                    self.assertIsNotNone(receipt)
                    assert receipt is not None
                    self.assertEqual(receipt.archive_sha256, plan.python.sha256)
                    self.assertEqual(receipt.relative_symlink_count, 1048)
                    self.assertEqual(len(receipt.casefold_directories), 8)
                    self.assertEqual(len(receipt.casefold_files), 25)
                    self.assertEqual(len(receipt.files), 4522)
                    self.assertEqual(receipt.output_file_count, 4522)
                    self.assertEqual(receipt.output_bytes, 193644409)
                else:
                    self.assertIsNone(plan.python.normalization)

    def test_canonical_linux_pbs_receipt_binds_the_complete_package_inventory(
        self,
    ) -> None:
        manifest, receipt_bytes = self._canonical_linux_pbs_manifest()
        validated = assembly.validate_package_manifest(
            manifest,
            normalization_receipt=receipt_bytes,
        )
        self.assertIs(validated, manifest)
        self.assertEqual(len(receipt_bytes), 1_031_213)
        self.assertEqual(_sha256(receipt_bytes), assembly.NORMALIZATION_SHA256)
        python_entries = [
            entry for entry in manifest["inventory"] if entry["component"] == "python"
        ]
        self.assertEqual(len(python_entries), 4_522)
        control = next(
            entry
            for entry in manifest["inventory"]
            if entry["path"] == assembly.NORMALIZATION_PACKAGE_PATH
        )
        self.assertEqual(
            control,
            {
                "component": "control",
                "mode": 0o644,
                "path": assembly.NORMALIZATION_PACKAGE_PATH,
                "sha256": assembly.NORMALIZATION_SHA256,
                "size": assembly.NORMALIZATION_SIZE,
            },
        )

    def test_verified_manifests_require_exact_committed_runtime_provenance(
        self,
    ) -> None:
        for target_id in ("linux-x64", "win32-x64"):
            with self.subTest(target_id=target_id):
                manifest, receipt, runtime_sources = self._verified_manifest(target_id)
                assembly.validate_package_manifest(
                    manifest,
                    normalization_receipt=receipt,
                    runtime_sources_provenance=runtime_sources,
                )

        synthetic_plan = self._plan()
        synthetic = assembly._package_manifest(
            synthetic_plan,
            assembly._output_files(synthetic_plan),
        )
        synthetic["assembly_kind"] = "verified_development_runtime"
        with self.assertRaisesRegex(
            assembly.RuntimeAssemblyError,
            "package_manifest_invalid",
        ):
            assembly.validate_package_manifest(synthetic)

        verified, receipt, runtime_sources = self._verified_manifest("linux-x64")
        altered = bytearray(runtime_sources)
        altered[-2] ^= 1
        with self.assertRaises(assembly.RuntimeAssemblyError):
            assembly.validate_package_manifest(
                verified,
                normalization_receipt=receipt,
                runtime_sources_provenance=bytes(altered),
            )

    def test_package_json_parser_enforces_exact_byte_node_and_depth_limits(
        self,
    ) -> None:
        exact_bytes = b"{}      "
        self.assertEqual(
            assembly.load_strict_json_bytes(
                exact_bytes,
                max_bytes=len(exact_bytes),
                max_depth=0,
                max_nodes=1,
            ),
            {},
        )
        with self.assertRaisesRegex(assembly.RuntimeSourcesError, "invalid_json"):
            assembly.load_strict_json_bytes(
                exact_bytes + b" ",
                max_bytes=len(exact_bytes),
                max_depth=0,
                max_nodes=1,
            )

        exact_nodes = b'{"values":[0,0]}'
        self.assertEqual(
            assembly.load_strict_json_bytes(
                exact_nodes,
                max_bytes=len(exact_nodes),
                max_depth=2,
                max_nodes=4,
            ),
            {"values": [0, 0]},
        )
        with self.assertRaisesRegex(assembly.RuntimeSourcesError, "invalid_json"):
            assembly.load_strict_json_bytes(
                exact_nodes,
                max_bytes=len(exact_nodes),
                max_depth=2,
                max_nodes=3,
            )

        exact_depth = b'{"a":{"b":{"c":{"d":0}}}}'
        self.assertEqual(
            assembly.load_strict_json_bytes(
                exact_depth,
                max_bytes=len(exact_depth),
                max_depth=4,
                max_nodes=5,
            )["a"]["b"]["c"]["d"],
            0,
        )
        over_depth = b'{"a":{"b":{"c":{"d":{"e":0}}}}}'
        with self.assertRaisesRegex(assembly.RuntimeSourcesError, "invalid_json"):
            assembly.load_strict_json_bytes(
                over_depth,
                max_bytes=len(over_depth),
                max_depth=4,
                max_nodes=6,
            )

    def test_package_manifest_limits_close_the_17023_entry_json_gap(self) -> None:
        exact = self._manifest_with_inventory_count(assembly.MAX_PACKAGE_INVENTORY_ENTRIES)
        self.assertEqual(
            _json_tree_metrics(exact),
            (
                assembly.MAX_PACKAGE_INVENTORY_ENTRIES * 6 + 46,
                assembly.MAX_PACKAGE_JSON_DEPTH,
            ),
        )
        exact_bytes = assembly._canonical_json_bytes(exact)
        self.assertEqual(
            assembly.load_strict_json_bytes(
                exact_bytes,
                max_bytes=assembly.MAX_PACKAGE_MANIFEST_BYTES,
                max_depth=assembly.MAX_PACKAGE_JSON_DEPTH,
                max_nodes=assembly.MAX_PACKAGE_JSON_NODES,
            ),
            exact,
        )
        assembly.validate_package_manifest(exact)

        one_over = self._manifest_with_inventory_count(assembly.MAX_PACKAGE_INVENTORY_ENTRIES + 1)
        one_over_bytes = assembly._canonical_json_bytes(one_over)
        self.assertEqual(
            assembly.load_strict_json_bytes(
                one_over_bytes,
                max_bytes=assembly.MAX_PACKAGE_MANIFEST_BYTES,
                max_depth=assembly.MAX_PACKAGE_JSON_DEPTH,
                max_nodes=assembly.MAX_PACKAGE_JSON_NODES,
            ),
            one_over,
        )
        with self.assertRaisesRegex(
            assembly.RuntimeAssemblyError,
            "package_manifest_invalid",
        ):
            assembly.validate_package_manifest(one_over)

        inconsistent = self._manifest_with_inventory_count(17_023)
        self.assertEqual(_json_tree_metrics(inconsistent), (102_184, 4))
        inconsistent_bytes = assembly._canonical_json_bytes(inconsistent)
        with self.assertRaisesRegex(assembly.RuntimeSourcesError, "invalid_json"):
            assembly.load_strict_json_bytes(
                inconsistent_bytes,
                max_bytes=assembly.MAX_PACKAGE_MANIFEST_BYTES,
                max_depth=assembly.MAX_PACKAGE_JSON_DEPTH,
                max_nodes=assembly.MAX_PACKAGE_JSON_NODES,
            )
        with self.assertRaisesRegex(
            assembly.RuntimeAssemblyError,
            "package_manifest_invalid",
        ):
            assembly.validate_package_manifest(inconsistent)

    def test_package_budget_covers_exact_pbs_codex_and_project_inventory(
        self,
    ) -> None:
        source_document = assembly._parse_runtime_sources_control(
            assembly._read_runtime_sources_bytes()
        )
        codex_target = next(
            item for item in source_document["codex"]["targets"] if item["target_id"] == "linux-x64"
        )
        exact_inventory = (
            4_522
            + len(codex_target["inventory"])
            + len(
                assembly._source_files(
                    ROOT,
                    "linux-x64",
                    assembly._PackageOutputBudget(),
                )
            )
            + 3
        )
        self.assertEqual(exact_inventory, 5_557)
        self.assertLessEqual(
            exact_inventory * assembly._PACKAGE_INVENTORY_ENTRY_JSON_NODES
            + assembly._PACKAGE_NON_INVENTORY_MAX_JSON_NODES,
            assembly.MAX_PACKAGE_JSON_NODES,
        )
        self.assertLessEqual(
            exact_inventory * assembly._PACKAGE_INVENTORY_ENTRY_MAX_CANONICAL_BYTES
            + assembly._PACKAGE_NON_INVENTORY_MAX_CANONICAL_BYTES,
            assembly.MAX_PACKAGE_MANIFEST_BYTES,
        )

    def test_assembler_preflights_manifest_readability_before_output_creation(
        self,
    ) -> None:
        output = self.root / "unreadable-manifest-output"
        with (
            mock.patch.object(assembly, "MAX_PACKAGE_JSON_NODES", 1),
            self.assertRaisesRegex(
                assembly.RuntimeAssemblyError,
                "package_manifest_limit",
            ),
        ):
            assembly.assemble_runtime_resources(self._plan(), output)
        self.assertFalse(output.exists())

    def test_source_collection_rejects_mocked_16389_files_before_read_or_output(
        self,
    ) -> None:
        plan = self._plan()
        output = self.root / "source-count-overflow-output"
        source_directory = self.source / "src/isoworld"
        regular_info = (source_directory / "__init__.py").lstat()
        original_scandir = assembly.os.scandir
        original_read = assembly._read_pinned_regular
        fake_stats = 0
        fake_reads = 0

        def fake_stat(*, follow_symlinks: bool = True) -> os.stat_result:
            nonlocal fake_stats
            self.assertFalse(follow_symlinks)
            fake_stats += 1
            return regular_info

        def iter_entries(path: os.PathLike[str] | str):
            with original_scandir(path) as entries:
                yield from entries
            if Path(path) == source_directory:
                for index in range(16_389):
                    name = f"mocked-{index:05d}.py"
                    yield SimpleNamespace(
                        name=name,
                        path=os.fspath(source_directory / name),
                        stat=fake_stat,
                    )

        def fake_scandir(path: os.PathLike[str] | str):
            return contextlib.closing(iter_entries(path))

        def tracked_read(path: Path, **kwargs: object) -> bytes:
            nonlocal fake_reads
            if path.parent == source_directory and path.name.startswith("mocked-"):
                fake_reads += 1
                return b""
            return original_read(path, **kwargs)

        with (
            mock.patch.object(assembly.os, "scandir", side_effect=fake_scandir),
            mock.patch.object(
                assembly,
                "_read_pinned_regular",
                side_effect=tracked_read,
            ),
            self.assertRaisesRegex(
                assembly.RuntimeAssemblyError,
                "output_limit_exceeded",
            ),
        ):
            assembly.assemble_runtime_resources(plan, output)
        self.assertFalse(output.exists())
        self.assertLess(fake_reads, 16_389)
        self.assertEqual(fake_stats, fake_reads + 1)

    def test_source_collection_honors_exact_remaining_package_bounds(
        self,
    ) -> None:
        plan = self._plan()
        captured: list[assembly._NamespaceBudget] = []
        original_factory = assembly._output_namespace_budget

        def capture_factory(code: str, field: str) -> assembly._NamespaceBudget:
            budget = original_factory(code, field)
            captured.append(budget)
            return budget

        with mock.patch.object(
            assembly,
            "_output_namespace_budget",
            side_effect=capture_factory,
        ):
            files = assembly._output_files(plan)
        self.assertEqual(len(captured), 1)
        baseline = captured[0]
        exact_files = len(baseline.files)
        exact_directories = len(baseline.directories)
        exact_nodes = len(baseline.node_paths)
        exact_bytes = sum(len(item.payload) for item in files.values())
        forge_files = sum(item.component == "forge" for item in files.values())

        def bounded_factory(
            code: str,
            field: str,
            *,
            file_limit: int = exact_files,
            node_limit: int = exact_nodes,
        ) -> assembly._NamespaceBudget:
            return assembly._NamespaceBudget(
                file_limit=file_limit,
                directory_limit=exact_directories,
                node_limit=node_limit,
                code=code,
                field=field,
            )

        exact_output = self.root / "source-exact-bound-output"
        with (
            mock.patch.object(
                assembly,
                "_output_namespace_budget",
                side_effect=bounded_factory,
            ),
            mock.patch.object(assembly, "MAX_OUTPUT_BYTES", exact_bytes),
        ):
            result = assembly.assemble_runtime_resources(plan, exact_output)
        self.assertEqual(result.files + 1, exact_files)
        self.assertEqual(result.bytes, exact_bytes)

        count_output = self.root / "source-count-one-over-output"
        with (
            mock.patch.object(
                assembly,
                "_output_namespace_budget",
                side_effect=lambda code, field: bounded_factory(
                    code,
                    field,
                    file_limit=exact_files - 1,
                ),
            ),
            mock.patch.object(assembly, "MAX_OUTPUT_BYTES", exact_bytes),
            self.assertRaisesRegex(
                assembly.RuntimeAssemblyError,
                "output_limit_exceeded",
            ),
        ):
            assembly.assemble_runtime_resources(plan, count_output)
        self.assertFalse(count_output.exists())

        node_output = self.root / "source-node-one-over-output"
        with (
            mock.patch.object(
                assembly,
                "_output_namespace_budget",
                side_effect=lambda code, field: bounded_factory(
                    code,
                    field,
                    node_limit=exact_nodes - 1,
                ),
            ),
            mock.patch.object(assembly, "MAX_OUTPUT_BYTES", exact_bytes),
            self.assertRaisesRegex(
                assembly.RuntimeAssemblyError,
                "output_limit_exceeded",
            ),
        ):
            assembly.assemble_runtime_resources(plan, node_output)
        self.assertFalse(node_output.exists())

        source_reads: list[Path] = []

        def track_source_read(path: Path, phase: str) -> None:
            if phase == "after_lstat" and path.is_relative_to(self.source):
                source_reads.append(path)

        assembly._READ_TEST_HOOK = track_source_read
        bytes_output = self.root / "source-byte-one-over-output"
        with (
            mock.patch.object(
                assembly,
                "_output_namespace_budget",
                side_effect=bounded_factory,
            ),
            mock.patch.object(assembly, "MAX_OUTPUT_BYTES", exact_bytes - 1),
            self.assertRaisesRegex(
                assembly.RuntimeAssemblyError,
                "output_limit_exceeded",
            ),
        ):
            assembly.assemble_runtime_resources(plan, bytes_output)
        assembly._READ_TEST_HOOK = None
        self.assertFalse(bytes_output.exists())
        self.assertLess(len(source_reads), forge_files)

    def test_portable_alias_contract_checks_every_intermediate_directory(
        self,
    ) -> None:
        normalization = assembly._load_archive_normalization()
        prefix = "runtime/python/linux-x64"
        receipted_paths = [f"{prefix}/{item.source}" for item in normalization.files]
        assembly._validate_portable_path_aliases(
            receipted_paths,
            normalization=normalization,
            prefix=prefix,
            code="package_manifest_invalid",
            field="$.inventory",
        )
        self.assertIn(
            assembly.CasefoldDirectoryPair(
                first="share/terminfo/A",
                second="share/terminfo/a",
            ),
            normalization.casefold_directories,
        )

        with self.assertRaisesRegex(
            assembly.RuntimeAssemblyError,
            "package_manifest_invalid",
        ):
            assembly._validate_portable_path_aliases(
                [
                    *receipted_paths,
                    f"{prefix}/lib/A/x.py",
                    f"{prefix}/lib/a/y.py",
                ],
                normalization=normalization,
                prefix=prefix,
                code="package_manifest_invalid",
                field="$.inventory",
            )

        plan = self._plan()
        manifest = assembly._package_manifest(plan, assembly._output_files(plan))
        manifest["inventory"].extend(
            (
                {
                    "component": "forge",
                    "mode": 0o644,
                    "path": f"{prefix}/lib/A/x.py",
                    "sha256": _sha256(b"x"),
                    "size": 1,
                },
                {
                    "component": "forge",
                    "mode": 0o644,
                    "path": f"{prefix}/lib/a/y.py",
                    "sha256": _sha256(b"y"),
                    "size": 1,
                },
            )
        )
        manifest["inventory"].sort(key=lambda item: item["path"].encode("utf-8"))
        forge_inventory = [
            entry for entry in manifest["inventory"] if entry["component"] == "forge"
        ]
        manifest["sources"]["forge"]["inventory_sha256"] = _sha256(
            assembly._canonical_json_bytes(forge_inventory)
        )
        with self.assertRaisesRegex(
            assembly.RuntimeAssemblyError,
            "package_manifest_invalid",
        ):
            assembly.validate_package_manifest(manifest)

    def test_linux_pbs_receipt_rejects_incomplete_missing_resealed_and_extra_groups(
        self,
    ) -> None:
        manifest, receipt_bytes = self._canonical_linux_pbs_manifest()

        incomplete = json.loads(json.dumps(manifest))
        incomplete["inventory"] = [
            entry
            for entry in incomplete["inventory"]
            if entry["path"] != "runtime/python/linux-x64/bin/python3.12"
        ]
        with self.assertRaises(assembly.RuntimeAssemblyError):
            assembly.validate_package_manifest(
                incomplete,
                normalization_receipt=receipt_bytes,
            )

        missing = json.loads(json.dumps(manifest))
        missing["sources"]["python"]["normalization"] = None
        with self.assertRaises(assembly.RuntimeAssemblyError):
            assembly.validate_package_manifest(
                missing,
                normalization_receipt=receipt_bytes,
            )

        receipt_document = json.loads(receipt_bytes)
        receipt_document["casefold_files"].append(receipt_document["casefold_files"][0])
        resealed_bytes = assembly._canonical_json_bytes(receipt_document)
        resealed = json.loads(json.dumps(manifest))
        resealed_identity = resealed["sources"]["python"]["normalization"]
        resealed_identity["size"] = len(resealed_bytes)
        resealed_identity["sha256"] = _sha256(resealed_bytes)
        resealed_control = next(
            entry
            for entry in resealed["inventory"]
            if entry["path"] == assembly.NORMALIZATION_PACKAGE_PATH
        )
        resealed_control["size"] = len(resealed_bytes)
        resealed_control["sha256"] = _sha256(resealed_bytes)
        with self.assertRaises(assembly.RuntimeAssemblyError):
            assembly.validate_package_manifest(
                resealed,
                normalization_receipt=resealed_bytes,
            )

        extra_group = json.loads(json.dumps(manifest))
        python3 = next(
            entry
            for entry in extra_group["inventory"]
            if entry["path"] == "runtime/python/linux-x64/bin/python3"
        )
        alias = dict(python3)
        alias["path"] = "runtime/python/linux-x64/bin/PYTHON3"
        extra_group["inventory"].append(alias)
        extra_group["inventory"].sort(key=lambda item: item["path"].encode("utf-8"))
        with self.assertRaises(assembly.RuntimeAssemblyError):
            assembly.validate_package_manifest(
                extra_group,
                normalization_receipt=receipt_bytes,
            )

        for altered in (
            receipt_bytes[:-1],
            b'{"format":"duplicate","format":"duplicate"}\\n',
            json.dumps(json.loads(receipt_bytes), indent=2).encode("utf-8"),
        ):
            with self.subTest(receipt_mutation=len(altered)):
                with self.assertRaises(assembly.RuntimeAssemblyError):
                    assembly.validate_package_manifest(
                        manifest,
                        normalization_receipt=altered,
                    )

        win_manifest = assembly._package_manifest(
            self._plan("win32-x64"),
            assembly._output_files(self._plan("win32-x64")),
        )
        win_manifest["sources"]["python"]["normalization"] = assembly._normalization_identity()
        with self.assertRaises(assembly.RuntimeAssemblyError):
            assembly.validate_package_manifest(win_manifest)

    def test_receipted_relative_symlink_preserves_distinct_casefold_files(
        self,
    ) -> None:
        archive_path = self.root / "receipted-python.tar.gz"
        python_payload = b"python"
        upper_payload = b"canonical"
        lower_payload = b"legacy"
        archive_payload = _tar_bytes(
            [
                ("python/bin/python3.12", python_payload, tarfile.REGTYPE, None),
                ("python/bin/python3", b"", tarfile.SYMTYPE, b"python3.12"),
                ("python/share/terminfo/E/Eterm", upper_payload, tarfile.REGTYPE, None),
                ("python/share/terminfo/e/eterm", lower_payload, tarfile.REGTYPE, None),
            ]
        )
        _write(archive_path, archive_payload)
        normalization = assembly.ArchiveNormalization(
            archive_sha256=_sha256(archive_payload),
            casefold_directories=(
                assembly.CasefoldDirectoryPair(
                    first="share/terminfo/E",
                    second="share/terminfo/e",
                ),
            ),
            casefold_files=(
                assembly.CasefoldFilePair(
                    first_sha256=_sha256(upper_payload),
                    first_mode=0o644,
                    first_size=len(upper_payload),
                    first_source="share/terminfo/E/Eterm",
                    first_target="share/terminfo/E/Eterm",
                    second_sha256=_sha256(lower_payload),
                    second_mode=0o644,
                    second_size=len(lower_payload),
                    second_source="share/terminfo/e/eterm",
                    second_target="share/terminfo/e/eterm",
                ),
            ),
            component="python",
            files=(
                assembly.MaterializationReceipt(
                    link="python3.12",
                    mode=0o755,
                    sha256=_sha256(python_payload),
                    size=len(python_payload),
                    source="bin/python3",
                    source_kind="symlink",
                    target="bin/python3.12",
                ),
                assembly.MaterializationReceipt(
                    link=None,
                    mode=0o755,
                    sha256=_sha256(python_payload),
                    size=len(python_payload),
                    source="bin/python3.12",
                    source_kind="regular",
                    target="bin/python3.12",
                ),
                assembly.MaterializationReceipt(
                    link=None,
                    mode=0o644,
                    sha256=_sha256(upper_payload),
                    size=len(upper_payload),
                    source="share/terminfo/E/Eterm",
                    source_kind="regular",
                    target="share/terminfo/E/Eterm",
                ),
                assembly.MaterializationReceipt(
                    link=None,
                    mode=0o644,
                    sha256=_sha256(lower_payload),
                    size=len(lower_payload),
                    source="share/terminfo/e/eterm",
                    source_kind="regular",
                    target="share/terminfo/e/eterm",
                ),
            ),
            max_symlink_depth=1,
            output_bytes=(len(python_payload) * 2 + len(upper_payload) + len(lower_payload)),
            output_file_count=4,
            payload_root="python",
            policy="materialize_relative_symlinks_preserve_case_sensitive_paths_v1",
            regular_file_count=3,
            relative_symlink_count=1,
            source_file_count=4,
            target_id="linux-x64",
        )
        spec = assembly.ArchiveSpec(
            component="python",
            path=archive_path,
            filename=archive_path.name,
            size=len(archive_payload),
            sha256=_sha256(archive_payload),
            payload_root="python",
            entrypoint="python/bin/python3",
            expected_inventory=None,
            normalization=normalization,
        )
        assembly._validate_archive_spec(spec, "python")
        payload = assembly._read_archive_payload(spec)
        by_path = {item.path: item for item in payload}
        self.assertEqual(
            set(by_path),
            {
                "bin/python3",
                "bin/python3.12",
                "share/terminfo/E/Eterm",
                "share/terminfo/e/eterm",
            },
        )
        self.assertEqual(by_path["bin/python3"].payload, python_payload)
        self.assertEqual(by_path["share/terminfo/E/Eterm"].payload, upper_payload)
        self.assertEqual(by_path["share/terminfo/e/eterm"].payload, lower_payload)
        self.assertNotEqual(
            by_path["share/terminfo/E/Eterm"].payload,
            by_path["share/terminfo/e/eterm"].payload,
        )
        plan = replace(self._plan(), python=spec)
        with (
            mock.patch.object(assembly.os, "name", "nt"),
            mock.patch.object(
                assembly,
                "_output_files",
                side_effect=AssertionError("Linux archive materialized on Windows"),
            ),
            self.assertRaisesRegex(
                assembly.RuntimeAssemblyError,
                "case_sensitive_filesystem_required",
            ),
        ):
            assembly.assemble_runtime_resources(
                plan,
                self.root / "must-not-materialize-linux-on-windows",
            )
        prefix = "runtime/python/linux-x64"
        upper_path = f"{prefix}/share/terminfo/E/Eterm"
        lower_path = f"{prefix}/share/terminfo/e/eterm"
        allowed = assembly._case_sensitive_output_groups(
            normalization,
            prefix,
            include_directories=False,
        )
        files: dict[str, assembly._OutputFile] = {}
        aliases: dict[tuple[str, ...], set[str]] = {}
        assembly._insert_output(
            files,
            aliases,
            allowed,
            upper_path,
            upper_payload,
            0o644,
            "python",
        )
        assembly._insert_output(
            files,
            aliases,
            allowed,
            lower_path,
            lower_payload,
            0o644,
            "python",
        )
        self.assertEqual(files[upper_path].payload, upper_payload)
        self.assertEqual(files[lower_path].payload, lower_payload)

    def test_receipted_symlinks_reject_absolute_outside_missing_and_cycles(self) -> None:
        cases = (
            ("absolute", b"/python/bin/python3.12", "archive_symlink_absolute"),
            ("outside", b"../../../escape", "archive_symlink_outside_payload"),
            ("missing", b"missing", "archive_symlink_missing"),
        )
        for label, link, code in cases:
            with self.subTest(label=label):
                path = self.root / f"unsafe-receipted-{label}.tar.gz"
                raw = _tar_bytes(
                    [
                        ("python/bin/python3.12", b"python", tarfile.REGTYPE, None),
                        ("python/bin/python3", b"", tarfile.SYMTYPE, link),
                    ]
                )
                _write(path, raw)
                normalization = assembly.ArchiveNormalization(
                    archive_sha256=_sha256(raw),
                    casefold_directories=(),
                    casefold_files=(),
                    component="python",
                    files=(),
                    max_symlink_depth=1,
                    output_bytes=12,
                    output_file_count=2,
                    payload_root="python",
                    policy=("materialize_relative_symlinks_preserve_case_sensitive_paths_v1"),
                    regular_file_count=1,
                    relative_symlink_count=1,
                    source_file_count=2,
                    target_id="linux-x64",
                )
                spec = assembly.ArchiveSpec(
                    component="python",
                    path=path,
                    filename=path.name,
                    size=len(raw),
                    sha256=_sha256(raw),
                    payload_root="python",
                    entrypoint="python/bin/python3.12",
                    expected_inventory=None,
                    normalization=normalization,
                )
                with self.assertRaisesRegex(assembly.RuntimeAssemblyError, code):
                    assembly._read_archive_payload(spec)

        path = self.root / "unsafe-receipted-cycle.tar.gz"
        raw = _tar_bytes(
            [
                ("python/bin/python3.12", b"python", tarfile.REGTYPE, None),
                ("python/bin/a", b"", tarfile.SYMTYPE, b"b"),
                ("python/bin/b", b"", tarfile.SYMTYPE, b"a"),
            ]
        )
        _write(path, raw)
        cycle_spec = assembly.ArchiveSpec(
            component="python",
            path=path,
            filename=path.name,
            size=len(raw),
            sha256=_sha256(raw),
            payload_root="python",
            entrypoint="python/bin/python3.12",
            expected_inventory=None,
            normalization=assembly.ArchiveNormalization(
                archive_sha256=_sha256(raw),
                casefold_directories=(),
                casefold_files=(),
                component="python",
                files=(),
                max_symlink_depth=2,
                output_bytes=18,
                output_file_count=3,
                payload_root="python",
                policy="materialize_relative_symlinks_preserve_case_sensitive_paths_v1",
                regular_file_count=1,
                relative_symlink_count=2,
                source_file_count=3,
                target_id="linux-x64",
            ),
        )
        with self.assertRaisesRegex(assembly.RuntimeAssemblyError, "archive_symlink_cycle"):
            assembly._read_archive_payload(cycle_spec)

    def test_linux_case_sensitive_stage_probe_rejects_aliased_identities(self) -> None:
        owner = object.__new__(assembly._PosixOwnedOutput)
        first = "runtime/python/linux-x64/share/terminfo/E/Eterm"
        second = "runtime/python/linux-x64/share/terminfo/e/eterm"
        owner.files = {
            PurePosixPath(first).parts: ((7, 11), -1),
            PurePosixPath(second).parts: ((7, 11), -1),
        }
        owner.directories = {}
        owner._require_all_bindings = lambda: None
        with self.assertRaisesRegex(
            assembly.RuntimeAssemblyError,
            "case_sensitive_filesystem_required",
        ):
            owner.require_case_sensitive_paths((frozenset((first, second)),))

    def test_synthetic_linux_and_windows_resources_are_complete_and_non_publishable(self) -> None:
        for target_id in ("linux-x64", "win32-x64"):
            with self.subTest(target_id=target_id):
                output = self._assemble(target_id, f"output-{target_id}")
                manifest = assembly.verify_runtime_tree(output)
                self.assertEqual(manifest["target_id"], target_id)
                self.assertFalse(manifest["release_ready"])
                self.assertEqual(manifest["redistribution_status"], "blocked")
                self.assertEqual(
                    manifest["open_blocker_codes"],
                    list(REQUIRED_BLOCKERS),
                )
                codex_path = output / manifest["launch"]["codex"]
                python_path = output / manifest["launch"]["python"]
                self.assertTrue(codex_path.is_file())
                self.assertTrue(python_path.is_file())
                site_packages = (
                    output / f"runtime/python/{target_id}/Lib/site-packages"
                    if target_id == "win32-x64"
                    else output / f"runtime/python/{target_id}/lib/python3.12/site-packages"
                )
                self.assertTrue((site_packages / "isoworld/__init__.py").is_file())
                self.assertTrue((site_packages / "worldforge/studio/__main__.py").is_file())
                self.assertTrue(
                    (
                        output
                        / f"runtime/python/{target_id}"
                        / "share/rpg-world-forge/schemas/example.schema.json"
                    ).is_file()
                )
                self.assertTrue(
                    (output / "protocol/codex-app-server-0.144.6/manifest.json").is_file()
                )
                self.assertFalse(any(path.suffix == ".pyc" for path in output.rglob("*")))
                launch = json.loads((output / "runtime-manifest.json").read_text("utf-8"))
                self.assertEqual(launch["version"], 3)
                self.assertNotIn("linux_arm64", launch["python"])
                self.assertNotIn("win32_arm64", launch["codex"])

    def test_archive_rejects_unsafe_names_links_duplicates_and_collisions(self) -> None:
        base_files = {"bin/codex": b"codex"}
        cases = (
            ("traversal", [("../escape", b"x", tarfile.REGTYPE, None)]),
            ("absolute", [("/escape", b"x", tarfile.REGTYPE, None)]),
            ("backslash", [("bad\\name", b"x", tarfile.REGTYPE, None)]),
            ("empty-component", [("bad//name", b"x", tarfile.REGTYPE, None)]),
            ("reserved", [("CON", b"x", tarfile.REGTYPE, None)]),
            (
                "symlink",
                [("package/vendor/x/bin/link", b"", tarfile.SYMTYPE, b"bin/codex")],
            ),
            (
                "hardlink",
                [("package/vendor/x/bin/link", b"", tarfile.LNKTYPE, b"bin/codex")],
            ),
            ("fifo", [("package/vendor/x/fifo", b"", tarfile.FIFOTYPE, None)]),
            (
                "duplicate",
                [("package/vendor/x/bin/codex", b"second", tarfile.REGTYPE, None)],
            ),
            (
                "casefold",
                [("package/vendor/x/bin/CODEX", b"second", tarfile.REGTYPE, None)],
            ),
            (
                "non-nfc",
                [("package/vendor/x/cafe\u0301", b"x", tarfile.REGTYPE, None)],
            ),
            (
                "depth",
                [("/".join(["d"] * 65), b"x", tarfile.REGTYPE, None)],
            ),
        )
        for label, extra in cases:
            with self.subTest(label=label):
                archive_root = self.root / f"unsafe-{label}"
                archive_root.mkdir()
                spec = _archive(
                    archive_root / "codex.tgz",
                    component="codex",
                    payload_root="package/vendor/x",
                    entrypoint="bin/codex",
                    files=base_files,
                    extra_entries=extra,
                )
                plan = self._plan()
                plan = replace(plan, codex=spec)
                with self.assertRaises(assembly.RuntimeAssemblyError):
                    assembly.assemble_runtime_resources(
                        plan,
                        self.root / f"unsafe-output-{label}",
                    )

    def test_archive_enforces_inventory_entry_member_and_expansion_limits(self) -> None:
        plan = self._plan()
        cases = (
            ("entry", "MAX_ARCHIVE_ENTRIES", 1),
            ("member", "MAX_ARCHIVE_MEMBER_BYTES", 1),
            ("expanded", "MAX_ARCHIVE_EXPANSION_RATIO", 0),
        )
        for label, constant, value in cases:
            with self.subTest(label=label):
                with (
                    mock.patch.object(assembly, constant, value),
                    self.assertRaises(assembly.RuntimeAssemblyError),
                ):
                    assembly.assemble_runtime_resources(
                        plan,
                        self.root / f"limited-{label}",
                    )
        wrong_pin = assembly.FilePin("bin/codex", 1, _sha256(b"x"))
        wrong_codex = replace(plan.codex, expected_inventory=(wrong_pin,))
        wrong_plan = replace(plan, codex=wrong_codex)
        with self.assertRaisesRegex(assembly.RuntimeAssemblyError, "archive_inventory_mismatch"):
            assembly.assemble_runtime_resources(wrong_plan, self.root / "wrong-inventory")

    def test_archive_namespace_budget_counts_implicit_directories(self) -> None:
        exact_root = self.root / "archive-namespace-exact"
        exact_root.mkdir()
        exact = _archive(
            exact_root / "python.tar.gz",
            component="python",
            payload_root="python",
            entrypoint="python3",
            files={"python3": b"python"},
        )
        with mock.patch.object(assembly, "MAX_ARCHIVE_ENTRIES", 2):
            self.assertEqual(
                tuple(item.path for item in assembly._read_archive_payload(exact)),
                ("python3",),
            )

        over_root = self.root / "archive-namespace-over"
        over_root.mkdir()
        over = _archive(
            over_root / "python.tar.gz",
            component="python",
            payload_root="python",
            entrypoint="bin/python3",
            files={"bin/python3": b"python"},
        )
        with (
            mock.patch.object(assembly, "MAX_ARCHIVE_ENTRIES", 2),
            self.assertRaisesRegex(
                assembly.RuntimeAssemblyError,
                "archive_entry_limit",
            ),
        ):
            assembly._read_archive_payload(over)

    def test_scan_tree_budgets_empty_directories_before_recursion(self) -> None:
        exact = self.root / "empty-directory-exact"
        exact.mkdir()
        (exact / "only").mkdir()
        with mock.patch.object(assembly, "MAX_OUTPUT_FILES", 1):
            files, directories = assembly._scan_tree(exact)
        self.assertEqual(files, {})
        self.assertEqual(set(directories), {"only"})

        forest = self.root / "empty-directory-forest"
        forest.mkdir()
        (forest / "first").mkdir()
        (forest / "second").mkdir()
        scanned: list[Path] = []
        original_scandir = os.scandir

        def tracked_scandir(path: os.PathLike[str] | str) -> os.ScandirIterator[str]:
            scanned.append(Path(path))
            return original_scandir(path)

        with (
            mock.patch.object(assembly, "MAX_OUTPUT_FILES", 1),
            mock.patch.object(assembly.os, "scandir", side_effect=tracked_scandir),
            self.assertRaisesRegex(
                assembly.RuntimeAssemblyError,
                "output_limit_exceeded",
            ),
        ):
            assembly._scan_tree(forest)
        self.assertEqual(scanned, [forest])

    def test_tree_verification_rejects_every_unowned_or_changed_entry_kind(self) -> None:
        mutations: list[tuple[str, Callable[[Path, dict[str, object]], None]]] = [
            (
                "missing",
                lambda root, manifest: (root / manifest["inventory"][0]["path"]).unlink(),
            ),
            (
                "extra",
                lambda root, _manifest: _write(root / "unexpected.txt", b"x"),
            ),
            (
                "altered",
                lambda root, manifest: _write(
                    root / manifest["inventory"][0]["path"],
                    b"altered",
                ),
            ),
            (
                "casefold",
                lambda root, _manifest: _write(root / "Runtime-Manifest.json", b"x"),
            ),
            (
                "non-nfc",
                lambda root, _manifest: _write(root / "cafe\u0301.txt", b"x"),
            ),
        ]
        for label, mutate in mutations:
            with self.subTest(label=label):
                output = self._assemble(name=f"mutated-{label}")
                manifest = assembly.verify_runtime_tree(output)
                mutate(output, manifest)
                with self.assertRaises(assembly.RuntimeAssemblyError):
                    assembly.verify_runtime_tree(output)

        output = self._assemble(name="mutated-symlink")
        manifest = assembly.verify_runtime_tree(output)
        target = output / manifest["inventory"][0]["path"]
        target.unlink()
        target.symlink_to(output / "runtime-package-manifest.json")
        with self.assertRaises(assembly.RuntimeAssemblyError):
            assembly.verify_runtime_tree(output)

        output = self._assemble(name="mutated-hardlink")
        manifest = assembly.verify_runtime_tree(output)
        first = output / manifest["inventory"][0]["path"]
        second = output / manifest["inventory"][1]["path"]
        first.unlink()
        os.link(second, first)
        with self.assertRaises(assembly.RuntimeAssemblyError):
            assembly.verify_runtime_tree(output)

        if hasattr(os, "mkfifo"):
            output = self._assemble(name="mutated-special")
            os.mkfifo(output / "unexpected-fifo")
            with self.assertRaises(assembly.RuntimeAssemblyError):
                assembly.verify_runtime_tree(output)

    def test_package_manifest_rejects_launch_component_and_source_entrypoint_tampering(
        self,
    ) -> None:
        output = self._assemble(name="manifest-component-tamper")
        manifest_path = output / assembly.PACKAGE_MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text("utf-8"))
        codex_entry = next(
            entry for entry in manifest["inventory"] if entry["path"] == manifest["launch"]["codex"]
        )
        codex_entry["component"] = "forge"
        _write(manifest_path, assembly._canonical_json_bytes(manifest))
        with self.assertRaises(assembly.RuntimeAssemblyError):
            assembly.verify_runtime_tree(output)

        output = self._assemble(name="manifest-entrypoint-tamper")
        manifest_path = output / assembly.PACKAGE_MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text("utf-8"))
        manifest["sources"]["codex"]["archive"]["entrypoint"] = (
            "package/vendor/x86_64-unknown-linux-musl/bin/other"
        )
        _write(manifest_path, assembly._canonical_json_bytes(manifest))
        with self.assertRaises(assembly.RuntimeAssemblyError):
            assembly.verify_runtime_tree(output)

    def test_resealed_runtime_manifest_cannot_redirect_launch_paths(self) -> None:
        output = self._assemble(name="launch-manifest-reseal")
        launch_path = output / assembly.LAUNCH_MANIFEST_NAME
        launch = json.loads(launch_path.read_text("utf-8"))
        launch["python"]["linux_x64"] = launch["codex"]["linux_x64"]
        launch_payload = assembly._canonical_json_bytes(launch)
        _write(launch_path, launch_payload)

        package_path = output / assembly.PACKAGE_MANIFEST_NAME
        package = json.loads(package_path.read_text("utf-8"))
        control = next(
            entry
            for entry in package["inventory"]
            if entry["path"] == assembly.LAUNCH_MANIFEST_NAME
        )
        control["size"] = len(launch_payload)
        control["sha256"] = _sha256(launch_payload)
        _write(package_path, assembly._canonical_json_bytes(package))

        with self.assertRaisesRegex(assembly.RuntimeAssemblyError, "launch_manifest_invalid"):
            assembly.verify_runtime_tree(output)

    @unittest.skipUnless(os.name == "posix", "POSIX replacement regression")
    def test_failed_assembly_preserves_foreign_replacements_without_path_chmod(
        self,
    ) -> None:
        plan = self._plan()
        output = self.root / "preserved-partial-output"
        target = output / assembly._launch_paths(plan.target_id)[0]
        displaced = self.root / "displaced-owned-file"
        foreign_payload = b"foreign replacement"
        foreign_mode = 0o600
        fired = False

        def swap_output(path: Path, phase: str) -> None:
            nonlocal fired
            if fired or path != target or phase != "after_lstat":
                return
            fired = True
            target.rename(displaced)
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                foreign_mode,
            )
            try:
                os.write(descriptor, foreign_payload)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

        assembly._READ_TEST_HOOK = swap_output
        with (
            mock.patch.object(os, "chmod", side_effect=AssertionError("path chmod used")),
            self.assertRaisesRegex(
                assembly.RuntimeAssemblyError,
                "filesystem_identity_changed",
            ),
        ):
            assembly.assemble_runtime_resources(plan, output)
        assembly._READ_TEST_HOOK = None
        self.assertEqual(target.read_bytes(), foreign_payload)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), foreign_mode)
        self.assertTrue(displaced.is_file())

    @unittest.skipUnless(os.name == "posix", "POSIX replacement regression")
    def test_output_creation_is_anchored_across_parent_and_intermediate_swaps(
        self,
    ) -> None:
        plan = self._plan()

        parent = self.root / "publish-parent"
        parent.mkdir()
        displaced_parent = self.root / "publish-parent-displaced"
        outside = self.root / "outside-parent"
        outside.mkdir()
        output = parent / "runtime-output"
        fired = False

        def swap_parent(path: Path, phase: str) -> None:
            nonlocal fired
            if fired or path != output or phase != "before_root_mkdir":
                return
            fired = True
            parent.rename(displaced_parent)
            parent.symlink_to(outside, target_is_directory=True)

        assembly._WRITE_TEST_HOOK = swap_parent
        with self.assertRaises(assembly.RuntimeAssemblyError):
            assembly.assemble_runtime_resources(plan, output)
        assembly._WRITE_TEST_HOOK = None
        self.assertFalse((outside / output.name).exists())
        self.assertFalse((displaced_parent / output.name).exists())

        output = self.root / "intermediate-output"
        displaced_runtime = self.root / "displaced-runtime"
        outside = self.root / "outside-intermediate"
        outside.mkdir()
        target = output / "runtime"
        fired = False

        def swap_intermediate(path: Path, phase: str) -> None:
            nonlocal fired
            if fired or path != target or phase != "after_directory_mkdir":
                return
            fired = True
            target.rename(displaced_runtime)
            target.symlink_to(outside, target_is_directory=True)

        assembly._WRITE_TEST_HOOK = swap_intermediate
        with self.assertRaisesRegex(
            assembly.RuntimeAssemblyError,
            "filesystem_identity_changed",
        ):
            assembly.assemble_runtime_resources(plan, output)
        assembly._WRITE_TEST_HOOK = None
        self.assertEqual(list(outside.iterdir()), [])
        self.assertTrue(displaced_runtime.is_dir())

        output = self.root / "file-parent-output"
        target = output / "protocol/codex-app-server-0.144.6/manifest.json"
        target_parent = target.parent
        displaced_file_parent = self.root / "displaced-file-parent"
        outside = self.root / "outside-file-parent"
        outside.mkdir()
        fired = False

        def swap_file_parent(path: Path, phase: str) -> None:
            nonlocal fired
            if fired or path != target or phase != "before_file_open":
                return
            fired = True
            target_parent.rename(displaced_file_parent)
            target_parent.symlink_to(outside, target_is_directory=True)

        assembly._WRITE_TEST_HOOK = swap_file_parent
        with self.assertRaises(assembly.RuntimeAssemblyError):
            assembly.assemble_runtime_resources(plan, output)
        assembly._WRITE_TEST_HOOK = None
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(list(displaced_file_parent.iterdir()), [])

    @unittest.skipIf(os.name == "nt", "native Windows backend is available")
    def test_output_creation_fails_closed_without_secure_host_primitives(self) -> None:
        output = self.root / "unsupported-host-output"
        with (
            mock.patch.object(assembly.os, "name", "nt"),
            self.assertRaisesRegex(
                assembly.RuntimeAssemblyError,
                "secure_primitive_unavailable",
            ),
        ):
            assembly._OwnedOutput(output)
        self.assertFalse(output.exists())

    @unittest.skipUnless(os.name == "posix", "POSIX replacement regression")
    def test_zip_publication_is_anchored_before_file_creation(self) -> None:
        output = self._assemble(name="zip-source-output")
        parent = self.root / "zip-parent"
        parent.mkdir()
        displaced = self.root / "zip-parent-displaced"
        outside = self.root / "zip-outside"
        outside.mkdir()
        destination = parent / "runtime.zip"
        fired = False

        def swap_parent(path: Path, phase: str) -> None:
            nonlocal fired
            if fired or path != destination or phase != "before_file_open":
                return
            fired = True
            parent.rename(displaced)
            parent.symlink_to(outside, target_is_directory=True)

        assembly._WRITE_TEST_HOOK = swap_parent
        with self.assertRaises(assembly.RuntimeAssemblyError):
            assembly.build_deterministic_zip(output, destination)
        assembly._WRITE_TEST_HOOK = None
        self.assertFalse((outside / destination.name).exists())
        self.assertFalse((displaced / destination.name).exists())

    @unittest.skipUnless(os.name == "posix", "POSIX rename replacement regression")
    def test_after_write_identical_parent_replacements_fail_closed(self) -> None:
        plan = self._plan()
        output = self.root / "after-write-output"
        target = output / "protocol/codex-app-server-0.144.6/manifest.json"
        displaced = self.root / "after-write-displaced"
        fired = False

        def replace_file_parent(path: Path, phase: str) -> None:
            nonlocal fired
            if fired or path != target or phase != "after_file_write":
                return
            fired = True
            target.parent.rename(displaced)
            shutil.copytree(displaced, target.parent)

        assembly._WRITE_TEST_HOOK = replace_file_parent
        with self.assertRaisesRegex(
            assembly.RuntimeAssemblyError,
            "filesystem_identity_changed",
        ):
            assembly.assemble_runtime_resources(plan, output)
        assembly._WRITE_TEST_HOOK = None
        self.assertEqual(target.read_bytes(), (displaced / target.name).read_bytes())

        source = self._assemble(name="after-write-zip-source")
        parent = self.root / "after-write-zip-parent"
        parent.mkdir()
        displaced_parent = self.root / "after-write-zip-parent-displaced"
        destination = parent / "runtime.zip"
        fired = False

        def replace_zip_parent(path: Path, phase: str) -> None:
            nonlocal fired
            if fired or path != destination or phase != "after_file_write":
                return
            fired = True
            parent.rename(displaced_parent)
            shutil.copytree(displaced_parent, parent)

        assembly._WRITE_TEST_HOOK = replace_zip_parent
        with self.assertRaisesRegex(
            assembly.RuntimeAssemblyError,
            "filesystem_identity_changed",
        ):
            assembly.build_deterministic_zip(source, destination)
        assembly._WRITE_TEST_HOOK = None
        self.assertEqual(
            destination.read_bytes(),
            (displaced_parent / destination.name).read_bytes(),
        )

    @unittest.skipUnless(os.name == "nt", "requires native Windows handles")
    def test_native_windows_backend_assembles_and_zips_windows_target(self) -> None:
        for target_id in ("win32-x64",):
            with self.subTest(target_id=target_id):
                output = self._assemble(
                    target_id,
                    f"native-windows-{target_id}",
                )
                self.assertEqual(
                    assembly.verify_runtime_tree(output)["target_id"],
                    target_id,
                )
                archive = self.root / f"native-windows-{target_id}.zip"
                assembly.build_deterministic_zip(output, archive)
                self.assertEqual(
                    assembly.verify_runtime_zip(archive)["target_id"],
                    target_id,
                )

    @unittest.skipUnless(os.name == "nt", "requires native Windows handles")
    def test_native_windows_retained_handles_block_after_write_parent_swaps(
        self,
    ) -> None:
        plan = self._plan()
        output = self.root / "native-windows-swap-output"
        target = output / "protocol/codex-app-server-0.144.6/manifest.json"
        preserved_copy = self.root / "native-windows-preserved-copy"
        displaced = self.root / "native-windows-displaced"
        blocked = False

        def attempt_directory_swap(path: Path, phase: str) -> None:
            nonlocal blocked
            if path != target or phase != "after_file_write":
                return
            shutil.copytree(target.parent, preserved_copy)
            try:
                target.parent.rename(displaced)
            except OSError:
                blocked = True
                assembly._fail("filesystem_identity_changed", "output")
            raise AssertionError("Windows allowed a retained directory replacement")

        assembly._WRITE_TEST_HOOK = attempt_directory_swap
        with self.assertRaisesRegex(
            assembly.RuntimeAssemblyError,
            "filesystem_identity_changed",
        ):
            assembly.assemble_runtime_resources(plan, output)
        assembly._WRITE_TEST_HOOK = None
        self.assertTrue(blocked)
        self.assertFalse(displaced.exists())
        self.assertEqual(
            target.read_bytes(),
            (preserved_copy / target.name).read_bytes(),
        )

        source = self._assemble(name="native-windows-zip-source")
        parent = self.root / "native-windows-zip-parent"
        parent.mkdir()
        destination = parent / "runtime.zip"
        preserved_zip = self.root / "native-windows-preserved.zip"
        displaced_parent = self.root / "native-windows-zip-displaced"
        blocked = False

        def attempt_zip_swap(path: Path, phase: str) -> None:
            nonlocal blocked
            if path != destination or phase != "after_file_write":
                return
            shutil.copyfile(destination, preserved_zip)
            try:
                parent.rename(displaced_parent)
            except OSError:
                blocked = True
                assembly._fail("filesystem_identity_changed", "zip")
            raise AssertionError("Windows allowed a retained ZIP parent replacement")

        assembly._WRITE_TEST_HOOK = attempt_zip_swap
        with self.assertRaisesRegex(
            assembly.RuntimeAssemblyError,
            "filesystem_identity_changed",
        ):
            assembly.build_deterministic_zip(source, destination)
        assembly._WRITE_TEST_HOOK = None
        self.assertTrue(blocked)
        self.assertFalse(displaced_parent.exists())
        self.assertEqual(destination.read_bytes(), preserved_zip.read_bytes())

    def test_pinned_tree_reads_reject_file_and_parent_swaps(self) -> None:
        output = self._assemble(name="swap-file")
        manifest = assembly.verify_runtime_tree(output)
        target = output / manifest["inventory"][0]["path"]
        replacement = target.with_name(f"{target.name}.replacement")
        original_payload = target.read_bytes()
        fired = False

        def swap_file(path: Path, phase: str) -> None:
            nonlocal fired
            if fired or path != target or phase != "after_lstat":
                return
            fired = True
            target.rename(replacement)
            _write(target, original_payload)

        assembly._READ_TEST_HOOK = swap_file
        with self.assertRaisesRegex(assembly.RuntimeAssemblyError, "filesystem_identity_changed"):
            assembly.verify_runtime_tree(output)
        assembly._READ_TEST_HOOK = None

        output = self._assemble(name="swap-parent")
        manifest = assembly.verify_runtime_tree(output)
        target = output / manifest["inventory"][0]["path"]
        parent = target.parent
        old_parent = parent.with_name(f"{parent.name}.old")
        original_payload = target.read_bytes()
        fired = False

        def swap_parent(path: Path, phase: str) -> None:
            nonlocal fired
            if fired or path != target or phase != "after_lstat":
                return
            fired = True
            parent.rename(old_parent)
            parent.mkdir()
            _write(parent / target.name, original_payload)

        assembly._READ_TEST_HOOK = swap_parent
        with self.assertRaises(assembly.RuntimeAssemblyError):
            assembly.verify_runtime_tree(output)

    def test_archive_read_rejects_identity_swap_before_extraction(self) -> None:
        plan = self._plan()
        archive_path = plan.codex.path
        replacement = archive_path.with_name("codex.original.tgz")
        original_payload = archive_path.read_bytes()
        fired = False

        def swap_archive(path: Path, phase: str) -> None:
            nonlocal fired
            if fired or path != archive_path or phase != "after_lstat":
                return
            fired = True
            archive_path.rename(replacement)
            _write(archive_path, original_payload)

        assembly._READ_TEST_HOOK = swap_archive
        output = self.root / "archive-swap-output"
        with self.assertRaisesRegex(
            assembly.RuntimeAssemblyError,
            "filesystem_identity_changed",
        ):
            assembly.assemble_runtime_resources(plan, output)
        self.assertFalse(output.exists())

    def test_deterministic_zip_is_identical_across_roots_and_verifies(self) -> None:
        first = self._assemble(name="first-root")
        second_source = self.root / "forge-copy"
        shutil.copytree(self.source, second_source)
        second_archives = self.root / "archives-copy"
        second_plan = self._plan(source=second_source, archives=second_archives)
        second = self.root / "second-root"
        assembly.assemble_runtime_resources(second_plan, second)
        for path in second.rglob("*"):
            with contextlib.suppress(OSError):
                os.utime(path, (1_800_000_000, 1_800_000_000), follow_symlinks=False)
        first_zip = self.root / "first.zip"
        second_zip = self.root / "second.zip"
        first_digest = assembly.build_deterministic_zip(first, first_zip)
        second_digest = assembly.build_deterministic_zip(second, second_zip)
        self.assertEqual(first_digest, second_digest)
        self.assertEqual(first_zip.read_bytes(), second_zip.read_bytes())
        manifest = assembly.verify_runtime_zip(first_zip)
        self.assertFalse(manifest["release_ready"])

        with zipfile.ZipFile(first_zip, "a") as archive:
            archive.writestr("unexpected.txt", b"x")
        with self.assertRaises(assembly.RuntimeAssemblyError):
            assembly.verify_runtime_zip(first_zip)

    def test_zip_namespace_budget_counts_every_implicit_directory(self) -> None:
        output = self._assemble(name="zip-namespace-output")
        archive_path = self.root / "zip-namespace.zip"
        assembly.build_deterministic_zip(output, archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            names = [item.filename for item in archive.infolist()]
        nodes = {
            "/".join(PurePosixPath(name).parts[:count])
            for name in names
            for count in range(1, len(PurePosixPath(name).parts) + 1)
        }
        self.assertGreater(len(nodes), len(names))
        with mock.patch.object(assembly, "MAX_OUTPUT_NODES", len(nodes)):
            self.assertEqual(
                assembly.verify_runtime_zip(archive_path)["target_id"],
                "linux-x64",
            )
        with (
            mock.patch.object(assembly, "MAX_OUTPUT_NODES", len(nodes) - 1),
            self.assertRaisesRegex(assembly.RuntimeAssemblyError, "zip_invalid"),
        ):
            assembly.verify_runtime_zip(archive_path)

    def test_zip_verifier_rejects_every_byte_different_noncanonical_variant(
        self,
    ) -> None:
        output = self._assemble(name="zip-canonical-output")
        canonical = self.root / "canonical.zip"
        assembly.build_deterministic_zip(output, canonical)
        variants = (
            "archive-comment",
            "entry-comment",
            "entry-extra",
            "attributes",
            "order",
            "timestamp",
            "compression",
        )
        for mutation in variants:
            with self.subTest(mutation=mutation):
                candidate = self.root / f"{mutation}.zip"
                _rewrite_zip(canonical, candidate, mutation)
                self.assertNotEqual(candidate.read_bytes(), canonical.read_bytes())
                with self.assertRaises(assembly.RuntimeAssemblyError):
                    assembly.verify_runtime_zip(candidate)

        flags = self.root / "flags.zip"
        _set_first_zip_flag(canonical, flags)
        with self.assertRaises(assembly.RuntimeAssemblyError):
            assembly.verify_runtime_zip(flags)

        trailing = self.root / "trailing.zip"
        trailing.write_bytes(canonical.read_bytes() + b"trailing")
        with self.assertRaises(assembly.RuntimeAssemblyError):
            assembly.verify_runtime_zip(trailing)

    def test_odd_source_date_epoch_is_normalized_to_zip_granularity(self) -> None:
        plan = replace(self._plan(), source_date_epoch=SOURCE_DATE_EPOCH + 1)
        output = self.root / "odd-epoch-output"
        assembly.assemble_runtime_resources(plan, output)
        archive = self.root / "odd-epoch.zip"
        assembly.build_deterministic_zip(output, archive)
        manifest = assembly.verify_runtime_zip(archive)
        self.assertEqual(manifest["source_date_epoch"], SOURCE_DATE_EPOCH + 1)

    def test_windows_portability_policy_rejects_reserved_and_alias_paths(self) -> None:
        for value in (
            "CON",
            "dir/aux.txt",
            "dir/trailing.",
            "dir/trailing ",
            "dir/name:stream",
            "dir\\name",
            "../escape",
            "/absolute",
        ):
            with self.subTest(value=value), self.assertRaises(assembly.RuntimeAssemblyError):
                assembly._portable_path(value, "test")

    def test_windows_unicode_string_lengths_are_checked_before_construction(self) -> None:
        self.assertEqual(
            assembly._windows_unicode_name_length("a" * 32_766, "test"),
            65_532,
        )
        for value in ("a" * 32_767, "\ud800"):
            with (
                self.subTest(length=len(value)),
                self.assertRaisesRegex(
                    assembly.RuntimeAssemblyError,
                    "invalid_path",
                ),
            ):
                assembly._windows_unicode_name_length(value, "test")

    def test_windows_open_anchor_and_relative_close_handles_when_state_fails(self) -> None:
        class FakeVoid:
            def __init__(self, value: object = None) -> None:
                self.value = value

        anchor_api = object.__new__(assembly._WindowsOutputApi)
        anchor_api.ctypes = SimpleNamespace(
            c_void_p=FakeVoid,
            cast=lambda value, _kind: SimpleNamespace(value=value),
        )
        anchor_api.CreateFileW = lambda *_args: 73
        anchor_api.state = mock.Mock(
            side_effect=assembly.RuntimeAssemblyError(
                "filesystem_identity_changed",
                "output",
            )
        )
        anchor_api.close = mock.Mock()
        with self.assertRaises(assembly.RuntimeAssemblyError):
            anchor_api.open_anchor("C:\\", "output")
        anchor_api.close.assert_called_once_with(73)

        class FakeHandle:
            def __init__(self, value: object = None) -> None:
                self.value = value

        relative_api = object.__new__(assembly._WindowsOutputApi)
        relative_api.ctypes = SimpleNamespace(
            byref=lambda value: value,
            cast=lambda value, _kind: SimpleNamespace(
                value=value.value if isinstance(value, FakeHandle) else value
            ),
            c_void_p=FakeVoid,
            create_unicode_buffer=lambda value: value,
            pointer=lambda value: value,
            sizeof=lambda _value: 1,
        )
        relative_api.wintypes = SimpleNamespace(HANDLE=FakeHandle, LPWSTR=object)
        relative_api.UnicodeString = lambda *_args: object()
        relative_api.ObjectAttributes = lambda *_args: object()
        relative_api.IoStatusBlock = lambda: object()

        def nt_create(output: FakeHandle, *_args: object) -> int:
            output.value = 91
            return 0

        relative_api.NtCreateFile = nt_create
        relative_api.state = mock.Mock(
            side_effect=assembly.RuntimeAssemblyError(
                "filesystem_identity_changed",
                "output",
            )
        )
        relative_api.close = mock.Mock()
        with self.assertRaises(assembly.RuntimeAssemblyError):
            relative_api.relative(
                7,
                "leaf",
                directory=False,
                create=True,
                field="output",
            )
        relative_api.close.assert_called_once_with(91)

    def test_archive_filenames_use_the_same_portable_alias_contract(self) -> None:
        output = self._assemble(name="filename-contract-output")
        package = json.loads((output / assembly.PACKAGE_MANIFEST_NAME).read_text("utf-8"))
        mutations = (
            ("reserved", "codex", "CON"),
            ("backslash", "codex", "bad\\name.tgz"),
            ("non-nfc", "codex", "cafe\u0301.tgz"),
            ("trailing-dot", "codex", "codex."),
            ("trailing-space", "codex", "codex "),
            (
                "alias",
                "python",
                package["sources"]["codex"]["archive"]["filename"].upper(),
            ),
        )
        for label, component, filename in mutations:
            with self.subTest(label=label):
                candidate = json.loads(json.dumps(package))
                candidate["sources"][component]["archive"]["filename"] = filename
                with self.assertRaises(assembly.RuntimeAssemblyError):
                    assembly.validate_package_manifest(candidate)


if __name__ == "__main__":
    unittest.main()
