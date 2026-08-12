from __future__ import annotations

import hashlib
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import ImageFont

from scripts.generate_generic_asset_fixtures import (
    NARRATIVE_FONT_FIXTURE_STRINGS,
    NARRATIVE_FONT_RENDERED_MASK_SHA256,
    _narrative_qa_evidence,
    _narrative_rendered_mask_manifest,
    _narrative_ttf,
    build_fixture_documents,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "multigenre-contracts"
PRINTABLE_ASCII = tuple(chr(codepoint) for codepoint in range(0x20, 0x7F))
CRITICAL_PAIRS = ("O0", "Il", "I1", "l1", "S5", "B8", "Z2", "G6")


def _tables(payload: bytes) -> dict[str, tuple[int, int]]:
    count = struct.unpack_from(">H", payload, 4)[0]
    return {
        tag.decode("ascii"): (offset, length)
        for index in range(count)
        for tag, _checksum, offset, length in [
            struct.unpack_from(">4sIII", payload, 12 + index * 16)
        ]
    }


def _format4_mapping(payload: bytes, tables: dict[str, tuple[int, int]]) -> dict[int, int]:
    cmap_offset, _cmap_length = tables["cmap"]
    _version, count = struct.unpack_from(">HH", payload, cmap_offset)
    for index in range(count):
        platform, encoding, relative = struct.unpack_from(
            ">HHI", payload, cmap_offset + 4 + index * 8
        )
        subtable = cmap_offset + relative
        if platform == 3 and encoding == 1 and struct.unpack_from(">H", payload, subtable)[0] == 4:
            break
    else:
        raise AssertionError("font has no Windows Unicode BMP format 4 cmap")
    length = struct.unpack_from(">H", payload, subtable + 2)[0]
    seg_count_x2 = struct.unpack_from(">H", payload, subtable + 6)[0]
    self_contained_end = subtable + length
    if self_contained_end > len(payload):
        raise AssertionError("format 4 cmap exceeds font bytes")
    segment_count = seg_count_x2 // 2
    end_offset = subtable + 14
    start_offset = end_offset + segment_count * 2 + 2
    delta_offset = start_offset + segment_count * 2
    range_offset = delta_offset + segment_count * 2
    ends = struct.unpack_from(f">{segment_count}H", payload, end_offset)
    starts = struct.unpack_from(f">{segment_count}H", payload, start_offset)
    deltas = struct.unpack_from(f">{segment_count}H", payload, delta_offset)
    ranges = struct.unpack_from(f">{segment_count}H", payload, range_offset)
    mapping: dict[int, int] = {}
    for segment, (start, end, delta, relative) in enumerate(
        zip(starts, ends, deltas, ranges, strict=True)
    ):
        if start == end == 0xFFFF:
            continue
        for codepoint in range(start, end + 1):
            if relative == 0:
                glyph_id = (codepoint + delta) & 0xFFFF
            else:
                glyph_word = range_offset + segment * 2 + relative + (codepoint - start) * 2
                glyph_id = struct.unpack_from(">H", payload, glyph_word)[0]
                if glyph_id:
                    glyph_id = (glyph_id + delta) & 0xFFFF
            mapping[codepoint] = glyph_id
    return mapping


def _glyph_bytes(
    payload: bytes,
    tables: dict[str, tuple[int, int]],
    glyph_id: int,
) -> bytes:
    head_offset, _head_length = tables["head"]
    loca_offset, _loca_length = tables["loca"]
    glyf_offset, _glyf_length = tables["glyf"]
    loca_format = struct.unpack_from(">h", payload, head_offset + 50)[0]
    if loca_format == 0:
        start, end = (
            value * 2 for value in struct.unpack_from(">2H", payload, loca_offset + glyph_id * 2)
        )
    else:
        start, end = struct.unpack_from(">2I", payload, loca_offset + glyph_id * 4)
    return payload[glyf_offset + start : glyf_offset + end]


def _mask(font: ImageFont.FreeTypeFont, text: str) -> tuple[tuple[int, int], bytes]:
    rendered = font.getmask(text, mode="L")
    return rendered.size, bytes(rendered)


def _fixture_strings() -> tuple[str, ...]:
    gamepack = json.loads(
        (
            EXAMPLES / "branching-narrative" / "artifacts" / "branching-narrative.gamepack.json"
        ).read_text(encoding="utf-8")
    )
    found: set[str] = set()
    stack: list[object] = [gamepack]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key in {"description", "label", "title"} and isinstance(value, str):
                    found.add(value)
                stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)
    return tuple(sorted(found, key=lambda value: value.encode("utf-8")))


class NarrativeFixtureFontTests(unittest.TestCase):
    def test_rendered_mask_evidence_requires_the_audited_pillow_version(self) -> None:
        import PIL

        with (
            mock.patch.object(PIL, "__version__", "12.3.1"),
            self.assertRaisesRegex(ValueError, "pinned Pillow 12.3.0"),
        ):
            _narrative_rendered_mask_manifest(_narrative_ttf())

    def test_printable_ascii_has_distinct_glyph_ids_and_sane_outlines(self) -> None:
        payload = _narrative_ttf()
        tables = _tables(payload)
        mapping = _format4_mapping(payload, tables)

        self.assertEqual(set(mapping), set(range(0x20, 0x7F)))
        glyph_ids = [mapping[codepoint] for codepoint in range(0x20, 0x7F)]
        self.assertEqual(len(glyph_ids), len(set(glyph_ids)))
        self.assertNotEqual(mapping[0x20], 0)

        space = _glyph_bytes(payload, tables, mapping[0x20])
        self.assertIn(len(space), {0, 12})
        if space:
            self.assertEqual(struct.unpack_from(">h", space)[0], 0)
        for character in PRINTABLE_ASCII[1:]:
            glyph = _glyph_bytes(payload, tables, mapping[ord(character)])
            self.assertGreaterEqual(len(glyph), 20, character)
            contour_count, x_min, y_min, x_max, y_max = struct.unpack_from(">hhhhh", glyph)
            self.assertGreater(contour_count, 0, character)
            self.assertGreater(x_max, x_min, character)
            self.assertGreater(y_max, y_min, character)

        head_offset, _head_length = tables["head"]
        hhea_offset, _hhea_length = tables["hhea"]
        name_offset, name_length = tables["name"]
        post_offset, _post_length = tables["post"]
        self.assertEqual(struct.unpack_from(">I", payload, head_offset + 4)[0], 0x0001199A)
        self.assertIn(
            "Version 1.1.0".encode("utf-16-be"),
            payload[name_offset : name_offset + name_length],
        )
        self.assertEqual(struct.unpack_from(">h", payload, hhea_offset + 14)[0], 70)
        self.assertEqual(struct.unpack_from(">I", payload, post_offset + 12)[0], 1)

    def test_rendered_ascii_masks_are_readable_and_critical_pairs_are_distinct(self) -> None:
        font = ImageFont.truetype(io.BytesIO(_narrative_ttf()), 32)
        space_size, space_mask = _mask(font, " ")
        self.assertGreater(space_size[0], 0)
        self.assertFalse(any(space_mask))

        masks = {character: _mask(font, character) for character in PRINTABLE_ASCII[1:]}
        self.assertEqual(len(masks), 94)
        for character, (size, mask) in masks.items():
            self.assertGreater(size[0], 0, character)
            self.assertGreater(size[1], 0, character)
            self.assertTrue(any(mask), character)
        self.assertEqual(len(set(masks.values())), len(masks))
        for left, right in CRITICAL_PAIRS:
            self.assertNotEqual(masks[left], masks[right], f"{left}/{right}")

    def test_every_branching_fixture_string_has_a_stable_nonblank_rendered_mask(self) -> None:
        fixture_strings = _fixture_strings()
        self.assertEqual(fixture_strings, NARRATIVE_FONT_FIXTURE_STRINGS)
        font = ImageFont.truetype(io.BytesIO(_narrative_ttf()), 24)
        hashes: dict[str, str] = {}
        for text in fixture_strings:
            size, mask = _mask(font, text)
            self.assertGreater(size[0], 0, text)
            self.assertGreater(size[1], 0, text)
            self.assertTrue(any(mask), text)
            hashes[text] = hashlib.sha256(struct.pack(">II", *size) + mask).hexdigest()
        self.assertEqual(hashes, NARRATIVE_FONT_RENDERED_MASK_SHA256)
        self.assertEqual(len(hashes), len(set(hashes.values())))

    def test_font_and_full_fixture_lineage_are_deterministic_across_roots(self) -> None:
        first = _narrative_ttf()
        second = _narrative_ttf()
        self.assertEqual(first, second)
        with (
            tempfile.TemporaryDirectory(prefix="world-forge-font-a-") as first_root,
            tempfile.TemporaryDirectory(prefix="world-forge-font-b-") as second_root,
        ):
            first_documents = build_fixture_documents(
                "branching-narrative",
                artifact_root=Path(first_root),
            )
            second_documents = build_fixture_documents(
                "branching-narrative",
                artifact_root=Path(second_root),
            )
        self.assertEqual(
            [
                (path.relative_to(ROOT).as_posix(), payload)
                for path, _document, payload in first_documents
            ],
            [
                (path.relative_to(ROOT).as_posix(), payload)
                for path, _document, payload in second_documents
            ],
        )
        documents = {
            path.relative_to(ROOT).as_posix(): document
            for path, document, _payload in first_documents
            if document is not None
        }
        specification = documents[
            "examples/multigenre-contracts/branching-narrative/assets/specs/narrative_ui_font.json"
        ]
        request = documents[
            "examples/multigenre-contracts/branching-narrative/assets/"
            "production/narrative_ui_font/request.json"
        ]
        receipt = documents[
            "examples/multigenre-contracts/branching-narrative/assets/"
            "production/narrative_ui_font/receipt.json"
        ]
        provenance = documents[
            "examples/multigenre-contracts/branching-narrative/assets/"
            "production/narrative_ui_font/provenance.json"
        ]
        license_record = documents[
            "examples/multigenre-contracts/branching-narrative/assets/"
            "production/narrative_ui_font/license.json"
        ]
        qa_report = documents[
            "examples/multigenre-contracts/branching-narrative/assets/"
            "production/narrative_ui_font/qa-report.json"
        ]
        self.assertEqual(len(specification["acceptance_criteria"]), 6)
        self.assertEqual(request["operation"]["version"], 2)
        self.assertEqual(request["toolchain_requirements"]["tool_version"], "1.1.0")
        self.assertEqual(receipt["executed_toolchain"]["tool_version"], "1.1.0")
        self.assertEqual(receipt["outputs"][0]["metadata"]["glyph_count"], 95)
        expected_evidence = _narrative_qa_evidence(first)
        self.assertEqual(
            receipt["execution_evidence"]["sanitized_log_hashes"],
            sorted(expected_evidence),
        )
        self.assertEqual(
            [result["evidence_hashes"][0] for result in qa_report["acceptance_criteria"]],
            list(expected_evidence),
        )
        self.assertEqual(
            provenance["components"][0]["component_version"],
            "1.1.0",
        )
        self.assertEqual(
            license_record["runtime_notice"]["text"],
            (
                "World Forge Tiny Fixture glyph designs and generated font bytes "
                "are project-authored and dedicated to the public domain under CC0-1.0."
            ),
        )


if __name__ == "__main__":
    unittest.main()
