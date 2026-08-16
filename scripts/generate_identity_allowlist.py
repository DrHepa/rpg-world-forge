#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from worldforge.identity_audit import (  # noqa: E402
    IdentityAuditError,
    ReviewedIdentityPolicy,
    audit_identities,
    refresh_identity_allowlist_evidence,
)

# Adding a path here is a security review decision, not a census shortcut. The
# generator may bind hashes and offsets only for these exact path/pattern rows.
_LEGACY_DASHED = "-".join(("rpg", "world", "forge"))
_LEGACY_CONTRACT_ADDITIONS = (
    "apps/studio/src/generated/studio-protocol-v3.d.ts",
    "apps/studio/src/generated/studio-protocol-v4.d.ts",
    "apps/studio/src/generated/studio-protocol-v5.d.ts",
    "apps/studio/src/renderer/creation-preview-state.ts",
    "apps/studio/src/renderer/creation-service.ts",
    "schemas/studio-protocol-v3.schema.json",
    "schemas/studio-protocol-v4.schema.json",
    "schemas/studio-protocol-v5.schema.json",
)
_REGRESSION_FIXTURE_ADDITIONS = (
    "apps/studio/tests/renderer/AppCreationNavigation.test.tsx",
    "apps/studio/tests/renderer/CreationAssetPipeline.test.tsx",
    "apps/studio/tests/renderer/CreationAssetPreview.test.tsx",
    "apps/studio/tests/renderer/CreationJobActivity.test.tsx",
    "apps/studio/tests/renderer/CreationMaterializationPipeline.test.tsx",
    "apps/studio/tests/renderer/CreationModuleWorkbench.test.tsx",
    "apps/studio/tests/renderer/CreationPhaseWorkspace.test.tsx",
    "apps/studio/tests/renderer/CreationProjectEntry.test.tsx",
    "apps/studio/tests/renderer/CreationRuntimePipeline.test.tsx",
    "apps/studio/tests/renderer/CreationWorkspace.test.tsx",
    "apps/studio/tests/renderer/creation-asset-pipeline-state.test.ts",
    "apps/studio/tests/renderer/creation-execution-state.test.ts",
    "apps/studio/tests/renderer/creation-materialization-pipeline-state.test.ts",
    "apps/studio/tests/renderer/creation-output-grant-state.test.ts",
    "apps/studio/tests/renderer/creation-preview-state.test.ts",
    "apps/studio/tests/renderer/creation-runtime-pipeline-state.test.ts",
    "apps/studio/tests/renderer/creation-service.test.ts",
    "apps/studio/tests/types/studio-protocol-v3-types.ts",
    "apps/studio/tests/main/protocol-validator-v5.test.ts",
    "tests/test_creation_scaffold_kinds.py",
    "tests/test_studio_creation_asset_jobs_v4.py",
    "tests/test_studio_creation_asset_release_v11.py",
    "tests/test_studio_creation_asset_seal_v4.py",
    "tests/test_studio_creation_evidence_v4.py",
    "tests/test_studio_creation_game_materialize_v4.py",
    "tests/test_studio_creation_game_package_extract_v4.py",
    "tests/test_studio_creation_game_package_v4.py",
    "tests/test_studio_creation_jobs_v4.py",
    "tests/test_studio_creation_materialization_bundle_v4.py",
    "tests/test_studio_creation_previews_v5.py",
    "tests/test_studio_creation_runtime_headless_v12.py",
    "tests/test_studio_creation_service_v3.py",
    "tests/test_studio_creation_v3.py",
    "tests/test_studio_protocol_v5.py",
)
REVIEWED_ADDITIONS: dict[tuple[str, str], ReviewedIdentityPolicy] = {
    (path, _LEGACY_DASHED): ReviewedIdentityPolicy(
        category="legacy_contract",
        justification="Retains the published Studio protocol or storage discriminator.",
    )
    for path in _LEGACY_CONTRACT_ADDITIONS
}
REVIEWED_ADDITIONS.update(
    {
        (path, _LEGACY_DASHED): ReviewedIdentityPolicy(
            category="regression_fixture",
            justification="Exercises retained Studio protocol and compatibility behavior.",
        )
        for path in _REGRESSION_FIXTURE_ADDITIONS
    }
)
REVIEWED_ADDITIONS[(".github/workflows/ci.yml", _LEGACY_DASHED)] = ReviewedIdentityPolicy(
    category="migration",
    justification=(
        "Restricts the hosted native authority workflow old/new repository bridge "
        "to trusted main pushes and the stable GitHub repository ID."
    ),
)
REVIEWED_ADDITIONS[("tests/test_hosted_native_release_authority.py", _LEGACY_DASHED)] = (
    ReviewedIdentityPolicy(
        category="regression_fixture",
        justification=(
            "Exercises the allowed old/new hosted repository bridge for native authority."
        ),
    )
)
REVIEWED_ADDITIONS[("src/worldforge/hosted_native_release_authority.py", _LEGACY_DASHED)] = (
    ReviewedIdentityPolicy(
        category="legacy_contract",
        justification=(
            "Binds the allowed old repository name in the hosted native authority bridge "
            "beside the future world-forge name and the stable GitHub repository ID."
        ),
    )
)
REVIEWED_ADDITIONS[("schemas/hosted-native-release-authority.schema.json", _LEGACY_DASHED)] = (
    ReviewedIdentityPolicy(
        category="legacy_contract",
        justification=(
            "Binds the allowed old repository name in the hosted native authority bridge "
            "beside the future world-forge name and the stable GitHub repository ID."
        ),
    )
)
REVIEWED_ADDITIONS[
    (
        "schemas/hosted-native-release-attestation-receipt.schema.json",
        _LEGACY_DASHED,
    )
] = ReviewedIdentityPolicy(
    category="legacy_contract",
    justification=(
        "Retains the explicit old/new hosted repository bridge for attestation receipts "
        "beside the stable GitHub repository ID."
    ),
)
REVIEWED_ADDITIONS[("scripts/verify_hosted_native_release.py", _LEGACY_DASHED)] = (
    ReviewedIdentityPolicy(
        category="migration",
        justification=(
            "Restricts the hosted receipt CLI old/new repository bridge to the "
            "trusted GitHub repository context and stable repository ID."
        ),
    )
)
REVIEWED_ADDITIONS[("docs/SUPPORT_MATRIX.md", _LEGACY_DASHED)] = ReviewedIdentityPolicy(
    category="legacy_contract",
    justification=(
        "Distinguishes retained published legacy project and runtime formats "
        "from the additive generic lane."
    ),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Regenerate exact legacy-identity file hashes and raw byte offsets."
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=ROOT,
        help="Repository source root (defaults to this checkout).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the committed evidence without rewriting it.",
    )
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        result = (
            audit_identities(arguments.source_root)
            if arguments.check
            else refresh_identity_allowlist_evidence(
                arguments.source_root,
                reviewed_policy=REVIEWED_ADDITIONS,
            )
        )
    except IdentityAuditError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"legacy identity allowlist: entries={result.entries} occurrences={result.occurrences}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
