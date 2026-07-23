from __future__ import annotations

import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from scripts.studio_runtime_sources import (
    DEFAULT_SOURCE,
    REQUIRED_BLOCKERS,
    RuntimeSourcesError,
    load_strict_json,
    load_strict_json_bytes,
    main,
    require_redistributable,
    validate_document,
    verify_source_file,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "apps/studio/packaging/runtime-sources.schema.json"
Mutation = Callable[[dict[str, Any]], None]
JsonPath = tuple[str | int, ...]


def _source() -> dict[str, Any]:
    return load_strict_json(DEFAULT_SOURCE)


def _all_object_schemas(value: Any, path: str = "$") -> list[str]:
    missing: list[str] = []
    if isinstance(value, dict):
        if value.get("type") == "object" and value.get("additionalProperties") is not False:
            missing.append(path)
        for key, child in value.items():
            missing.extend(_all_object_schemas(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            missing.extend(_all_object_schemas(child, f"{path}[{index}]"))
    return missing


def _numeric_paths(value: Any, path: JsonPath = ()) -> list[tuple[JsonPath, int]]:
    records: list[tuple[JsonPath, int]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            records.extend(_numeric_paths(child, (*path, key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            records.extend(_numeric_paths(child, (*path, index)))
    elif isinstance(value, int) and not isinstance(value, bool):
        records.append((path, value))
    return records


def _json_with_numeric_token(document: dict[str, Any], path: JsonPath, token: str) -> bytes:
    mutated = copy.deepcopy(document)
    cursor: Any = mutated
    for component in path[:-1]:
        cursor = cursor[component]
    sentinel = "__RWF_NUMERIC_TOKEN__"
    cursor[path[-1]] = sentinel
    encoded = json.dumps(mutated, ensure_ascii=True, separators=(",", ":"))
    needle = json.dumps(sentinel)
    if encoded.count(needle) != 1:
        raise AssertionError("numeric token sentinel was not unique")
    return encoded.replace(needle, token).encode("utf-8")


def _deep_json(*, array: bool, depth: int) -> bytes:
    if array:
        return (b"[" * depth) + b"0" + (b"]" * depth)
    return (b'{"x":' * depth) + b"0" + (b"}" * depth)


def _reseal_codex_commit(value: dict[str, Any]) -> None:
    replacement = "6d1fbf26c43abc65a203928b2e31561cb039e06d"
    value["codex"]["commit"] = replacement
    for target in value["codex"]["targets"]:
        target["npm_provenance"]["source_commit"] = replacement


def _reseal_python_commit(value: dict[str, Any]) -> None:
    replacement = "1e4d9c24b72d28573e622518f09b16aef4a33be8"
    value["python"]["commit"] = replacement
    value["python"]["release_attestation"]["subject_commit_sha1"] = replacement


class StudioRuntimeSourcesTests(unittest.TestCase):
    def assert_invalid(self, document: dict[str, Any]) -> None:
        with self.assertRaises(RuntimeSourcesError):
            validate_document(document)

    def assert_mutations_invalid(self, mutations: list[Mutation]) -> None:
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                source = _source()
                mutate(source)
                self.assert_invalid(source)

    def python_accepts(self, document: dict[str, Any]) -> bool:
        try:
            validate_document(document)
        except RuntimeSourcesError:
            return False
        return True

    def test_checked_in_source_is_strictly_valid_and_fail_closed(self) -> None:
        report = verify_source_file()

        self.assertEqual(
            report.as_dict(),
            {
                "format": "rpg-world-forge.studio_runtime_sources_verification",
                "format_version": 1,
                "valid": True,
                "release_ready": False,
                "release_status": "blocked",
                "open_blocker_codes": list(REQUIRED_BLOCKERS),
                "target_ids": ["linux-x64", "win32-x64"],
            },
        )

    def test_checked_in_contract_pins_exact_runtime_constants(self) -> None:
        source = _source()
        codex_linux, codex_windows = source["codex"]["targets"]
        python_linux, python_windows = source["python"]["targets"]

        self.assertEqual(source["codex"]["version"], "0.144.6")
        self.assertEqual(
            source["codex"]["commit"],
            "5d1fbf26c43abc65a203928b2e31561cb039e06d",
        )
        self.assertEqual(
            codex_linux["archive"],
            {
                "filename": "codex-0.144.6-linux-x64.tgz",
                "url": ("https://registry.npmjs.org/@openai/codex/-/codex-0.144.6-linux-x64.tgz"),
                "size": 131212687,
                "sha256": ("b6752eb2e8c10e6fcc96ac5c1c8ad8342cdb9a74504fb84686addf081a7d2868"),
            },
        )
        self.assertEqual(
            codex_linux["sri"],
            (
                "sha512-4E7EnzCg0OnBxCyYnwJ+qnZwWHYe0YScr5ucKWbngE9u4+0XrpWELqq2Kn9jl"
                "5GZK8MDjU7PrJwFIwusHOHjuw=="
            ),
        )
        self.assertEqual(
            codex_linux["payload_root"],
            "package/vendor/x86_64-unknown-linux-musl",
        )
        self.assertEqual(codex_windows["archive"]["size"], 145169047)
        self.assertEqual(
            codex_windows["archive"]["sha256"],
            "e04afbe9841be306455d075ad414993a946c94a399e55d7f9ec223f734cd4101",
        )
        self.assertEqual(
            codex_windows["payload_root"],
            "package/vendor/x86_64-pc-windows-msvc",
        )
        self.assertEqual(len(codex_linux["inventory"]), 6)
        self.assertEqual(len(codex_windows["inventory"]), 6)

        self.assertEqual(source["python"]["python_version"], "3.12.13")
        self.assertEqual(source["python"]["release"], "20260718")
        self.assertEqual(
            source["python"]["commit"],
            "0e4d9c24b72d28573e622518f09b16aef4a33be8",
        )
        self.assertEqual(python_linux["runtime_archive"]["size"], 34199823)
        self.assertEqual(
            python_linux["runtime_archive"]["sha256"],
            "5854aa6ec71cad00334d5065633c210b2e7feb40956767a59a91791cadcf0b79",
        )
        self.assertEqual(python_linux["metadata_archive"]["size"], 112247883)
        self.assertEqual(
            python_linux["metadata_archive"]["sha256"],
            "a92c3870c2907c2ad9b8d93c2af28626b5fba535c0abf60ddc01492dec697d2f",
        )
        self.assertEqual(
            python_linux["python_json"],
            {
                "path": "python/PYTHON.json",
                "size": 116971,
                "sha256": ("9392b3b8fcc51192e2e174c29e39b1c04a5954f470ab7651a708bb75fe7d00ce"),
            },
        )
        self.assertEqual(
            python_linux["license_inventory"],
            {
                "format": "sha256-tab-size-tab-path-lf",
                "entry_count": 19,
                "size": 1999,
                "sha256": ("cbc93e43a66eec5a259fd0bfa0931cc43433cb5e8d6590179875d30e5c9d613e"),
            },
        )
        self.assertEqual(python_windows["runtime_archive"]["size"], 21932298)
        self.assertEqual(
            python_windows["runtime_archive"]["sha256"],
            "0d422a1439ec308e03f47df551bc30f5994727c456e414b026d202bcda9b7c1c",
        )
        self.assertEqual(python_windows["metadata_archive"]["size"], 43451823)
        self.assertEqual(
            python_windows["metadata_archive"]["sha256"],
            "00de77fbc37588bc89d2f47df14f400c35e201afa2a3d7ffb495eab0945da2db",
        )
        self.assertEqual(
            python_windows["python_json"]["sha256"],
            "facf73b63d9e851d25292b063067cb0826dc44e6f291e3ade963a2cb0a46665d",
        )
        self.assertEqual(
            python_windows["license_inventory"]["sha256"],
            "4fbafee9e54187b96f6210417da89d14c35c2073320469afceb596d46a2ad966",
        )
        self.assertEqual(
            source["python"]["cpython_source"],
            {
                "filename": "Python-3.12.13.tar.xz",
                "url": ("https://www.python.org/ftp/python/3.12.13/Python-3.12.13.tar.xz"),
                "size": 20801708,
                "sha256": ("c08bc65a81971c1dd5783182826503369466c7e67374d1646519adf05207b684"),
            },
        )

    def test_strict_loader_rejects_duplicate_nonfinite_overflow_and_non_object(self) -> None:
        invalid_documents = (
            b'{"format":1,"format":1}',
            b'{"value":NaN}',
            b'{"value":Infinity}',
            b'{"value":-Infinity}',
            b'{"value":1e999999}',
            b"[]",
        )
        for raw in invalid_documents:
            with self.subTest(raw=raw), self.assertRaises(RuntimeSourcesError):
                load_strict_json_bytes(raw)

    def test_strict_loader_rejects_invalid_utf8(self) -> None:
        with self.assertRaisesRegex(RuntimeSourcesError, "not UTF-8"):
            load_strict_json_bytes(b'{"value":"\xff"}')

    def test_numeric_lexical_equivalents_match_json_schema_integer_semantics(self) -> None:
        source = _source()
        numeric_fields = _numeric_paths(source)
        self.assertGreater(len(numeric_fields), 20)

        for path, value in numeric_fields:
            for token in (f"{value}.0", f"{value}.000", f"{value}e0", f"{value}E+0"):
                with self.subTest(path=path, token=token):
                    document = load_strict_json_bytes(_json_with_numeric_token(source, path, token))
                    validate_document(document)
            for token in ("true", f"{value}.5"):
                with self.subTest(path=path, token=token), self.assertRaises(RuntimeSourcesError):
                    document = load_strict_json_bytes(_json_with_numeric_token(source, path, token))
                    validate_document(document)

    def test_strict_loader_redacts_deep_object_and_array_nesting(self) -> None:
        depth = sys.getrecursionlimit() + 200
        for array in (False, True):
            with self.subTest(array=array):
                with self.assertRaises(RuntimeSourcesError) as captured:
                    load_strict_json_bytes(_deep_json(array=array, depth=depth))
                self.assertEqual(captured.exception.code, "invalid_json")
                self.assertEqual(captured.exception.context, "source")
                self.assertNotIn("Traceback", str(captured.exception))

    def test_cli_subprocess_redacts_deep_nesting_failures(self) -> None:
        depth = sys.getrecursionlimit() + 200
        token = "deep-cli-host-path-secret-token"
        script = ROOT / "scripts/studio_runtime_sources.py"
        for array in (False, True):
            with (
                self.subTest(array=array),
                tempfile.TemporaryDirectory(prefix=f"{token}-") as directory,
            ):
                source = Path(directory) / f"{token}.json"
                source.write_bytes(_deep_json(array=array, depth=depth))
                completed = subprocess.run(
                    [sys.executable, str(script), "--source", str(source)],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                )
            self.assertEqual(completed.returncode, 1)
            self.assertEqual(completed.stdout, "")
            error = json.loads(completed.stderr)
            self.assertEqual(error["error"]["code"], "invalid_json")
            self.assertEqual(error["error"]["context"], "source")
            for forbidden in (
                "/home",
                str(ROOT),
                str(source),
                source.name,
                token,
                "Traceback",
                "RecursionError",
            ):
                self.assertNotIn(forbidden, completed.stderr)

    def test_boolean_and_float_number_traps_are_rejected(self) -> None:
        self.assert_mutations_invalid(
            [
                lambda value: value["codex"]["targets"][0]["archive"].__setitem__("size", True),
                lambda value: value["python"]["targets"][1]["python_json"].__setitem__(
                    "size", True
                ),
                lambda value: value["python"]["release_attestation"].__setitem__(
                    "release_database_id", True
                ),
                lambda value: value["python"]["cpython_source"].__setitem__("size", 1.5),
            ]
        )

    def test_resealed_wrong_target_version_commit_hash_and_sri_are_rejected(self) -> None:
        self.assert_mutations_invalid(
            [
                lambda value: value["supported_targets"].__setitem__(0, "darwin-x64"),
                lambda value: value["codex"]["targets"][0].__setitem__("target_id", "win32-x64"),
                lambda value: value["codex"].__setitem__("version", "0.144.7"),
                lambda value: value["codex"].__setitem__(
                    "commit", "6d1fbf26c43abc65a203928b2e31561cb039e06d"
                ),
                lambda value: value["python"].__setitem__("release", "20260719"),
                lambda value: value["python"].__setitem__(
                    "commit", "1e4d9c24b72d28573e622518f09b16aef4a33be8"
                ),
                lambda value: value["codex"]["targets"][0]["archive"].__setitem__(
                    "sha256",
                    ("a6752eb2e8c10e6fcc96ac5c1c8ad8342cdb9a74504fb84686addf081a7d2868"),
                ),
                lambda value: value["python"]["targets"][0]["metadata_archive"].__setitem__(
                    "sha256",
                    ("b92c3870c2907c2ad9b8d93c2af28626b5fba535c0abf60ddc01492dec697d2f"),
                ),
                lambda value: value["codex"]["targets"][0].__setitem__(
                    "sri",
                    (
                        "sha512-eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4"
                        "eHh4eHh4eHh4eHh4eHh4eHh4eA=="
                    ),
                ),
            ]
        )

    def test_urls_reject_hosts_queries_fragments_and_credentials(self) -> None:
        self.assert_mutations_invalid(
            [
                lambda value: value["codex"]["targets"][0]["archive"].__setitem__(
                    "url",
                    "https://example.com/@openai/codex/-/codex-0.144.6-linux-x64.tgz",
                ),
                lambda value: value["codex"]["targets"][0]["archive"].__setitem__(
                    "url",
                    ("https://registry.npmjs.org/@openai/codex/-/codex-0.144.6-linux-x64.tgz?x=1"),
                ),
                lambda value: value["python"]["cpython_source"].__setitem__(
                    "url",
                    ("https://user:secret@www.python.org/ftp/python/3.12.13/Python-3.12.13.tar.xz"),
                ),
                lambda value: value["python"]["cpython_source"].__setitem__(
                    "url",
                    ("https://www.python.org/ftp/python/3.12.13/Python-3.12.13.tar.xz#digest"),
                ),
            ]
        )

    def test_filenames_paths_and_casefold_collisions_are_rejected(self) -> None:
        self.assert_mutations_invalid(
            [
                lambda value: value["codex"]["release_sha256sums"].__setitem__(
                    "filename", "../SHA256SUMS"
                ),
                lambda value: value["codex"]["release_sha256sums"].__setitem__("filename", "CON"),
                lambda value: value["codex"]["targets"][0]["inventory"][1].__setitem__(
                    "path",
                    value["codex"]["targets"][0]["inventory"][0]["path"].upper(),
                ),
                lambda value: value["codex"]["targets"][0].__setitem__(
                    "payload_root", "package/vendor/../vendor"
                ),
                lambda value: value["python"]["targets"][1]["runtime_archive"].__setitem__(
                    "filename",
                    value["python"]["targets"][0]["runtime_archive"]["filename"].upper(),
                ),
            ]
        )

    def test_missing_blocker_is_rejected_even_when_open_codes_are_resealed(self) -> None:
        source = _source()
        removed = source["blockers"].pop()
        source["redistribution"]["open_blocker_codes"].remove(removed["code"])

        self.assert_invalid(source)

    def test_ready_reseal_is_rejected_until_policy_and_evidence_are_updated(self) -> None:
        source = _source()
        for blocker in source["blockers"]:
            blocker["status"] = "closed"
        source["redistribution"] = {
            "status": "ready",
            "release_ready": True,
            "open_blocker_codes": [],
        }

        self.assert_invalid(source)

    def test_require_redistributable_refuses_the_checked_in_source(self) -> None:
        with self.assertRaisesRegex(RuntimeSourcesError, "redistribution is blocked"):
            require_redistributable(_source())

    def test_cli_reports_valid_blocked_status_and_fails_release_assertion(self) -> None:
        accepted_stdout = io.StringIO()
        with redirect_stdout(accepted_stdout):
            self.assertEqual(main([]), 0)
        accepted = json.loads(accepted_stdout.getvalue())
        self.assertIs(accepted["valid"], True)
        self.assertIs(accepted["release_ready"], False)

        rejected_stderr = io.StringIO()
        with redirect_stderr(rejected_stderr):
            self.assertEqual(main(["--require-redistributable"]), 1)
        rejected = json.loads(rejected_stderr.getvalue())
        self.assertIs(rejected["valid"], False)
        self.assertEqual(
            rejected["error"],
            {
                "code": "redistribution_blocked",
                "context": "$.redistribution",
                "message": "Studio runtime redistribution is blocked",
            },
        )

    def test_schema_and_source_are_closed_and_correlated(self) -> None:
        schema = load_strict_json(SCHEMA)
        source = _source()

        self.assertEqual(_all_object_schemas(schema), [])
        self.assertEqual(schema["const"], source)
        self.assertEqual(schema["$id"], source["schema_id"])
        self.assertEqual(set(schema["required"]), set(source))
        self.assertEqual(schema["properties"]["format"]["const"], source["format"])
        self.assertEqual(
            schema["properties"]["format_version"]["const"],
            source["format_version"],
        )
        self.assertEqual(
            schema["$defs"]["codex"]["properties"]["version"]["const"],
            "0.144.6",
        )
        self.assertEqual(
            schema["$defs"]["python"]["properties"]["python_version"]["const"],
            "3.12.13",
        )
        self.assertEqual(
            schema["$defs"]["python"]["properties"]["release"]["const"],
            "20260718",
        )
        self.assertEqual(
            schema["$defs"]["blocker_code"]["enum"],
            list(REQUIRED_BLOCKERS),
        )

    def test_python_rejects_the_pinned_mutation_corpus(self) -> None:
        mutations: list[Mutation] = [
            lambda value: value["codex"]["targets"][0]["archive"].__setitem__("size", 131212688),
            lambda value: value["codex"]["targets"][0]["inventory"][0].__setitem__(
                "size", 298516529
            ),
            lambda value: value["python"]["cpython_source"].__setitem__("size", 20801709),
            lambda value: value["python"]["targets"][0]["runtime_archive"].__setitem__(
                "size", 34199824
            ),
            lambda value: value["python"]["targets"][0]["metadata_archive"].__setitem__(
                "size", 112247884
            ),
            lambda value: value["python"]["targets"][0]["python_json"].__setitem__("size", 116972),
            _reseal_codex_commit,
            _reseal_python_commit,
            lambda value: value["codex"]["targets"][0]["inventory"][1].__setitem__(
                "path", value["codex"]["targets"][0]["inventory"][0]["path"]
            ),
            lambda value: value["codex"]["targets"][1].__setitem__("target_id", "linux-x64"),
            lambda value: value["python"]["targets"][1].__setitem__("target_id", "linux-x64"),
            lambda value: value["blockers"][0].__setitem__("components", ["Codex", "codex"]),
            lambda value: value["blockers"][0].__setitem__("reason", "x" * 513),
            lambda value: value["blockers"][0].__setitem__("components", ["x" * 129]),
            lambda value: value["codex"]["targets"][0]["archive"].__setitem__("size", True),
            lambda value: value.__setitem__("attacker_extra_key", "secret-value"),
        ]

        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                document = _source()
                mutate(document)
                self.assertFalse(self.python_accepts(document))

    def test_public_exceptions_redact_attacker_controlled_values(self) -> None:
        secret = "host-path-user-token-secret"
        cases: list[Callable[[], object]] = [
            lambda: load_strict_json(Path(f"/tmp/{secret}/runtime-sources.json")),
            lambda: load_strict_json_bytes(('{"' + secret + '":1,"' + secret + '":2}').encode()),
            lambda: validate_document(
                {
                    **_source(),
                    secret: "arbitrary-private-value",
                }
            ),
            lambda: validate_document(
                {
                    **_source(),
                    secret: "todo",
                }
            ),
            lambda: validate_document(
                {
                    **_source(),
                    "codex": {
                        **_source()["codex"],
                        "targets": [
                            {
                                **_source()["codex"]["targets"][0],
                                "archive": {
                                    **_source()["codex"]["targets"][0]["archive"],
                                    "url": (
                                        "https://user:"
                                        + secret
                                        + "@registry.npmjs.org/private?"
                                        + secret
                                    ),
                                },
                            },
                            _source()["codex"]["targets"][1],
                        ],
                    },
                }
            ),
        ]
        forbidden = (
            secret,
            "arbitrary-private-value",
            "user:",
            "/tmp/",
            "runtime-sources.json",
        )
        for index, call in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(RuntimeSourcesError) as captured:
                call()
            rendered = str(captured.exception)
            for value in forbidden:
                self.assertNotIn(value, rendered)
            self.assertRegex(
                rendered,
                r"^[a-z_]+ at (?:source|\$[^:]*): [A-Za-z]",
            )

    def test_cli_error_is_machine_readable_and_redacts_source_path(self) -> None:
        secret = "cli-host-path-token-secret"
        with tempfile.TemporaryDirectory(prefix=f"{secret}-") as directory:
            missing = Path(directory) / f"{secret}.json"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(main(["--source", str(missing)]), 1)
            malformed = Path(directory) / "malformed.json"
            malformed.write_text(
                '{"' + secret + '":1,"' + secret + '":2}',
                encoding="utf-8",
            )
            malformed_stdout = io.StringIO()
            malformed_stderr = io.StringIO()
            with redirect_stdout(malformed_stdout), redirect_stderr(malformed_stderr):
                self.assertEqual(main(["--source", str(malformed)]), 1)

        self.assertEqual(stdout.getvalue(), "")
        rendered = stderr.getvalue()
        self.assertNotIn(secret, rendered)
        self.assertNotIn(str(missing), rendered)
        error = json.loads(rendered)
        self.assertEqual(
            error,
            {
                "format": "rpg-world-forge.studio_runtime_sources_verification",
                "format_version": 1,
                "valid": False,
                "error": {
                    "code": "source_read_failed",
                    "context": "source",
                    "message": "runtime provenance source could not be read",
                },
            },
        )
        self.assertEqual(malformed_stdout.getvalue(), "")
        malformed_rendered = malformed_stderr.getvalue()
        self.assertNotIn(secret, malformed_rendered)
        malformed_error = json.loads(malformed_rendered)
        self.assertEqual(malformed_error["error"]["code"], "invalid_json")
        self.assertEqual(malformed_error["error"]["context"], "source")

    def test_source_contains_no_placeholder_markers(self) -> None:
        text = DEFAULT_SOURCE.read_text(encoding="utf-8").casefold()
        for marker in ("placeholder", "replace-me", "replace_me", "todo", "tbd"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, text)


if __name__ == "__main__":
    unittest.main()
