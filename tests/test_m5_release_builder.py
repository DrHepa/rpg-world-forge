from __future__ import annotations

import base64
import csv
import gzip
import hashlib
import io
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import scripts.build_release as release_builder

ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)


def _copy_committed_fixture(destination: Path) -> None:
    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name in {".git", ".pytest_cache", ".ruff_cache", "__pycache__"}
            or name.endswith(".pyc")
        }

    shutil.copytree(ROOT, destination, ignore=ignore)
    subprocess.run(["git", "init"], cwd=destination, check=True, stdout=subprocess.PIPE)
    subprocess.run(
        ["git", "config", "user.email", "release-test@example.invalid"],
        cwd=destination,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Release Test"],
        cwd=destination,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=destination, check=True)
    subprocess.run(
        ["git", "commit", "-m", "test: fixture release source"],
        cwd=destination,
        check=True,
        stdout=subprocess.PIPE,
    )


def _run_builder(repo: Path, output: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo / "must-not-leak")
    return subprocess.run(
        [str(PYTHON), str(repo / "scripts/build_release.py"), "--output-dir", str(output)],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _tar_payload(entries: list[tuple[tarfile.TarInfo, bytes | None]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for info, payload in entries:
            if payload is None:
                archive.addfile(info)
            else:
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _artifact_pair(root: Path, name: str, payload: bytes) -> tuple[Path, Path]:
    left = root / "left" / name
    right = root / "right" / name
    left.parent.mkdir(exist_ok=True)
    right.parent.mkdir(exist_ok=True)
    left.write_bytes(payload)
    right.write_bytes(payload)
    return left, right


def _inventory_directories(inventory: dict[str, bytes]) -> tuple[str, ...]:
    directories = {""}
    for relative in inventory:
        parts = relative.split("/")[:-1]
        for length in range(1, len(parts) + 1):
            directories.add("/".join(parts[:length]))
    return tuple(sorted(directories))


def _write_public_sdist(
    path: Path,
    identity: release_builder.ReleaseIdentity,
    inventory: dict[str, bytes],
    *,
    canonical_root: str = "share/world-forge",
    legacy_root: str = "share/rpg-world-forge",
    extra_public_directory: str | None = None,
    extra_public_file: tuple[str, bytes] | None = None,
    package_name: str | None = None,
    package_version: str | None = None,
    archive_root: str | None = None,
) -> None:
    root = identity.sdist_root if archive_root is None else archive_root
    entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
    root_info = tarfile.TarInfo(root)
    root_info.type = tarfile.DIRTYPE
    entries.append((root_info, None))
    metadata = (
        "Metadata-Version: 2.4\n"
        f"Name: {identity.name if package_name is None else package_name}\n"
        f"Version: {identity.version if package_version is None else package_version}\n\n"
    ).encode()
    entries.append((tarfile.TarInfo(f"{root}/PKG-INFO"), metadata))
    for public_root in (canonical_root, legacy_root):
        for relative in _inventory_directories(inventory):
            directory = f"{root}/{public_root}"
            if relative:
                directory += f"/{relative}"
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            entries.append((info, None))
        for relative, payload in inventory.items():
            entries.append((tarfile.TarInfo(f"{root}/{public_root}/{relative}"), payload))
    if extra_public_directory is not None:
        info = tarfile.TarInfo(f"{root}/{canonical_root}/{extra_public_directory}")
        info.type = tarfile.DIRTYPE
        entries.append((info, None))
    if extra_public_file is not None:
        relative, payload = extra_public_file
        entries.append((tarfile.TarInfo(f"{root}/{canonical_root}/{relative}"), payload))
    payload = _tar_payload(entries)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as archive:
            archive.write(payload)


def _write_public_wheel(
    path: Path,
    identity: release_builder.ReleaseIdentity,
    inventory: dict[str, bytes],
    *,
    canonical_root: str | None = None,
    legacy_root: str | None = None,
    extra_public_directory: str | None = None,
    extra_public_file: tuple[str, bytes] | None = None,
) -> None:
    canonical = canonical_root or f"{identity.data_root}/data/share/world-forge"
    legacy = legacy_root or f"{identity.data_root}/data/share/rpg-world-forge"
    metadata = (
        f"Metadata-Version: 2.4\nName: {identity.name}\nVersion: {identity.version}\n\n"
    ).encode()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("worldforge/__init__.py", b"")
        archive.writestr(f"{identity.dist_info_root}/METADATA", metadata)
        archive.writestr(f"{identity.dist_info_root}/WHEEL", b"Wheel-Version: 1.0\n")
        archive.writestr(f"{identity.dist_info_root}/RECORD", b"")
        archive.writestr(
            f"{identity.dist_info_root}/entry_points.txt",
            b"[console_scripts]\nworldforge = worldforge.cli:main\n",
        )
        archive.writestr(f"{identity.dist_info_root}/licenses/LICENSE", b"fixture license\n")
        archive.writestr(f"{identity.dist_info_root}/top_level.txt", b"worldforge\n")
        for public_root in (canonical, legacy):
            for relative, payload in inventory.items():
                archive.writestr(f"{public_root}/{relative}", payload)
        if extra_public_directory is not None:
            archive.writestr(f"{canonical}/{extra_public_directory}/", b"")
        if extra_public_file is not None:
            relative, payload = extra_public_file
            archive.writestr(f"{canonical}/{relative}", payload)


class M5ReleaseBuilderTests(unittest.TestCase):
    def test_wheel_rejects_non_regular_member_kinds_before_any_read(self) -> None:
        identity = release_builder._release_identity(ROOT)
        inventory = release_builder._release_public_data_inventory(ROOT)
        invalid_kinds = (
            ("regular mode with slash", "worldforge/regular/", stat.S_IFREG | 0o644, 0),
            ("directory mode without slash", "worldforge/directory", stat.S_IFDIR | 0o755, 0),
            ("DOS directory without slash", "worldforge/dos-directory", 0o644, 0x10),
            ("symlink", "worldforge/symlink", stat.S_IFLNK | 0o777, 0),
            ("slash symlink", "worldforge/symlink/", stat.S_IFLNK | 0o777, 0x10),
            ("FIFO", "worldforge/fifo", stat.S_IFIFO | 0o644, 0),
            ("character device", "worldforge/character", stat.S_IFCHR | 0o644, 0),
            ("block device", "worldforge/block", stat.S_IFBLK | 0o644, 0),
            ("socket", "worldforge/socket", stat.S_IFSOCK | 0o644, 0),
            ("unknown type", "worldforge/unknown", 0o030644, 0),
        )

        def write_case(path: Path, name: str, unix_mode: int, dos_attributes: int) -> None:
            _write_public_wheel(path, identity, inventory)
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (unix_mode << 16) | dos_attributes
            with zipfile.ZipFile(path, mode="a") as archive:
                archive.writestr(info, b"untrusted\n")

        with tempfile.TemporaryDirectory(prefix="rwf-wheel-kinds-") as temporary:
            root = Path(temporary)
            for validator in ("canonicalize", "record", "source-free-public-data"):
                for index, (label, name, unix_mode, dos_attributes) in enumerate(invalid_kinds):
                    with self.subTest(validator=validator, label=label):
                        case_root = root / validator / str(index)
                        case_root.mkdir(parents=True)
                        source = case_root / identity.wheel_filename
                        write_case(source, name, unix_mode, dos_attributes)
                        with (
                            patch.object(
                                zipfile.ZipFile,
                                "read",
                                side_effect=AssertionError(
                                    "wheel payload read before kind closure"
                                ),
                            ),
                            self.assertRaisesRegex(
                                release_builder.ReleaseBuildError,
                                "wheel member kind",
                            ),
                        ):
                            if validator == "canonicalize":
                                destination = case_root / "canonical" / identity.wheel_filename
                                destination.parent.mkdir()
                                release_builder._canonicalize_wheel(
                                    source,
                                    destination,
                                    identity,
                                )
                            elif validator == "record":
                                release_builder._verify_wheel_record(source, identity)
                            else:
                                with patch.object(
                                    release_builder,
                                    "ROOT",
                                    case_root / "missing-source",
                                ):
                                    release_builder._verify_wheel_public_data(source, identity)

    def test_wheel_accepts_omitted_unix_types_and_consistent_dos_directories(self) -> None:
        identity = release_builder._release_identity(ROOT)
        inventory = release_builder._release_public_data_inventory(ROOT)
        valid_kinds = (
            ("worldforge/implicit-file", 0o644, 0),
            ("worldforge/implicit-directory/", 0o755, 0),
            ("worldforge/dos-directory/", 0, 0x10),
            ("worldforge/explicit-file", stat.S_IFREG | 0o644, 0),
            ("worldforge/explicit-directory/", stat.S_IFDIR | 0o755, 0),
        )
        with tempfile.TemporaryDirectory(prefix="rwf-wheel-kind-controls-") as temporary:
            root = Path(temporary)
            source = root / "source" / identity.wheel_filename
            canonical = root / "canonical" / identity.wheel_filename
            source.parent.mkdir()
            canonical.parent.mkdir()
            _write_public_wheel(source, identity, inventory)
            with zipfile.ZipFile(source, mode="a") as archive:
                for name, unix_mode, dos_attributes in valid_kinds:
                    info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                    info.create_system = 3
                    info.external_attr = (unix_mode << 16) | dos_attributes
                    archive.writestr(info, b"" if name.endswith("/") else b"trusted\n")

            release_builder._canonicalize_wheel(source, canonical, identity)
            release_builder._verify_wheel_record(canonical, identity)
            with patch.object(release_builder, "ROOT", root / "missing-source"):
                release_builder._verify_wheel_public_data(canonical, identity)

    def test_wheel_applies_the_full_portable_path_policy_in_every_validator(
        self,
    ) -> None:
        identity = release_builder._release_identity(ROOT)
        inventory = release_builder._release_public_data_inventory(ROOT)
        invalid_cases: tuple[tuple[str, tuple[tuple[str, bool], ...]], ...] = (
            ("absolute", (("/worldforge/absolute.py", False),)),
            ("backslash", ((r"worldforge\backslash.py", False),)),
            ("drive", (("C:/worldforge/drive.py", False),)),
            ("UNC", (("//server/share/module.py", False),)),
            ("empty segment", (("worldforge//empty.py", False),)),
            ("dot segment", (("worldforge/./dot.py", False),)),
            ("parent segment", (("worldforge/../parent.py", False),)),
            ("control character", (("worldforge/control\x01.py", False),)),
            ("forbidden punctuation", (("worldforge/question?.py", False),)),
            ("reserved CON", (("worldforge/CON", False),)),
            ("reserved PRN extension", (("worldforge/PRN.txt", False),)),
            ("reserved AUX extension", (("worldforge/AUX.config", False),)),
            ("reserved NUL extension", (("worldforge/NUL.py", False),)),
            ("reserved COM1 extension", (("worldforge/COM1.dll", False),)),
            ("reserved COM9 extension", (("worldforge/COM9.dll", False),)),
            ("reserved LPT1 extension", (("worldforge/LPT1.log", False),)),
            ("reserved LPT9 extension", (("worldforge/LPT9.log", False),)),
            ("reserved COM superscript one", (("worldforge/CoM¹.dll", False),)),
            ("reserved COM superscript two", (("worldforge/com².dll", False),)),
            ("reserved COM superscript three", (("worldforge/COM³.dll", False),)),
            ("reserved LPT superscript one", (("worldforge/LpT¹.log", False),)),
            ("reserved LPT superscript two", (("worldforge/lpt².log", False),)),
            ("reserved LPT superscript three", (("worldforge/LPT³.log", False),)),
            ("reserved CONIN dollar", (("worldforge/CoNiN$.json", False),)),
            ("reserved CONOUT dollar", (("worldforge/conout$.json", False),)),
            ("256-byte component", ((f"worldforge/{'a' * 256}", False),)),
            (
                "total UTF-8 path bound",
                ((("a" * 200 + "/") * 6 + "tail.py", False),),
            ),
            (
                "non-NFC component",
                (("worldforge/cafe\N{COMBINING ACUTE ACCENT}.py", False),),
            ),
            (
                "ancestor casefold collision",
                (("WORLDFORGE/other.py", False),),
            ),
            (
                "ancestor Unicode casefold collision",
                (
                    ("worldforge/Caf\N{LATIN SMALL LETTER E WITH ACUTE}/one.py", False),
                    ("worldforge/CAF\N{LATIN CAPITAL LETTER E WITH ACUTE}/two.py", False),
                ),
            ),
            (
                "file ancestor",
                (
                    ("worldforge/prefix", False),
                    ("worldforge/prefix/child.py", False),
                ),
            ),
            (
                "child before file ancestor",
                (
                    ("worldforge/reverse/child.py", False),
                    ("worldforge/reverse", False),
                ),
            ),
            (
                "explicit file-directory clash",
                (
                    ("worldforge/explicit", False),
                    ("worldforge/explicit/", True),
                ),
            ),
        )

        def write_case(
            path: Path,
            entries: tuple[tuple[str, bool], ...],
        ) -> None:
            _write_public_wheel(path, identity, inventory)
            with zipfile.ZipFile(path, mode="a") as archive:
                for name, directory in entries:
                    archive.writestr(name, b"" if directory else b"untrusted\n")

        with tempfile.TemporaryDirectory(prefix="rwf-wheel-portable-") as temporary:
            root = Path(temporary)
            for validator in ("canonicalize", "record", "source-free-public-data"):
                for index, (label, entries) in enumerate(invalid_cases):
                    with self.subTest(validator=validator, label=label):
                        case_root = root / validator / str(index)
                        case_root.mkdir(parents=True)
                        source = case_root / identity.wheel_filename
                        write_case(source, entries)
                        with self.assertRaises(release_builder.ReleaseBuildError):
                            if validator == "canonicalize":
                                destination = case_root / "canonical" / identity.wheel_filename
                                destination.parent.mkdir()
                                release_builder._canonicalize_wheel(
                                    source,
                                    destination,
                                    identity,
                                )
                            elif validator == "record":
                                release_builder._verify_wheel_record(source, identity)
                            else:
                                with patch.object(
                                    release_builder,
                                    "ROOT",
                                    case_root / "missing-source",
                                ):
                                    release_builder._verify_wheel_public_data(source, identity)

    def test_wheel_utf8_member_limits_are_exact_for_multibyte_names(self) -> None:
        identity = release_builder._release_identity(ROOT)
        inventory = release_builder._release_public_data_inventory(ROOT)
        component_255 = "é" * 127 + "a"
        component_256 = "é" * 128
        total_1024 = "worldforge/" + "/".join(
            ("\u00e9" * 126 + "a", "\u00e9" * 126 + "b", "\u00e9" * 126 + "c", "\u00e9" * 125 + "d")
        )
        total_1025 = "worldforge/" + "/".join(
            ("\u00e9" * 126 + "a", "\u00e9" * 126 + "b", "\u00e9" * 126 + "c", "\u00e9" * 126)
        )
        self.assertEqual(255, len(component_255.encode("utf-8")))
        self.assertEqual(256, len(component_256.encode("utf-8")))
        self.assertEqual(1024, len(total_1024.encode("utf-8")))
        self.assertEqual(1025, len(total_1025.encode("utf-8")))

        with tempfile.TemporaryDirectory(prefix="rwf-wheel-byte-limits-") as temporary:
            root = Path(temporary)
            for index, member in enumerate((f"worldforge/{component_255}", total_1024)):
                with self.subTest(valid_bytes=len(member.encode("utf-8"))):
                    source = root / "valid" / str(index) / identity.wheel_filename
                    canonical = root / "valid" / str(index) / "canonical" / identity.wheel_filename
                    source.parent.mkdir(parents=True)
                    canonical.parent.mkdir()
                    _write_public_wheel(source, identity, inventory)
                    with zipfile.ZipFile(source, mode="a") as archive:
                        archive.writestr(member, b"boundary\n")
                    release_builder._canonicalize_wheel(source, canonical, identity)
                    release_builder._verify_wheel_record(canonical, identity)
                    with patch.object(release_builder, "ROOT", root / "missing-source"):
                        release_builder._verify_wheel_public_data(canonical, identity)

            for validator in ("canonicalize", "record", "source-free-public-data"):
                for index, member in enumerate((f"worldforge/{component_256}", total_1025)):
                    with self.subTest(
                        validator=validator,
                        invalid_bytes=len(member.encode("utf-8")),
                    ):
                        case_root = root / "invalid" / validator / str(index)
                        case_root.mkdir(parents=True)
                        source = case_root / identity.wheel_filename
                        _write_public_wheel(source, identity, inventory)
                        with zipfile.ZipFile(source, mode="a") as archive:
                            archive.writestr(member, b"boundary\n")
                        with self.assertRaisesRegex(
                            release_builder.ReleaseBuildError,
                            "unsafe release archive member",
                        ):
                            if validator == "canonicalize":
                                destination = case_root / "canonical" / identity.wheel_filename
                                destination.parent.mkdir()
                                release_builder._canonicalize_wheel(
                                    source,
                                    destination,
                                    identity,
                                )
                            elif validator == "record":
                                release_builder._verify_wheel_record(source, identity)
                            else:
                                with patch.object(
                                    release_builder,
                                    "ROOT",
                                    case_root / "missing-source",
                                ):
                                    release_builder._verify_wheel_public_data(source, identity)

    def test_wheel_rejects_reserved_metadata_anchors_at_every_depth(self) -> None:
        identity = release_builder._release_identity(ROOT)
        inventory = release_builder._release_public_data_inventory(ROOT)
        invalid_members = (
            "worldforge/nested/METADATA",
            "worldforge/nested/PKG-INFO",
            "worldforge/nested/WHEEL",
            "worldforge/nested/RECORD",
            "worldforge/nested/entry_points.txt",
            "worldforge/nested/top_level.txt",
            "worldforge/nested/EGG-INFO/payload.txt",
            f"{identity.dist_info_root}/licenses/METADATA",
            f"{identity.data_root}/data/share/world-forge/contracts/PKG-INFO",
        )

        def write_case(path: Path, member: str) -> None:
            _write_public_wheel(path, identity, inventory)
            with zipfile.ZipFile(path, mode="a") as archive:
                archive.writestr(member, b"untrusted distribution metadata\n")

        with tempfile.TemporaryDirectory(prefix="rwf-wheel-anchors-") as temporary:
            root = Path(temporary)
            for validator in ("canonicalize", "record", "source-free-public-data"):
                for index, member in enumerate(invalid_members):
                    with self.subTest(validator=validator, member=member):
                        case_root = root / validator / str(index)
                        case_root.mkdir(parents=True)
                        source = case_root / identity.wheel_filename
                        write_case(source, member)
                        with self.assertRaisesRegex(
                            release_builder.ReleaseBuildError,
                            "reserved wheel metadata anchor",
                        ):
                            if validator == "canonicalize":
                                destination = case_root / "canonical" / identity.wheel_filename
                                destination.parent.mkdir()
                                release_builder._canonicalize_wheel(
                                    source,
                                    destination,
                                    identity,
                                )
                            elif validator == "record":
                                release_builder._verify_wheel_record(source, identity)
                            else:
                                with patch.object(
                                    release_builder,
                                    "ROOT",
                                    case_root / "missing-source",
                                ):
                                    release_builder._verify_wheel_public_data(source, identity)

    def test_wheel_valid_portable_controls_are_deterministic(self) -> None:
        identity = release_builder._release_identity(ROOT)
        inventory = release_builder._release_public_data_inventory(ROOT)
        valid_members = {
            "worldforge/visuals/": b"",
            "worldforge/visuals/caf\N{LATIN SMALL LETTER E WITH ACUTE}.py": b"NFC = True\n",
            "worldforge/visuals/COM⁴.dll": b"nearby superscript device name\n",
            "worldforge/visuals/LPT⁴.log": b"nearby superscript device name\n",
            "worldforge/visuals/CONIN$X.json": b"nearby console device name\n",
            "worldforge/visuals/XCONOUT$.json": b"nearby console device name\n",
            "worldforge/visuals/METADATA%2E": b"literal percent text\n",
            "worldforge/visuals/entry_points%2Etxt": b"literal percent text\n",
            "worldforge/visuals/import_safe.py": b"VALUE = True\n",
        }
        with tempfile.TemporaryDirectory(prefix="rwf-wheel-controls-") as temporary:
            root = Path(temporary)
            source = root / "source" / identity.wheel_filename
            left = root / "left" / identity.wheel_filename
            right = root / "right" / identity.wheel_filename
            source.parent.mkdir()
            left.parent.mkdir()
            right.parent.mkdir()
            _write_public_wheel(source, identity, inventory)
            with zipfile.ZipFile(source, mode="a") as archive:
                for name, payload in valid_members.items():
                    archive.writestr(name, payload)

            with patch.object(release_builder, "ROOT", root / "missing-source"):
                release_builder._verify_wheel_public_data(source, identity)
            release_builder._canonicalize_wheel(source, left, identity)
            release_builder._canonicalize_wheel(source, right, identity)
            release_builder._verify_wheel_record(left, identity)
            with patch.object(release_builder, "ROOT", root / "missing-source"):
                release_builder._verify_wheel_public_data(left, identity)
            self.assertEqual(left.read_bytes(), right.read_bytes())
            self.assertEqual(
                hashlib.sha256(left.read_bytes()).hexdigest(),
                hashlib.sha256(right.read_bytes()).hexdigest(),
            )

    def test_wheel_requires_one_consistent_dist_info_identity(self) -> None:
        def write_wheel(path: Path, members: dict[str, bytes]) -> None:
            with zipfile.ZipFile(path, "w") as archive:
                for name, payload in members.items():
                    archive.writestr(name, payload)

        identity = release_builder._release_identity(ROOT)
        inventory = release_builder._release_public_data_inventory(ROOT)
        metadata = (
            f"Metadata-Version: 2.4\nName: {identity.name}\nVersion: {identity.version}\n\n"
        ).encode()
        with tempfile.TemporaryDirectory(prefix="rwf-wheel-identity-") as temporary:
            root = Path(temporary)
            source = root / "source" / identity.wheel_filename
            destination = root / "canonical" / identity.wheel_filename
            source.parent.mkdir()
            destination.parent.mkdir()
            base_members = {
                "worldforge/__init__.py": b"",
                f"{identity.dist_info_root}/METADATA": metadata,
                f"{identity.dist_info_root}/RECORD": b"",
                f"{identity.dist_info_root}/WHEEL": b"Wheel-Version: 1.0\n",
                f"{identity.dist_info_root}/entry_points.txt": (
                    b"[console_scripts]\nworldforge = worldforge.cli:main\n"
                ),
                f"{identity.dist_info_root}/licenses/LICENSE": b"fixture license\n",
                f"{identity.dist_info_root}/top_level.txt": b"worldforge\n",
            }
            for public_root in (
                f"{identity.data_root}/data/share/world-forge",
                f"{identity.data_root}/data/share/rpg-world-forge",
            ):
                for relative, payload in inventory.items():
                    base_members[f"{public_root}/{relative}"] = payload

            for metadata_anchor in (
                "PKG-INFO",
                "METADATA",
                "WHEEL",
                "RECORD",
                "entry_points.txt",
                "top_level.txt",
            ):
                with self.subTest("bare wheel-root metadata", name=metadata_anchor):
                    write_wheel(
                        source,
                        {
                            **base_members,
                            metadata_anchor: b"untrusted distribution metadata\n",
                        },
                    )
                    with self.assertRaisesRegex(
                        release_builder.ReleaseBuildError,
                        "reserved wheel-root metadata",
                    ):
                        release_builder._canonicalize_wheel(source, destination, identity)

            for nested_namespace in (
                "worldforge/foreign-1.0.dist-info/METADATA",
                "worldforge/foreign-1.0.data/payload.bin",
                "worldforge/foreign-1.0.egg-info/PKG-INFO",
                f"worldforge/{identity.dist_info_root}/METADATA",
                (
                    f"{identity.data_root}/data/share/world-forge/"
                    "nested/foreign-1.0.data/payload.bin"
                ),
            ):
                with self.subTest("nested reserved namespace", name=nested_namespace):
                    write_wheel(
                        source,
                        {
                            **base_members,
                            nested_namespace: b"untrusted distribution metadata\n",
                        },
                    )
                    with self.assertRaisesRegex(
                        release_builder.ReleaseBuildError,
                        "reserved wheel metadata namespace",
                    ):
                        release_builder._canonicalize_wheel(source, destination, identity)

            with self.subTest("reserved substrings without reserved suffixes remain valid"):
                write_wheel(
                    source,
                    {
                        **base_members,
                        "worldforge/cache.dist-info.json": b"{}\n",
                        "worldforge/model.data.json": b"{}\n",
                        "worldforge/egg-info-helper.py": b"VALUE = True\n",
                    },
                )
                release_builder._canonicalize_wheel(source, destination, identity)

            with self.subTest("extra dist-info without RECORD"):
                write_wheel(
                    source,
                    {
                        **base_members,
                        "unrelated-1.0.dist-info/METADATA": (
                            b"Metadata-Version: 2.4\nName: unrelated\nVersion: 1.0\n\n"
                        ),
                    },
                )
                with self.assertRaisesRegex(
                    release_builder.ReleaseBuildError,
                    "exactly one .dist-info",
                ):
                    release_builder._canonicalize_wheel(source, destination, identity)

            with self.subTest("extra empty dist-info root"):
                write_wheel(
                    source,
                    {
                        **base_members,
                        "unrelated-1.0.dist-info/": b"",
                    },
                )
                with self.assertRaisesRegex(
                    release_builder.ReleaseBuildError,
                    "exactly one .dist-info",
                ):
                    release_builder._canonicalize_wheel(source, destination, identity)

            with self.subTest("metadata name mismatch"):
                write_wheel(
                    source,
                    {
                        **base_members,
                        f"{identity.dist_info_root}/METADATA": (
                            b"Metadata-Version: 2.4\nName: another-project\nVersion: 0.7.0\n\n"
                        ),
                    },
                )
                with self.assertRaisesRegex(
                    release_builder.ReleaseBuildError,
                    "METADATA name",
                ):
                    release_builder._canonicalize_wheel(source, destination, identity)

            with self.subTest("metadata version mismatch"):
                write_wheel(
                    source,
                    {
                        **base_members,
                        f"{identity.dist_info_root}/METADATA": (
                            b"Metadata-Version: 2.4\nName: world-forge\nVersion: 0.7.1\n\n"
                        ),
                    },
                )
                with self.assertRaisesRegex(
                    release_builder.ReleaseBuildError,
                    "METADATA version",
                ):
                    release_builder._canonicalize_wheel(source, destination, identity)

            with self.subTest("missing WHEEL metadata"):
                members = dict(base_members)
                del members[f"{identity.dist_info_root}/WHEEL"]
                write_wheel(source, members)
                with self.assertRaisesRegex(
                    release_builder.ReleaseBuildError,
                    "missing WHEEL",
                ):
                    release_builder._canonicalize_wheel(source, destination, identity)

            with self.subTest("missing audited dist-info metadata"):
                members = dict(base_members)
                del members[f"{identity.dist_info_root}/entry_points.txt"]
                write_wheel(source, members)
                with self.assertRaisesRegex(
                    release_builder.ReleaseBuildError,
                    "dist-info metadata inventory.*entry_points.txt",
                ):
                    release_builder._canonicalize_wheel(source, destination, identity)

            with self.subTest("extra nested dist-info metadata"):
                write_wheel(
                    source,
                    {
                        **base_members,
                        f"{identity.dist_info_root}/unreviewed/nested.json": b"{}\n",
                    },
                )
                with self.assertRaisesRegex(
                    release_builder.ReleaseBuildError,
                    "unexpected .dist-info metadata entry",
                ):
                    release_builder._canonicalize_wheel(source, destination, identity)

            with self.subTest("foreign data root"):
                write_wheel(
                    source,
                    {
                        **base_members,
                        "another_project-9.9.9.data/data/share/world-forge/foreign.json": (b"{}\n"),
                    },
                )
                with self.assertRaisesRegex(
                    release_builder.ReleaseBuildError,
                    "foreign .data root",
                ):
                    release_builder._canonicalize_wheel(source, destination, identity)

            with self.subTest("extra top-level distribution metadata"):
                write_wheel(
                    source,
                    {
                        **base_members,
                        "another_project-1.0.egg-info/PKG-INFO": (
                            b"Metadata-Version: 2.4\nName: another-project\nVersion: 1.0\n\n"
                        ),
                    },
                )
                with self.assertRaisesRegex(
                    release_builder.ReleaseBuildError,
                    "foreign top-level metadata namespace",
                ):
                    release_builder._canonicalize_wheel(source, destination, identity)

            with self.subTest("internally consistent foreign identity"):
                foreign_root = "another_project-1.0.dist-info"
                write_wheel(
                    source,
                    {
                        "worldforge/__init__.py": b"",
                        f"{foreign_root}/METADATA": (
                            b"Metadata-Version: 2.4\nName: another-project\nVersion: 1.0\n\n"
                        ),
                        f"{foreign_root}/RECORD": b"",
                        f"{foreign_root}/WHEEL": b"Wheel-Version: 1.0\n",
                    },
                )
                with self.assertRaisesRegex(
                    release_builder.ReleaseBuildError,
                    "expected release identity",
                ):
                    release_builder._canonicalize_wheel(source, destination, identity)

            with self.subTest("unexpected wheel filename"):
                wrong_name = root / "world_forge-9.9.9-py3-none-any.whl"
                write_wheel(wrong_name, base_members)
                with self.assertRaisesRegex(
                    release_builder.ReleaseBuildError,
                    "wheel filename",
                ):
                    release_builder._verify_wheel_record(wrong_name, identity)

    def test_wheel_rejects_win32_trailing_dot_and_space_aliases_on_every_path(
        self,
    ) -> None:
        identity = release_builder._release_identity(ROOT)
        inventory = release_builder._release_public_data_inventory(ROOT)
        invalid_members = (
            ("root PKG-INFO dot file", "PKG-INFO.", False),
            ("root METADATA dot file", "METADATA.", False),
            ("root METADATA space file", "METADATA ", False),
            ("root WHEEL dot file", "WHEEL.", False),
            ("root RECORD space file", "RECORD ", False),
            ("root entry points dot file", "entry_points.txt.", False),
            ("root top level space file", "top_level.txt ", False),
            ("root metadata dot directory", "METADATA./", True),
            (
                "nested mixed-case dist-info dot ancestor",
                "worldforge/Foreign-1.0.DIST-INFO./METADATA",
                False,
            ),
            (
                "nested dist-info space ancestor",
                "worldforge/foreign-1.0.dist-info /METADATA",
                False,
            ),
            (
                "nested data dot ancestor",
                "worldforge/foreign-1.0.data./payload.bin",
                False,
            ),
            (
                "nested egg-info space ancestor",
                "worldforge/foreign-1.0.egg-info /PKG-INFO",
                False,
            ),
            ("generic trailing-dot file", "worldforge/assets/sprite.png.", False),
            ("generic trailing-space ancestor", "worldforge/assets /sprite.png", False),
            ("generic trailing-dot directory", "worldforge/assets./", True),
            (
                "nested dist-info dot directory",
                "worldforge/foreign-1.0.dist-info./",
                True,
            ),
        )
        valid_controls = {
            "worldforge/module.v1.py": b"VALUE = True\n",
            "worldforge/name .txt": b"embedded space is portable\n",
            "worldforge/METADATA%2E": b"percent text is not decoded\n",
            "worldforge/METADATA%20": b"percent text is not decoded\n",
            "worldforge/caf\N{LATIN SMALL LETTER E WITH ACUTE}.py": b"NFC = True\n",
            "worldforge/fullwidth\N{FULLWIDTH FULL STOP}": b"unicode punctuation\n",
            "worldforge/cache.DIST-INFO.json": b"{}\n",
            "worldforge/foreign-1.0.dist-info%2E/payload.bin": b"literal percent text\n",
        }

        def write_extra_member(
            path: Path,
            member_name: str,
            *,
            directory: bool,
        ) -> None:
            _write_public_wheel(path, identity, inventory)
            with zipfile.ZipFile(path, mode="a") as archive:
                name = (
                    member_name if not directory or member_name.endswith("/") else member_name + "/"
                )
                archive.writestr(name, b"" if directory else b"untrusted metadata\n")

        with tempfile.TemporaryDirectory(prefix="rwf-wheel-win32-alias-") as temporary:
            root = Path(temporary)
            for validator in ("canonicalize", "record", "source-free-public-data"):
                for index, (label, member_name, directory) in enumerate(invalid_members):
                    with self.subTest(validator=validator, label=label, name=member_name):
                        case_root = root / validator / str(index)
                        case_root.mkdir(parents=True)
                        source = case_root / identity.wheel_filename
                        write_extra_member(source, member_name, directory=directory)
                        with self.assertRaisesRegex(
                            release_builder.ReleaseBuildError,
                            "Win32 trailing-dot/space alias",
                        ):
                            if validator == "canonicalize":
                                destination = case_root / "canonical" / identity.wheel_filename
                                destination.parent.mkdir()
                                release_builder._canonicalize_wheel(
                                    source,
                                    destination,
                                    identity,
                                )
                            elif validator == "record":
                                release_builder._verify_wheel_record(source, identity)
                            else:
                                with patch.object(
                                    release_builder,
                                    "ROOT",
                                    case_root / "missing-source",
                                ):
                                    release_builder._verify_wheel_public_data(source, identity)

            controls_source = root / "controls" / "source" / identity.wheel_filename
            controls_destination = root / "controls" / "canonical" / identity.wheel_filename
            controls_source.parent.mkdir(parents=True)
            controls_destination.parent.mkdir(parents=True)
            _write_public_wheel(controls_source, identity, inventory)
            with zipfile.ZipFile(controls_source, mode="a") as archive:
                for member_name, payload in valid_controls.items():
                    archive.writestr(member_name, payload)
            release_builder._canonicalize_wheel(
                controls_source,
                controls_destination,
                identity,
            )
            release_builder._verify_wheel_record(controls_destination, identity)
            with patch.object(release_builder, "ROOT", root / "missing-source"):
                release_builder._verify_wheel_public_data(controls_destination, identity)

            for label, member_name in (
                ("mixed separator", "worldforge\\METADATA."),
                ("non-NFC Unicode", "worldforge/cafe\N{COMBINING ACUTE ACCENT}.py"),
            ):
                with self.subTest(control=label, name=member_name):
                    source = root / "controls" / label / identity.wheel_filename
                    destination = root / "controls" / label / "canonical" / identity.wheel_filename
                    source.parent.mkdir(parents=True)
                    destination.parent.mkdir(parents=True)
                    write_extra_member(source, member_name, directory=False)
                    with self.assertRaisesRegex(
                        release_builder.ReleaseBuildError,
                        "unsafe release archive member",
                    ):
                        release_builder._canonicalize_wheel(source, destination, identity)

    def test_release_identity_is_exactly_pinned_and_cannot_be_forged(self) -> None:
        identity = release_builder._release_identity(ROOT)
        self.assertEqual((identity.name, identity.version), ("world-forge", "0.7.0"))
        with tempfile.TemporaryDirectory(prefix="rwf-release-identity-") as temporary:
            root = Path(temporary)
            with self.subTest("pyproject version 9.9.9"):
                pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
                marker = 'version = "0.7.0"'
                self.assertEqual(pyproject.count(marker), 1)
                (root / "pyproject.toml").write_text(
                    pyproject.replace(marker, 'version = "9.9.9"'),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    release_builder.ReleaseBuildError,
                    "trusted project version must be '0.7.0'",
                ):
                    release_builder._release_identity(root)

            forged = release_builder.ReleaseIdentity(
                name="world-forge",
                version="9.9.9",
                archive_name="world_forge",
                archive_version="9.9.9",
                dist_info_root="world_forge-9.9.9.dist-info",
                data_root="world_forge-9.9.9.data",
                sdist_root="world_forge-9.9.9",
                sdist_filename="world_forge-9.9.9.tar.gz",
                wheel_filename="world_forge-9.9.9-py3-none-any.whl",
            )
            source = root / forged.wheel_filename
            destination = root / "canonical" / forged.wheel_filename
            destination.parent.mkdir()
            _write_public_wheel(
                source,
                forged,
                release_builder._release_public_data_inventory(ROOT),
            )
            with self.subTest("caller-supplied internally consistent 9.9.9 identity"):
                with self.assertRaisesRegex(
                    release_builder.ReleaseBuildError,
                    "trusted canonical project identity",
                ):
                    release_builder._canonicalize_wheel(source, destination, forged)

    def test_release_archives_require_exact_embedded_public_bridge_inventory(
        self,
    ) -> None:
        identity = release_builder._release_identity(ROOT)
        inventory = release_builder._release_public_data_inventory(ROOT)
        missing_inventory = dict(inventory)
        missing_inventory.pop(sorted(missing_inventory)[-1])
        with tempfile.TemporaryDirectory(prefix="rwf-release-public-") as temporary:
            root = Path(temporary)
            sdist = root / identity.sdist_filename
            wheel = root / identity.wheel_filename
            _write_public_sdist(sdist, identity, inventory)
            _write_public_wheel(wheel, identity, inventory)

            with patch.object(release_builder, "ROOT", root / "missing-source"):
                release_builder._verify_sdist_public_data(sdist, identity)
                release_builder._verify_wheel_public_data(wheel, identity)

            with self.subTest("sdist misplaced canonical root"):
                _write_public_sdist(
                    sdist,
                    identity,
                    inventory,
                    canonical_root="nested/share/world-forge",
                )
                with self.assertRaisesRegex(
                    release_builder.ReleaseBuildError,
                    "exact public data location|misplaced public data",
                ):
                    release_builder._verify_sdist_public_data(sdist, identity)

            with self.subTest("wheel misplaced canonical root"):
                _write_public_wheel(
                    wheel,
                    identity,
                    inventory,
                    canonical_root="nested/share/world-forge",
                )
                with self.assertRaisesRegex(
                    release_builder.ReleaseBuildError,
                    "exact public data location|misplaced public data",
                ):
                    release_builder._verify_wheel_public_data(wheel, identity)

            for label, writer, verifier, target in (
                (
                    "sdist incomplete",
                    _write_public_sdist,
                    release_builder._verify_sdist_public_data,
                    sdist,
                ),
                (
                    "wheel incomplete",
                    _write_public_wheel,
                    release_builder._verify_wheel_public_data,
                    wheel,
                ),
            ):
                with self.subTest(label):
                    writer(target, identity, missing_inventory)
                    with self.assertRaisesRegex(
                        release_builder.ReleaseBuildError,
                        "incomplete|missing public data",
                    ):
                        verifier(target, identity)

            for label, writer, verifier, target in (
                (
                    "sdist extra file",
                    _write_public_sdist,
                    release_builder._verify_sdist_public_data,
                    sdist,
                ),
                (
                    "wheel extra file",
                    _write_public_wheel,
                    release_builder._verify_wheel_public_data,
                    wheel,
                ),
            ):
                with self.subTest(label):
                    writer(
                        target,
                        identity,
                        inventory,
                        extra_public_file=("contracts/unreviewed.txt", b"unreviewed"),
                    )
                    with self.assertRaisesRegex(
                        release_builder.ReleaseBuildError,
                        "unexpected public data",
                    ):
                        verifier(target, identity)

            for label, writer, verifier, target in (
                (
                    "sdist extra directory",
                    _write_public_sdist,
                    release_builder._verify_sdist_public_data,
                    sdist,
                ),
                (
                    "wheel extra directory",
                    _write_public_wheel,
                    release_builder._verify_wheel_public_data,
                    wheel,
                ),
            ):
                with self.subTest(label):
                    writer(
                        target,
                        identity,
                        inventory,
                        extra_public_directory="schemas/unreviewed-empty",
                    )
                    with self.assertRaisesRegex(
                        release_builder.ReleaseBuildError,
                        "unexpected public data directory",
                    ):
                        verifier(target, identity)

    def test_sdist_identity_is_anchored_to_trusted_project_configuration(self) -> None:
        identity = release_builder._release_identity(ROOT)
        inventory = release_builder._release_public_data_inventory(ROOT)
        with tempfile.TemporaryDirectory(prefix="rwf-sdist-identity-") as temporary:
            root = Path(temporary)
            expected = root / identity.sdist_filename
            canonical = root / "canonical" / identity.sdist_filename
            canonical.parent.mkdir()
            _write_public_sdist(expected, identity, inventory)
            release_builder._canonicalize_sdist(expected, canonical, 0, identity)

            with self.subTest("unexpected filename"):
                wrong_filename = root / "another_project-1.0.tar.gz"
                _write_public_sdist(wrong_filename, identity, inventory)
                with self.assertRaisesRegex(
                    release_builder.ReleaseBuildError,
                    "sdist filename",
                ):
                    release_builder._verify_sdist_public_data(wrong_filename, identity)

            with self.subTest("internally consistent foreign root and metadata"):
                _write_public_sdist(
                    expected,
                    identity,
                    inventory,
                    archive_root="another_project-1.0",
                    package_name="another-project",
                    package_version="1.0",
                )
                with self.assertRaisesRegex(
                    release_builder.ReleaseBuildError,
                    "expected release identity|sdist root",
                ):
                    release_builder._canonicalize_sdist(expected, canonical, 0, identity)

            with self.subTest("metadata version mismatch"):
                _write_public_sdist(
                    expected,
                    identity,
                    inventory,
                    package_version="0.7.1",
                )
                with self.assertRaisesRegex(
                    release_builder.ReleaseBuildError,
                    "PKG-INFO version",
                ):
                    release_builder._canonicalize_sdist(expected, canonical, 0, identity)

    def test_release_builder_uses_git_archive_and_publishes_reproducible_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rwf-release-test-") as temporary:
            workspace = Path(temporary)
            repo = workspace / "repo"
            output = workspace / "release"
            _copy_committed_fixture(repo)
            (repo / "UNTRACKED_PRIVATE_SENTINEL.txt").write_text("must not ship", encoding="utf-8")

            result = _run_builder(repo, output)

            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            sdists = sorted(output.glob("*.tar.gz"))
            wheels = sorted(output.glob("*.whl"))
            self.assertEqual(1, len(sdists))
            self.assertEqual(1, len(wheels))
            self.assertEqual(
                {sdists[0].name, wheels[0].name}, {path.name for path in output.iterdir()}
            )
            self.assertIn("sha256=", result.stdout)

            second = _run_builder(repo, output)
            self.assertNotEqual(0, second.returncode)
            self.assertIn("refusing to replace existing artifact", second.stderr)

            with tarfile.open(sdists[0], "r:gz") as archive:
                names = set(archive.getnames())
                infos = archive.getmembers()
                sdist_files = {
                    info.name: archive.extractfile(info).read() for info in infos if info.isfile()
                }
            self.assertTrue(any(name.endswith("README.md") for name in names))
            self.assertTrue(any("/docs/ARCHITECTURE.md" in name for name in names))
            self.assertTrue(any("/tests/test_m5_release_builder.py" in name for name in names))
            self.assertTrue(any("/tests/test_m5_release_readiness.py" in name for name in names))
            self.assertTrue(any("/scripts/verify_m5_release.py" in name for name in names))
            self.assertTrue(any("/.agents/skills/" in name for name in names))
            self.assertTrue(any("/authoring/prompts/00_BOUNDARY.md" in name for name in names))
            self.assertTrue(any("/examples/foundation/" in name for name in names))
            self.assertTrue(any("/schemas/source-manifest.schema.json" in name for name in names))
            self.assertTrue(any("/contracts/README.md" in name for name in names))
            canonical_public = {
                name.split("/share/world-forge/", 1)[1]: payload
                for name, payload in sdist_files.items()
                if "/share/world-forge/" in name
            }
            legacy_public = {
                name.split("/share/rpg-world-forge/", 1)[1]: payload
                for name, payload in sdist_files.items()
                if "/share/rpg-world-forge/" in name
            }
            self.assertTrue(canonical_public)
            self.assertEqual(canonical_public, legacy_public)
            self.assertIn("contracts/catalog.json", canonical_public)
            self.assertIn(
                "schemas/legacy-identity-allowlist.schema.json",
                canonical_public,
            )
            self.assertFalse(any("UNTRACKED_PRIVATE_SENTINEL" in name for name in names))
            self.assertFalse(
                any("__pycache__" in name or ".pytest_cache" in name for name in names)
            )
            self.assertEqual(sorted(info.name for info in infos), [info.name for info in infos])
            self.assertTrue(all(info.uid == 0 and info.gid == 0 for info in infos))
            self.assertTrue(all(not info.pax_headers for info in infos))

            with zipfile.ZipFile(wheels[0]) as archive:
                wheel_names = set(archive.namelist())
                record_names = [name for name in wheel_names if name.endswith(".dist-info/RECORD")]
                record = archive.read(record_names[0]).decode("utf-8")
                infos = archive.infolist()
            self.assertEqual(1, len(record_names))
            self.assertIn(
                "world_forge-0.7.0.data/data/share/world-forge/schemas/",
                "\n".join(wheel_names),
            )
            self.assertIn(
                "world_forge-0.7.0.data/data/share/rpg-world-forge/schemas/",
                "\n".join(wheel_names),
            )
            self.assertIn("source-manifest.schema.json", "\n".join(wheel_names))
            self.assertIn(
                "world_forge-0.7.0.data/data/share/world-forge/contracts/README.md",
                wheel_names,
            )
            self.assertIn(
                "world_forge-0.7.0.data/data/share/rpg-world-forge/contracts/README.md",
                wheel_names,
            )
            self.assertFalse(any(name.startswith("rpg_world_forge-") for name in wheel_names))
            self.assertIn(record_names[0] + ",,", record)
            self.assertEqual(
                sorted(info.filename for info in infos), [info.filename for info in infos]
            )
            self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in infos))
            self.assertTrue(all(info.create_system == 3 for info in infos))
            self.assertTrue(
                all(stat.S_IFMT(info.external_attr >> 16) == stat.S_IFREG for info in infos)
            )
            self.assertTrue(
                all((info.external_attr >> 16) & 0o777 in {0o644, 0o755} for info in infos)
            )

            rebuilt = workspace / "rebuilt-from-sdist"
            rebuilt.mkdir()
            rebuilt_result = subprocess.run(
                [
                    str(PYTHON),
                    "-m",
                    "pip",
                    "wheel",
                    "--no-deps",
                    "--no-build-isolation",
                    "--wheel-dir",
                    str(rebuilt),
                    str(sdists[0]),
                ],
                cwd=workspace,
                env={
                    **os.environ,
                    "PIP_NO_INDEX": "1",
                    "PYTHONNOUSERSITE": "1",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                rebuilt_result.returncode,
                rebuilt_result.stdout + rebuilt_result.stderr,
            )
            rebuilt_wheels = list(rebuilt.glob("*.whl"))
            self.assertEqual(1, len(rebuilt_wheels))
            with zipfile.ZipFile(rebuilt_wheels[0]) as rebuilt_archive:
                rebuilt_names = set(rebuilt_archive.namelist())
            self.assertEqual(
                1,
                len({name.split("/", 1)[0] for name in rebuilt_names if ".dist-info/" in name}),
            )
            self.assertTrue(
                any(
                    ".data/data/share/world-forge/contracts/catalog.json" in name
                    for name in rebuilt_names
                )
            )
            self.assertTrue(
                any(
                    ".data/data/share/rpg-world-forge/contracts/catalog.json" in name
                    for name in rebuilt_names
                )
            )

            with zipfile.ZipFile(wheels[0]) as archive:
                rows = list(csv.reader(io.StringIO(archive.read(record_names[0]).decode("utf-8"))))
                records = {row[0]: row[1:] for row in rows}
                self.assertEqual(set(archive.namelist()), set(records))
                for name in archive.namelist():
                    digest, size = records[name]
                    if name == record_names[0]:
                        self.assertEqual(["", ""], [digest, size])
                        continue
                    payload = archive.read(name)
                    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
                    expected_digest = "sha256=" + encoded.decode("ascii").rstrip("=")
                    self.assertEqual(expected_digest, digest)
                    self.assertEqual(str(len(payload)), size)

            shutil.rmtree(repo / "src")
            self.assertFalse((repo / "src").exists())
            clean_cwd = workspace / "source-free-cwd"
            clean_cwd.mkdir()
            from scripts import verify_m5_release

            verify_m5_release._verify_clean_install(
                wheels[0],
                workspace / "source-free-venv",
                clean_cwd,
                forbidden_source_roots=(repo / "src", ROOT / "src"),
            )

    def test_git_archive_extraction_rejects_links_unsafe_paths_and_collisions(self) -> None:
        regular = tarfile.TarInfo("safe.txt")
        symbolic = tarfile.TarInfo("linked.txt")
        symbolic.type = tarfile.SYMTYPE
        symbolic.linkname = "safe.txt"
        hardlink = tarfile.TarInfo("hardlinked.txt")
        hardlink.type = tarfile.LNKTYPE
        hardlink.linkname = "safe.txt"
        traversal = tarfile.TarInfo("../escape.txt")
        upper = tarfile.TarInfo("Content/File.txt")
        lower = tarfile.TarInfo("content/file.txt")
        aliased_directory = tarfile.TarInfo("Assets")
        aliased_directory.type = tarfile.DIRTYPE
        aliased_child = tarfile.TarInfo("assets/file.txt")
        cases = {
            "symbolic link": [(regular, b"safe"), (symbolic, None)],
            "hard link": [(regular, b"safe"), (hardlink, None)],
            "traversal": [(traversal, b"escape")],
            "portable collision": [(upper, b"upper"), (lower, b"lower")],
            "portable parent collision": [
                (aliased_directory, None),
                (aliased_child, b"child"),
            ],
        }
        with tempfile.TemporaryDirectory(prefix="rwf-archive-test-") as temporary:
            root = Path(temporary)
            for index, (label, entries) in enumerate(cases.items()):
                with self.subTest(label=label):
                    destination = root / f"case-{index}"
                    with self.assertRaises(release_builder.ReleaseBuildError):
                        release_builder._extract_archive(_tar_payload(entries), destination)
                    self.assertFalse(destination.exists())

    def test_build_environment_is_minimal_and_uses_isolated_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rwf-env-test-") as temporary:
            environment_root = Path(temporary) / "environment"
            inherited = {
                "PATH": "untrusted-path",
                "PYTHONHOME": "untrusted-home",
                "PYTHONPATH": "untrusted-pythonpath",
                "SECRET_TOKEN": "must-not-leak",
                "SystemRoot": "C:\\Windows",
                "windir": "C:\\Windows",
            }
            with patch.dict(release_builder.os.environ, inherited, clear=True):
                environment = release_builder._build_environment(1234, environment_root)

            self.assertNotIn("PATH", environment)
            self.assertNotIn("PYTHONHOME", environment)
            self.assertNotIn("PYTHONPATH", environment)
            self.assertNotIn("SECRET_TOKEN", environment)
            self.assertEqual("C:\\Windows", environment["SYSTEMROOT"])
            self.assertEqual("C:\\Windows", environment["WINDIR"])
            self.assertNotIn("SystemRoot", environment)
            self.assertNotIn("windir", environment)
            self.assertEqual("1234", environment["SOURCE_DATE_EPOCH"])
            self.assertEqual(str(environment_root / "home"), environment["HOME"])
            self.assertEqual(str(environment_root / "home"), environment["USERPROFILE"])
            self.assertEqual(str(environment_root / "tmp"), environment["TMP"])
            self.assertTrue((environment_root / "home").is_dir())
            self.assertTrue((environment_root / "tmp").is_dir())

    def test_one_immutable_commit_oid_drives_epoch_and_archive(self) -> None:
        commit_oid = "a" * 40
        with tempfile.TemporaryDirectory(prefix="rwf-oid-test-") as temporary:
            root = Path(temporary)
            repo = root / "repo"
            output = root / "output"
            repo.mkdir()
            with (
                patch.object(release_builder, "_require_supported_platform"),
                patch.object(release_builder, "_verify_toolchain"),
                patch.object(release_builder, "_head_oid", return_value=commit_oid),
                patch.object(release_builder, "_source_date_epoch", return_value=123) as epoch,
                patch.object(release_builder, "_git_archive", return_value=b"archive") as archive,
                patch.object(release_builder, "_extract_archive"),
                patch.object(
                    release_builder,
                    "_build_from_source",
                    side_effect=[(root / "a.tar.gz", root / "a.whl")] * 2,
                ),
                patch.object(release_builder, "_publish_verified", return_value=[]),
            ):
                self.assertEqual([], release_builder.build_release(repo, output))
            epoch.assert_called_once_with(repo.resolve(), commit_oid)
            archive.assert_called_once_with(repo.resolve(), commit_oid)

    def test_release_builder_accepts_only_supported_desktop_platforms(self) -> None:
        release_builder._require_supported_platform("linux")
        release_builder._require_supported_platform("linux2")
        release_builder._require_supported_platform("win32")
        with self.assertRaisesRegex(
            release_builder.ReleaseBuildError, "supported only on Linux and Windows"
        ):
            release_builder._require_supported_platform("darwin")

    def test_publication_refuses_a_preexisting_artifact_without_partial_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rwf-publish-collision-") as temporary:
            root = Path(temporary)
            first = _artifact_pair(root, "package.tar.gz", b"sdist")
            second = _artifact_pair(root, "package.whl", b"wheel")
            output = root / "output"
            output.mkdir()
            existing = output / second[0].name
            existing.write_bytes(b"foreign")

            with self.assertRaisesRegex(
                release_builder.ReleaseBuildError, "refusing to replace existing artifact"
            ):
                release_builder._publish_verified(
                    (first[0], second[0]), (first[1], second[1]), output
                )

            self.assertFalse((output / first[0].name).exists())
            self.assertEqual(b"foreign", existing.read_bytes())
            self.assertEqual([existing.name], [path.name for path in output.iterdir()])

    def test_publication_routes_path_identity_through_the_native_helper(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rwf-publish-native-stat-") as temporary:
            root = Path(temporary)
            first = _artifact_pair(root, "package.tar.gz", b"sdist")
            second = _artifact_pair(root, "package.whl", b"wheel")
            output = root / "output"
            artifact_names = {first[0].name, second[0].name}
            real_lstat = Path.lstat
            real_path_file_stat = release_builder.path_file_stat

            def divergent_lstat(path: Path) -> object:
                info = real_lstat(path)
                if path.parent == output and path.name in artifact_names:
                    return SimpleNamespace(st_dev=info.st_dev + 1, st_ino=info.st_ino + 1)
                return info

            with (
                patch.object(Path, "lstat", autospec=True, side_effect=divergent_lstat),
                patch.object(
                    release_builder,
                    "path_file_stat",
                    wraps=real_path_file_stat,
                ) as native_path_stat,
            ):
                published = release_builder._publish_verified(
                    (first[0], second[0]), (first[1], second[1]), output
                )
                with self.assertRaisesRegex(
                    release_builder.ReleaseBuildError,
                    "refusing to replace existing artifact",
                ):
                    release_builder._publish_verified(
                        (first[0], second[0]), (first[1], second[1]), output
                    )

            self.assertEqual(
                [output / first[0].name, output / second[0].name],
                published,
            )
            self.assertEqual(b"sdist", published[0].read_bytes())
            self.assertEqual(b"wheel", published[1].read_bytes())
            self.assertGreater(native_path_stat.call_count, 0)

    def test_publication_rolls_back_only_its_owned_links_after_partial_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rwf-publish-rollback-") as temporary:
            root = Path(temporary)
            first = _artifact_pair(root, "package.tar.gz", b"sdist")
            second = _artifact_pair(root, "package.whl", b"wheel")
            output = root / "output"
            original_link = release_builder.os.link
            calls: list[tuple[Path, Path]] = []

            def fail_second_link(source: str | Path, destination: str | Path) -> None:
                source_path = Path(source)
                destination_path = Path(destination)
                calls.append((source_path, destination_path))
                self.assertEqual(output, source_path.parent)
                self.assertEqual(output, destination_path.parent)
                if len(calls) == 2:
                    raise OSError("simulated publication failure")
                original_link(source_path, destination_path)

            with patch.object(release_builder.os, "link", side_effect=fail_second_link):
                with self.assertRaisesRegex(
                    release_builder.ReleaseBuildError, "simulated publication failure"
                ):
                    release_builder._publish_verified(
                        (first[0], second[0]), (first[1], second[1]), output
                    )

            self.assertEqual([], list(output.iterdir()))

    def test_publication_preserves_replaced_foreign_file_during_rollback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rwf-publish-identity-") as temporary:
            root = Path(temporary)
            first = _artifact_pair(root, "package.tar.gz", b"sdist")
            second = _artifact_pair(root, "package.whl", b"wheel")
            output = root / "output"
            original_link = release_builder.os.link
            first_target = output / first[0].name
            calls = 0

            def replace_before_failure(source: str | Path, destination: str | Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 1:
                    original_link(source, destination)
                    return
                first_target.unlink()
                first_target.write_bytes(b"foreign replacement")
                raise OSError("simulated publication failure")

            with patch.object(release_builder.os, "link", side_effect=replace_before_failure):
                with self.assertRaises(release_builder.ReleaseBuildError):
                    release_builder._publish_verified(
                        (first[0], second[0]), (first[1], second[1]), output
                    )

            self.assertEqual(b"foreign replacement", first_target.read_bytes())
            self.assertEqual([first_target.name], [path.name for path in output.iterdir()])


if __name__ == "__main__":
    unittest.main()
