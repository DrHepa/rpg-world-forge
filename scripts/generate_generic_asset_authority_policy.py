"""Generate the exact code-owned authority policy for canonical multigenre fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.generate_generic_asset_fixtures import (
    CASES,
    KNOWN_FIXTURE_GAMEPACK_HASHES,
    build_fixture_documents,
)
from worldforge.gamepack import load_gamepack, serialize_gamepack
from worldforge.generic_asset_authority import (
    ASSET_QA_REVIEW_RECEIPT_FORMAT,
    ASSET_RELEASE_AUTHORITY_FORMAT,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "multigenre-contracts"
TARGET = ROOT / "src" / "worldforge" / "generic_asset_fixture_policy.py"


def _identity(document: dict[str, Any], id_field: str) -> dict[str, Any]:
    return {
        "format": document["format"],
        "format_version": document["format_version"],
        "id": document[id_field],
        "content_hash": document["content_hash"],
    }


def _payload_record(case_root: Path, path: Path, payload: bytes) -> dict[str, Any]:
    return {
        "path": path.relative_to(case_root).as_posix(),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def _one_document(
    documents: list[tuple[Path, dict[str, Any], bytes]],
    identity: dict[str, Any],
) -> tuple[Path, dict[str, Any], bytes]:
    matches = [
        item
        for item in documents
        if item[1].get("format") == identity["format"]
        and item[1].get("format_version") == identity["format_version"]
        and item[1].get("content_hash") == identity["content_hash"]
        and identity["id"] in item[1].values()
    ]
    if len(matches) != 1:
        raise ValueError(f"authority identity did not resolve exactly once: {identity}")
    return matches[0]


def build_policy() -> dict[str, Any]:
    policy: dict[str, Any] = {}
    for case in CASES:
        case_root = EXAMPLES / case
        generated = build_fixture_documents(case)
        gamepack_path = case_root / "artifacts" / f"{case}.gamepack.json"
        gamepack_payload = serialize_gamepack(load_gamepack(gamepack_path))
        source_closure = sorted(
            [
                *(
                    _payload_record(case_root, path, payload)
                    for path, _document, payload in generated
                ),
                _payload_record(case_root, gamepack_path, gamepack_payload),
            ],
            key=lambda item: str(item["path"]).encode("utf-8"),
        )
        documents = [
            (path, document, payload)
            for path, document, payload in generated
            if document is not None
        ]
        binaries = {path: payload for path, document, payload in generated if document is None}
        manifest_items = [
            item for item in documents if item[1].get("format") == "world-forge.asset_manifest"
        ]
        release_items = [
            item for item in documents if item[1].get("format") == ASSET_RELEASE_AUTHORITY_FORMAT
        ]
        review_items = [
            item for item in documents if item[1].get("format") == ASSET_QA_REVIEW_RECEIPT_FORMAT
        ]
        if len(manifest_items) != 1 or len(release_items) != 1 or not review_items:
            raise ValueError(f"{case} authority output closure is incomplete")
        manifest_path, manifest, manifest_payload = manifest_items[0]
        release_path, release, release_payload = release_items[0]
        reviews: list[dict[str, Any]] = []
        for review_path, review, review_payload in sorted(
            review_items,
            key=lambda item: str(item[1]["review_receipt_id"]).encode("utf-8"),
        ):
            sources: dict[str, Any] = {}
            for field in ("specification", "processing_receipt", "qa_report"):
                source_path, source, source_payload = _one_document(
                    documents,
                    review["lineage"][field],
                )
                sources[field] = {
                    **_payload_record(case_root, source_path, source_payload),
                    "identity": review["lineage"][field],
                }
            output_path = case_root / review["reviewed_output"]["locator"]
            try:
                output_payload = binaries[output_path]
            except KeyError as exc:
                raise ValueError(f"{case} retained reviewed output is absent") from exc
            reviews.append(
                {
                    **_payload_record(case_root, review_path, review_payload),
                    "identity": _identity(review, "review_receipt_id"),
                    "authority": review["authority"],
                    "sources": sources,
                    "retained_output": _payload_record(
                        case_root,
                        output_path,
                        output_payload,
                    ),
                }
            )
        policy[case] = {
            "gamepack_content_hash": KNOWN_FIXTURE_GAMEPACK_HASHES[case],
            "source_closure": source_closure,
            "manifest": {
                **_payload_record(case_root, manifest_path, manifest_payload),
                "identity": _identity(manifest, "manifest_id"),
            },
            "assetpack_identity": release["candidate_assetpack"],
            "reviews": reviews,
            "release": {
                **_payload_record(case_root, release_path, release_payload),
                "identity": _identity(release, "release_authority_id"),
                "authority": release["authority"],
            },
        }
    return policy


def build_policy_module() -> bytes:
    rendered = json.dumps(
        build_policy(),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    chunks = [rendered[index : index + 76] for index in range(0, len(rendered), 76)]
    literal = "\n".join(f"    {chunk!r}" for chunk in chunks)
    return (
        '"""AUTO-GENERATED exact generic asset fixture authority policy."""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import json\n"
        "\n"
        "_POLICY_JSON = (\n"
        f"{literal}\n"
        ")\n"
        "\n"
        "REPOSITORY_FIXTURE_ASSET_AUTHORITY_POLICY = json.loads(_POLICY_JSON)\n"
        "\n"
        '__all__ = ["REPOSITORY_FIXTURE_ASSET_AUTHORITY_POLICY"]\n'
    ).encode()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    payload = build_policy_module()
    if args.check:
        if not TARGET.is_file() or TARGET.read_bytes() != payload:
            raise SystemExit(f"{TARGET.relative_to(ROOT)} is out of date")
        return 0
    TARGET.write_bytes(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
