from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from scripts import studio_runtime_inputs as runtime_inputs
from scripts.studio_runtime_inputs import (
    InputArtifact,
    RuntimeInputsError,
    _fetch_artifacts,
    _resolve_target_inputs,
    _verify_artifacts,
    fetch_runtime_inputs,
    main,
    verify_runtime_inputs,
)
from scripts.studio_runtime_sources import REQUIRED_BLOCKERS

TARGET = "linux-x64"


def _artifact(
    payload: bytes = b"pinned runtime input",
    *,
    component: str = "test-input",
    filename: str = "input.bin",
    url: str = "https://github.com/input.bin",
    sha256: str | None = None,
    sha512: bool = False,
) -> InputArtifact:
    return InputArtifact(
        component=component,
        filename=filename,
        url=url,
        size=len(payload),
        sha256=sha256 or hashlib.sha256(payload).hexdigest(),
        sha512=hashlib.sha512(payload).hexdigest() if sha512 else None,
    )


class FakeHeaders:
    def __init__(self, values: dict[str, list[str] | str] | None = None) -> None:
        self.values: dict[str, list[str]] = {}
        for name, value in (values or {}).items():
            self.values[name.casefold()] = value if isinstance(value, list) else [value]

    def get_all(self, name: str) -> list[str] | None:
        value = self.values.get(name.casefold())
        return None if value is None else list(value)


class FakeResponse:
    def __init__(
        self,
        payload: bytes,
        *,
        url: str = "https://github.com/input.bin",
        status: int = 200,
        headers: dict[str, list[str] | str] | None = None,
        redirects: tuple[str, ...] = (),
        fail_after_reads: int | None = None,
        read_hook: object | None = None,
    ) -> None:
        self.status = status
        self.headers = FakeHeaders(
            {"Content-Length": str(len(payload))} if headers is None else headers
        )
        self.rwf_redirect_chain = redirects
        self._url = url
        self._payload = io.BytesIO(payload)
        self._reads = 0
        self._fail_after_reads = fail_after_reads
        self._read_hook = read_hook
        self.closed = False

    def geturl(self) -> str:
        return self._url

    def read(self, amount: int = -1) -> bytes:
        self._reads += 1
        if callable(self._read_hook):
            self._read_hook(self._reads)
        if self._fail_after_reads is not None and self._reads > self._fail_after_reads:
            raise OSError("attacker network error /secret/response")
        return self._payload.read(amount)

    def close(self) -> None:
        self.closed = True


class FakeOpener:
    def __init__(self, factory: object) -> None:
        self.factory = factory
        self.calls: list[tuple[object, float]] = []

    def open(self, request: object, *, timeout: float) -> FakeResponse:
        self.calls.append((request, timeout))
        if callable(self.factory):
            return self.factory(request)
        raise AssertionError("network opener must not be called")


class FakeWindowsHandleApi:
    """POSIX-backed seam for exercising Windows handle-binding logic."""

    def open_directory(self, path: Path) -> int:
        return os.open(
            path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )

    def open_entry(self, path: Path) -> int:
        return os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )

    def info(self, handle: int) -> object:
        info = os.fstat(handle)
        attributes = 0x10 if stat.S_ISDIR(info.st_mode) else 0
        return runtime_inputs._WindowsHandleInfo(
            identity=(info.st_dev, info.st_ino),
            attributes=attributes,
            link_count=info.st_nlink,
            size=info.st_size,
        )

    def duplicate_to_fd(self, handle: int, *, writable: bool) -> int:
        if writable:
            raise AssertionError("offline seam must not request writable handles")
        return os.dup(handle)

    def close(self, handle: int) -> None:
        os.close(handle)


def _cache_file(cache: Path, artifact: InputArtifact) -> Path:
    return cache / TARGET / artifact.component / artifact.filename


def _write_cache(cache: Path, artifact: InputArtifact, payload: bytes) -> Path:
    destination = _cache_file(cache, artifact)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    destination.chmod(0o600)
    return destination


class StudioRuntimeInputsTests(unittest.TestCase):
    def test_pinned_manifest_resolves_exact_bounded_inventory(self) -> None:
        document = runtime_inputs._manifest_document()

        for target in ("linux-x64", "win32-x64"):
            with self.subTest(target=target):
                artifacts = _resolve_target_inputs(document, target)
                self.assertEqual(
                    [artifact.component for artifact in artifacts],
                    [
                        "codex-package",
                        "codex-release",
                        "codex-checksums",
                        "python-runtime",
                        "python-metadata",
                        "python-source",
                        "python-checksums",
                    ],
                )
                self.assertEqual(len(artifacts), 7)
                self.assertTrue(all(artifact.url.startswith("https://") for artifact in artifacts))
                self.assertIsNotNone(artifacts[0].sha512)
                self.assertTrue(all(artifact.size > 0 for artifact in artifacts))

    def test_fetch_success_and_offline_verification_retain_blocked_status(self) -> None:
        payload = b"small synthetic runtime"
        artifact = _artifact(payload, sha512=True)
        opener = FakeOpener(lambda _request: FakeResponse(payload))
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)

            fetched = _fetch_artifacts(TARGET, cache, (artifact,), opener)
            verified = _verify_artifacts(TARGET, cache, (artifact,))

            self.assertTrue(fetched.valid)
            self.assertEqual(fetched.items[0].status, "downloaded")
            self.assertTrue(verified.valid)
            self.assertEqual(verified.items[0].status, "verified")
            self.assertEqual(fetched.as_dict()["redistribution_status"], "blocked")
            self.assertFalse(fetched.as_dict()["release_ready"])
            self.assertEqual(fetched.as_dict()["open_blocker_codes"], list(REQUIRED_BLOCKERS))
            destination = _cache_file(cache, artifact)
            self.assertEqual(destination.read_bytes(), payload)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
            self.assertEqual(list(destination.parent.glob(".rwf-input-*.part")), [])
            request, timeout = opener.calls[0]
            self.assertEqual(timeout, runtime_inputs.NETWORK_TIMEOUT_SECONDS)
            self.assertEqual(request.get_header("User-agent"), runtime_inputs.USER_AGENT)
            self.assertEqual(request.get_header("Accept-encoding"), "identity")
            self.assertEqual(request.get_header("Cache-control"), "no-transform")

    def test_public_fetch_uses_injected_opener_without_arbitrary_input_arguments(self) -> None:
        payload = b"public seam"
        artifact = _artifact(payload)
        opener = FakeOpener(lambda _request: FakeResponse(payload))
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("scripts.studio_runtime_inputs._manifest_document", return_value={}),
            patch(
                "scripts.studio_runtime_inputs._resolve_target_inputs",
                return_value=(artifact,),
            ),
        ):
            report = fetch_runtime_inputs(TARGET, Path(directory), opener=opener)

        self.assertTrue(report.valid)
        self.assertEqual(report.items[0].status, "downloaded")
        self.assertEqual(len(opener.calls), 1)

    def test_exact_existing_cache_is_reused_without_network(self) -> None:
        payload = b"reusable"
        artifact = _artifact(payload)
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            _write_cache(cache, artifact, payload)
            opener = FakeOpener(object())

            report = _fetch_artifacts(TARGET, cache, (artifact,), opener)

            self.assertEqual(report.items[0].status, "reused")
            self.assertEqual(opener.calls, [])

    def test_final_offline_validation_rehashes_the_original_pinned_descriptor(self) -> None:
        payload = b"first hash bytes"
        mutated = b"mutated in place"
        self.assertEqual(len(payload), len(mutated))
        artifact = _artifact(payload)
        original = runtime_inputs._inspect_cached
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            destination = _write_cache(cache, artifact, payload)
            mutated_once = False

            def inspect_then_mutate(
                parent: object,
                current_artifact: InputArtifact,
                **kwargs: object,
            ) -> object:
                nonlocal mutated_once
                result = original(parent, current_artifact, **kwargs)
                if not mutated_once and result.valid:
                    mutated_once = True
                    destination.write_bytes(mutated)
                return result

            with patch(
                "scripts.studio_runtime_inputs._inspect_cached",
                side_effect=inspect_then_mutate,
            ):
                report = _verify_artifacts(TARGET, cache, (artifact,))

            self.assertFalse(report.valid)
            self.assertNotEqual(report.items[0].status, "verified")
            self.assertEqual(destination.read_bytes(), mutated)

    def test_final_fetch_validation_rehashes_every_previously_pinned_file(self) -> None:
        first_payload = b"first fetched"
        mutated = b"first mutate!"
        self.assertEqual(len(first_payload), len(mutated))
        second_payload = b"second fetched"
        first = _artifact(
            first_payload,
            component="first-input",
            filename="first.bin",
            url="https://github.com/first.bin",
        )
        second = _artifact(
            second_payload,
            component="second-input",
            filename="second.bin",
            url="https://github.com/second.bin",
        )
        original = runtime_inputs._download_one
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)

            def download_then_mutate(
                parent: object,
                current_artifact: InputArtifact,
                opener: object,
                *,
                clock: object,
            ) -> str:
                status = original(
                    parent,
                    current_artifact,
                    opener,
                    clock=clock,
                )
                if current_artifact.component == second.component:
                    _cache_file(cache, first).write_bytes(mutated)
                return status

            responses = {
                first.url: first_payload,
                second.url: second_payload,
            }

            def response_for(request: object) -> FakeResponse:
                return FakeResponse(
                    responses[request.full_url],
                    url=request.full_url,
                )

            with (
                patch(
                    "scripts.studio_runtime_inputs._download_one",
                    side_effect=download_then_mutate,
                ),
                self.assertRaises(RuntimeInputsError) as captured,
            ):
                _fetch_artifacts(
                    TARGET,
                    cache,
                    (first, second),
                    FakeOpener(response_for),
                )

            self.assertEqual(captured.exception.code, "cache_entry_unsafe")
            self.assertEqual(_cache_file(cache, first).read_bytes(), mutated)

    def test_offline_verify_is_read_only_and_never_constructs_an_opener(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            cache = Path(parent) / "absent-cache"
            with patch(
                "scripts.studio_runtime_inputs.urllib.request.build_opener",
                side_effect=AssertionError("network construction forbidden"),
            ) as build:
                report = verify_runtime_inputs(TARGET, cache, offline=True)

            self.assertFalse(report.valid)
            self.assertTrue(all(item.status == "missing" for item in report.items))
            self.assertFalse(cache.exists())
            build.assert_not_called()

    def test_verify_reports_missing_tampered_truncated_and_oversized(self) -> None:
        payload = b"12345678"
        artifact = _artifact(payload)
        cases = {
            "missing": None,
            "wrong_sha256": b"abcdefgh",
            "wrong_size_short": b"123",
            "wrong_size_long": b"123456789",
        }
        expected = {
            "missing": "missing",
            "wrong_sha256": "wrong_sha256",
            "wrong_size_short": "wrong_size",
            "wrong_size_long": "wrong_size",
        }
        for name, cached in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                cache = Path(directory)
                if cached is not None:
                    _write_cache(cache, artifact, cached)

                report = _verify_artifacts(TARGET, cache, (artifact,))

                self.assertFalse(report.valid)
                self.assertEqual(report.items[0].status, expected[name])

    def test_verify_reports_every_artifact_in_fixed_order(self) -> None:
        payload = b"inventory"
        verified = _artifact(payload, component="verified", filename="verified.bin")
        tampered = _artifact(payload, component="tampered", filename="tampered.bin")
        missing = _artifact(payload, component="missing", filename="missing.bin")
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            _write_cache(cache, verified, payload)
            _write_cache(cache, tampered, b"tampered!")

            report = _verify_artifacts(
                TARGET,
                cache,
                (verified, tampered, missing),
            )

            self.assertEqual(
                [(item.artifact.component, item.status) for item in report.items],
                [
                    ("verified", "verified"),
                    ("tampered", "wrong_sha256"),
                    ("missing", "missing"),
                ],
            )
            self.assertFalse(report.valid)

    def test_fetch_never_overwrites_or_deletes_mismatched_cache(self) -> None:
        payload = b"expected"
        artifact = _artifact(payload)
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            destination = _write_cache(cache, artifact, b"attacker")
            opener = FakeOpener(lambda _request: FakeResponse(payload))

            with self.assertRaises(RuntimeInputsError) as captured:
                _fetch_artifacts(TARGET, cache, (artifact,), opener)

            self.assertEqual(captured.exception.code, "cache_conflict")
            self.assertEqual(destination.read_bytes(), b"attacker")
            self.assertEqual(opener.calls, [])

    def test_response_requires_one_exact_content_length_and_identity_encoding(self) -> None:
        payload = b"response"
        artifact = _artifact(payload)
        invalid_headers = (
            {},
            {"Content-Length": "08"},
            {"Content-Length": "7"},
            {"Content-Length": ["8", "8"]},
            {"Content-Length": "8", "Content-Encoding": "gzip"},
            {"Content-Length": "8", "Transfer-Encoding": "chunked"},
            {"Content-Length": "8", "Content-Range": "bytes 0-7/8"},
        )
        for headers in invalid_headers:
            with (
                self.subTest(headers=headers),
                tempfile.TemporaryDirectory() as directory,
            ):
                cache = Path(directory)
                opener = FakeOpener(lambda _request, h=headers: FakeResponse(payload, headers=h))

                with self.assertRaises(RuntimeInputsError) as captured:
                    _fetch_artifacts(TARGET, cache, (artifact,), opener)

                self.assertEqual(captured.exception.code, "response_invalid")
                self.assertFalse(_cache_file(cache, artifact).exists())

    def test_truncated_and_oversized_response_bodies_are_rejected(self) -> None:
        expected = b"12345678"
        artifact = _artifact(expected)
        for name, body in (("truncated", b"1234"), ("oversized", b"123456789")):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                response = FakeResponse(
                    body,
                    headers={"Content-Length": str(len(expected))},
                )
                opener = FakeOpener(lambda _request, r=response: r)

                with self.assertRaises(RuntimeInputsError) as captured:
                    _fetch_artifacts(TARGET, Path(directory), (artifact,), opener)

                self.assertEqual(captured.exception.code, "download_size_mismatch")
                self.assertEqual(
                    list((Path(directory) / TARGET / artifact.component).glob(".rwf-input-*.part")),
                    [],
                )

    def test_sha256_and_sri_sha512_mismatch_are_rejected(self) -> None:
        payload = b"digest"
        artifacts = (
            _artifact(payload, sha256="1" * 64),
            InputArtifact(
                component="test-input",
                filename="input.bin",
                url="https://github.com/input.bin",
                size=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                sha512="2" * 128,
            ),
        )
        for artifact in artifacts:
            with self.subTest(sha512=artifact.sha512), tempfile.TemporaryDirectory() as directory:
                opener = FakeOpener(lambda _request: FakeResponse(payload))

                with self.assertRaises(RuntimeInputsError) as captured:
                    _fetch_artifacts(TARGET, Path(directory), (artifact,), opener)

                self.assertEqual(captured.exception.code, "download_digest_mismatch")
                self.assertFalse(_cache_file(Path(directory), artifact).exists())

    def test_exact_github_release_asset_redirect_is_authorized_without_network(self) -> None:
        payload = b"redirect"
        artifact = _artifact(
            payload,
            url=("https://github.com/openai/codex/releases/download/rust-v0.144.6/input.bin"),
        )
        asset_url = (
            "https://release-assets.githubusercontent.com/"
            "github-production-release-asset/965415649/"
            "01234567-89ab-cdef-0123-456789abcdef"
            "?sp=r&sv=2025-11-05&sr=b&spr=https"
            "&se=2026-07-23T18%3A00%3A00Z&sig=signed%2Fvalue%3D"
        )
        allowed = FakeResponse(payload, url=asset_url, redirects=(asset_url,))
        with tempfile.TemporaryDirectory() as directory:
            report = _fetch_artifacts(
                TARGET,
                Path(directory),
                (artifact,),
                FakeOpener(lambda _request: allowed),
            )
            self.assertTrue(report.valid)

    def test_github_release_redirect_rejects_arbitrary_boundaries_and_loops(self) -> None:
        payload = b"redirect"
        artifact = _artifact(
            payload,
            url=("https://github.com/openai/codex/releases/download/rust-v0.144.6/input.bin"),
        )
        valid_path = (
            "/github-production-release-asset/965415649/01234567-89ab-cdef-0123-456789abcdef"
        )
        valid_query = (
            "sp=r&sv=2025-11-05&sr=b&spr=https&se=2026-07-23T18%3A00%3A00Z&sig=signed%2Fvalue%3D"
        )
        rejected = (
            (f"https://example.com{valid_path}?{valid_query}",),
            (f"http://release-assets.githubusercontent.com{valid_path}?{valid_query}",),
            (
                "https://release-assets.githubusercontent.com/"
                f"arbitrary/{valid_path.rsplit('/', 1)[-1]}?{valid_query}",
            ),
            (f"https://release-assets.githubusercontent.com{valid_path}",),
            (
                f"https://release-assets.githubusercontent.com{valid_path}?{valid_query}",
                artifact.url,
            ),
        )
        for redirects in rejected:
            with self.subTest(redirects=redirects), tempfile.TemporaryDirectory() as directory:
                response = FakeResponse(
                    payload,
                    url=redirects[-1],
                    redirects=redirects,
                )

                with self.assertRaises(RuntimeInputsError) as captured:
                    _fetch_artifacts(
                        TARGET,
                        Path(directory),
                        (artifact,),
                        FakeOpener(lambda _request, r=response: r),
                    )

                self.assertEqual(captured.exception.code, "redirect_rejected")

        non_release = _artifact(payload, url="https://github.com/input.bin")
        asset_url = f"https://release-assets.githubusercontent.com{valid_path}?{valid_query}"
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeInputsError) as captured:
                _fetch_artifacts(
                    TARGET,
                    Path(directory),
                    (non_release,),
                    FakeOpener(
                        lambda _request: FakeResponse(
                            payload,
                            url=asset_url,
                            redirects=(asset_url,),
                        )
                    ),
                )
        self.assertEqual(captured.exception.code, "redirect_rejected")

    def test_excessive_redirect_history_is_rejected(self) -> None:
        payload = b"redirect"
        artifact = _artifact(payload)
        url = "https://github.com/input.bin"
        response = FakeResponse(
            payload,
            redirects=tuple(url for _ in range(runtime_inputs.MAX_REDIRECTS + 1)),
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeInputsError) as captured:
                _fetch_artifacts(
                    TARGET,
                    Path(directory),
                    (artifact,),
                    FakeOpener(lambda _request: response),
                )
        self.assertEqual(captured.exception.code, "redirect_rejected")

    def test_network_interruption_removes_only_owned_partial_temp(self) -> None:
        payload = b"partial"
        artifact = _artifact(payload)
        response = FakeResponse(payload, fail_after_reads=0)
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)

            with self.assertRaises(RuntimeInputsError) as captured:
                _fetch_artifacts(
                    TARGET,
                    cache,
                    (artifact,),
                    FakeOpener(lambda _request: response),
                )

            self.assertEqual(captured.exception.code, "download_interrupted")
            self.assertFalse(_cache_file(cache, artifact).exists())
            self.assertEqual(
                list((cache / TARGET / artifact.component).glob(".rwf-input-*.part")),
                [],
            )
            self.assertEqual(
                list((cache / TARGET / artifact.component).glob(".rwf-quarantine-*")),
                [],
            )

    def test_one_cumulative_deadline_covers_open_and_every_streaming_read(self) -> None:
        payload = b"drip"
        artifact = _artifact(payload)
        now = [0.0]

        class DripResponse(FakeResponse):
            def read(self, _amount: int = -1) -> bytes:
                now[0] += runtime_inputs.DOWNLOAD_DEADLINE_SECONDS / 3
                return self._payload.read(1)

        response = DripResponse(payload)
        opener = FakeOpener(lambda _request: response)
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            with self.assertRaises(RuntimeInputsError) as captured:
                _fetch_artifacts(
                    TARGET,
                    cache,
                    (artifact,),
                    opener,
                    clock=lambda: now[0],
                )

            self.assertEqual(captured.exception.code, "download_interrupted")
            self.assertTrue(response.closed)
            self.assertFalse(_cache_file(cache, artifact).exists())
            self.assertEqual(
                list((cache / TARGET / artifact.component).glob(".rwf-input-*.part")),
                [],
            )

        now[0] = 0.0

        def slow_open(_request: object) -> FakeResponse:
            now[0] = runtime_inputs.DOWNLOAD_DEADLINE_SECONDS + 1
            return FakeResponse(payload)

        response_opener = FakeOpener(slow_open)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeInputsError) as captured:
                _fetch_artifacts(
                    TARGET,
                    Path(directory),
                    (artifact,),
                    response_opener,
                    clock=lambda: now[0],
                )
        self.assertEqual(captured.exception.code, "download_interrupted")

    @unittest.skipUnless(
        sys.platform.startswith("linux") and hasattr(os, "O_TMPFILE"),
        "Linux unnamed temporary exercise",
    )
    def test_posix_publication_and_cleanup_never_act_on_a_swapped_temp_name(self) -> None:
        owned_payload = b"owned descriptor bytes"
        foreign = b"foreign path replacement"
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            with runtime_inputs._open_cache_directory(
                cache,
                create=True,
            ) as cache_root:
                with runtime_inputs._open_component_directory(
                    cache_root,
                    TARGET,
                    create=True,
                ) as target:
                    with runtime_inputs._open_component_directory(
                        target,
                        "test-input",
                        create=True,
                    ) as parent:
                        descriptor, name, identity = parent.create_temporary()
                        os.write(descriptor, owned_payload)
                        os.fsync(descriptor)
                        os.close(descriptor)
                        replacement = parent.path / name
                        replacement.write_bytes(foreign)

                        runtime_inputs._publish_no_replace(
                            parent,
                            name,
                            "published.bin",
                            identity,
                        )
                        cleanup = parent.cleanup_owned(name, identity)

                        self.assertEqual(cleanup, "preserved")
                        self.assertEqual(replacement.read_bytes(), foreign)
                        self.assertEqual(
                            (parent.path / "published.bin").read_bytes(),
                            owned_payload,
                        )
                        self.assertEqual(
                            list(parent.path.glob(".rwf-quarantine-*")),
                            [],
                        )

    @unittest.skipUnless(os.name == "posix", "POSIX parent identity exercise")
    def test_parent_replacement_fails_closed_and_cleans_original_temp(self) -> None:
        payload = b"parent replacement"
        artifact = _artifact(payload)
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            parent = cache / TARGET / artifact.component
            moved = cache / TARGET / f"{artifact.component}-moved"

            def replace_parent(_read_number: int) -> None:
                if moved.exists():
                    return
                parent.rename(moved)
                parent.mkdir()

            response = FakeResponse(payload, read_hook=replace_parent)
            with self.assertRaises(RuntimeInputsError) as captured:
                _fetch_artifacts(
                    TARGET,
                    cache,
                    (artifact,),
                    FakeOpener(lambda _request: response),
                )

            self.assertEqual(captured.exception.code, "cache_parent_changed")
            self.assertEqual(list(moved.glob(".rwf-input-*.part")), [])
            self.assertEqual(list(parent.iterdir()), [])

    @unittest.skipUnless(os.name == "posix", "POSIX target identity exercise")
    def test_offline_inventory_pins_one_target_directory(self) -> None:
        payload = b"target snapshot"
        first = _artifact(payload, component="first", filename="first.bin")
        second = _artifact(payload, component="second", filename="second.bin")
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            _write_cache(cache, first, payload)
            _write_cache(cache, second, payload)
            target = cache / TARGET
            moved = cache / f"{TARGET}-moved"
            original = runtime_inputs._inspect_cached
            calls = 0

            def replace_after_first(
                parent: object,
                artifact: InputArtifact,
                **kwargs: object,
            ) -> object:
                nonlocal calls
                result = original(parent, artifact, **kwargs)
                calls += 1
                if calls == 1:
                    target.rename(moved)
                    target.mkdir()
                return result

            with (
                patch(
                    "scripts.studio_runtime_inputs._inspect_cached",
                    side_effect=replace_after_first,
                ),
                self.assertRaises(RuntimeInputsError) as captured,
            ):
                _verify_artifacts(TARGET, cache, (first, second))

            self.assertEqual(captured.exception.code, "cache_parent_changed")

    @unittest.skipUnless(os.name == "posix", "POSIX-backed Windows handle seam")
    def test_windows_handle_seam_rejects_target_parent_swap_during_final_bytes(self) -> None:
        payload = b"windows handle snapshot"
        artifact = _artifact(payload)
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            _write_cache(cache, artifact, payload)
            target = cache / TARGET
            moved = cache / f"{TARGET}-moved"
            fake_windows = FakeWindowsHandleApi()
            original_read = runtime_inputs._read_and_hash
            swapped = False

            def swap_after_bytes(*args: object, **kwargs: object) -> object:
                nonlocal swapped
                result = original_read(*args, **kwargs)
                if not swapped:
                    swapped = True
                    target.rename(moved)
                    target.mkdir()
                return result

            def open_windows_cache(path: Path, *, create: bool) -> object:
                return runtime_inputs._open_windows_directory(
                    path,
                    (),
                    create=create,
                )

            with (
                patch("scripts.studio_runtime_inputs._WINDOWS_API", fake_windows),
                patch(
                    "scripts.studio_runtime_inputs._open_cache_directory",
                    side_effect=open_windows_cache,
                ),
                patch(
                    "scripts.studio_runtime_inputs._read_and_hash",
                    side_effect=swap_after_bytes,
                ),
                self.assertRaises(RuntimeInputsError) as captured,
            ):
                _verify_artifacts(TARGET, cache, (artifact,))

            self.assertEqual(captured.exception.code, "cache_parent_changed")
            self.assertTrue(moved.is_dir())
            self.assertTrue(target.is_dir())

    def test_secure_directory_primitive_has_no_path_only_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch("scripts.studio_runtime_inputs._HAS_SECURE_DIR_FD", False),
                patch("scripts.studio_runtime_inputs._WINDOWS_API", None),
                self.assertRaises(RuntimeInputsError) as captured,
            ):
                runtime_inputs._open_cache_directory(
                    root,
                    create=False,
                )
            self.assertEqual(captured.exception.code, "secure_primitive_unavailable")

            path_only = runtime_inputs._Directory(root, None, runtime_inputs._identity(root.stat()))
            with self.assertRaises(RuntimeInputsError) as component_error:
                runtime_inputs._open_component_directory(
                    path_only,
                    "test-input",
                    create=False,
                )
            self.assertEqual(
                component_error.exception.code,
                "secure_primitive_unavailable",
            )

    @unittest.skipUnless(os.name == "nt", "native Windows handle-sharing exercise")
    def test_native_windows_handles_block_target_swap_through_final_read(self) -> None:
        payload = b"native windows snapshot"
        artifact = _artifact(payload)
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            _write_cache(cache, artifact, payload)
            target = cache / TARGET
            moved = cache / f"{TARGET}-moved"
            original = runtime_inputs._read_and_hash
            attempted: list[str] = []

            def attempt_swap(*args: object, **kwargs: object) -> object:
                result = original(*args, **kwargs)
                try:
                    target.rename(moved)
                except OSError:
                    attempted.append("blocked")
                else:
                    attempted.append("renamed")
                return result

            with patch(
                "scripts.studio_runtime_inputs._read_and_hash",
                side_effect=attempt_swap,
            ):
                report = _verify_artifacts(TARGET, cache, (artifact,))

            self.assertTrue(report.valid)
            self.assertEqual(attempted, ["blocked", "blocked"])
            self.assertTrue(target.is_dir())
            self.assertFalse(moved.exists())

    def test_no_replace_race_reuses_exact_winner(self) -> None:
        payload = b"winner"
        artifact = _artifact(payload)
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)

            def race(
                parent: object,
                _temporary_name: str,
                destination_name: str,
                _identity: tuple[int, int],
            ) -> None:
                (parent.path / destination_name).write_bytes(payload)
                raise FileExistsError

            with patch("scripts.studio_runtime_inputs._publish_no_replace", side_effect=race):
                report = _fetch_artifacts(
                    TARGET,
                    cache,
                    (artifact,),
                    FakeOpener(lambda _request: FakeResponse(payload)),
                )

            self.assertEqual(report.items[0].status, "reused")
            self.assertEqual(_cache_file(cache, artifact).read_bytes(), payload)

    def test_no_replace_race_preserves_mismatched_winner(self) -> None:
        payload = b"winner"
        artifact = _artifact(payload)
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            hostile = b"loser!"

            def race(
                parent: object,
                _temporary_name: str,
                destination_name: str,
                _identity: tuple[int, int],
            ) -> None:
                (parent.path / destination_name).write_bytes(hostile)
                raise FileExistsError

            with (
                patch("scripts.studio_runtime_inputs._publish_no_replace", side_effect=race),
                self.assertRaises(RuntimeInputsError) as captured,
            ):
                _fetch_artifacts(
                    TARGET,
                    cache,
                    (artifact,),
                    FakeOpener(lambda _request: FakeResponse(payload)),
                )

            self.assertEqual(captured.exception.code, "cache_conflict")
            self.assertEqual(_cache_file(cache, artifact).read_bytes(), hostile)

    def test_concurrent_fetches_publish_one_exact_entry(self) -> None:
        payload = b"concurrent payload"
        artifact = _artifact(payload)
        barrier = threading.Barrier(2)

        def factory(_request: object) -> FakeResponse:
            first = True

            def wait_once(_read_number: int) -> None:
                nonlocal first
                if first:
                    first = False
                    barrier.wait(timeout=5)

            return FakeResponse(payload, read_hook=wait_once)

        opener = FakeOpener(factory)
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(
                    pool.map(
                        lambda _index: _fetch_artifacts(
                            TARGET,
                            cache,
                            (artifact,),
                            opener,
                        ),
                        range(2),
                    )
                )

            self.assertTrue(all(report.valid for report in results))
            self.assertEqual(
                sorted(report.items[0].status for report in results),
                ["downloaded", "reused"],
            )
            self.assertEqual(_cache_file(cache, artifact).read_bytes(), payload)
            self.assertEqual(_cache_file(cache, artifact).stat().st_nlink, 1)

    def test_symlink_hardlink_and_nonregular_cache_entries_are_unsafe(self) -> None:
        payload = b"unsafe"
        artifact = _artifact(payload)
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            destination = _cache_file(cache, artifact)
            destination.parent.mkdir(parents=True)
            outside = cache / "outside"
            outside.write_bytes(payload)
            try:
                destination.symlink_to(outside)
            except OSError:
                self.skipTest("symlink creation unavailable")
            self.assertEqual(
                _verify_artifacts(TARGET, cache, (artifact,)).items[0].status,
                "unsafe",
            )

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            destination = _write_cache(cache, artifact, payload)
            alias = cache / "alias"
            try:
                os.link(destination, alias)
            except OSError:
                self.skipTest("hardlink creation unavailable")
            self.assertEqual(
                _verify_artifacts(TARGET, cache, (artifact,)).items[0].status,
                "unsafe",
            )

        if os.name == "posix":
            with tempfile.TemporaryDirectory() as directory:
                cache = Path(directory)
                destination = _cache_file(cache, artifact)
                destination.parent.mkdir(parents=True)
                os.mkfifo(destination)
                self.assertEqual(
                    _verify_artifacts(TARGET, cache, (artifact,)).items[0].status,
                    "unsafe",
                )

    def test_symlinked_cache_parent_and_casefold_alias_fail_closed(self) -> None:
        payload = b"unsafe parent"
        artifact = _artifact(payload)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            outside = root / "outside"
            outside.mkdir()
            try:
                cache.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation unavailable")
            with self.assertRaises(RuntimeInputsError):
                _verify_artifacts(TARGET, cache, (artifact,))

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            (cache / "LINUX-X64").mkdir()
            with self.assertRaises(RuntimeInputsError) as captured:
                _verify_artifacts(TARGET, cache, (artifact,))
            self.assertEqual(captured.exception.code, "cache_root_unsafe")

    def test_aliases_injected_after_fetch_fail_closed_and_preserve_evidence(self) -> None:
        payload = b"alias injection"
        artifact = _artifact(payload, component="test-input")
        original = runtime_inputs._download_one
        injections = ("component", "filename", "target")
        for injection in injections:
            with self.subTest(injection=injection), tempfile.TemporaryDirectory() as directory:
                cache = Path(directory)

                def inject_alias(
                    parent: object,
                    current_artifact: InputArtifact,
                    opener: object,
                    *,
                    clock: object,
                    selected: str = injection,
                    cache_root: Path = cache,
                ) -> str:
                    status = original(
                        parent,
                        current_artifact,
                        opener,
                        clock=clock,
                    )
                    if selected == "component":
                        (parent.path.parent / "TEST-INPUT").mkdir()
                    elif selected == "filename":
                        (parent.path / "INPUT.BIN").write_bytes(b"foreign alias")
                    else:
                        (cache_root / "LINUX-X64").mkdir()
                    return status

                with (
                    patch(
                        "scripts.studio_runtime_inputs._download_one",
                        side_effect=inject_alias,
                    ),
                    self.assertRaises(RuntimeInputsError),
                ):
                    _fetch_artifacts(
                        TARGET,
                        cache,
                        (artifact,),
                        FakeOpener(lambda _request: FakeResponse(payload)),
                    )

                self.assertEqual(_cache_file(cache, artifact).read_bytes(), payload)
                if injection == "component":
                    self.assertTrue((cache / TARGET / "TEST-INPUT").is_dir())
                elif injection == "filename":
                    self.assertEqual(
                        (cache / TARGET / artifact.component / "INPUT.BIN").read_bytes(),
                        b"foreign alias",
                    )
                else:
                    self.assertTrue((cache / "LINUX-X64").is_dir())

    def test_file_and_parent_sync_failures_do_not_replace_existing_entries(self) -> None:
        payload = b"sync"
        artifact = _artifact(payload)
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            with (
                patch(
                    "scripts.studio_runtime_inputs.os.fsync",
                    side_effect=OSError("secret sync path"),
                ),
                self.assertRaises(RuntimeInputsError) as captured,
            ):
                _fetch_artifacts(
                    TARGET,
                    cache,
                    (artifact,),
                    FakeOpener(lambda _request: FakeResponse(payload)),
                )
            self.assertEqual(captured.exception.code, "sync_failed")
            self.assertFalse(_cache_file(cache, artifact).exists())

        if os.name == "posix":
            with tempfile.TemporaryDirectory() as directory:
                cache = Path(directory)
                with (
                    patch(
                        "scripts.studio_runtime_inputs._sync_parent",
                        side_effect=RuntimeInputsError("sync_failed", "cache"),
                    ),
                    self.assertRaises(RuntimeInputsError) as captured,
                ):
                    _fetch_artifacts(
                        TARGET,
                        cache,
                        (artifact,),
                        FakeOpener(lambda _request: FakeResponse(payload)),
                    )
                self.assertEqual(captured.exception.code, "sync_failed")
                self.assertFalse(_cache_file(cache, artifact).exists())

            with tempfile.TemporaryDirectory() as directory:
                cache = Path(directory)
                calls = 0
                original = runtime_inputs._sync_parent

                def fail_after_publish(parent: object) -> None:
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        raise RuntimeInputsError("sync_failed", "cache")
                    original(parent)

                with (
                    patch(
                        "scripts.studio_runtime_inputs._sync_parent",
                        side_effect=fail_after_publish,
                    ),
                    self.assertRaises(RuntimeInputsError) as captured,
                ):
                    _fetch_artifacts(
                        TARGET,
                        cache,
                        (artifact,),
                        FakeOpener(lambda _request: FakeResponse(payload)),
                    )
                self.assertEqual(captured.exception.code, "sync_failed")
                self.assertEqual(_cache_file(cache, artifact).read_bytes(), payload)

    def test_api_rejects_bool_type_relative_and_noncanonical_paths(self) -> None:
        invalid_calls = (
            lambda: verify_runtime_inputs(True, Path("/tmp/cache")),
            lambda: verify_runtime_inputs(TARGET, True),
            lambda: verify_runtime_inputs(TARGET, Path("relative")),
            lambda: verify_runtime_inputs(TARGET, Path("/tmp/../tmp/cache")),
            lambda: verify_runtime_inputs(TARGET, Path("/tmp/cache"), offline=False),
            lambda: fetch_runtime_inputs(
                "darwin-x64", Path("/tmp/cache"), opener=FakeOpener(object())
            ),
        )
        for index, call in enumerate(invalid_calls):
            with self.subTest(index=index), self.assertRaises(RuntimeInputsError) as captured:
                call()
            self.assertEqual(captured.exception.code, "invalid_argument")
            self.assertEqual(captured.exception.exit_code, 2)

    def test_cli_argument_errors_are_structured_redacted_and_exit_two(self) -> None:
        secret = "attacker-secret-" + ("x" * 5000)
        stderr = io.StringIO()
        stdout = io.StringIO()
        with patch("sys.stderr", stderr), patch("sys.stdout", stdout):
            code = main(
                [
                    "verify",
                    "--offline",
                    "--target",
                    secret,
                    "--cache-dir",
                    "/tmp/cache",
                ]
            )

        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        error = json.loads(stderr.getvalue())
        self.assertEqual(error["error"]["code"], "invalid_argument")
        self.assertEqual(error["redistribution_status"], "blocked")
        self.assertNotIn(secret, stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_cli_verify_requires_explicit_offline_and_never_fetches(self) -> None:
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            code = main(
                [
                    "verify",
                    "--target",
                    TARGET,
                    "--cache-dir",
                    "/tmp/cache",
                ]
            )
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "invalid_argument")

    def test_cli_rejects_every_abbreviated_long_option(self) -> None:
        cases = (
            [
                "verify",
                "--off",
                "--target",
                TARGET,
                "--cache-dir",
                "/tmp/cache",
            ],
            [
                "verify",
                "--offline",
                "--tar",
                TARGET,
                "--cache-dir",
                "/tmp/cache",
            ],
            [
                "verify",
                "--offline",
                "--target",
                TARGET,
                "--cache",
                "/tmp/cache",
            ],
        )
        for argv in cases:
            with self.subTest(argv=argv):
                stderr = io.StringIO()
                stdout = io.StringIO()
                with patch("sys.stderr", stderr), patch("sys.stdout", stdout):
                    code = main(argv)
                self.assertEqual(code, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(
                    json.loads(stderr.getvalue())["error"]["code"],
                    "invalid_argument",
                )
                self.assertNotIn("Traceback", stderr.getvalue())

    def test_network_errors_are_redacted_without_traceback(self) -> None:
        payload = b"network"
        artifact = _artifact(payload)

        def fail(_request: object) -> FakeResponse:
            raise OSError("credential=secret path=/home/attacker")

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeInputsError) as captured:
                _fetch_artifacts(
                    TARGET,
                    Path(directory),
                    (artifact,),
                    FakeOpener(fail),
                )
        rendered = json.dumps(runtime_inputs._error_payload(captured.exception), sort_keys=True)
        self.assertEqual(captured.exception.code, "network_failed")
        self.assertNotIn("secret", rendered)
        self.assertNotIn("/home", rendered)
        self.assertNotIn("Traceback", rendered)


if __name__ == "__main__":
    unittest.main()
