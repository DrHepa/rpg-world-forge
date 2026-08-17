from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import stat
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from unittest.mock import patch

import worldforge.asset_io as asset_io_module
from worldforge.asset_io import (
    AssetContractError,
    bind_content_hash,
    encoded_json,
    read_bound_bytes,
    read_json_object,
    write_bytes_atomic,
    write_json_atomic,
    write_json_cooperative_replace,
)


class AssetIOTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "requires POSIX retained descriptors")
    def test_identity_atomic_replace_flushes_staged_dentry_before_exchange(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "project.json"
            source = b'{"format_version":2}\n'
            target = b'{"format_version":3}\n'
            destination.write_bytes(source)
            before = os.stat(destination, follow_symlinks=False)
            directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            events: list[tuple[str, int | None]] = []
            real_fsync = os.fsync
            real_exchange = asset_io_module._linux_exchange_names

            def tracked_fsync(descriptor: int) -> None:
                events.append(("fsync", descriptor))
                real_fsync(descriptor)

            def tracked_exchange(parent: int, source_name: str, destination_name: str) -> None:
                events.append(("exchange", None))
                real_exchange(parent, source_name, destination_name)

            try:
                with (
                    patch.object(asset_io_module.os, "fsync", side_effect=tracked_fsync),
                    patch.object(
                        asset_io_module,
                        "_linux_exchange_names",
                        side_effect=tracked_exchange,
                    ),
                ):
                    published = asset_io_module.write_bytes_identity_atomic_replace_at(
                        directory_fd,
                        "project.json",
                        target,
                        expected_sha256=hashlib.sha256(source).hexdigest(),
                        expected_identity=(before.st_dev, before.st_ino),
                        staging_name=".project.exchange",
                    )
            finally:
                os.close(directory_fd)

            exchange_index = events.index(("exchange", None))
            self.assertIn(("fsync", directory_fd), events[:exchange_index])
            after = os.stat(destination, follow_symlinks=False)
            self.assertEqual(published, (after.st_dev, after.st_ino))
            self.assertEqual(target, destination.read_bytes())
            self.assertEqual(source, (root / ".project.exchange").read_bytes())

    @unittest.skipUnless(os.name == "posix", "requires POSIX retained descriptors")
    def test_identity_atomic_replace_fails_before_exchange_when_stage_flush_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "project.json"
            source = b'{"format_version":2}\n'
            destination.write_bytes(source)
            before = os.stat(destination, follow_symlinks=False)
            directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            real_fsync = os.fsync
            failed = False

            def fail_first_directory_flush(descriptor: int) -> None:
                nonlocal failed
                if descriptor == directory_fd and not failed:
                    failed = True
                    raise OSError("staged dentry flush interrupted")
                real_fsync(descriptor)

            try:
                with (
                    patch.object(
                        asset_io_module.os,
                        "fsync",
                        side_effect=fail_first_directory_flush,
                    ),
                    patch.object(asset_io_module, "_linux_exchange_names") as exchange,
                    self.assertRaisesRegex(
                        AssetContractError,
                        "durably flush identity-atomic staging entry",
                    ),
                ):
                    asset_io_module.write_bytes_identity_atomic_replace_at(
                        directory_fd,
                        "project.json",
                        b'{"format_version":3}\n',
                        expected_sha256=hashlib.sha256(source).hexdigest(),
                        expected_identity=(before.st_dev, before.st_ino),
                        staging_name=".project.exchange",
                    )
                exchange.assert_not_called()
            finally:
                os.close(directory_fd)

            after = os.stat(destination, follow_symlinks=False)
            self.assertTrue(failed)
            self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
            self.assertEqual(source, destination.read_bytes())
            self.assertFalse((root / ".project.exchange").exists())

    @unittest.skipUnless(os.name == "posix", "requires POSIX retained descriptors")
    def test_identity_atomic_replace_fails_closed_without_exchange_primitive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "project.json"
            source = b'{"format_version":2}\n'
            destination.write_bytes(source)
            before = os.stat(destination, follow_symlinks=False)
            directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with (
                    patch.object(
                        asset_io_module,
                        "_linux_exchange_names",
                        side_effect=AssetContractError(
                            "identity-atomic replacement is unavailable"
                        ),
                    ),
                    self.assertRaisesRegex(
                        AssetContractError,
                        "identity-atomic replacement is unavailable",
                    ),
                ):
                    asset_io_module.write_bytes_identity_atomic_replace_at(
                        directory_fd,
                        "project.json",
                        b'{"format_version":3}\n',
                        expected_sha256=hashlib.sha256(source).hexdigest(),
                        expected_identity=(before.st_dev, before.st_ino),
                        staging_name=".project.exchange",
                    )
            finally:
                os.close(directory_fd)

            after = os.stat(destination, follow_symlinks=False)
            self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
            self.assertEqual(source, destination.read_bytes())
            self.assertFalse((root / ".project.exchange").exists())

    @unittest.skipUnless(os.name == "posix", "requires POSIX retained descriptors")
    def test_identity_atomic_replace_fails_closed_without_anonymous_temp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "project.json"
            source = b'{"format_version":2}\n'
            destination.write_bytes(source)
            before = os.stat(destination, follow_symlinks=False)
            directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            real_open = os.open

            def unsupported_tmpfile(path, flags, *args, **kwargs):
                if flags & getattr(os, "O_TMPFILE", 0):
                    raise OSError(errno.EOPNOTSUPP, "anonymous temporary unsupported")
                return real_open(path, flags, *args, **kwargs)

            try:
                with (
                    patch.object(asset_io_module.os, "open", side_effect=unsupported_tmpfile),
                    self.assertRaisesRegex(
                        AssetContractError,
                        "identity-atomic replacement is unavailable",
                    ),
                ):
                    asset_io_module.write_bytes_identity_atomic_replace_at(
                        directory_fd,
                        "project.json",
                        b'{"format_version":3}\n',
                        expected_sha256=hashlib.sha256(source).hexdigest(),
                        expected_identity=(before.st_dev, before.st_ino),
                        staging_name=".project.exchange",
                    )
            finally:
                os.close(directory_fd)

            after = os.stat(destination, follow_symlinks=False)
            self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
            self.assertEqual(source, destination.read_bytes())
            self.assertFalse((root / ".project.exchange").exists())

    def test_bound_reader_does_not_create_a_missing_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary) / "missing/nested"

            with self.assertRaises(AssetContractError):
                read_bound_bytes(parent / "contract.json")

            self.assertFalse(parent.exists())

    def test_reader_rejects_non_finite_json_numbers(self) -> None:
        for literal in ("NaN", "Infinity", "-Infinity", "1e999"):
            with self.subTest(literal=literal), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "contract.json"
                path.write_text(f'{{"value": {literal}}}\n', encoding="utf-8")

                with self.assertRaisesRegex(AssetContractError, "non-finite JSON number"):
                    read_json_object(path)

    def test_writer_rejects_non_finite_json_numbers_without_creating_output(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "new/output.json"

                with self.assertRaisesRegex(AssetContractError, "strict JSON"):
                    write_json_atomic(path, {"value": value})

                self.assertFalse(path.exists())
                self.assertFalse(path.parent.exists())

    def test_writer_rejects_symbolic_link_parent_without_external_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir()
            output_parent = root / "outputs"
            output_parent.symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(AssetContractError, "safe directory"):
                write_json_atomic(output_parent / "leak.json", {"safe": True})

            self.assertFalse((outside / "leak.json").exists())

    def test_writer_creates_verified_real_parents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "real/nested/output.json"

            write_json_atomic(path, {"safe": True})

            self.assertEqual({"safe": True}, read_json_object(path))

    def test_writer_does_not_replace_file_created_at_publish_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "contract.json"
            concurrent = {"owner": "concurrent"}
            original_link = asset_io_module._linux_link_descriptor_no_replace

            def create_destination_then_link(
                descriptor: int,
                parent_descriptor: int,
                destination_name: str,
            ) -> None:
                destination.write_bytes(encoded_json(concurrent))
                original_link(
                    descriptor,
                    parent_descriptor,
                    destination_name,
                )

            with (
                patch.object(
                    asset_io_module,
                    "_linux_link_descriptor_no_replace",
                    side_effect=create_destination_then_link,
                ),
                self.assertRaisesRegex(AssetContractError, "Refusing to overwrite"),
            ):
                write_json_atomic(destination, {"owner": "forge"})

            self.assertEqual(concurrent, read_json_object(destination))
            self.assertEqual([], list(destination.parent.glob(".contract.json.*")))

    def test_cooperative_writer_rechecks_hash_after_lock_acquisition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "contract.json"
            initial = bind_content_hash({"value": "initial"})
            concurrent = bind_content_hash({"value": "concurrent"})
            write_json_atomic(destination, initial)
            original_read = asset_io_module._read_json_object_entry

            def replace_before_cas_read(
                parent: object,
                name: str,
            ) -> dict[str, object]:
                destination.write_bytes(encoded_json(concurrent))
                return original_read(parent, name)

            with (
                patch.object(
                    asset_io_module,
                    "_read_json_object_entry",
                    side_effect=replace_before_cas_read,
                ),
                self.assertRaisesRegex(AssetContractError, "Content changed before publishing"),
            ):
                write_json_cooperative_replace(
                    destination,
                    bind_content_hash({"value": "forge"}),
                    expected_cooperative_content_hash=initial["content_hash"],
                )

            self.assertEqual(concurrent, read_json_object(destination))
            self.assertFalse((destination.parent / ".contract.json.lock").exists())

    def test_atomic_writer_rejects_fixed_path_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "contract.json"
            initial = bind_content_hash({"value": "initial"})
            write_json_atomic(destination, initial)

            with self.assertRaisesRegex(
                AssetContractError,
                "fixed-path JSON replacement is unavailable",
            ):
                write_json_atomic(
                    destination,
                    bind_content_hash({"value": "replacement"}),
                    overwrite=True,
                    expected_content_hash=initial["content_hash"],
                )

            self.assertEqual(initial, read_json_object(destination))
            unsupported = Path(temporary) / "missing/contract.json"
            with self.assertRaisesRegex(
                AssetContractError,
                "fixed-path JSON replacement is unavailable",
            ):
                write_json_atomic(unsupported, {"value": "replacement"}, overwrite=True)
            self.assertFalse(unsupported.parent.exists())

    def test_writer_rejects_parent_replaced_after_validation(self) -> None:
        writers = (
            ("json", lambda path: write_json_atomic(path, {"owner": "forge"})),
            ("bytes", lambda path: write_bytes_atomic(path, b"forge")),
        )
        for label, writer in writers:
            with self.subTest(writer=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                destination = root / "publish/nested/contract.json"
                moved = root / "publish-original"
                outside = root / "outside"
                (outside / "nested").mkdir(parents=True)
                original_create = asset_io_module._create_temporary_entry

                def replace_ancestor_after_retention(
                    parent: object,
                    prefix: str,
                    *,
                    root: Path = root,
                    moved: Path = moved,
                    outside: Path = outside,
                    original_create: Callable[[object, str], object] = original_create,
                ) -> object:
                    (root / "publish").rename(moved)
                    (root / "publish").symlink_to(outside, target_is_directory=True)
                    return original_create(parent, prefix)

                with (
                    patch.object(
                        asset_io_module,
                        "_create_temporary_entry",
                        side_effect=replace_ancestor_after_retention,
                    ),
                    self.assertRaisesRegex(AssetContractError, "ancestry changed") as caught,
                ):
                    writer(destination)

                self.assertEqual("output_ancestry_changed", caught.exception.reason_code)
                self.assertNotIn(str(root), str(caught.exception))
                self.assertNotIn("Errno", str(caught.exception))
                self.assertFalse((outside / destination.name).exists())
                self.assertEqual([], list((outside / "nested").iterdir()))
                self.assertEqual([], list((moved / "nested").iterdir()))

    @unittest.skipUnless(os.name == "posix", "requires POSIX retained descriptors")
    def test_writers_normalize_os_errors_during_ancestry_reverification(self) -> None:
        writers: tuple[tuple[str, Callable[[Path], object]], ...] = (
            ("json", lambda path: write_json_atomic(path, {"owner": "forge"})),
            ("bytes", lambda path: write_bytes_atomic(path, b"forge")),
        )
        for label, writer in writers:
            for injected_errno in (errno.ELOOP, errno.ENOTDIR):
                with (
                    self.subTest(writer=label, errno=injected_errno),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    destination = Path(temporary) / "nested/contract.json"
                    original_open = asset_io_module._open_posix_ancestry
                    calls = 0

                    def fail_reverification(
                        path: Path,
                        *,
                        create: bool,
                        injected_errno: int = injected_errno,
                        original_open: Callable[..., object] = original_open,
                    ) -> object:
                        nonlocal calls
                        calls += 1
                        if calls == 3:
                            raise OSError(
                                injected_errno,
                                "private native error",
                                str(path),
                            )
                        return original_open(path, create=create)

                    with (
                        patch.object(
                            asset_io_module,
                            "_open_posix_ancestry",
                            side_effect=fail_reverification,
                        ),
                        self.assertRaisesRegex(
                            AssetContractError,
                            "ancestry changed",
                        ) as caught,
                    ):
                        writer(destination)

                    self.assertEqual(
                        "output_ancestry_changed",
                        caught.exception.reason_code,
                    )
                    self.assertNotIn(str(destination.parent), str(caught.exception))
                    self.assertNotIn("private native error", str(caught.exception))
                    self.assertFalse(destination.exists())

    @unittest.skipUnless(os.name == "posix", "requires POSIX retained descriptors")
    def test_writers_normalize_identity_and_close_reverification_failures(self) -> None:
        writers: tuple[tuple[str, Callable[[Path], object]], ...] = (
            ("json", lambda path: write_json_atomic(path, {"owner": "forge"})),
            ("bytes", lambda path: write_bytes_atomic(path, b"forge")),
        )
        for label, writer in writers:
            for failure in ("identity", "close"):
                with (
                    self.subTest(writer=label, failure=failure),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    destination = Path(temporary) / "nested/contract.json"
                    original_open = asset_io_module._open_posix_ancestry
                    original_close = asset_io_module._close_posix_descriptors
                    open_calls = 0
                    close_calls = 0

                    def mismatch_identity(
                        path: Path,
                        *,
                        create: bool,
                        original_open: Callable[
                            ...,
                            tuple[list[int], tuple[tuple[int, int], ...]],
                        ] = original_open,
                    ) -> tuple[list[int], tuple[tuple[int, int], ...]]:
                        nonlocal open_calls
                        open_calls += 1
                        descriptors, identities = original_open(path, create=create)
                        if open_calls == 3:
                            final = identities[-1]
                            identities = (*identities[:-1], (final[0], final[1] + 1))
                        return descriptors, identities

                    def fail_after_close(
                        descriptors: list[int] | tuple[int, ...],
                        *,
                        original_close: Callable[
                            [list[int] | tuple[int, ...]],
                            None,
                        ] = original_close,
                    ) -> None:
                        nonlocal close_calls
                        close_calls += 1
                        original_close(descriptors)
                        if close_calls == 1:
                            raise AssetContractError("private close failure")

                    patches = (
                        patch.object(
                            asset_io_module,
                            "_open_posix_ancestry",
                            side_effect=mismatch_identity,
                        )
                        if failure == "identity"
                        else patch.object(
                            asset_io_module,
                            "_close_posix_descriptors",
                            side_effect=fail_after_close,
                        )
                    )
                    with (
                        patches,
                        self.assertRaisesRegex(
                            AssetContractError,
                            "ancestry changed",
                        ) as caught,
                    ):
                        writer(destination)

                    self.assertEqual(
                        "output_ancestry_changed",
                        caught.exception.reason_code,
                    )
                    self.assertNotIn(str(destination.parent), str(caught.exception))
                    self.assertNotIn("private close failure", str(caught.exception))
                    self.assertFalse(destination.exists())

    def test_writer_fails_closed_without_a_handle_bound_publication_primitive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "contract.json"

            with (
                patch.object(asset_io_module, "_DIR_FD_PUBLICATION", False),
                self.assertRaisesRegex(AssetContractError, "secure publication"),
            ):
                write_json_atomic(destination, {"safe": True})

            self.assertFalse(destination.exists())

    def test_writer_cleanup_never_unlinks_a_foreign_temp_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "contract.json"

            with patch.object(
                asset_io_module.os,
                "unlink",
                side_effect=AssertionError("pathname cleanup is unsafe"),
            ):
                write_json_atomic(destination, {"safe": True})

            self.assertEqual({"safe": True}, read_json_object(destination))
            self.assertEqual([], list(root.glob(".contract.json.*")))

    def test_writer_rejects_windows_reparse_parent_before_opening_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "reparse-parent"
            parent.mkdir()
            destination = parent / "contract.json"
            real_stat = os.stat(parent, follow_symlinks=False)
            reparse_stat = SimpleNamespace(
                st_mode=real_stat.st_mode,
                st_dev=real_stat.st_dev,
                st_ino=real_stat.st_ino,
                st_nlink=real_stat.st_nlink,
                st_size=real_stat.st_size,
                st_mtime_ns=real_stat.st_mtime_ns,
                st_ctime_ns=real_stat.st_ctime_ns,
                st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
            )

            original_directory_identity = asset_io_module._directory_info_identity

            def controlled_directory_identity(
                info: object,
                *,
                path: Path,
            ) -> tuple[int, int]:
                if path == parent:
                    return original_directory_identity(reparse_stat, path=path)
                if path == destination:
                    raise AssertionError("reparse target was inspected")
                return original_directory_identity(info, path=path)

            with (
                patch.object(
                    asset_io_module,
                    "_directory_info_identity",
                    side_effect=controlled_directory_identity,
                ),
                self.assertRaisesRegex(AssetContractError, "safe directory"),
            ):
                write_json_atomic(destination, {"safe": False})
            self.assertFalse(destination.exists())

    def test_durable_writer_flushes_parent_after_exact_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "contract.json"
            with patch.object(asset_io_module, "_sync_output_parent") as flush:
                write_json_atomic(
                    destination,
                    {"safe": True},
                    durable_parent=True,
                )
            flush.assert_called_once()
            self.assertEqual(destination.parent, flush.call_args.args[0].path)
            self.assertEqual({"safe": True}, read_json_object(destination))

    def test_writer_rejects_reparse_temporary_without_named_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "contract.json"
            original_descriptor_stat = asset_io_module.descriptor_file_stat

            def reparse_info(descriptor: int) -> object:
                info = original_descriptor_stat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    return info
                return SimpleNamespace(
                    st_mode=info.st_mode,
                    st_dev=info.st_dev,
                    st_ino=info.st_ino,
                    st_nlink=info.st_nlink,
                    st_size=info.st_size,
                    st_mtime_ns=info.st_mtime_ns,
                    st_ctime_ns=info.st_ctime_ns,
                    st_file_attributes=getattr(
                        stat,
                        "FILE_ATTRIBUTE_REPARSE_POINT",
                        0x400,
                    ),
                )

            with (
                patch.object(
                    asset_io_module,
                    "descriptor_file_stat",
                    side_effect=reparse_info,
                ),
                self.assertRaisesRegex(AssetContractError, "Temporary output"),
            ):
                write_json_atomic(destination, {"safe": False})
            self.assertFalse(destination.exists())
            self.assertEqual([], list(root.iterdir()))

    def test_windows_reparse_state_is_rejected_from_the_retained_handle(self) -> None:
        api = object.__new__(asset_io_module._WindowsPublicationApi)
        reparse = SimpleNamespace(
            st_mode=stat.S_IFREG | stat.S_IRUSR,
            st_dev=1,
            st_ino=2,
            st_nlink=1,
            st_size=0,
            st_mtime_ns=0,
            st_ctime_ns=0,
            st_file_attributes=getattr(
                stat,
                "FILE_ATTRIBUTE_REPARSE_POINT",
                0x400,
            ),
        )
        with (
            patch.object(
                asset_io_module,
                "windows_handle_file_stat",
                return_value=reparse,
            ),
            self.assertRaisesRegex(AssetContractError, "safe regular file"),
        ):
            api._state(7, directory=False, context="temporary output")

    def test_windows_rename_ex_uses_parent_bound_nt_request_and_conservative_buffer(self) -> None:
        api = object.__new__(asset_io_module._WindowsPublicationApi)
        self.assertEqual(22, api._WIN32_FILE_RENAME_INFO_EX)
        self.assertEqual(65, api._NT_FILE_RENAME_INFORMATION_EX)

        for destination_name in ("project.json", "projet-é.json"):
            with self.subTest(destination_name=destination_name):
                captured: list[tuple[int, object, bytes, int, int]] = []

                def nt_set_information(
                    handle: ctypes.c_void_p,
                    io_status: object,
                    buffer: ctypes.Array[ctypes.c_char],
                    buffer_size: int,
                    information_class: int,
                    captured: list[tuple[int, object, bytes, int, int]] = captured,
                ) -> int:
                    captured.append(
                        (
                            int(handle.value or 0),
                            io_status,
                            ctypes.string_at(buffer, buffer_size),
                            buffer_size,
                            information_class,
                        )
                    )
                    return 0

                api.nt_set_information = nt_set_information
                api.nt_status_to_dos_error = lambda _status: 317
                api.set_information = lambda *_args: self.fail(
                    "migration rename_ex fell back to SetFileInformationByHandle"
                )
                api.rename_ex(41, 73, destination_name)

                encoded = destination_name.encode("utf-16-le")
                expected_size = max(
                    ctypes.sizeof(asset_io_module._WindowsFileRenameInformationEx),
                    asset_io_module._WindowsFileRenameInformationEx.filename.offset + len(encoded),
                )
                self.assertEqual(1, len(captured))
                handle, io_status, raw, buffer_size, information_class = captured[0]
                self.assertEqual(41, handle)
                self.assertIsNotNone(io_status)
                self.assertEqual(65, information_class)
                self.assertNotEqual(22, information_class)
                self.assertEqual(expected_size, buffer_size)
                information = asset_io_module._WindowsFileRenameInformationEx.from_buffer_copy(raw)
                self.assertEqual(0x3, information.flags)
                self.assertEqual(73, information.root_directory)
                self.assertEqual(len(encoded), information.filename_length)
                offset = asset_io_module._WindowsFileRenameInformationEx.filename.offset
                self.assertEqual(encoded, raw[offset : offset + len(encoded)])

    def test_windows_rename_ex_maps_ntstatus_without_kernel32_fallback(self) -> None:
        api = object.__new__(asset_io_module._WindowsPublicationApi)
        api.nt_set_information = lambda *_args: -1073741790
        api.nt_status_to_dos_error = lambda status: 5 if status == -1073741790 else 317
        api.set_information = lambda *_args: self.fail(
            "migration rename_ex fell back to SetFileInformationByHandle"
        )

        with self.assertRaisesRegex(
            AssetContractError, "Could not publish Windows migration target: error 5"
        ):
            api.rename_ex(41, 73, "project.json")

    def test_windows_rename_ex_rejects_non_simple_destination_names(self) -> None:
        api = object.__new__(asset_io_module._WindowsPublicationApi)
        api.set_information = lambda *_args: self.fail(
            "unsafe destination reached FileRenameInfoEx"
        )

        for destination_name in ("", ".", "..", "nested/project.json", "nested\\project.json"):
            with (
                self.subTest(destination_name=destination_name),
                self.assertRaisesRegex(AssetContractError, "target name is invalid"),
            ):
                api.rename_ex(41, 73, destination_name)

    def test_windows_directory_creation_can_pin_names_without_requesting_delete(self) -> None:
        api = object.__new__(asset_io_module._WindowsPublicationApi)
        captured: list[dict[str, int]] = []

        def open_relative(
            _parent: int,
            _name: str,
            **arguments: int | str,
        ) -> int:
            captured.append(
                {key: value for key, value in arguments.items() if isinstance(value, int)}
            )
            return 91

        api._open_relative = open_relative
        api._state = lambda *_args, **_kwargs: SimpleNamespace()

        self.assertEqual(
            91,
            api.create_directory(73, "trusted-evidence", request_delete=False),
        )
        self.assertEqual(1, len(captured))
        self.assertEqual(0, captured[0]["access"] & api._DELETE)
        self.assertEqual(0, captured[0]["share"] & api._SHARE_DELETE)
        self.assertEqual(api._FILE_CREATE, captured[0]["disposition"])

        captured.clear()
        self.assertEqual(91, api.create_directory(73, "temporary-stage"))
        self.assertEqual(api._DELETE, captured[0]["access"] & api._DELETE)
        self.assertEqual(0, captured[0]["share"] & api._SHARE_DELETE)

    def test_windows_output_ancestry_shares_delete_only_for_external_anchor(self) -> None:
        api = object.__new__(asset_io_module._WindowsPublicationApi)
        api.nt_status_to_dos_error = lambda _status: 317
        api._state = lambda *_args, **_kwargs: SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_dev=11,
            st_ino=len(opened_handles),
            st_file_attributes=0,
        )
        opened_handles: list[int] = []
        create_file_shares: list[int] = []
        relative_shares: list[int] = []

        def create_file(
            _path: str,
            _access: int,
            share: int,
            _security: object,
            _disposition: int,
            _flags: int,
            _template: object,
        ) -> int:
            create_file_shares.append(share)
            opened_handles.append(41)
            return 41

        def nt_create_file(
            opened: object,
            _access: int,
            _attributes: object,
            _io_status: object,
            _allocation: object,
            _file_attributes: int,
            share: int,
            _disposition: int,
            _options: int,
            _ea_buffer: object,
            _ea_length: int,
        ) -> int:
            relative_shares.append(share)
            handle = 50 + len(relative_shares)
            opened_handles.append(handle)
            ctypes.cast(opened, ctypes.POINTER(ctypes.c_void_p)).contents.value = handle
            return 0

        api._create_file_w = create_file
        api.nt_create_file = nt_create_file

        handles, identities = api.open_ancestry(PureWindowsPath("X:/forge/project"), create=True)

        self.assertEqual([41, 51, 52], handles)
        self.assertEqual(3, len(identities))
        self.assertEqual(
            [api._SHARE_READ | api._SHARE_WRITE | api._SHARE_DELETE],
            create_file_shares,
        )
        self.assertEqual([api._SHARE_READ | api._SHARE_WRITE] * 2, relative_shares)

    def test_windows_output_ancestry_rejects_root_only_parent(self) -> None:
        api = object.__new__(asset_io_module._WindowsPublicationApi)
        api._create_file_w = lambda *_args: self.fail("root-only anchor must not be opened")

        with self.assertRaisesRegex(AssetContractError, "filesystem root"):
            api.open_ancestry(PureWindowsPath("X:/"), create=True)

    def test_windows_failed_temporary_cleanup_targets_only_the_retained_handle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            owned = root / "owned.tmp"
            descriptor = os.open(owned, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            events: list[tuple[str, int]] = []
            api = SimpleNamespace(
                mark_delete_on_close=lambda handle: events.append(("delete", handle)),
                close=lambda handle: events.append(("close", handle)),
            )
            parent = asset_io_module._PinnedOutputParent(
                root,
                (),
                windows_api=api,
                windows_handles=(11,),
            )
            entry = asset_io_module._TemporaryEntry(
                descriptor=descriptor,
                identity=(1, 2),
                windows_handle=22,
            )

            with patch.object(
                asset_io_module.os,
                "unlink",
                side_effect=AssertionError("pathname cleanup is unsafe"),
            ):
                asset_io_module._close_temporary_entry(parent, entry)

            self.assertEqual([("delete", 22), ("close", 22)], events)

    def test_windows_published_json_identity_verification_shares_delete_only_after_rename(
        self,
    ) -> None:
        info = SimpleNamespace(
            st_mode=stat.S_IFREG | stat.S_IRUSR,
            st_dev=7,
            st_ino=11,
            st_nlink=1,
            st_size=17,
            st_mtime_ns=0,
            st_ctime_ns=0,
            st_file_attributes=0,
        )
        events: list[tuple[str, object]] = []

        class _Api:
            def open_existing_file_strict(
                self,
                parent_handle: int,
                name: str,
                *,
                sealed: bool = False,
                delete: bool = False,
                share_delete: bool = False,
                write: bool = False,
            ) -> int:
                events.append(
                    (
                        "open-strict",
                        parent_handle,
                        name,
                        sealed,
                        delete,
                        share_delete,
                        write,
                    )
                )
                return 99

            def strict_entry_info(self, handle: int, *, context: str):
                events.append(("strict-info", handle, context))
                return info

            def close(self, handle: int) -> None:
                events.append(("close", handle))

        parent = asset_io_module._PinnedOutputParent(
            Path("C:/safe"),
            (),
            windows_api=_Api(),
            windows_handles=(8,),
        )

        self.assertIs(asset_io_module._published_file_info(parent, "report.json"), info)
        self.assertEqual(
            [
                ("open-strict", 8, "report.json", False, False, True, False),
                ("strict-info", 99, "published output C:/safe/report.json"),
                ("close", 99),
            ],
            events,
        )

    def test_windows_entry_operations_are_relative_to_the_retained_parent(self) -> None:
        events: list[tuple[str, int, str]] = []
        info = SimpleNamespace(
            st_mode=stat.S_IFREG | stat.S_IRUSR,
            st_dev=1,
            st_ino=2,
            st_nlink=1,
            st_size=0,
            st_mtime_ns=0,
            st_ctime_ns=0,
            st_file_attributes=0,
        )
        api = SimpleNamespace(
            open_existing_entry=lambda handle, name: (
                events.append(("open", handle, name)),
                22,
            )[1],
            entry_info=lambda handle, context: info,
            close=lambda handle: events.append(("close", handle, "")),
        )
        parent = asset_io_module._PinnedOutputParent(
            Path("C:/retained/output"),
            ((1, 1), (1, 2)),
            windows_api=api,
            windows_handles=(10, 11),
        )

        self.assertIs(info, asset_io_module._entry_info(parent, "contract.json"))
        self.assertEqual(
            [
                ("open", 11, "contract.json"),
                ("close", 22, ""),
            ],
            events,
        )

    def test_windows_temp_and_read_use_the_retained_parent_handle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.json"
            source.write_bytes(encoded_json({"safe": True}))
            source_descriptor = os.open(source, os.O_RDWR)
            source_info = os.fstat(source_descriptor)
            events: list[tuple[str, int, str]] = []
            api = SimpleNamespace(
                create_temporary=lambda handle, name: (
                    events.append(("create", handle, name)),
                    21,
                )[1],
                open_existing_file=lambda handle, name: (
                    events.append(("read", handle, name)),
                    22,
                )[1],
                _state=lambda handle, directory, context: source_info,
                duplicate_to_descriptor=lambda handle, writable: os.dup(source_descriptor),
                mark_delete_on_close=lambda handle: events.append(("delete", handle, "")),
                close=lambda handle: events.append(("close", handle, "")),
            )
            parent = asset_io_module._PinnedOutputParent(
                Path("C:/retained/output"),
                ((1, 1), (1, 2)),
                windows_api=api,
                windows_handles=(10, 11),
            )
            try:
                entry = asset_io_module._create_temporary_entry(parent, ".contract.")
                asset_io_module._close_temporary_entry(parent, entry)
                self.assertEqual(
                    {"safe": True},
                    asset_io_module._read_json_object_entry(parent, "contract.json"),
                )
            finally:
                os.close(source_descriptor)

            self.assertEqual(11, events[0][1])
            self.assertTrue(events[0][2].startswith(".contract."))
            self.assertIn(("read", 11, "contract.json"), events)

    def test_windows_parent_durability_is_relative_to_retained_ancestor(self) -> None:
        events: list[tuple[int, str, tuple[int, int], str]] = []
        api = SimpleNamespace(
            flush_relative_directory=lambda handle, name, expected, context: events.append(
                (handle, name, expected, context)
            ),
        )
        parent = asset_io_module._PinnedOutputParent(
            Path("C:/retained/output"),
            ((1, 1), (1, 2), (1, 3)),
            windows_api=api,
            windows_handles=(10, 11, 12),
        )

        with patch.object(asset_io_module._PinnedOutputParent, "assert_current"):
            asset_io_module._sync_output_parent(parent)

        self.assertEqual(
            [
                (
                    11,
                    "output",
                    (1, 3),
                    "published JSON parent C:/retained/output",
                )
            ],
            events,
        )


if __name__ == "__main__":
    unittest.main()
