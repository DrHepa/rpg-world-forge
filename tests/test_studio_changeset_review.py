from __future__ import annotations

import hashlib
import json
import unittest

from worldforge.studio.changeset_review import (
    ReviewDiffError,
    build_changeset_diff,
    compute_review_sha256,
)


def _operation(
    *,
    path: str = "source/lore.json",
    kind: str = "replace",
    base: bytes | None = b'{"old":1}\n',
    proposed: bytes | None = b'{"new":2}\n',
) -> tuple[dict[str, object], tuple[bytes | None, bytes | None]]:
    return (
        {
            "path": path,
            "operation": kind,
            "base_sha256": None if base is None else hashlib.sha256(base).hexdigest(),
            "base_size": 0 if base is None else len(base),
            "proposed_sha256": (None if proposed is None else hashlib.sha256(proposed).hexdigest()),
            "size": 0 if proposed is None else len(proposed),
        },
        (base, proposed),
    )


def _record(operations: list[dict[str, object]]) -> dict[str, object]:
    return {
        "format": "rpg-world-forge.studio_changeset",
        "format_version": 2,
        "changeset_id": "change_01",
        "workspace_id": "workspace_01",
        "status": "staged",
        "review_sha256": compute_review_sha256(operations),
        "operations": operations,
        "created_at": "2026-07-23T00:00:00Z",
        "updated_at": "2026-07-23T00:00:00Z",
    }


class ChangesetReviewTests(unittest.TestCase):
    def test_review_hash_is_canonical_and_operation_order_sensitive(self) -> None:
        first, _ = _operation(path="source/a.json")
        second, _ = _operation(path="source/b.json", base=b"old\n", proposed=b"new\n")
        expected_payload = {
            "format": "rpg-world-forge.studio_changeset_review",
            "format_version": 1,
            "operations": [first, second],
        }
        expected = hashlib.sha256(
            json.dumps(
                expected_payload,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        self.assertEqual(expected, compute_review_sha256([first, second]))
        self.assertNotEqual(expected, compute_review_sha256([second, first]))

    def test_diff_preserves_unicode_and_missing_final_newline(self) -> None:
        operation, payloads = _operation(
            path="source/lore.txt",
            base="old café\nlast".encode(),
            proposed="new café\nlast\n".encode(),
        )

        result = build_changeset_diff(_record([operation]), [payloads])

        self.assertTrue(result["available"])
        entry = result["operations"][0]
        self.assertIsNone(entry["json_pointer_changes"])
        changed_lines = [
            (line["kind"], line["text"])
            for hunk in entry["text_hunks"]
            for line in hunk["lines"]
            if line["kind"] != "context"
        ]
        self.assertEqual(
            [
                ("remove", "old café\n"),
                ("remove", "last"),
                ("add", "new café\n"),
                ("add", "last\n"),
            ],
            changed_lines,
        )

    def test_strict_json_diff_escapes_pointers_and_keeps_exact_text_hunks(self) -> None:
        base = b'{"a/b":{"~key":1},"same":true}\n'
        proposed = b'{"a/b":{"~key":2},"same":true}\n'
        operation, payloads = _operation(base=base, proposed=proposed)

        result = build_changeset_diff(_record([operation]), [payloads])
        entry = result["operations"][0]

        self.assertEqual(
            [
                {
                    "operation": "replace",
                    "pointer": "/a~1b/~0key",
                    "old_value": 1,
                    "value": 2,
                }
            ],
            entry["json_pointer_changes"],
        )
        self.assertEqual("remove", entry["text_hunks"][0]["lines"][0]["kind"])
        self.assertEqual(base.decode(), entry["text_hunks"][0]["lines"][0]["text"])
        self.assertEqual("add", entry["text_hunks"][0]["lines"][1]["kind"])
        self.assertEqual(proposed.decode(), entry["text_hunks"][0]["lines"][1]["text"])

    def test_ambiguous_json_falls_back_to_exact_text_and_output_never_truncates(self) -> None:
        operation, payloads = _operation(
            base=b'{"duplicate":1,"duplicate":2}\n',
            proposed=b'{"duplicate":3}\n',
        )
        record = _record([operation])

        result = build_changeset_diff(record, [payloads])
        self.assertIsNone(result["operations"][0]["json_pointer_changes"])
        with self.assertRaisesRegex(ReviewDiffError, "exceeds"):
            build_changeset_diff(record, [payloads], max_bytes=64)

    def test_strict_json_diff_preserves_exact_decimal_numbers(self) -> None:
        operation, payloads = _operation(
            base=b'{"value":9007199254740992.0}\n',
            proposed=b'{"value":9007199254740993.0}\n',
        )

        result = build_changeset_diff(_record([operation]), [payloads])

        self.assertEqual(
            [
                {
                    "operation": "replace",
                    "pointer": "/value",
                    "old_value": {"json_number": "9007199254740992"},
                    "value": {"json_number": "9007199254740993"},
                }
            ],
            result["operations"][0]["json_pointer_changes"],
        )

        precise, precise_payloads = _operation(
            base=b'{"value":0.10000000000000001}\n',
            proposed=b'{"value":0.1}\n',
        )
        precise_result = build_changeset_diff(_record([precise]), [precise_payloads])
        self.assertEqual(
            {
                "operation": "replace",
                "pointer": "/value",
                "old_value": {"json_number": "0.10000000000000001"},
                "value": {"json_number": "0.1"},
            },
            precise_result["operations"][0]["json_pointer_changes"][0],
        )

        added, added_payloads = _operation(
            base=b'{"values":[]}\n',
            proposed=b'{"values":[1.0000000000000001]}\n',
        )
        added_result = build_changeset_diff(_record([added]), [added_payloads])
        self.assertEqual(
            {
                "operation": "add",
                "pointer": "/values/0",
                "value": {"json_number": "1.0000000000000001"},
            },
            added_result["operations"][0]["json_pointer_changes"][0],
        )

        equivalent, equivalent_payloads = _operation(
            base=b'{"value":1}\n', proposed=b'{"value":1.0}\n'
        )
        equivalent_result = build_changeset_diff(_record([equivalent]), [equivalent_payloads])
        self.assertEqual([], equivalent_result["operations"][0]["json_pointer_changes"])


if __name__ == "__main__":
    unittest.main()
