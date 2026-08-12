"""Generate deterministic generic runtime contract fixtures without executing games."""

from __future__ import annotations

import argparse
import copy
import json
import tempfile
from pathlib import Path

from worldforge.__main__ import _resolve_generic_assetpack_cli_source
from worldforge.creation_contracts import canonical_creation_hash
from worldforge.generic_assetpack import seal_generic_assetpack
from worldforge.generic_runtime import (
    build_builtin_runtime_adapters,
    build_game_runtime_composition,
    build_game_runtime_snapshot,
    build_historical_runtime_adapters,
    build_runtime_adapter_registry,
    build_runtime_evidence,
    build_runtime_support_report,
    serialize_game_runtime_composition,
    serialize_runtime_adapter,
    serialize_runtime_adapter_registry,
    serialize_runtime_snapshot,
    serialize_runtime_support_report,
)
from worldforge.integrity import canonical_json_bytes
from worldforge.runtime_support_authority import (
    derive_runtime_support_report,
    initialize_runtime_support_authority,
    serialize_runtime_support_authority,
)

ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _reseal_runtime_document(document: dict[str, object]) -> dict[str, object]:
    format_name = document["format"]
    if format_name == "world-forge.game_runtime_snapshot":
        document["tree_hash"] = canonical_creation_hash({"files": document["files"]})
        document["snapshot_id"] = (
            "runtime_snapshot_"
            + canonical_creation_hash(
                {
                    "runtime_api": document["runtime_api"],
                    "adapter_descriptors": document["adapter_descriptors"],
                    "files": document["files"],
                    "tree_hash": document["tree_hash"],
                }
            )[:40]
        )
    elif format_name == "world-forge.runtime_adapter_registry":
        document["registry_id"] = (
            "runtime_registry_"
            + canonical_creation_hash(
                {
                    "runtime_snapshot": document["runtime_snapshot"],
                    "adapters": document["adapters"],
                }
            )[:40]
        )
    elif format_name == "world-forge.game_runtime_composition":
        document["composition_id"] = (
            "runtime_composition_"
            + canonical_creation_hash(
                {
                    field: document[field]
                    for field in (
                        "gamepack",
                        "asset_inventory",
                        "assetpack",
                        "adapter",
                        "registry",
                        "runtime_snapshot",
                        "platforms",
                        "bindings",
                    )
                }
            )[:40]
        )
    elif format_name == "world-forge.runtime_evidence":
        document["evidence_id"] = (
            "runtime_evidence_"
            + canonical_creation_hash(
                {
                    field: document[field]
                    for field in (
                        "composition",
                        "adapter",
                        "platform",
                        "execution_status",
                        "packaging_status",
                        "checks",
                    )
                }
            )[:40]
        )
    elif format_name == "world-forge.runtime_support_report":
        document["report_id"] = (
            "runtime_support_"
            + canonical_creation_hash(
                {
                    field: document[field]
                    for field in (
                        "gamepack",
                        "composition",
                        "adapter",
                        "evidence",
                        "dimensions",
                        "compatibility_status",
                        "mechanics",
                        "features",
                        "missing_capabilities",
                        "reason_codes",
                        "supported",
                    )
                }
            )[:40]
        )
    document["content_hash"] = canonical_creation_hash(document)
    return document


def _evidence_checks(prefix: str) -> list[dict[str, object]]:
    return [
        {
            "check_id": "check:headless_determinism",
            "kind": "headless",
            "status": "passed",
            "evidence_id": f"{prefix}_headless",
            "content_hash": "1" * 64,
        },
        {
            "check_id": "check:native_raylib",
            "kind": "native",
            "status": "passed",
            "evidence_id": f"{prefix}_native",
            "content_hash": "2" * 64,
        },
        {
            "check_id": "check:package_verification",
            "kind": "packaging",
            "status": "passed",
            "evidence_id": f"{prefix}_package",
            "content_hash": "3" * 64,
        },
        {
            "check_id": "check:save_replay",
            "kind": "save_replay",
            "status": "passed",
            "evidence_id": f"{prefix}_replay",
            "content_hash": "4" * 64,
        },
    ]


def _parity_case(
    case_id: str,
    kind: str,
    document: dict[str, object],
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "kind": kind,
        "document": document,
    }


def build_runtime_fixtures(
    source_root: str | Path = ROOT,
) -> dict[str, bytes]:
    root = Path(source_root)
    adapters = build_builtin_runtime_adapters()
    snapshot = build_game_runtime_snapshot(
        root / "src" / "gamepack_runtime",
        adapter_runtime_root=root / "src" / "gamepack_raylib_2d",
        adapters=adapters,
    )
    registry = build_runtime_adapter_registry(
        adapters=adapters,
        snapshot=snapshot,
    )
    generated: dict[str, bytes] = {
        (
            f"examples/multigenre-contracts/runtime/adapters/{adapter['adapter_id']}.json"
        ): serialize_runtime_adapter(adapter)
        for adapter in adapters
    }
    generated.update(
        {
            (
                "examples/multigenre-contracts/runtime/adapters/historical/"
                f"{adapter['adapter_id']}@{adapter['adapter_version']}.json"
            ): serialize_runtime_adapter(adapter)
            for adapter in build_historical_runtime_adapters()
        }
    )
    generated.update(
        {
            "examples/multigenre-contracts/runtime/snapshot.json": (
                serialize_runtime_snapshot(snapshot)
            ),
            "examples/multigenre-contracts/runtime/registry.json": (
                serialize_runtime_adapter_registry(registry)
            ),
        }
    )
    gamepacks: dict[str, dict[str, object]] = {}
    compositions: dict[str, dict[str, object]] = {}
    reports: dict[str, dict[str, object]] = {}
    with tempfile.TemporaryDirectory(prefix="world-forge-runtime-fixtures-") as temporary:
        temporary_root = Path(temporary)
        for name in ("abstract-puzzle", "branching-narrative"):
            fixture_root = root / "examples" / "multigenre-contracts" / name
            gamepack = _read(fixture_root / "artifacts" / f"{name}.gamepack.json")
            inventory = _read(fixture_root / "assets" / "inventory.json")
            source = _resolve_generic_assetpack_cli_source(
                fixture_root / "assets" / "manifest.json"
            )
            verified = seal_generic_assetpack(
                temporary_root / f"{name}-assetpack",
                **source,
            )
            try:
                composition = build_game_runtime_composition(
                    gamepack,
                    inventory,
                    verified.root,
                    registry=registry,
                    snapshot=snapshot,
                )
                authority = initialize_runtime_support_authority(
                    gamepack=gamepack,
                    inventory=inventory,
                    composition=composition,
                    registry=registry,
                    snapshot=snapshot,
                    verified_assetpack=verified,
                    asset_release_authority=source["release_authority"],
                )
                report = derive_runtime_support_report(authority)
            finally:
                verified.close()
            generated[f"examples/multigenre-contracts/{name}/runtime/composition.json"] = (
                serialize_game_runtime_composition(composition)
            )
            generated[f"examples/multigenre-contracts/{name}/runtime/support-report.json"] = (
                serialize_runtime_support_report(report)
            )
            generated[f"examples/multigenre-contracts/{name}/runtime/support-authority.json"] = (
                serialize_runtime_support_authority(authority.document)
            )
            gamepacks[name] = gamepack
            compositions[name] = composition
            reports[name] = report

    puzzle_composition = compositions["abstract-puzzle"]
    linux_evidence = build_runtime_evidence(
        puzzle_composition,
        platform_id="platform:linux_x86_64",
        execution_status="native_verified",
        packaging_status="verified",
        checks=_evidence_checks("parity_linux"),
    )
    windows_evidence = build_runtime_evidence(
        puzzle_composition,
        platform_id="platform:windows_x86_64",
        execution_status="native_verified",
        packaging_status="verified",
        checks=_evidence_checks("parity_windows"),
    )
    full_evidence_report = build_runtime_support_report(
        puzzle_composition,
        gamepack=gamepacks["abstract-puzzle"],
        registry=registry,
        snapshot=snapshot,
        evidence=[linux_evidence, windows_evidence],
    )
    positive_report = copy.deepcopy(full_evidence_report)
    positive_report["dimensions"]["adapter"] = "verified"
    positive_report["dimensions"]["release"] = "ready"
    evidence_ids = sorted(
        (linux_evidence["evidence_id"], windows_evidence["evidence_id"]),
        key=lambda item: item.encode("utf-8"),
    )
    for mechanic in positive_report["mechanics"]:
        mechanic["status"] = "supported_current"
        mechanic["reason_codes"] = []
        mechanic["test_evidence"] = list(evidence_ids)
        mechanic["native_evidence"] = list(evidence_ids)
    for feature in positive_report["features"]:
        feature["status"] = "supported_current"
        feature["reason_codes"] = []
        feature["evidence_ids"] = list(evidence_ids)
    positive_report["compatibility_status"] = "supported"
    positive_report["missing_capabilities"] = []
    positive_report["reason_codes"] = []
    positive_report["supported"] = True
    _reseal_runtime_document(positive_report)

    valid_cases = [
        _parity_case(
            "valid-adapter",
            "generic-runtime-adapter",
            copy.deepcopy(adapters[0]),
        ),
        _parity_case(
            "valid-registry",
            "generic-runtime-adapter-registry",
            copy.deepcopy(registry),
        ),
        _parity_case(
            "valid-snapshot",
            "game-runtime-snapshot",
            copy.deepcopy(snapshot),
        ),
        _parity_case(
            "valid-composition",
            "game-runtime-composition",
            copy.deepcopy(puzzle_composition),
        ),
        _parity_case(
            "valid-evidence",
            "generic-runtime-evidence",
            copy.deepcopy(linux_evidence),
        ),
        _parity_case(
            "valid-blocked-support",
            "generic-runtime-support-report",
            copy.deepcopy(reports["abstract-puzzle"]),
        ),
        _parity_case(
            "valid-full-evidence-blocked-support",
            "generic-runtime-support-report",
            copy.deepcopy(full_evidence_report),
        ),
        _parity_case(
            "valid-positive-support-structure",
            "generic-runtime-support-report",
            copy.deepcopy(positive_report),
        ),
    ]

    invalid_cases: list[dict[str, object]] = []

    def invalid(
        case_id: str,
        kind: str,
        source: dict[str, object],
        mutate: object,
    ) -> None:
        document = copy.deepcopy(source)
        mutate(document)
        _reseal_runtime_document(document)
        invalid_cases.append(_parity_case(case_id, kind, document))

    invalid(
        "adapter-feature-order",
        "generic-runtime-adapter",
        adapters[0],
        lambda document: document["supported_features"].reverse(),
    )
    invalid(
        "adapter-feature-duplicate",
        "generic-runtime-adapter",
        adapters[0],
        lambda document: document["supported_features"].append(document["supported_features"][0]),
    )
    invalid(
        "adapter-presentation-duplicate",
        "generic-runtime-adapter",
        adapters[0],
        lambda document: document["presentations"].append(
            copy.deepcopy(document["presentations"][0])
        ),
    )
    invalid(
        "adapter-platform-order",
        "generic-runtime-adapter",
        adapters[0],
        lambda document: document["platforms"].reverse(),
    )
    invalid(
        "adapter-platform-projection",
        "generic-runtime-adapter",
        adapters[0],
        lambda document: document["platforms"][0].__setitem__(
            "platform_family",
            "platform:windows",
        ),
    )
    invalid(
        "adapter-binding-order",
        "generic-runtime-adapter",
        adapters[1],
        lambda document: document["asset_bindings"].reverse(),
    )
    invalid(
        "adapter-binding-duplicate",
        "generic-runtime-adapter",
        adapters[1],
        lambda document: document["asset_bindings"].append(
            copy.deepcopy(document["asset_bindings"][0])
        ),
    )
    invalid(
        "adapter-execution-semantics-policy",
        "generic-runtime-adapter",
        adapters[0],
        lambda document: document["execution_semantics"].__setitem__(
            "content_hash",
            "f" * 64,
        ),
    )

    def mutate_registry_policy(document: dict[str, object]) -> None:
        adapter = document["adapters"][0]
        adapter["execution_semantics"]["content_hash"] = "e" * 64
        _reseal_runtime_document(adapter)

    invalid(
        "registry-nested-execution-semantics-policy",
        "generic-runtime-adapter-registry",
        registry,
        mutate_registry_policy,
    )
    invalid(
        "snapshot-file-order",
        "game-runtime-snapshot",
        snapshot,
        lambda document: document["files"].reverse(),
    )
    invalid(
        "snapshot-file-size-limit",
        "game-runtime-snapshot",
        snapshot,
        lambda document: document["files"][0].__setitem__(
            "size_bytes",
            4 * 1024 * 1024 + 1,
        ),
    )

    def mutate_snapshot_aggregate(document: dict[str, object]) -> None:
        for index in range(9):
            document["files"].append(
                {
                    "path": f"gamepack_runtime/oversized-{index:02d}.py",
                    "sha256": f"{index + 1:064x}",
                    "size_bytes": 4 * 1024 * 1024,
                }
            )
        document["files"].sort(key=lambda item: item["path"].encode("utf-8"))

    invalid(
        "snapshot-aggregate-size-limit",
        "game-runtime-snapshot",
        snapshot,
        mutate_snapshot_aggregate,
    )
    invalid(
        "composition-platform-order",
        "game-runtime-composition",
        puzzle_composition,
        lambda document: document["platforms"].reverse(),
    )
    invalid(
        "composition-platform-projection",
        "game-runtime-composition",
        puzzle_composition,
        lambda document: document["platforms"][0].__setitem__(
            "platform_family",
            "platform:windows",
        ),
    )
    invalid(
        "composition-binding-order",
        "game-runtime-composition",
        compositions["branching-narrative"],
        lambda document: document["bindings"].reverse(),
    )
    invalid(
        "composition-binding-size-limit",
        "game-runtime-composition",
        puzzle_composition,
        lambda document: document["bindings"][0].__setitem__(
            "size_bytes",
            16 * 1024 * 1024 + 1,
        ),
    )
    invalid(
        "evidence-check-order",
        "generic-runtime-evidence",
        linux_evidence,
        lambda document: document["checks"].reverse(),
    )
    invalid(
        "evidence-external-id-duplicate",
        "generic-runtime-evidence",
        linux_evidence,
        lambda document: document["checks"][1].__setitem__(
            "evidence_id",
            document["checks"][0]["evidence_id"],
        ),
    )
    invalid(
        "evidence-concrete-backend",
        "generic-runtime-evidence",
        linux_evidence,
        lambda document: document["platform"].__setitem__(
            "backend",
            "backend:unspecified",
        ),
    )
    invalid(
        "evidence-platform-projection",
        "generic-runtime-evidence",
        linux_evidence,
        lambda document: document["platform"].__setitem__(
            "platform_family",
            "platform:windows",
        ),
    )
    invalid(
        "support-feature-order",
        "generic-runtime-support-report",
        reports["abstract-puzzle"],
        lambda document: document["features"].reverse(),
    )
    invalid(
        "support-reason-duplicate",
        "generic-runtime-support-report",
        reports["abstract-puzzle"],
        lambda document: document["reason_codes"].append(document["reason_codes"][0]),
    )
    invalid(
        "support-evidence-reference-order",
        "generic-runtime-support-report",
        full_evidence_report,
        lambda document: document["evidence"].reverse(),
    )

    def mutate_crossed_execution(document: dict[str, object]) -> None:
        first, second = document["dimensions"]["execution"]
        first["evidence_ids"], second["evidence_ids"] = (
            second["evidence_ids"],
            first["evidence_ids"],
        )

    invalid(
        "support-crossed-execution-evidence",
        "generic-runtime-support-report",
        full_evidence_report,
        mutate_crossed_execution,
    )
    invalid(
        "support-positive-feature-evidence-order",
        "generic-runtime-support-report",
        positive_report,
        lambda document: document["features"][0]["evidence_ids"].reverse(),
    )
    invalid(
        "support-positive-mechanic-evidence-empty",
        "generic-runtime-support-report",
        positive_report,
        lambda document: document["mechanics"][0].__setitem__(
            "test_evidence",
            [],
        ),
    )
    invalid(
        "support-positive-execution-evidence-empty",
        "generic-runtime-support-report",
        positive_report,
        lambda document: document["dimensions"]["execution"][0].__setitem__(
            "evidence_ids",
            [],
        ),
    )

    def mutate_evidence_free_overclaim(document: dict[str, object]) -> None:
        document["evidence"] = []
        document["dimensions"]["adapter"] = "verified"
        document["dimensions"]["packaging"] = "verified"
        document["dimensions"]["release"] = "ready"
        for execution in document["dimensions"]["execution"]:
            execution["status"] = "native_verified"
            execution["evidence_ids"] = []
        for mechanic in document["mechanics"]:
            mechanic["status"] = "supported_current"
            mechanic["reason_codes"] = []
            mechanic["test_evidence"] = []
            mechanic["native_evidence"] = []
        for feature in document["features"]:
            feature["status"] = "supported_current"
            feature["reason_codes"] = []
            feature["evidence_ids"] = []
        document["compatibility_status"] = "supported"
        document["missing_capabilities"] = []
        document["reason_codes"] = []
        document["supported"] = True

    invalid(
        "support-evidence-free-overclaim",
        "generic-runtime-support-report",
        reports["abstract-puzzle"],
        mutate_evidence_free_overclaim,
    )
    invalid(
        "support-blocked-reason-empty",
        "generic-runtime-support-report",
        reports["abstract-puzzle"],
        lambda document: document["mechanics"][0].__setitem__(
            "reason_codes",
            [],
        ),
    )
    invalid(
        "support-platform-projection",
        "generic-runtime-support-report",
        reports["abstract-puzzle"],
        lambda document: document["dimensions"]["execution"][0]["platform"].__setitem__(
            "platform_family",
            "platform:windows",
        ),
    )

    generated["tests/fixtures/generic-runtime/parity-corpus.json"] = canonical_json_bytes(
        {
            "execution_semantics": adapters[0]["execution_semantics"],
            "format": "world-forge.runtime_parity_corpus",
            "format_version": 1,
            "invalid": invalid_cases,
            "valid": valid_cases,
        }
    )
    generated["tests/fixtures/generic-runtime/unsupported-capabilities.json"] = (
        canonical_json_bytes(
            {
                "cases": [
                    {
                        "profile": "profile:branching_foldback",
                        "required_feature_ids": ["logic:foldback"],
                        "expected_status": "unsupported",
                        "missing_feature_ids": ["logic:foldback"],
                    },
                    {
                        "profile": "profile:action_with_framing_narrative",
                        "required_feature_ids": ["action:realtime_combat"],
                        "expected_status": "unsupported",
                        "missing_feature_ids": ["action:realtime_combat"],
                    },
                    {
                        "profile": "profile:roguelite_storylets",
                        "required_feature_ids": ["roguelite:run_reset"],
                        "expected_status": "unsupported",
                        "missing_feature_ids": ["roguelite:run_reset"],
                    },
                    {
                        "profile": "profile:simulation_management",
                        "required_feature_ids": ["simulation:economy"],
                        "expected_status": "unsupported",
                        "missing_feature_ids": ["simulation:economy"],
                    },
                    {
                        "profile": "profile:sports_season",
                        "required_feature_ids": ["sports:season"],
                        "expected_status": "unsupported",
                        "missing_feature_ids": ["sports:season"],
                    },
                    {
                        "profile": "profile:strategy_scenario",
                        "required_feature_ids": ["strategy:turn_order"],
                        "expected_status": "unsupported",
                        "missing_feature_ids": ["strategy:turn_order"],
                    },
                ],
                "policy": (
                    "Required capabilities resolve by exact feature ID, never by genre label."
                ),
            }
        )
    )
    return dict(sorted(generated.items()))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--source-root", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.source_root.resolve()
    stale: list[str] = []
    for relative, payload in build_runtime_fixtures(root).items():
        path = root / relative
        if args.check:
            if not path.exists() or path.read_bytes() != payload:
                stale.append(relative)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    if stale:
        raise SystemExit("generic runtime fixtures are stale: " + ", ".join(stale))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
