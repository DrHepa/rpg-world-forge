from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from apps.studio.scripts import shell_package_snapshot as snapshot


class StudioShellPackageSnapshotTests(unittest.TestCase):
    def test_windows_verification_reader_coexists_with_retained_writer_only(self) -> None:
        class FakeVoid:
            def __init__(self, value: object = None) -> None:
                self.value = value

        class FakeHandle:
            def __init__(self, value: object = None) -> None:
                self.value = value

        api = SimpleNamespace()
        api.ctypes = SimpleNamespace(
            byref=lambda value: value,
            cast=lambda value, _kind: SimpleNamespace(
                value=value.value if isinstance(value, FakeHandle) else value
            ),
            c_void_p=FakeVoid,
            create_unicode_buffer=lambda value: value,
            pointer=lambda value: value,
            sizeof=lambda _value: 1,
        )
        api.wintypes = SimpleNamespace(HANDLE=FakeHandle, LPWSTR=object)
        api.UnicodeString = lambda *_args: object()
        api.ObjectAttributes = lambda *_args: object()
        api.IoStatusBlock = lambda: object()
        calls: list[tuple[int, int]] = []
        opened: list[tuple[int, int]] = []

        def nt_create(
            output: FakeHandle,
            access: int,
            _attributes: object,
            _io_status: object,
            _allocation: object,
            _file_attributes: int,
            share: int,
            _disposition: int,
            _options: int,
            _ea: object,
            _ea_length: int,
        ) -> int:
            calls.append((access, share))
            for existing_access, existing_share in opened:
                if (
                    access & snapshot._WindowsReader.GENERIC_READ
                    and not existing_share & snapshot._WindowsReader.FILE_SHARE_READ
                    or access & snapshot._WindowsReader.GENERIC_WRITE
                    and not existing_share & snapshot._WindowsReader.FILE_SHARE_WRITE
                    or existing_access & snapshot._WindowsReader.GENERIC_READ
                    and not share & snapshot._WindowsReader.FILE_SHARE_READ
                    or existing_access & snapshot._WindowsReader.GENERIC_WRITE
                    and not share & snapshot._WindowsReader.FILE_SHARE_WRITE
                ):
                    return 0xC0000043 - (1 << 32)
            opened.append((access, share))
            output.value = 70 + len(opened)
            return 0

        api.NtCreateFile = nt_create
        api.state = lambda _handle, _field: SimpleNamespace(
            is_directory=False,
            is_reparse=False,
            nlink=1,
        )
        api.close = lambda _handle: None
        reader = object.__new__(snapshot._WindowsReader)
        reader.api = api
        reader.ctypes = api.ctypes
        reader.wintypes = api.wintypes

        reader.create(7, "shell-package-manifest.json")
        with self.assertRaises(snapshot.SnapshotError) as incompatible:
            reader.open(7, "shell-package-manifest.json")
        self.assertEqual("package_entry_changed", incompatible.exception.code)

        reader.open(
            7,
            "shell-package-manifest.json",
            share_write=True,
        )
        with self.assertRaises(snapshot.SnapshotError):
            reader.create(7, "shell-package-manifest.json")

        writer_access, writer_share = calls[0]
        reader_access, reader_share = calls[2]
        self.assertTrue(writer_access & snapshot._WindowsReader.GENERIC_WRITE)
        self.assertEqual(snapshot._WindowsReader.FILE_SHARE_READ, writer_share)
        self.assertTrue(reader_access & snapshot._WindowsReader.GENERIC_READ)
        self.assertEqual(
            snapshot._WindowsReader.FILE_SHARE_READ | snapshot._WindowsReader.FILE_SHARE_WRITE,
            reader_share,
        )
        self.assertFalse(writer_share & snapshot._WindowsReader.FILE_SHARE_WRITE)

    def test_finalize_uses_writer_compatible_reopen_for_owned_manifest(self) -> None:
        payload = b'{"status":"blocked"}\n'

        class FakeChain:
            def require_bindings(self) -> None:
                pass

        class FakeApi:
            def __init__(self) -> None:
                self.closed: list[int] = []

            def state(self, handle: int, _field: str) -> SimpleNamespace:
                if handle == 1:
                    return SimpleNamespace(
                        identity=(1, 1),
                        is_directory=True,
                        is_reparse=False,
                    )
                return SimpleNamespace(
                    identity=(2, 2),
                    is_directory=False,
                    is_reparse=False,
                    nlink=1,
                    size=len(payload),
                )

            def close(self, handle: int) -> None:
                self.closed.append(handle)

        class FakeReader:
            def __init__(self) -> None:
                self.share_write: list[bool] = []

            def chunks(self, _handle: int, _size: int):
                yield payload

            def open(
                self,
                _parent: int,
                _name: str,
                *,
                share_write: bool = False,
            ) -> int:
                self.share_write.append(share_write)
                if not share_write:
                    raise snapshot.SnapshotError("package_entry_changed")
                return 3

        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            name = "shell-package-manifest.json"
            (root / name).write_bytes(payload)
            directory = snapshot._Directory(
                absolute=root,
                children=(name,),
                handle=1,
                identity=(1, 1),
                name="",
                parent=None,
                relative="",
            )
            record = snapshot._File(
                handle=2,
                identity=(2, 2),
                name=name,
                nlink=1,
                parent=directory,
                payload=payload,
                relative=snapshot.SHELL_MANIFEST_PATH,
                sha256=hashlib.sha256(payload).hexdigest(),
                size=len(payload),
                retained_writer=True,
            )
            tree = object.__new__(snapshot._WindowsPinnedTree)
            tree.chain = FakeChain()
            tree.api = FakeApi()
            tree.reader = FakeReader()
            tree.directories = {"": directory}
            tree.files = {snapshot.SHELL_MANIFEST_PATH: record}
            tree.snapshot_chain = None

            tree.finalize()

            self.assertEqual([True], tree.reader.share_write)
            self.assertEqual([3], tree.api.closed)


if __name__ == "__main__":
    unittest.main()
