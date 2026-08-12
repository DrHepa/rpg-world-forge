from __future__ import annotations

import importlib
import re
import stat
import sysconfig
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from isoworld.runtime_io import RuntimeIOError, decode_json_object
from worldforge.asset_io import AssetContractError, read_json_object
from worldforge.integrity import canonical_json_bytes
from worldforge.retained_tree import (
    RetainedTreeError,
    RetainedTreeHook,
    RetainedTreeSnapshot,
    capture_retained_named_child_trees,
)

CATALOG_FORMAT = "rpg-world-forge.contract_catalog"
CATALOG_VERSION = 1
CATALOG_RELATIVE_PATH = PurePosixPath("contracts/catalog.json")
CANONICAL_SHARE_DIRECTORY = PurePosixPath("share/world-forge")
LEGACY_SHARE_DIRECTORY = PurePosixPath("share/rpg-world-forge")
SHARE_DIRECTORY = CANONICAL_SHARE_DIRECTORY
_PUBLIC_DATA_DIRECTORIES = ("contracts", "schemas")
_ENTRY_KEYS = frozenset(
    {
        "id",
        "title",
        "kind",
        "schema",
        "format",
        "version",
        "python_symbols",
        "cli_commands",
        "fixtures",
        "tests",
        "docs",
        "m5_phases",
    }
)
_TOP_LEVEL_KEYS = frozenset({"format", "format_version", "contracts"})
_PATH_LIST_FIELDS = ("fixtures", "tests", "docs")
_ALLOWED_PHASES = frozenset({"M1", "M2", "M2.5", "M3", "M4", "M5", "M6"})
_PORTABLE_PATH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CATALOG_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_CLI_COMMAND_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_ALLOWED_SYMBOL_ROOTS = frozenset({"worldforge", "isoworld", "gamepack_runtime"})


@dataclass(frozen=True)
class ContractAuditResult:
    catalog_path: Path
    mode: str
    contracts: int


@dataclass(frozen=True)
class _InstalledPublicData:
    catalog_path: Path
    directories: tuple[str, ...]
    inventory: dict[str, bytes]
    root: Path


class ContractCatalogError(ValueError):
    """Raised when the public contract catalog is incomplete or inconsistent."""


def _issue(path: str, message: str) -> str:
    return f"{path}: {message}"


def _portable_relative_path(value: object, *, context: str) -> str:
    if not isinstance(value, str):
        raise ContractCatalogError(_issue(context, "must be a string path"))
    if not value or "\\" in value or _PORTABLE_PATH_RE.fullmatch(value) is None:
        raise ContractCatalogError(_issue(context, "must be a non-empty ASCII POSIX path"))
    if unicodedata.normalize("NFC", value) != value:
        raise ContractCatalogError(_issue(context, "must be NFC normalized"))
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:
        raise ContractCatalogError(_issue(context, "must be a relative portable path"))
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ContractCatalogError(_issue(context, "must not contain empty, . or .. segments"))
    for part in path.parts:
        if unicodedata.normalize("NFC", part) != part or part.casefold() in {".", ".."}:
            raise ContractCatalogError(_issue(context, "contains a non-portable segment"))
    return value


def _catalog_identifier(value: object, *, context: str) -> str:
    if not isinstance(value, str) or _CATALOG_ID_RE.fullmatch(value) is None:
        raise ContractCatalogError(_issue(context, "must be a lowercase ASCII identifier"))
    return value


def _cli_command(value: object, *, context: str) -> str:
    if not isinstance(value, str) or _CLI_COMMAND_RE.fullmatch(value) is None:
        raise ContractCatalogError(_issue(context, "must be a lowercase ASCII CLI command"))
    return value


def _python_symbol(value: object, *, context: str) -> tuple[str, str]:
    if not isinstance(value, str) or value.count(":") != 1:
        raise ContractCatalogError(_issue(context, f"invalid symbol {value!r}"))
    module_name, attribute_path = value.split(":", 1)
    module_parts = module_name.split(".")
    attribute_parts = attribute_path.split(".")
    if (
        not module_parts
        or module_parts[0] not in _ALLOWED_SYMBOL_ROOTS
        or any(_IDENTIFIER_RE.fullmatch(part) is None for part in module_parts)
        or not attribute_parts
        or any(_IDENTIFIER_RE.fullmatch(part) is None for part in attribute_parts)
    ):
        raise ContractCatalogError(_issue(context, f"invalid or disallowed symbol {value}"))
    return module_name, attribute_path


def _safe_regular_file(root: Path, relative: str, *, context: str) -> Path:
    safe_relative = PurePosixPath(_portable_relative_path(relative, context=context))
    current = root
    for part in safe_relative.parts[:-1]:
        current /= part
        try:
            info = current.lstat()
        except OSError as exc:
            raise ContractCatalogError(_issue(context, f"missing parent {safe_relative}")) from exc
        if not stat.S_ISDIR(info.st_mode) or current.is_symlink():
            raise ContractCatalogError(_issue(context, f"unsafe parent {safe_relative}"))
    target = current / safe_relative.parts[-1]
    try:
        info = target.lstat()
    except OSError as exc:
        raise ContractCatalogError(_issue(context, f"missing source {safe_relative}")) from exc
    if target.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ContractCatalogError(
            _issue(context, f"source is not a standalone regular file: {safe_relative}")
        )
    return target


def _read_canonical_catalog(
    catalog_path: Path,
    *,
    retained_bytes: bytes | None = None,
) -> dict[str, Any]:
    if retained_bytes is None:
        try:
            raw = catalog_path.read_bytes()
        except OSError as exc:
            raise ContractCatalogError(
                _issue("catalog", f"could not read catalog bytes: {exc}")
            ) from exc
        try:
            catalog = read_json_object(catalog_path)
        except AssetContractError as exc:
            raise ContractCatalogError(str(exc)) from exc
    else:
        raw = retained_bytes
        try:
            catalog = decode_json_object(raw, source=catalog_path)
        except RuntimeIOError as exc:
            raise ContractCatalogError(str(exc)) from exc
    canonical = canonical_json_bytes(catalog)
    if raw != canonical:
        raise ContractCatalogError(_issue("catalog", "must use canonical sorted JSON bytes"))
    return catalog


def _string_list(entry: dict[str, Any], field: str, *, context: str) -> list[str]:
    value = entry.get(field)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContractCatalogError(_issue(f"{context}/{field}", "must be a string array"))
    if len(set(value)) != len(value):
        raise ContractCatalogError(_issue(f"{context}/{field}", "contains duplicates"))
    return value


def _validate_catalog_shape(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    unknown = set(catalog) - _TOP_LEVEL_KEYS
    missing = _TOP_LEVEL_KEYS - set(catalog)
    if unknown:
        fields = ", ".join(sorted(unknown))
        raise ContractCatalogError(_issue("catalog", f"contains unknown fields: {fields}"))
    if missing:
        fields = ", ".join(sorted(missing))
        raise ContractCatalogError(_issue("catalog", f"missing fields: {fields}"))
    if catalog.get("format") != CATALOG_FORMAT or catalog.get("format_version") != CATALOG_VERSION:
        raise ContractCatalogError(_issue("catalog", "format or format_version is unsupported"))
    entries = catalog.get("contracts")
    if not isinstance(entries, list) or not entries:
        raise ContractCatalogError(_issue("contracts", "must be a non-empty array"))
    return entries


def _validate_entries(entries: list[dict[str, Any]]) -> None:
    ids: set[str] = set()
    formats: set[tuple[str, object]] = set()
    schemas: set[str] = set()
    all_paths: dict[str, str] = {}
    for index, entry in enumerate(entries):
        context = f"contracts/{index}"
        if not isinstance(entry, dict):
            raise ContractCatalogError(_issue(context, "must be an object"))
        unknown = set(entry) - _ENTRY_KEYS
        missing = _ENTRY_KEYS - set(entry)
        if unknown:
            fields = ", ".join(sorted(unknown))
            raise ContractCatalogError(_issue(context, f"contains unknown fields: {fields}"))
        if missing:
            fields = ", ".join(sorted(missing))
            raise ContractCatalogError(_issue(context, f"missing fields: {fields}"))
        contract_id = _catalog_identifier(entry.get("id"), context=f"{context}/id")
        if contract_id in ids:
            raise ContractCatalogError(
                _issue(f"{context}/id", f"duplicate contract id {contract_id}")
            )
        ids.add(contract_id)
        if entry.get("kind") != "json-schema":
            raise ContractCatalogError(_issue(f"{context}/kind", "must be json-schema"))
        for field in ("title", "format"):
            if not isinstance(entry.get(field), str) or not entry[field]:
                raise ContractCatalogError(
                    _issue(f"{context}/{field}", "must be a non-empty string")
                )
        version = entry.get("version")
        if isinstance(version, bool) or not isinstance(version, int | str) or version == "":
            raise ContractCatalogError(_issue(f"{context}/version", "must be an integer or string"))
        if isinstance(version, int) and version < 1:
            raise ContractCatalogError(
                _issue(f"{context}/version", "integer version must be at least 1")
            )
        format_key = (entry["format"], version)
        if format_key in formats:
            raise ContractCatalogError(
                _issue(context, f"duplicate format/version {format_key[0]} {format_key[1]}")
            )
        formats.add(format_key)
        schema = _portable_relative_path(entry.get("schema"), context=f"{context}/schema")
        if schema in schemas:
            raise ContractCatalogError(_issue(f"{context}/schema", f"duplicate schema {schema}"))
        schemas.add(schema)
        for field in ("python_symbols", "cli_commands", "m5_phases", *_PATH_LIST_FIELDS):
            _string_list(entry, field, context=context)
        if not entry["docs"]:
            raise ContractCatalogError(_issue(f"{context}/docs", "must contain at least one path"))
        for symbol_index, symbol in enumerate(entry["python_symbols"]):
            _python_symbol(symbol, context=f"{context}/python_symbols/{symbol_index}")
        for command_index, command in enumerate(entry["cli_commands"]):
            _cli_command(command, context=f"{context}/cli_commands/{command_index}")
        for phase in entry["m5_phases"]:
            if phase not in _ALLOWED_PHASES:
                raise ContractCatalogError(_issue(f"{context}/m5_phases", f"unknown phase {phase}"))
        for field in ("schema", *_PATH_LIST_FIELDS):
            values = [entry["schema"]] if field == "schema" else entry[field]
            for value_index, value in enumerate(values):
                path_context = f"{context}/{field}/{value_index}"
                relative = _portable_relative_path(value, context=path_context)
                folded = relative.casefold()
                existing = all_paths.setdefault(folded, relative)
                if existing != relative:
                    raise ContractCatalogError(
                        _issue(path_context, f"casefold path collision with {existing}")
                    )
    if "contract-catalog" not in ids:
        raise ContractCatalogError(_issue("contracts", "catalog schema entry is missing"))
    if "schemas/contract-catalog.schema.json" not in schemas:
        raise ContractCatalogError(_issue("contracts", "catalog schema source is missing"))


def _validate_fixture_identity(
    path: Path,
    *,
    context: str,
    schema_format: object | None,
    schema_version_field: str | None,
    schema_version: object | None,
) -> None:
    try:
        fixture = read_json_object(path)
    except AssetContractError as exc:
        raise ContractCatalogError(
            _issue(context, f"could not strict-read JSON fixture: {exc}")
        ) from exc
    if schema_format is not None and fixture.get("format") != schema_format:
        raise ContractCatalogError(
            _issue(
                f"{context}/format",
                f"fixture value {fixture.get('format')!r} does not match "
                f"schema/catalog {schema_format!r}",
            )
        )
    if schema_version_field is not None and fixture.get(schema_version_field) != schema_version:
        raise ContractCatalogError(
            _issue(
                f"{context}/{schema_version_field}",
                f"fixture value {fixture.get(schema_version_field)!r} does not match "
                f"schema/catalog {schema_version!r}",
            )
        )


def _read_catalog_json(
    path: Path,
    relative: str,
    *,
    retained_inventory: dict[str, bytes] | None,
) -> dict[str, Any]:
    if retained_inventory is None:
        try:
            return read_json_object(path)
        except AssetContractError as exc:
            raise ContractCatalogError(str(exc)) from exc
    try:
        payload = retained_inventory[relative]
    except KeyError as exc:
        raise ContractCatalogError(
            _issue("installed", f"missing public data file {relative}")
        ) from exc
    try:
        return decode_json_object(payload, source=path)
    except RuntimeIOError as exc:
        raise ContractCatalogError(str(exc)) from exc


def _validate_schema_formats(
    entries: list[dict[str, Any]],
    root: Path,
    *,
    full_source: bool,
    retained_inventory: dict[str, bytes] | None = None,
) -> None:
    catalog_schemas = {entry["schema"] for entry in entries}
    if full_source:
        source_schemas = {
            path.relative_to(root).as_posix() for path in (root / "schemas").glob("*.json")
        }
        missing = source_schemas - catalog_schemas
        extra = catalog_schemas - source_schemas
        if missing or extra:
            details = []
            if missing:
                details.append(f"missing {', '.join(sorted(missing))}")
            if extra:
                details.append(f"extra {', '.join(sorted(extra))}")
            raise ContractCatalogError(_issue("contracts", "; ".join(details)))
    for index, entry in enumerate(entries):
        context = f"contracts/{index}"
        schema_path = (
            root / entry["schema"]
            if retained_inventory is not None
            else _safe_regular_file(root, entry["schema"], context=f"{context}/schema")
        )
        schema = _read_catalog_json(
            schema_path,
            entry["schema"],
            retained_inventory=retained_inventory,
        )
        if schema.get("title") != entry["title"]:
            raise ContractCatalogError(_issue(context, "title does not match schema title"))
        props = schema.get("properties")
        if props is None:
            props = {}
        if not isinstance(props, dict):
            raise ContractCatalogError(_issue(context, "schema properties must be an object"))
        schema_format = None
        schema_version = None
        schema_version_field = None
        if isinstance(props.get("format"), dict):
            schema_format = props["format"].get("const")
        for version_field in ("format_version", "schema_version", "version"):
            version_schema = props.get(version_field)
            if not isinstance(version_schema, dict):
                continue
            if version_schema.get("const") is not None:
                schema_version = version_schema["const"]
                schema_version_field = version_field
                break
            enum = version_schema.get("enum")
            if isinstance(enum, list) and enum and all(isinstance(item, int) for item in enum):
                schema_version = max(enum)
                schema_version_field = version_field
                break
        if schema_format is not None and schema_format != entry["format"]:
            raise ContractCatalogError(_issue(context, "format does not match schema const"))
        if schema_version is not None and schema_version != entry["version"]:
            raise ContractCatalogError(_issue(context, "version does not match schema const"))
        if full_source:
            for field in _PATH_LIST_FIELDS:
                for value_index, relative in enumerate(entry[field]):
                    path_context = f"{context}/{field}/{value_index}"
                    path = _safe_regular_file(root, relative, context=path_context)
                    if (
                        field == "fixtures"
                        and PurePosixPath(relative).suffix.casefold() == ".json"
                        and (schema_format is not None or schema_version_field is not None)
                    ):
                        _validate_fixture_identity(
                            path,
                            context=path_context,
                            schema_format=schema_format,
                            schema_version_field=schema_version_field,
                            schema_version=schema_version,
                        )


def _validate_python_symbols(entries: list[dict[str, Any]]) -> None:
    for index, entry in enumerate(entries):
        for symbol in entry["python_symbols"]:
            context = f"contracts/{index}/python_symbols"
            module_name, attribute_path = _python_symbol(symbol, context=context)
            try:
                target: object = importlib.import_module(module_name)
                for attribute in attribute_path.split("."):
                    target = getattr(target, attribute)
            except (ImportError, AttributeError) as exc:
                raise ContractCatalogError(_issue(context, f"cannot import {symbol}")) from exc


def _validate_cli_commands(entries: list[dict[str, Any]]) -> None:
    from worldforge.__main__ import build_parser

    subparsers = build_parser()._subparsers._group_actions  # noqa: SLF001
    choices: set[str] = set()
    for action in subparsers:
        choices.update(getattr(action, "choices", {}).keys())
    for index, entry in enumerate(entries):
        for command in entry["cli_commands"]:
            if command not in choices:
                raise ContractCatalogError(
                    _issue(f"contracts/{index}/cli_commands", f"unknown CLI command {command}")
                )


def _candidate_install_prefixes() -> list[Path]:
    candidates: list[Path] = []
    data_path = sysconfig.get_paths().get("data")
    if data_path:
        candidates.append(Path(data_path))
    prefix = sysconfig.get_config_var("prefix") or sysconfig.get_path("data")
    candidates.append(Path(prefix))
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _capture_public_data_snapshot(
    root: Path,
    snapshot: RetainedTreeSnapshot,
) -> _InstalledPublicData:
    for relative in (*snapshot.directories, *snapshot.files):
        if relative:
            _portable_relative_path(relative, context="installed")
    actual_directories = set(snapshot.directories)
    for directory_name in _PUBLIC_DATA_DIRECTORIES:
        if directory_name not in actual_directories:
            raise ContractCatalogError(
                _issue(
                    "installed",
                    f"partial installed public data tree: missing directory {directory_name}",
                )
            )
    catalog_relative = CATALOG_RELATIVE_PATH.as_posix()
    if catalog_relative not in snapshot.files:
        raise ContractCatalogError(
            _issue("installed", "partial installed public data tree: catalog is missing")
        )
    return _InstalledPublicData(
        catalog_path=root / CATALOG_RELATIVE_PATH,
        directories=snapshot.directories,
        inventory=snapshot.files,
        root=root,
    )


def _installed_roots(
    prefixes: list[Path],
    *,
    verification_hook: RetainedTreeHook | None = None,
) -> _InstalledPublicData | None:
    canonical: list[_InstalledPublicData] = []
    legacy: list[_InstalledPublicData] = []
    for prefix in prefixes:
        try:
            snapshots = capture_retained_named_child_trees(
                prefix,
                container_name=CANONICAL_SHARE_DIRECTORY.parts[0],
                child_names=(
                    CANONICAL_SHARE_DIRECTORY.name,
                    LEGACY_SHARE_DIRECTORY.name,
                ),
                verification_hook=verification_hook,
            )
        except RetainedTreeError as exc:
            boundary = (
                "unsafe installed public data root"
                if str(exc).startswith("named child is not")
                else "unsafe installed public data tree"
            )
            raise ContractCatalogError(_issue("installed", f"{boundary}: {exc}")) from exc
        canonical_snapshot = snapshots[CANONICAL_SHARE_DIRECTORY.name]
        legacy_snapshot = snapshots[LEGACY_SHARE_DIRECTORY.name]
        if canonical_snapshot is not None:
            canonical.append(
                _capture_public_data_snapshot(
                    prefix / CANONICAL_SHARE_DIRECTORY,
                    canonical_snapshot,
                )
            )
        if legacy_snapshot is not None:
            legacy.append(
                _capture_public_data_snapshot(
                    prefix / LEGACY_SHARE_DIRECTORY,
                    legacy_snapshot,
                )
            )
    discovered = [*canonical, *legacy]
    if discovered:
        reference = discovered[0]
        for candidate in discovered[1:]:
            if (
                candidate.directories != reference.directories
                or candidate.inventory != reference.inventory
            ):
                raise ContractCatalogError(
                    _issue(
                        "installed",
                        "canonical and legacy installed public data trees diverge globally",
                    )
                )
    if canonical:
        return canonical[0]
    if legacy:
        return legacy[0]
    return None


def _expected_installed_public_paths(entries: list[dict[str, Any]]) -> set[str]:
    expected = {CATALOG_RELATIVE_PATH.as_posix()}
    for entry in entries:
        expected.add(entry["schema"])
        for field in _PATH_LIST_FIELDS:
            expected.update(
                relative
                for relative in entry[field]
                if relative.startswith(("contracts/", "schemas/"))
            )
    return expected


def _expected_installed_public_directories(files: set[str]) -> set[str]:
    expected = {""}
    for relative in files:
        parts = PurePosixPath(relative).parts[:-1]
        for length in range(1, len(parts) + 1):
            expected.add(PurePosixPath(*parts[:length]).as_posix())
    return expected


def _validate_installed_inventory(
    entries: list[dict[str, Any]],
    inventory: dict[str, bytes],
    directories: tuple[str, ...],
) -> None:
    expected = _expected_installed_public_paths(entries)
    actual = set(inventory)
    missing = expected - actual
    extra = actual - expected
    if missing:
        raise ContractCatalogError(
            _issue("installed", f"missing public data file {sorted(missing)[0]}")
        )
    if extra:
        raise ContractCatalogError(
            _issue("installed", f"unexpected public data file {sorted(extra)[0]}")
        )
    expected_directories = _expected_installed_public_directories(expected)
    actual_directories = set(directories)
    missing_directories = expected_directories - actual_directories
    extra_directories = actual_directories - expected_directories
    if missing_directories:
        raise ContractCatalogError(
            _issue(
                "installed",
                f"missing public data directory {sorted(missing_directories)[0]}",
            )
        )
    if extra_directories:
        raise ContractCatalogError(
            _issue(
                "installed",
                f"unexpected public data directory {sorted(extra_directories)[0]}",
            )
        )


def _catalog_path(
    source_root: str | Path | None,
) -> tuple[
    Path,
    Path,
    str,
    bool,
    dict[str, bytes] | None,
    tuple[str, ...] | None,
]:
    if source_root is not None:
        root = Path(source_root).resolve()
        catalog_path = _safe_regular_file(root, CATALOG_RELATIVE_PATH.as_posix(), context="catalog")
        return catalog_path, root, "source", True, None, None
    installed = _installed_roots(_candidate_install_prefixes())
    if installed is not None:
        return (
            installed.catalog_path,
            installed.root,
            "installed",
            False,
            installed.inventory,
            installed.directories,
        )
    raise ContractCatalogError("contracts/catalog.json could not be found")


def load_contract_catalog(source_root: str | Path | None = None) -> dict[str, Any]:
    path, _root, _mode, _full_source, inventory, directories = _catalog_path(source_root)
    retained = None if inventory is None else inventory[CATALOG_RELATIVE_PATH.as_posix()]
    catalog = _read_canonical_catalog(path, retained_bytes=retained)
    if inventory is not None:
        entries = _validate_catalog_shape(catalog)
        _validate_entries(entries)
        assert directories is not None
        _validate_installed_inventory(entries, inventory, directories)
    return catalog


def audit_contracts(source_root: str | Path | None = None) -> ContractAuditResult:
    catalog_path, root, mode, full_source, inventory, directories = _catalog_path(source_root)
    retained = None if inventory is None else inventory[CATALOG_RELATIVE_PATH.as_posix()]
    catalog = _read_canonical_catalog(catalog_path, retained_bytes=retained)
    entries = _validate_catalog_shape(catalog)
    _validate_entries(entries)
    if inventory is not None:
        assert directories is not None
        _validate_installed_inventory(entries, inventory, directories)
    _validate_schema_formats(
        entries,
        root,
        full_source=full_source,
        retained_inventory=inventory,
    )
    _validate_python_symbols(entries)
    _validate_cli_commands(entries)
    return ContractAuditResult(catalog_path=catalog_path, mode=mode, contracts=len(entries))
