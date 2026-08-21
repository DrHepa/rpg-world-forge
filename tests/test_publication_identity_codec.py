from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from gamepack_runtime import game_package as game_package_contract_module
from gamepack_runtime.distribution import canonical_contract_hash
from worldforge import game_materialization_bundle as materialization_module
from worldforge import game_package as game_package_module
from worldforge import game_runtime_bundle as runtime_module
from worldforge import generic_assetpack as assetpack_module
from worldforge import standalone_game as standalone_module
from worldforge._publication_identity import (
    PublicationIdentityCodecError,
    decode_publication_identity,
    encode_publication_identity,
)
from worldforge.creation_contracts import _decode_creation_object
from worldforge.integrity import canonical_json_bytes

MAX_WINDOWS_IDENTITY = (2**64 - 1, 2**128 - 1)


def _windows_journal_documents(
    *,
    state: str,
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    tuple[Path, Path, Path],
]:
    digest = "a" * 64
    operation_id = "0" * 32
    stage_identity = None if state == "intent" else MAX_WINDOWS_IDENTITY
    assetpack_destination = Path("assetpack")
    materialization_destination = Path("materialization")
    runtime_destination = Path("runtime")
    with patch.object(os, "name", "nt"):
        assetpack = assetpack_module._journal_document(  # noqa: SLF001
            operation_id=operation_id,
            state=state,
            stage=Path(f".{assetpack_destination.name}.assetpack-{operation_id}"),
            destination=assetpack_destination,
            stage_identity=stage_identity,
            manifest={
                "assetpack_id": "assetpack_test",
                "content_hash": digest,
                "inventory": {"content_hash": digest},
                "release_ready_manifest": {"content_hash": digest},
            },
            manifest_payload=b"{}",
        )
        materialization = materialization_module._journal_document(  # noqa: SLF001
            operation_id=operation_id,
            state=state,
            stage=Path(
                f".{materialization_destination.name}.game-materialization-bundle-{operation_id}"
            ),
            destination=materialization_destination,
            parent_identity=MAX_WINDOWS_IDENTITY,
            stage_identity=stage_identity,
            manifest={
                "materialization_bundle_id": "game_materialization_bundle_" + "0" * 36,
                "content_hash": digest,
                "tree_hash": digest,
                "lineage": {
                    "runtime_bundle_hash": digest,
                    "runtime_implementation_hash": digest,
                    "platform_lock_set_hash": digest,
                },
            },
            manifest_payload=b"{}",
        )
        runtime = runtime_module._journal_document(  # noqa: SLF001
            operation_id=operation_id,
            state=state,
            stage=Path(f".{runtime_destination.name}.game-runtime-bundle-{operation_id}"),
            destination=runtime_destination,
            stage_identity=stage_identity,
            manifest={
                "bundle_id": "game_runtime_bundle_" + "0" * 48,
                "content_hash": digest,
                "tree_hash": digest,
                "contracts": {
                    name: {"content_hash": digest}
                    for name in (
                        "gamepack",
                        "runtime_snapshot",
                        "runtime_adapter",
                        "runtime_adapter_registry",
                        "runtime_composition",
                        "runtime_support_report",
                    )
                },
                "assetpack": {
                    "manifest": {"content_hash": digest},
                    "root_hash": digest,
                    "inventory_hash": digest,
                },
                "runtime_snapshot_tree": {"tree_hash": digest},
            },
            manifest_payload=b"{}",
        )
    return (
        assetpack,
        materialization,
        runtime,
        (assetpack_destination, materialization_destination, runtime_destination),
    )


def _standalone_journal_document(
    *, state: str, windows: bool, destination: Path = Path("standalone")
) -> dict[str, object]:
    digest = "b" * 64
    operation_id = "1" * 32
    stage_identity = None if state == "intent" else MAX_WINDOWS_IDENTITY
    parent_identity = MAX_WINDOWS_IDENTITY if windows else (123, 456)
    with patch.object(os, "name", "nt" if windows else "posix"):
        return standalone_module._journal_document(  # noqa: SLF001
            operation_id=operation_id,
            state=state,
            stage=Path(f".{destination.name}.standalone-stage-{operation_id}"),
            destination=destination,
            parent_identity=parent_identity,
            stage_identity=stage_identity if windows else (789, 101112),
            manifest={"content_hash": digest},
            lock={"content_hash": digest, "tree_hash": digest},
            materialization_hash=digest,
        )


def _game_package_journal_document(
    *, state: str, windows: bool, destination: Path = Path("extracted-game")
) -> dict[str, object]:
    digest = "c" * 64
    operation_id = "2" * 32
    stage_identity = None if state == "intent" else MAX_WINDOWS_IDENTITY
    parent_identity = MAX_WINDOWS_IDENTITY if windows else (123, 456)
    manifest_payload = b'{"manifest":"payload"}'
    lock_payload = b'{"lock":"payload"}'
    files_inventory = [
        {
            "path": game_package_module.GAME_MANIFEST_PATH,
            "sha256": hashlib.sha256(manifest_payload).hexdigest(),
            "size_bytes": len(manifest_payload),
        },
        {
            "path": game_package_module.GAME_LOCK_PATH,
            "sha256": hashlib.sha256(lock_payload).hexdigest(),
            "size_bytes": len(lock_payload),
        },
    ]
    lineage = {
        "gamepack_hash": digest,
        "assetpack_hash": digest,
        "runtime_snapshot_hash": digest,
        "runtime_composition_hash": digest,
        "runtime_bundle_hash": digest,
    }
    standalone_game = {
        "format": "world-forge.standalone_game",
        "format_version": 1,
        "game_id": "identity_test_game",
        "content_hash": digest,
    }
    payload_lock = {
        "format": "world-forge.standalone_game_lock",
        "format_version": 1,
        "id": "identity_test_lock",
        "content_hash": digest,
        "tree_hash": digest,
    }
    seed = game_package_contract_module._package_id_seed(  # noqa: SLF001
        game_id="identity_test_game",
        lineage=lineage,
        standalone_game=standalone_game,
        payload_lock=payload_lock,
        files=files_inventory,
    )
    package_manifest = {
        "format": "world-forge.game_package",
        "format_version": 1,
        "package_id": "game_package_" + canonical_contract_hash(seed)[:40],
        **seed,
        "content_hash": "",
    }
    package_manifest["content_hash"] = canonical_contract_hash(package_manifest)
    files = {
        game_package_module.GAME_MANIFEST_PATH: manifest_payload,
        game_package_module.GAME_LOCK_PATH: lock_payload,
    }
    with patch.object(os, "name", "nt" if windows else "posix"):
        return game_package_module._journal_document(  # noqa: SLF001
            operation_id=operation_id,
            state=state,
            stage=Path(f".{destination.name}.game-package-stage-{operation_id}"),
            destination=destination,
            parent_identity=parent_identity,
            stage_identity=stage_identity if windows else (789, 101112),
            archive_sha256=digest,
            package_manifest=package_manifest,
            files=files,
        )


class PublicationIdentityCodecTests(unittest.TestCase):
    def test_windows_identity_uses_exact_fixed_width_lowercase_hex(self) -> None:
        encoded = encode_publication_identity(MAX_WINDOWS_IDENTITY, windows=True)

        self.assertEqual(
            {
                "volume_serial": "ffffffffffffffff",
                "file_id": "ffffffffffffffffffffffffffffffff",
            },
            encoded,
        )
        self.assertEqual(MAX_WINDOWS_IDENTITY, decode_publication_identity(encoded))

    def test_legacy_safe_numeric_identity_remains_byte_identical(self) -> None:
        encoded = encode_publication_identity((123, 456), windows=False)

        self.assertEqual({"device": 123, "inode": 456}, encoded)
        self.assertEqual((123, 456), decode_publication_identity(encoded))
        self.assertEqual(
            b'{\n  "device": 123,\n  "inode": 456\n}\n',
            canonical_json_bytes(encoded),
        )

    def test_identity_decoder_rejects_noncanonical_or_unsafe_shapes(self) -> None:
        malformed = (
            {"volume_serial": "FFFFFFFFFFFFFFFF", "file_id": "f" * 32},
            {"volume_serial": "f" * 15, "file_id": "f" * 32},
            {"volume_serial": "f" * 17, "file_id": "f" * 32},
            {"volume_serial": "f" * 16, "file_id": "f" * 31},
            {"volume_serial": "f" * 16, "file_id": "f" * 33},
            {"volume_serial": "f" * 16, "file_id": "g" * 32},
            {"volume_serial": "f" * 16, "file_id": "f" * 32, "extra": 0},
            {"volume_serial": "f" * 16, "inode": 1},
            {"device": 1, "file_id": "f" * 32},
            {"device": 2**53, "inode": 1},
            {"device": 1, "inode": 2**53},
            {"device": True, "inode": 1},
            {"device": -1, "inode": 1},
        )

        for value in malformed:
            with self.subTest(value=value), self.assertRaises(PublicationIdentityCodecError):
                decode_publication_identity(value)

    def test_identity_encoder_rejects_overflow_and_unsafe_posix_values(self) -> None:
        for identity, windows in (
            ((2**64, 1), True),
            ((1, 2**128), True),
            ((2**53, 1), False),
            ((1, 2**53), False),
            ((True, 1), True),
            ((-1, 1), False),
        ):
            with (
                self.subTest(identity=identity, windows=windows),
                self.assertRaises(PublicationIdentityCodecError),
            ):
                encode_publication_identity(identity, windows=windows)


class PublicationJournalIdentityIntegrationTests(unittest.TestCase):
    def _strict_roundtrip(self, journal: dict[str, object]) -> dict[str, object]:
        payload = canonical_json_bytes(journal)
        return _decode_creation_object(payload, Path("publication-journal.json"))

    def test_all_strict_journals_roundtrip_max_width_windows_identity(self) -> None:
        assetpack, materialization, runtime, destinations = _windows_journal_documents(
            state="copying"
        )
        assetpack_destination, materialization_destination, runtime_destination = destinations

        assetpack_validated = assetpack_module._validate_journal(  # noqa: SLF001
            self._strict_roundtrip(assetpack), assetpack_destination
        )
        materialization_validated = materialization_module._validate_journal(  # noqa: SLF001
            self._strict_roundtrip(materialization), materialization_destination
        )
        runtime_validated = runtime_module._validate_journal(  # noqa: SLF001
            self._strict_roundtrip(runtime), runtime_destination
        )

        self.assertEqual(
            MAX_WINDOWS_IDENTITY,
            assetpack_module._identity_from_document(  # noqa: SLF001
                assetpack_validated["stage_identity"],
                "journal.stage_identity",
            ),
        )
        self.assertEqual(
            MAX_WINDOWS_IDENTITY,
            materialization_module._identity_from_document(  # noqa: SLF001
                materialization_validated["parent_identity"],
                context="journal.parent_identity",
            ),
        )
        self.assertEqual(
            MAX_WINDOWS_IDENTITY,
            materialization_module._identity_from_document(  # noqa: SLF001
                materialization_validated["stage_identity"],
                context="journal.stage_identity",
            ),
        )
        self.assertEqual(
            MAX_WINDOWS_IDENTITY,
            runtime_module._identity_from_document(  # noqa: SLF001
                runtime_validated["stage_identity"]
            ),
        )

        expected = {
            "volume_serial": "ffffffffffffffff",
            "file_id": "ffffffffffffffffffffffffffffffff",
        }
        self.assertEqual(expected, assetpack["stage_identity"])
        self.assertEqual(expected, materialization["parent_identity"])
        self.assertEqual(expected, materialization["stage_identity"])
        self.assertEqual(expected, runtime["stage_identity"])

    def test_all_journals_append_and_reread_max_width_windows_transition(self) -> None:
        intent = _windows_journal_documents(state="intent")
        copying = _windows_journal_documents(state="copying")
        modules = (assetpack_module, materialization_module, runtime_module)

        with tempfile.TemporaryDirectory(prefix="wf-publication-identity-") as temporary:
            root = Path(temporary)
            for index, module in enumerate(modules):
                intent_document = intent[index]
                copying_document = copying[index]
                destination = root / copying[3][index]
                journal_path = root / f"journal-{index}.json"
                lock = Mock()
                lock.parent = None
                identity = module._write_journal(  # noqa: SLF001
                    journal_path,
                    intent_document,
                    lock=lock,
                    create=True,
                )
                module._write_journal(  # noqa: SLF001
                    journal_path,
                    copying_document,
                    lock=lock,
                    create=False,
                    expected_document=intent_document,
                    expected_identity=identity,
                )
                reader = (
                    module._read_journal_record_state  # noqa: SLF001
                    if module is assetpack_module
                    else module._read_journal_state  # noqa: SLF001
                )
                loaded = reader(journal_path, destination)

                self.assertIsNotNone(loaded)
                assert loaded is not None
                self.assertEqual(copying_document, loaded[0])
                self.assertEqual(
                    MAX_WINDOWS_IDENTITY,
                    decode_publication_identity(loaded[0]["stage_identity"]),
                )

    def test_all_journal_readers_reject_mixed_identity_shapes(self) -> None:
        mixed = {"device": 1, "file_id": "0" * 32}
        cases = (
            (
                assetpack_module._identity_from_document,  # noqa: SLF001
                (mixed, "journal.stage_identity"),
                {},
                assetpack_module.GenericAssetpackError,
            ),
            (
                materialization_module._identity_from_document,  # noqa: SLF001
                (mixed,),
                {"context": "journal.stage_identity"},
                materialization_module.GameMaterializationBundleError,
            ),
            (
                runtime_module._identity_from_document,  # noqa: SLF001
                (mixed,),
                {},
                runtime_module.GameRuntimeBundleError,
            ),
        )

        for reader, args, kwargs, error in cases:
            with self.subTest(reader=reader.__module__), self.assertRaises(error):
                reader(*args, **kwargs)

    def test_standalone_and_package_journal_writers_encode_max_width_windows_identity(self) -> None:
        expected = {
            "volume_serial": "ffffffffffffffff",
            "file_id": "ffffffffffffffffffffffffffffffff",
        }

        standalone = _standalone_journal_document(state="copying", windows=True)
        package = _game_package_journal_document(state="copying", windows=True)

        self.assertEqual(expected, standalone["parent_identity"])
        self.assertEqual(expected, standalone["stage_identity"])
        self.assertEqual(expected, package["parent_identity"])
        self.assertEqual(expected, package["stage_identity"])
        self.assertEqual(
            MAX_WINDOWS_IDENTITY,
            decode_publication_identity(standalone["stage_identity"]),
        )
        self.assertEqual(
            MAX_WINDOWS_IDENTITY,
            decode_publication_identity(package["stage_identity"]),
        )

    def test_standalone_and_package_journals_strictly_roundtrip_windows_identity(self) -> None:
        standalone = self._strict_roundtrip(
            _standalone_journal_document(state="copying", windows=True)
        )
        package = self._strict_roundtrip(
            _game_package_journal_document(state="copying", windows=True)
        )

        self.assertEqual(
            MAX_WINDOWS_IDENTITY,
            decode_publication_identity(standalone["parent_identity"]),
        )
        self.assertEqual(
            MAX_WINDOWS_IDENTITY,
            decode_publication_identity(standalone["stage_identity"]),
        )
        self.assertEqual(
            MAX_WINDOWS_IDENTITY,
            decode_publication_identity(package["parent_identity"]),
        )
        self.assertEqual(
            MAX_WINDOWS_IDENTITY,
            decode_publication_identity(package["stage_identity"]),
        )

    def test_standalone_and_package_readers_reject_bad_identity_shapes(self) -> None:
        malformed = (
            {"volume_serial": "FFFFFFFFFFFFFFFF", "file_id": "f" * 32},
            {"volume_serial": "f" * 16, "file_id": "f" * 32, "extra": 0},
            {"volume_serial": "f" * 16, "inode": 1},
            {"device": 2**53, "inode": 1},
            {"device": 1, "inode": 2**53},
        )
        cases = (
            (
                standalone_module._identity_from_document,
                standalone_module.StandaloneGameError,
            ),
            (
                game_package_module._identity_from_document,
                game_package_module.WorldForgeGamePackageError,
            ),
        )

        for value in malformed:
            for reader, error in cases:
                with (
                    self.subTest(value=value, reader=reader.__module__),
                    self.assertRaises(error),
                ):
                    reader(value, context="journal.stage_identity")

    def test_standalone_and_package_posix_journal_identity_bytes_remain_stable(self) -> None:
        standalone = _standalone_journal_document(state="copying", windows=False)
        package = _game_package_journal_document(state="copying", windows=False)

        self.assertEqual(
            b'{\n  "device": 123,\n  "inode": 456\n}\n',
            canonical_json_bytes(standalone["parent_identity"]),
        )
        self.assertEqual(
            b'{\n  "device": 789,\n  "inode": 101112\n}\n',
            canonical_json_bytes(standalone["stage_identity"]),
        )
        self.assertEqual(
            b'{\n  "device": 123,\n  "inode": 456\n}\n',
            canonical_json_bytes(package["parent_identity"]),
        )
        self.assertEqual(
            b'{\n  "device": 789,\n  "inode": 101112\n}\n',
            canonical_json_bytes(package["stage_identity"]),
        )

    def test_standalone_and_package_append_and_reread_windows_transition(self) -> None:
        modules_and_factories = (
            (standalone_module, _standalone_journal_document, "standalone"),
            (game_package_module, _game_package_journal_document, "extracted-game"),
        )

        with tempfile.TemporaryDirectory(prefix="wf-standalone-package-identity-") as temporary:
            root = Path(temporary)
            for module, factory, destination_name in modules_and_factories:
                destination = root / destination_name
                intent_document = factory(
                    state="intent",
                    windows=True,
                    destination=destination,
                )
                copying_document = factory(
                    state="copying",
                    windows=True,
                    destination=destination,
                )
                with patch.object(
                    module,
                    "_optional_directory_identity",
                    return_value=MAX_WINDOWS_IDENTITY,
                ):
                    identity = module._write_journal(  # noqa: SLF001
                        destination,
                        intent_document,
                        create=True,
                    )
                    module._write_journal(  # noqa: SLF001
                        destination,
                        copying_document,
                        create=False,
                        expected_document=intent_document,
                        expected_identity=identity,
                    )
                    loaded = module._read_journal(destination)  # noqa: SLF001

                self.assertIsNotNone(loaded)
                assert loaded is not None
                self.assertEqual(copying_document, loaded[0])
                self.assertEqual(
                    MAX_WINDOWS_IDENTITY,
                    decode_publication_identity(loaded[0]["stage_identity"]),
                )

    def test_journal_versions_remain_private_v1(self) -> None:
        self.assertEqual(1, assetpack_module.GENERIC_ASSETPACK_JOURNAL_VERSION)
        self.assertEqual(1, materialization_module.GAME_MATERIALIZATION_BUNDLE_JOURNAL_VERSION)
        self.assertEqual(1, runtime_module.GAME_RUNTIME_BUNDLE_JOURNAL_VERSION)
        self.assertEqual(1, standalone_module._JOURNAL_VERSION)  # noqa: SLF001
        self.assertEqual(1, game_package_module._JOURNAL_VERSION)  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
