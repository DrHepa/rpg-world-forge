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
    _NARRATIVE_FONT_ADVANCE,
    _NARRATIVE_GLYPH_ROWS,
    NARRATIVE_FONT_DESIGN_MASK_SHA256,
    NARRATIVE_FONT_DESIGN_MASK_VERSION,
    NARRATIVE_FONT_FIXTURE_STRINGS,
    _narrative_design_mask_evidence_manifest,
    _narrative_pillow_basic_smoke,
    _narrative_qa_evidence,
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
    def test_design_mask_vectors_are_literal_and_do_not_recompute_from_source(self) -> None:
        source = (ROOT / "scripts" / "generate_generic_asset_fixtures.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(
            "NARRATIVE_FONT_DESIGN_MASK_SHA256 = _narrative_design_mask_sha256()",
            source,
        )
        self.assertEqual(
            NARRATIVE_FONT_DESIGN_MASK_SHA256,
            {
                "A visible choice": (
                    "00d87e226f12d354144a3e7e4f63b93d7d201acb0b380410b9f319ce0bb6666e"
                ),
                "Branching Narrative": (
                    "edf34c5706e06229a621efff28e10345c9bf99541620aa009ee88d853d8a91f7"
                ),
                "Choose the left symbol": (
                    "3e8e9bcc199067d231a03ea72410833128b95c0593e526dda5271ee47d6dc5c2"
                ),
                "Choose the right symbol": (
                    "3700c0f607fcc756a744af1be67c9c4941fbfe751f61ec863c06e340da7edcbc"
                ),
                "Left ending": "1255b70e4150c7d2ce9bea4cfca50bd65516361929a69152e9ffdb63de97a13d",
                "Neutral authored branching-choice logic": (
                    "2ef090ee70898fbaef53009f3a73f5a4fb8cc8e19f287fe62e7c2bc1fcd49ca2"
                ),
                "Neutral branching units": (
                    "60e874a8c8ecfc1be7964f05ddecb2f35900c86d8f42e976d515020415b4eee9"
                ),
                "Right ending": "8d901f2a2d6fb66991ae55d1268764f2c060ba66f90d4cc1a0cba7c26f6865f0",
                "Select one authored option.": (
                    "df81b0f68e8f98802bf4874eaca3989b2e8fb827ae1311df6b2e03570bb63e5f"
                ),
            },
        )

    def test_release_evidence_uses_versioned_design_masks_not_pillow_rasters(self) -> None:
        payload = _narrative_ttf()
        baseline = _narrative_qa_evidence(payload)
        self.assertEqual(NARRATIVE_FONT_DESIGN_MASK_VERSION, "narrative-design-mask-v2")
        self.assertIn(
            _narrative_design_mask_evidence_manifest(payload),
            baseline,
        )
        with (
            mock.patch("PIL.ImageFont.FreeTypeFont.getmask", side_effect=AssertionError("raster")),
            mock.patch(
                "scripts.generate_generic_asset_fixtures._narrative_pillow_basic_smoke",
                return_value={"layout_engine": "BASIC"},
            ),
        ):
            self.assertEqual(_narrative_qa_evidence(payload), baseline)

    def test_pillow_basic_smoke_uses_explicit_basic_and_is_not_hash_authoritative(self) -> None:
        captured: dict[str, object] = {}
        original_truetype = ImageFont.truetype

        def capture_layout(*args: object, **kwargs: object) -> ImageFont.FreeTypeFont:
            captured["layout_engine"] = kwargs.get("layout_engine")
            return original_truetype(*args, **kwargs)

        with mock.patch("PIL.ImageFont.truetype", side_effect=capture_layout):
            smoke = _narrative_pillow_basic_smoke(_narrative_ttf())
        self.assertIs(captured["layout_engine"], ImageFont.Layout.BASIC)
        self.assertEqual(smoke["layout_engine"], "BASIC")
        self.assertEqual(smoke["fixture_count"], len(NARRATIVE_FONT_FIXTURE_STRINGS))
        self.assertGreater(smoke["max_width"], smoke["min_width"])

    def test_raqm_availability_and_default_layout_cannot_change_release_evidence(self) -> None:
        payload = _narrative_ttf()
        baseline = _narrative_qa_evidence(payload)
        with mock.patch.object(ImageFont.core, "HAVE_RAQM", not ImageFont.core.HAVE_RAQM):
            self.assertEqual(_narrative_qa_evidence(payload), baseline)

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

    def test_every_branching_fixture_string_has_a_stable_nonblank_design_mask(self) -> None:
        fixture_strings = _fixture_strings()
        self.assertEqual(fixture_strings, NARRATIVE_FONT_FIXTURE_STRINGS)
        hashes: dict[str, str] = {}
        for text in fixture_strings:
            width = len(text) * _NARRATIVE_FONT_ADVANCE
            height = 7
            mask = b"\n".join(
                b"0".join(
                    _NARRATIVE_GLYPH_ROWS[character][row].encode("ascii") for character in text
                )
                for row in range(height)
            )
            self.assertEqual((width, height), (len(text) * _NARRATIVE_FONT_ADVANCE, 7))
            self.assertIn(b"1", mask, text)
            hashes[text] = hashlib.sha256(struct.pack(">II", width, height) + mask).hexdigest()
        self.assertEqual(hashes, NARRATIVE_FONT_DESIGN_MASK_SHA256)
        self.assertEqual(len(hashes), len(set(hashes.values())))

    def test_release_evidence_fails_on_glyph_cmap_advance_fixture_or_missing_smoke(self) -> None:
        payload = _narrative_ttf()
        with mock.patch.dict(
            _NARRATIVE_GLYPH_ROWS,
            {"A": ("11111", "10001", "11111", "10001", "10001", "10001", "10001")},
        ):
            with self.assertRaisesRegex(ValueError, "design masks"):
                _narrative_qa_evidence(_narrative_ttf())
        mutated_cmap = bytearray(payload)
        cmap_offset, _length = _tables(payload)["cmap"]
        mutated_cmap[cmap_offset + 31] ^= 0x01
        with self.assertRaisesRegex(ValueError, "generated TTF source"):
            _narrative_qa_evidence(bytes(mutated_cmap))
        with mock.patch("scripts.generate_generic_asset_fixtures._NARRATIVE_FONT_ADVANCE", 601):
            with self.assertRaisesRegex(ValueError, "design masks"):
                _narrative_qa_evidence(_narrative_ttf())
        with mock.patch(
            "scripts.generate_generic_asset_fixtures.NARRATIVE_FONT_FIXTURE_STRINGS",
            (*NARRATIVE_FONT_FIXTURE_STRINGS, "Added fixture ☃"),
        ):
            with self.assertRaisesRegex(ValueError, "fixture character coverage"):
                _narrative_qa_evidence(payload)
        with mock.patch(
            "scripts.generate_generic_asset_fixtures._narrative_pillow_basic_smoke",
            side_effect=AssertionError("smoke removed"),
        ):
            with self.assertRaisesRegex(AssertionError, "smoke removed"):
                _narrative_qa_evidence(payload)

    def test_release_evidence_fail_closes_on_all_printable_source_shape(self) -> None:
        payload = _narrative_ttf()
        with mock.patch.dict(
            _NARRATIVE_GLYPH_ROWS,
            {"~": ("00000", "00000", "00000", "00000", "00000", "00000", "00000")},
        ):
            with self.assertRaisesRegex(ValueError, r"non-space glyph source is blank: U\+007E"):
                _narrative_qa_evidence(_narrative_ttf())
        missing_tilde = dict(_NARRATIVE_GLYPH_ROWS)
        del missing_tilde["~"]
        with mock.patch.dict(_NARRATIVE_GLYPH_ROWS, missing_tilde, clear=True):
            with self.assertRaisesRegex(ValueError, "printable ASCII"):
                _narrative_qa_evidence(payload)
        with mock.patch.dict(_NARRATIVE_GLYPH_ROWS, {"~": ("00000",) * 6}):
            with self.assertRaisesRegex(ValueError, "exact 5x7"):
                _narrative_qa_evidence(payload)

    def test_release_evidence_fail_closes_on_ttf_hmtx_glyf_and_checksum_drift(self) -> None:
        payload = _narrative_ttf()
        tables = _tables(payload)
        hmtx_offset, _hmtx_length = tables["hmtx"]
        mutated_hmtx = bytearray(payload)
        mutated_hmtx[hmtx_offset] ^= 0x01
        with self.assertRaisesRegex(ValueError, "generated TTF source"):
            _narrative_qa_evidence(bytes(mutated_hmtx))

        mapping = _format4_mapping(payload, tables)
        glyph = mapping[ord("~")]
        glyf_offset, _glyf_length = tables["glyf"]
        glyph_start = glyf_offset + len(_glyph_bytes(payload, tables, 0))
        for glyph_id in range(1, glyph):
            glyph_start += len(_glyph_bytes(payload, tables, glyph_id))
        mutated_glyf = bytearray(payload)
        mutated_glyf[glyph_start + 10] ^= 0x01
        with self.assertRaisesRegex(ValueError, "generated TTF source"):
            _narrative_qa_evidence(bytes(mutated_glyf))

        head_offset, _head_length = tables["head"]
        mutated_checksum = bytearray(payload)
        mutated_checksum[head_offset + 8] ^= 0x01
        with self.assertRaisesRegex(ValueError, "generated TTF source"):
            _narrative_qa_evidence(bytes(mutated_checksum))

    def test_valid_nonfixture_glyph_revision_rotates_evidence_without_direct_rejection(
        self,
    ) -> None:
        source = (ROOT / "scripts" / "generate_generic_asset_fixtures.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("valid authored glyph revisions rotate evidence", source)
        baseline_ttf = _narrative_ttf()
        baseline_evidence = _narrative_qa_evidence(baseline_ttf)
        baseline_design = _narrative_design_mask_evidence_manifest(baseline_ttf)
        pinned_vectors = dict(NARRATIVE_FONT_DESIGN_MASK_SHA256)
        revised_tilde = ("00000", "00000", "10010", "01101", "00000", "00000", "00000")
        with mock.patch.dict(_NARRATIVE_GLYPH_ROWS, {"~": revised_tilde}):
            revised_ttf = _narrative_ttf()
            revised_evidence = _narrative_qa_evidence(revised_ttf)
            revised_design = _narrative_design_mask_evidence_manifest(revised_ttf)
            self.assertNotEqual(revised_ttf, baseline_ttf)
            self.assertNotEqual(revised_evidence, baseline_evidence)
            self.assertNotEqual(revised_design, baseline_design)
            self.assertEqual(NARRATIVE_FONT_DESIGN_MASK_SHA256, pinned_vectors)
            with tempfile.TemporaryDirectory(prefix="world-forge-font-drift-") as temp:
                generated = build_fixture_documents(
                    "branching-narrative",
                    artifact_root=Path(temp),
                )
        drifted = [
            path.relative_to(ROOT).as_posix()
            for path, _document, payload in generated
            if path.exists() and path.read_bytes() != payload
        ]
        self.assertIn(
            "examples/multigenre-contracts/branching-narrative/assets/production/"
            "narrative_ui_font/candidates/narrative-ui.ttf",
            drifted,
        )
        self.assertIn(
            "examples/multigenre-contracts/branching-narrative/assets/manifest.json",
            drifted,
        )

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
