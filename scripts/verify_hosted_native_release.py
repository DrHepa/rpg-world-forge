#!/usr/bin/env python3
"""Build and verify hosted-native release attestation receipts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (_REPO_ROOT, _REPO_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from worldforge.hosted_native_release_authority import (  # noqa: E402
    HostedNativeReleaseAuthorityError,
    build_hosted_native_release_authority,
    derive_hosted_native_release_authority,
    load_hosted_native_release_authority,
    serialize_hosted_native_release_authority,
    verify_attested_hosted_native_release_authority_file,
    verify_hosted_native_aggregate,
    verify_hosted_native_row_file,
    write_hosted_native_attestation_receipt,
)
from worldforge.integrity import canonical_json_bytes  # noqa: E402

_ALLOWED_REPOS = {"DrHepa/rpg-world-forge", "DrHepa/world-forge"}


def _source_from_github_env() -> dict[str, object]:
    env = os.environ
    repo = env.get("GITHUB_REPOSITORY", "")
    ref = env.get("GITHUB_REF", "")
    event = env.get("GITHUB_EVENT_NAME", "")
    revision = env.get("GITHUB_SHA", "")
    workflow_ref = env.get("GITHUB_WORKFLOW_REF", "")
    workflow_sha = env.get("GITHUB_WORKFLOW_SHA", revision)
    run_id = env.get("GITHUB_RUN_ID", "")
    attempt_raw = env.get("GITHUB_RUN_ATTEMPT", "")
    repo_id = env.get("GITHUB_REPOSITORY_ID", "")
    if (
        repo_id != "1305601753"
        or repo not in _ALLOWED_REPOS
        or ref != "refs/heads/main"
        or event != "push"
    ):
        raise HostedNativeReleaseAuthorityError(
            "hosted_native_source_invalid", "not the hosted main push repository context"
        )
    if (
        workflow_ref != f"{repo}/.github/workflows/ci.yml@refs/heads/main"
        or workflow_sha != revision
    ):
        raise HostedNativeReleaseAuthorityError(
            "hosted_native_source_invalid", "workflow context is not pinned to this revision"
        )
    try:
        attempt = int(attempt_raw)
    except ValueError as exc:
        raise HostedNativeReleaseAuthorityError(
            "hosted_native_source_invalid", "run attempt is invalid"
        ) from exc
    input_tree_hash = env.get("WORLD_FORGE_INPUT_TREE_HASH", "")
    return {
        "repository_id": repo_id,
        "repository": repo,
        "workflow_ref": workflow_ref,
        "workflow_sha": workflow_sha,
        "revision": revision,
        "input_tree_hash": input_tree_hash,
        "ref": ref,
        "event": event,
        "run_id": run_id,
        "run_attempt": attempt,
    }


def _write_stdout(document: object) -> None:
    sys.stdout.buffer.write(canonical_json_bytes(document))


def _cmd_build_candidate(args: argparse.Namespace) -> int:
    rows = [verify_hosted_native_row_file(path) for path in args.rows]
    aggregate = verify_hosted_native_aggregate(rows)
    document = build_hosted_native_release_authority(aggregate, source=_source_from_github_env())
    payload = serialize_hosted_native_release_authority(document)
    if args.output:
        Path(args.output).write_bytes(payload)
        _write_stdout(
            {"ok": True, "authority_id": document["authority_id"], "output": str(args.output)}
        )
    else:
        sys.stdout.buffer.write(payload)
    return 0


def _cmd_verify_write_receipt(args: argparse.Namespace) -> int:
    rows = [verify_hosted_native_row_file(path) for path in args.rows]
    aggregate = verify_hosted_native_aggregate(rows)
    candidate_document = load_hosted_native_release_authority(args.candidate)
    candidate = derive_hosted_native_release_authority(aggregate, candidate_document)
    receipt = write_hosted_native_attestation_receipt(
        candidate,
        authority_path=args.candidate,
        bundle_path=args.bundle,
        gh_archive_path=args.gh_archive,
        gh_path=args.gh,
        receipt_path=args.receipt,
        attestation_id=args.attestation_id,
        attestation_url=args.attestation_url,
    )
    _write_stdout({"ok": True, "receipt_id": receipt["receipt_id"], "receipt": str(args.receipt)})
    return 0


def _cmd_reverify(args: argparse.Namespace) -> int:
    rows = [verify_hosted_native_row_file(path) for path in args.rows]
    aggregate = verify_hosted_native_aggregate(rows)
    candidate_document = load_hosted_native_release_authority(args.candidate)
    candidate = derive_hosted_native_release_authority(aggregate, candidate_document)
    authority = verify_attested_hosted_native_release_authority_file(
        candidate, args.receipt, args.gh_archive, args.gh
    )
    _write_stdout({"ok": True, "authority_id": authority.document["authority_id"]})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-candidate")
    build.add_argument("--row", dest="rows", action="append", required=True, type=Path)
    build.add_argument("--output", type=Path)
    build.set_defaults(func=_cmd_build_candidate)
    write = sub.add_parser("verify-write-receipt")
    write.add_argument("--row", dest="rows", action="append", required=True, type=Path)
    write.add_argument("--candidate", required=True, type=Path)
    write.add_argument("--bundle", required=True, type=Path)
    write.add_argument("--gh-archive", required=True, type=Path)
    write.add_argument("--gh", required=True, type=Path)
    write.add_argument("--receipt", required=True, type=Path)
    write.add_argument("--attestation-id", required=True)
    write.add_argument("--attestation-url", required=True)
    write.set_defaults(func=_cmd_verify_write_receipt)
    reverify = sub.add_parser("reverify")
    reverify.add_argument("--row", dest="rows", action="append", required=True, type=Path)
    reverify.add_argument("--candidate", required=True, type=Path)
    reverify.add_argument("--receipt", required=True, type=Path)
    reverify.add_argument("--gh-archive", required=True, type=Path)
    reverify.add_argument("--gh", required=True, type=Path)
    reverify.set_defaults(func=_cmd_reverify)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except HostedNativeReleaseAuthorityError as exc:
        sys.stdout.write(json.dumps({"ok": False, "reason_code": exc.reason_code}) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
