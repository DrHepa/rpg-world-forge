from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worldforge.narrative_analysis import analyze_project
from worldforge.project import load_source_project
from worldforge.repository_boundary import FORGE_ROOT
from worldforge.scaffold import create_world_project
from worldforge.studio import changesets
from worldforge.studio.authoring import (
    MAX_SOURCE_DEPTH,
    MAX_SOURCE_DOCUMENT_BYTES,
    MAX_SOURCE_DOCUMENTS,
)
from worldforge.studio.contracts import (
    EXACT_ASSET_CATALOG_METHODS,
    EXACT_ASSET_PREVIEW_METHODS,
    EXACT_CHANGESET_METHODS,
    METHODS,
)
from worldforge.studio.errors import StudioError
from worldforge.studio.service import StudioService
from worldforge.studio.storage import StudioStore


class StudioAuthoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.world = self.root / "world"
        create_world_project(
            self.world,
            world_id="studio_world",
            title="Studio World",
            language="en",
            actor_id="hero",
            actor_name="Hero",
        )
        self.store = StudioStore(self.root / "studio-data")
        self.addCleanup(self.store.close)
        self.service = StudioService(self.store)
        self.call(
            "workspace.register",
            {
                "workspace_id": "workspace_01",
                "forge_root": str(FORGE_ROOT),
                "world_root": str(self.world),
            },
        )

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        response = self.service.handle(
            {
                "protocol": "rpg-world-forge.studio_protocol",
                "protocol_version": 1,
                "kind": "request",
                "request_id": "request-1",
                "method": method,
                "params": params,
            }
        )
        return response["result"]

    def workspace_params(self) -> dict[str, object]:
        return {"workspace_id": "workspace_01"}

    def test_overview_is_deterministic_and_does_not_expose_authoritative_paths(self) -> None:
        with (
            mock.patch(
                "worldforge.world_lifecycle._exclusive_lifecycle_lock",
                side_effect=AssertionError("overview must not create a lifecycle lock"),
            ),
            mock.patch(
                "worldforge.world_lifecycle._read_object",
                side_effect=AssertionError("overview must use captured control bytes"),
            ),
        ):
            first = self.call("workspace.overview", self.workspace_params())
        second = self.call("workspace.overview", self.workspace_params())

        self.assertEqual(first, second)
        overview = first["overview"]
        self.assertEqual("workspace_01", overview["workspace_id"])
        self.assertEqual("studio_world", overview["project"]["world_id"])
        self.assertEqual("0.1.0", overview["project"]["world_version"])
        self.assertEqual(0, overview["status"]["revision"])
        self.assertFalse(overview["capabilities"]["providers"])
        self.assertTrue(overview["capabilities"]["source_inspection"])
        self.assertNotIn(str(self.world), json.dumps(first))
        self.assertNotIn(str(FORGE_ROOT), json.dumps(first))

    def test_list_contains_only_manifest_declared_documents_with_same_read_hashes(self) -> None:
        unreferenced = self.world / "source/private-notes.txt"
        unreferenced.write_text("not part of the loaded source graph\n", encoding="utf-8")

        result = self.call("source.list", self.workspace_params())
        documents = result["documents"]
        paths = [item["path"] for item in documents]
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(
            {
                "source/actors/hero.json",
                "source/manifest.json",
                "source/maps/starting_area.json",
                "source/tile_types/ground.json",
                "source/world.json",
            },
            set(paths),
        )
        self.assertNotIn("source/private-notes.txt", paths)
        self.assertFalse(any(path.endswith("README.md") for path in paths))
        for item in documents:
            payload = (self.world / item["path"]).read_bytes()
            self.assertEqual(len(payload), item["size"])
            self.assertEqual(hashlib.sha256(payload).hexdigest(), item["sha256"])

    def test_read_returns_exact_utf8_and_strict_json_from_the_hashed_bytes(self) -> None:
        path = "source/actors/hero.json"
        payload = (self.world / path).read_bytes()
        result = self.call("source.read", {**self.workspace_params(), "path": path})
        document = result["document"]

        self.assertEqual(path, document["path"])
        self.assertEqual("actors", document["kind"])
        self.assertEqual(payload.decode("utf-8"), document["content"])
        self.assertEqual(json.loads(payload), document["json"])
        self.assertEqual(hashlib.sha256(payload).hexdigest(), document["sha256"])
        self.assertEqual(len(payload), document["size"])

        for unsafe in (
            "source/../.worldforge/project.json",
            "/source/world.json",
            "source\\world.json",
        ):
            with self.subTest(path=unsafe), self.assertRaisesRegex(StudioError, "portable"):
                self.call("source.read", {**self.workspace_params(), "path": unsafe})
        with self.assertRaisesRegex(StudioError, "not declared"):
            self.call(
                "source.read",
                {**self.workspace_params(), "path": "source/actors/README.md"},
            )

    def test_validate_and_analyze_reuse_the_existing_in_memory_domain_logic(self) -> None:
        before = self.tree_digest()
        validation = self.call("world.validate", self.workspace_params())["validation"]
        analysis = self.call("world.analyze", self.workspace_params())

        self.assertTrue(validation["valid"])
        self.assertEqual("release", validation["profile"])
        self.assertEqual([], validation["diagnostics"])
        self.assertTrue(analysis["validation"]["valid"])
        expected = analyze_project(load_source_project(self.world / "source/manifest.json"))
        self.assertEqual(expected, analysis["analysis"])
        self.assertEqual(before, self.tree_digest())

    def test_malformed_json_is_structured_and_never_runs_analysis(self) -> None:
        actor = self.world / "source/actors/hero.json"
        actor.write_text('{"id":"hero","id":"duplicate"}\n', encoding="utf-8")

        with self.assertRaisesRegex(StudioError, "duplicate JSON object key") as raised:
            self.call(
                "source.read",
                {**self.workspace_params(), "path": "source/actors/hero.json"},
            )
        self.assertEqual("invalid_request", raised.exception.code)
        validation = self.call("world.validate", self.workspace_params())["validation"]
        analysis = self.call("world.analyze", self.workspace_params())
        self.assertFalse(validation["valid"])
        self.assertEqual("source_error", validation["diagnostics"][0]["code"])
        self.assertEqual("source/actors/hero.json", validation["diagnostics"][0]["path"])
        self.assertIsNone(analysis["analysis"])
        self.assertNotIn(str(self.world), json.dumps(validation))

    def test_read_rejects_ambiguous_json_and_invalid_utf8(self) -> None:
        actor = self.world / "source/actors/hero.json"
        cases = (
            b'{"value":NaN}\n',
            b'{"value":1e9999}\n',
            b'["not-an-object"]\n',
            b'{"bad":"\xff"}\n',
        )
        for payload in cases:
            with self.subTest(payload=payload):
                actor.write_bytes(payload)
                with self.assertRaises(StudioError) as raised:
                    self.call(
                        "source.read",
                        {**self.workspace_params(), "path": "source/actors/hero.json"},
                    )
                self.assertEqual("invalid_request", raised.exception.code)
                self.assertNotIn("Traceback", raised.exception.message)

    def test_hard_links_fail_closed(self) -> None:
        actor = self.world / "source/actors/hero.json"
        original = actor.read_bytes()
        actor.unlink()
        external = self.root / "external.json"
        external.write_bytes(original)
        os.link(external, actor)
        with self.assertRaisesRegex(StudioError, "hard link"):
            self.call("source.list", self.workspace_params())

    def test_overview_rejects_hardlinked_control_file(self) -> None:
        project = self.world / ".worldforge/project.json"
        payload = project.read_bytes()
        project.unlink()
        external = self.root / "external-project.json"
        external.write_bytes(payload)
        try:
            os.link(external, project)
        except OSError as exc:
            self.skipTest(f"hardlinks are unavailable: {exc}")
        with self.assertRaises(StudioError) as raised:
            self.call("workspace.overview", self.workspace_params())
        self.assertEqual("conflict", raised.exception.code)
        self.assertNotIn(str(self.world), raised.exception.message)

    def test_overview_rejects_symlinked_control_file(self) -> None:
        status = self.world / ".worldforge/status.json"
        status_payload = status.read_bytes()
        status.unlink()
        external_status = self.root / "external-status.json"
        external_status.write_bytes(status_payload)
        try:
            status.symlink_to(external_status)
        except OSError as exc:
            self.skipTest(f"symlinks are unavailable: {exc}")
        with self.assertRaises(StudioError) as raised:
            self.call("workspace.overview", self.workspace_params())
        self.assertEqual("conflict", raised.exception.code)
        self.assertNotIn(str(self.world), raised.exception.message)

    def test_casefold_collisions_fail_closed_when_the_filesystem_can_represent_them(self) -> None:
        alias = self.world / "source/Actors"
        try:
            alias.mkdir()
        except FileExistsError:
            self.skipTest("filesystem cannot represent case-distinct sibling names")
        with self.assertRaisesRegex(StudioError, "collision"):
            self.call("source.list", self.workspace_params())

    def test_symlinked_source_document_is_rejected_when_supported(self) -> None:
        actor = self.world / "source/actors/hero.json"
        payload = actor.read_bytes()
        external = self.root / "external.json"
        external.write_bytes(payload)
        actor.unlink()
        try:
            actor.symlink_to(external)
        except OSError as exc:
            self.skipTest(f"symlinks are unavailable: {exc}")
        with self.assertRaisesRegex(StudioError, "source boundary"):
            self.call("source.list", self.workspace_params())

    def test_same_read_metadata_change_is_rejected(self) -> None:
        real_stat = changesets.descriptor_file_stat
        calls = 0

        def changed_stat(descriptor: int):
            nonlocal calls
            calls += 1
            info = real_stat(descriptor)
            if calls != 4:
                return info

            class Changed:
                st_dev = info.st_dev
                st_ino = info.st_ino
                st_mode = info.st_mode
                st_nlink = info.st_nlink
                st_size = info.st_size
                st_mtime_ns = info.st_mtime_ns + 1
                st_ctime_ns = info.st_ctime_ns

            return Changed()

        with mock.patch.object(changesets, "descriptor_file_stat", side_effect=changed_stat):
            with self.assertRaisesRegex(StudioError, "changed while"):
                self.call(
                    "source.read",
                    {**self.workspace_params(), "path": "source/actors/hero.json"},
                )

    def test_overview_rejects_same_size_control_mutation(self) -> None:
        real_stat = changesets.descriptor_file_stat
        calls = 0

        def changed_stat(descriptor: int):
            nonlocal calls
            calls += 1
            info = real_stat(descriptor)
            if calls != 4:
                return info

            class Changed:
                st_dev = info.st_dev
                st_ino = info.st_ino
                st_mode = info.st_mode
                st_nlink = info.st_nlink
                st_size = info.st_size
                st_mtime_ns = info.st_mtime_ns + 1
                st_ctime_ns = info.st_ctime_ns

            return Changed()

        with mock.patch.object(changesets, "descriptor_file_stat", side_effect=changed_stat):
            with self.assertRaises(StudioError) as raised:
                self.call("workspace.overview", self.workspace_params())
        self.assertEqual("conflict", raised.exception.code)

    def test_overview_rejects_same_size_control_replacement(self) -> None:
        project = self.world / ".worldforge/project.json"
        replacement = project.with_name("project.replacement")
        replacement.write_bytes(b" " * len(project.read_bytes()))
        real_stat = changesets.descriptor_file_stat
        calls = 0

        def replace_after_read(descriptor: int):
            nonlocal calls
            calls += 1
            info = real_stat(descriptor)
            if calls == 4:
                os.replace(replacement, project)
            return info

        with mock.patch.object(changesets, "descriptor_file_stat", side_effect=replace_after_read):
            with self.assertRaises(StudioError) as raised:
                self.call("workspace.overview", self.workspace_params())
        self.assertEqual("conflict", raised.exception.code)

    @unittest.skipUnless(os.name == "posix", "mocked Windows directory-handle semantics")
    def test_overview_exercises_mocked_windows_pinned_directory_path(self) -> None:
        next_handle = iter(range(100, 200))
        with (
            mock.patch.object(changesets, "_platform_name", return_value="nt"),
            mock.patch.object(
                changesets,
                "_windows_lock_directory",
                side_effect=lambda _path: next(next_handle),
            ) as lock_directory,
            mock.patch.object(changesets, "_windows_close_handle") as close_handle,
        ):
            result = self.call("workspace.overview", self.workspace_params())

        self.assertEqual("studio_world", result["overview"]["project"]["world_id"])
        self.assertGreaterEqual(lock_directory.call_count, 8)
        self.assertEqual(lock_directory.call_count, close_handle.call_count)

    @unittest.skipUnless(os.name == "nt", "native Windows directory-handle semantics")
    def test_overview_exercises_native_windows_pinned_directory_path(self) -> None:
        real_lock = changesets._windows_lock_directory
        with mock.patch.object(changesets, "_windows_lock_directory", wraps=real_lock) as lock:
            result = self.call("workspace.overview", self.workspace_params())
        self.assertEqual("studio_world", result["overview"]["project"]["world_id"])
        self.assertGreaterEqual(lock.call_count, 8)

    def test_registered_world_root_replacement_is_rejected(self) -> None:
        moved = self.root / "world-original"
        self.world.rename(moved)
        self.world.mkdir()

        for method in ("workspace.overview", "source.list", "world.validate"):
            with self.subTest(method=method), self.assertRaises(StudioError) as raised:
                self.call(method, self.workspace_params())
            self.assertEqual("conflict", raised.exception.code)
            self.assertNotIn(str(self.world), raised.exception.message)

    def test_source_count_depth_and_byte_budgets_fail_closed(self) -> None:
        actor = self.world / "source/actors/hero.json"
        actor.write_bytes(b'{"blob":"' + (b"x" * MAX_SOURCE_DOCUMENT_BYTES) + b'"}\n')
        with self.assertRaisesRegex(StudioError, "source-document limit"):
            self.call("source.list", self.workspace_params())

        self.recreate_world()
        manifest = json.loads((self.world / "source/manifest.json").read_text(encoding="utf-8"))
        manifest["collections"]["actors"] = [
            f"actors/actor_{index}.json" for index in range(MAX_SOURCE_DOCUMENTS - 1)
        ]
        self.write_manifest(manifest)
        with self.assertRaisesRegex(StudioError, "document limit"):
            self.call("source.list", self.workspace_params())

        manifest["collections"]["actors"] = [
            "/".join(["deep"] * (MAX_SOURCE_DEPTH - 1) + ["actor.json"])
        ]
        self.write_manifest(manifest)
        with self.assertRaisesRegex(StudioError, "depth limit"):
            self.call("source.list", self.workspace_params())

    def test_python_schema_and_generated_method_contract_have_one_method_catalog(self) -> None:
        schema = json.loads((FORGE_ROOT / "schemas/studio-protocol.schema.json").read_text())
        self.assertEqual(METHODS, frozenset(schema["$defs"]["method"]["enum"]))
        discriminated = (
            set(schema["$defs"]["legacyMethod"]["enum"])
            | set(schema["$defs"]["workspaceScopedAuthoringMethod"]["enum"])
            | set(EXACT_CHANGESET_METHODS)
            | set(EXACT_ASSET_CATALOG_METHODS)
            | set(EXACT_ASSET_PREVIEW_METHODS)
            | {"source.read", "job.create", "job.cancel"}
        )
        self.assertEqual(METHODS, frozenset(discriminated))
        for method in (
            "workspace.overview",
            "source.list",
            "source.read",
            "world.validate",
            "world.analyze",
        ):
            self.assertIn(method, METHODS)

    def recreate_world(self) -> None:
        actor = self.world / "source/actors/hero.json"
        actor.write_text(
            json.dumps(
                {
                    "id": "hero",
                    "display_name": "Hero",
                    "playable": True,
                    "spawn": {"map_id": "starting_area", "x": 3, "y": 3},
                    "color": [194, 137, 255, 255],
                    "tags": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def write_manifest(self, payload: dict[str, object]) -> None:
        (self.world / "source/manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def tree_digest(self) -> dict[str, str]:
        return {
            path.relative_to(self.world).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(self.world.rglob("*"))
            if path.is_file()
        }


if __name__ == "__main__":
    unittest.main()
