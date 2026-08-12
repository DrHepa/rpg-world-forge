from __future__ import annotations

import argparse
import binascii
import hashlib
import io
import json
import os
import stat
import struct
import tempfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.generate_m5_neutral import sfnt_checksum
from worldforge.asset_io import (
    prepare_output_path,
    read_json_object,
    write_json_atomic,
    write_json_cooperative_replace,
)
from worldforge.creation_contracts import canonical_creation_hash
from worldforge.gamepack import load_gamepack
from worldforge.generic_asset_authority import (
    GenericAssetAuthorityError,
    RetainedAssetQaReviewRecord,
    RetainedAssetReleaseAuthorityRecord,
    VerifiedAssetQaReview,
    VerifiedAssetReleaseAuthority,
    build_asset_qa_review_receipt,
    build_asset_release_authority,
    serialize_asset_qa_review_receipt,
    serialize_asset_release_authority,
    verify_asset_qa_review,
    verify_asset_release_authority,
)
from worldforge.generic_asset_processing import (
    build_asset_manifest,
    build_asset_processing_receipt,
    build_asset_processing_recipe,
    build_asset_qa_report,
    serialize_asset_processing_contract,
)
from worldforge.generic_asset_production import (
    GenericAssetProductionError,
    _safe_artifact_bytes,
    build_asset_license_record,
    build_asset_production_receipt,
    build_asset_production_request,
    build_asset_provenance_record,
    build_asset_selection,
    serialize_production_contract,
)
from worldforge.generic_assetpack import build_generic_assetpack_manifest
from worldforge.generic_assets import (
    build_asset_inventory,
    build_asset_specification,
    build_asset_style,
    build_asset_subject,
    build_asset_target,
    serialize_asset_contract,
)
from worldforge.integrity import canonical_json_bytes
from worldforge.studio.changesets import (
    _open_pinned_parent,
    _reject_pinned_collision,
    _safe_entry_snapshot,
)
from worldforge.studio.errors import StudioError
from worldforge.studio.workspaces import _pinned_ancestor_identities

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "multigenre-contracts"
CASES = (
    "abstract-puzzle",
    "branching-narrative",
    "action-framing",
    "faction-strategy",
    "modular-roguelite",
    "sports-career",
)
KNOWN_FIXTURE_GAMEPACK_HASHES = {
    "abstract-puzzle": "0510d69d0f78d3e80810aa26dd4b76752416809f7733e731274ac8d7f35dac09",
    "branching-narrative": "56b8a5393615603ca3a6bbc1a55cf557cadee2e05cf03a8b4714b4536e6cb7b7",
    "action-framing": "cadf114a3e2ed9a74d8664137c2a09888e5383468fbb98e9ded7bd79acbca506",
    "faction-strategy": "7a1f8b4f8436a73a4df5653987061d7f965abc9248f45367c928a87e1110bf63",
    "modular-roguelite": "73a63d1dfc360893198abfdb1502f94595f08570aaead157d531f6cf80cfa8ea",
    "sports-career": "3a1d776f9d6404fb34bf851621d3d3989ac5a2b995c536737958c07b11b507e3",
}
ASSET_FIXTURES: dict[str, dict[str, Any]] = {
    "abstract-puzzle": {
        "asset_id": "board_ui",
        "binding_ids": ("board_texture",),
        "kind": "ui",
        "role": "texture",
        "media_type": "image/png",
        "selected_format": "asset:png",
        "runtime_path": "assets/ui/board.png",
        "candidate_name": "board.png",
        "review_profile": "puzzle_readability",
        "qa_profile": "ui_readability",
        "seed": 20260727,
        "qa_evidence": "5" * 64,
        "selection_evidence": "7" * 64,
        "provenance_evidence": "8" * 64,
        "component_license_evidence": "9" * 64,
        "license_evidence": "a" * 64,
        "started_evidence": "3" * 64,
        "completed_evidence": "4" * 64,
        "rights_evidence": "6" * 64,
        "notice": "Puzzle board fixture is dedicated to the public domain under CC0-1.0.",
        "acceptance_criteria": (
            "Every board symbol remains distinguishable without color.",
            "The exact runtime output matches the reviewed target.",
        ),
    },
    "branching-narrative": {
        "asset_id": "narrative_ui_font",
        "binding_ids": ("choice_panel", "ending_panel"),
        "kind": "font",
        "role": "font",
        "media_type": "font/ttf",
        "selected_format": "asset:font",
        "runtime_path": "assets/fonts/narrative-ui.ttf",
        "candidate_name": "narrative-ui.ttf",
        "review_profile": "localized_text",
        "qa_profile": "localized_text",
        "acceptance_criteria": (
            "Choice and ending text use the same reviewed font bytes.",
            "Critical pairs O/0, I/l/1, S/5, B/8, Z/2 and G/6 remain visually distinct.",
            "Every non-space printable ASCII glyph has a bounded nonblank outline.",
            (
                "Every source-locale fixture string matches its pinned Pillow 12.3.0 "
                "rendered-mask evidence."
            ),
            "Printable ASCII U+0020-U+007E maps one code point to one distinct nonzero glyph ID.",
            "Space has a positive advance and no visible outline.",
        ),
    },
    "action-framing": {
        "asset_id": "action_hud",
        "binding_ids": ("action_hud_visual",),
        "kind": "ui",
        "role": "texture",
        "media_type": "image/png",
        "selected_format": "asset:png",
        "runtime_path": "assets/ui/action-hud.png",
        "candidate_name": "action-hud.png",
        "review_profile": "action_information_hierarchy",
        "qa_profile": "action_information_hierarchy",
        "png_layout": "action_hud",
        "acceptance_criteria": (
            "Mission progress and framing context remain distinguishable without color.",
            "The bounded action state remains readable at the reference resolution.",
        ),
    },
    "faction-strategy": {
        "asset_id": "strategy_map",
        "binding_ids": ("strategy_map_visual",),
        "kind": "ui",
        "role": "texture",
        "media_type": "image/png",
        "selected_format": "asset:png",
        "runtime_path": "assets/ui/strategy-map.png",
        "candidate_name": "strategy-map.png",
        "review_profile": "strategy_state_readability",
        "qa_profile": "strategy_state_readability",
        "png_layout": "faction_map",
        "acceptance_criteria": (
            "Faction identities and influence state remain distinguishable without color.",
            "The authored victory threshold is legible in the strategy view.",
        ),
    },
    "modular-roguelite": {
        "asset_id": "storylet_cards",
        "binding_ids": ("storylet_card_visual",),
        "kind": "ui",
        "role": "texture",
        "media_type": "image/png",
        "selected_format": "asset:png",
        "runtime_path": "assets/ui/storylet-cards.png",
        "candidate_name": "storylet-cards.png",
        "review_profile": "storylet_sequence_readability",
        "qa_profile": "storylet_sequence_readability",
        "png_layout": "storylet_cards",
        "acceptance_criteria": (
            "Modular storylet state remains distinguishable without color.",
            "Expedition depth remains legible throughout the bounded run.",
        ),
    },
    "sports-career": {
        "asset_id": "season_dashboard",
        "binding_ids": ("season_dashboard_visual",),
        "kind": "ui",
        "role": "texture",
        "media_type": "image/png",
        "selected_format": "asset:png",
        "runtime_path": "assets/ui/season-dashboard.png",
        "candidate_name": "season-dashboard.png",
        "review_profile": "season_progress_readability",
        "qa_profile": "season_progress_readability",
        "png_layout": "season_dashboard",
        "acceptance_criteria": (
            "Season points and career context remain distinguishable without color.",
            "The authored season target remains legible at the reference resolution.",
        ),
    },
}
SEMANTIC_PNG_CASES = tuple(
    case for case, descriptor in ASSET_FIXTURES.items() if "png_layout" in descriptor
)
NARRATIVE_FONT_FIXTURE_STRINGS = (
    "A visible choice",
    "Branching Narrative",
    "Choose the left symbol",
    "Choose the right symbol",
    "Left ending",
    "Neutral authored branching-choice logic",
    "Neutral branching units",
    "Right ending",
    "Select one authored option.",
)
NARRATIVE_FONT_RENDERED_MASK_SHA256 = {
    "A visible choice": "8f971702da519edeab147fcc217945251863028335c887438a897c6be6572152",
    "Branching Narrative": "468375697409d4df14a44e95649dccb1e776d2e2628d8e3caeb09d19af2044f7",
    "Choose the left symbol": ("b285bf83bd34286a55920fde65482df4bbc2d82676b07f65a6669a8089aa82c9"),
    "Choose the right symbol": ("6389324dcccbba43c2c1637b9b1e2239e0d8492458a7c4de4ff8de2881aebbf4"),
    "Left ending": "926ab20f4811031ea84732b354388072b89193187498c09a102a62802d5a8c40",
    "Neutral authored branching-choice logic": (
        "fcdbe0ec07786eff6381730f05ff7c6c83fc748c787c09b0e89fcb10891b213b"
    ),
    "Neutral branching units": ("aafcb5c2a2d1d5c1fb352c871b44321ea309368c05140a3c4de920ff6319f6a1"),
    "Right ending": "dc2cb33f53acb41c55950d195800f47d5aa0f9202e234cff97b942898edb969a",
    "Select one authored option.": (
        "965456cf83ac73a429befe60de4dee702f14ed276453e1fac247d34c3406909e"
    ),
}
_NARRATIVE_FONT_ACCEPTANCE_CRITERIA = (
    "Choice and ending text use the same reviewed font bytes.",
    ("Critical pairs O/0, I/l/1, S/5, B/8, Z/2 and G/6 remain visually distinct."),
    "Every non-space printable ASCII glyph has a bounded nonblank outline.",
    ("Every source-locale fixture string matches its pinned Pillow 12.3.0 rendered-mask evidence."),
    ("Printable ASCII U+0020-U+007E maps one code point to one distinct nonzero glyph ID."),
    "Space has a positive advance and no visible outline.",
)

# This 5x7 design was authored for World Forge and is emitted under CC0-1.0.
# It is deliberately stored as source data rather than copied from an external
# font, so the generated fixture has exact, reviewable, offline provenance.
_NARRATIVE_GLYPH_ROWS = {
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
    "!": ("00100", "00100", "00100", "00100", "00100", "00000", "00100"),
    '"': ("01010", "01010", "01010", "00000", "00000", "00000", "00000"),
    "#": ("01010", "11111", "01010", "01010", "11111", "01010", "00000"),
    "$": ("00100", "01111", "10100", "01110", "00101", "11110", "00100"),
    "%": ("11001", "11010", "00100", "01000", "10110", "00110", "00000"),
    "&": ("01100", "10010", "10100", "01000", "10101", "10010", "01101"),
    "'": ("00100", "00100", "01000", "00000", "00000", "00000", "00000"),
    "(": ("00010", "00100", "01000", "01000", "01000", "00100", "00010"),
    ")": ("01000", "00100", "00010", "00010", "00010", "00100", "01000"),
    "*": ("00000", "10101", "01110", "11111", "01110", "10101", "00000"),
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    ",": ("00000", "00000", "00000", "00000", "00110", "00100", "01000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "00110", "00110"),
    "/": ("00001", "00010", "00100", "00100", "01000", "10000", "00000"),
    "0": ("01110", "10011", "10101", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "10100", "00100", "00100", "00100", "11111"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    ":": ("00000", "00110", "00110", "00000", "00110", "00110", "00000"),
    ";": ("00000", "00110", "00110", "00000", "00110", "00100", "01000"),
    "<": ("00010", "00100", "01000", "10000", "01000", "00100", "00010"),
    "=": ("00000", "00000", "11111", "00000", "11111", "00000", "00000"),
    ">": ("01000", "00100", "00010", "00001", "00010", "00100", "01000"),
    "?": ("01110", "10001", "00001", "00010", "00100", "00000", "00100"),
    "@": ("01110", "10001", "10111", "10101", "10111", "10000", "01110"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "[": ("01110", "01000", "01000", "01000", "01000", "01000", "01110"),
    "\\": ("10000", "01000", "00100", "00100", "00010", "00001", "00000"),
    "]": ("01110", "00010", "00010", "00010", "00010", "00010", "01110"),
    "^": ("00100", "01010", "10001", "00000", "00000", "00000", "00000"),
    "_": ("00000", "00000", "00000", "00000", "00000", "00000", "11111"),
    "`": ("01000", "00100", "00010", "00000", "00000", "00000", "00000"),
    "a": ("00000", "00000", "01110", "00001", "01111", "10001", "01111"),
    "b": ("10000", "10000", "10110", "11001", "10001", "10001", "11110"),
    "c": ("00000", "00000", "01110", "10001", "10000", "10001", "01110"),
    "d": ("00001", "00001", "01101", "10011", "10001", "10001", "01111"),
    "e": ("00000", "00000", "01110", "10001", "11111", "10000", "01110"),
    "f": ("00110", "01001", "01000", "11100", "01000", "01000", "01000"),
    "g": ("00000", "01111", "10001", "10001", "01111", "00001", "01110"),
    "h": ("10000", "10000", "10110", "11001", "10001", "10001", "10001"),
    "i": ("00100", "00000", "01100", "00100", "00100", "00100", "01110"),
    "j": ("00010", "00000", "00110", "00010", "00010", "10010", "01100"),
    "k": ("10000", "10000", "10010", "10100", "11000", "10100", "10010"),
    "l": ("01100", "00100", "00100", "00100", "00100", "00100", "01110"),
    "m": ("00000", "00000", "11010", "10101", "10101", "10101", "10101"),
    "n": ("00000", "00000", "10110", "11001", "10001", "10001", "10001"),
    "o": ("00000", "00000", "01110", "10001", "10001", "10001", "01110"),
    "p": ("00000", "11110", "10001", "10001", "11110", "10000", "10000"),
    "q": ("00000", "01111", "10001", "10001", "01111", "00001", "00001"),
    "r": ("00000", "00000", "10110", "11001", "10000", "10000", "10000"),
    "s": ("00000", "00000", "01111", "10000", "01110", "00001", "11110"),
    "t": ("01000", "01000", "11100", "01000", "01000", "01001", "00110"),
    "u": ("00000", "00000", "10001", "10001", "10001", "10011", "01101"),
    "v": ("00000", "00000", "10001", "10001", "10001", "01010", "00100"),
    "w": ("00000", "00000", "10001", "10001", "10101", "10101", "01010"),
    "x": ("00000", "00000", "10001", "01010", "00100", "01010", "10001"),
    "y": ("00000", "10001", "10001", "10001", "01111", "00001", "01110"),
    "z": ("00000", "00000", "11111", "00010", "00100", "01000", "11111"),
    "{": ("00011", "00100", "00100", "11000", "00100", "00100", "00011"),
    "|": ("00100", "00100", "00100", "00000", "00100", "00100", "00100"),
    "}": ("11000", "00100", "00100", "00011", "00100", "00100", "11000"),
    "~": ("00000", "00000", "01001", "10110", "00000", "00000", "00000"),
}


def _review(*, evidence_id: str, content_hash: str, rationale: str) -> dict[str, Any]:
    return {
        "reviewer_id": "asset_director",
        "rationale": rationale,
        "evidence": [
            {
                "evidence_id": evidence_id,
                "content_hash": content_hash,
            }
        ],
    }


def _visual(gamepack: dict[str, Any]) -> dict[str, Any]:
    presentation = gamepack["presentation"]
    return {
        "presentation_mode": presentation["mode"],
        "visual_language": presentation["visual_language"],
        "camera": presentation["camera"],
        "coordinate_system": ("text_flow" if presentation["mode"] == "text" else "screen_2d"),
        "reference_resolution": {"width": 1280, "height": 720},
        "aspect_ratio": {"width": 16, "height": 9},
        "palette": {
            "direction": "A restrained neutral palette keeps interactive states distinct.",
            "minimum_contrast_ratio": 7,
            "color_independent": True,
        },
        "readability": {
            "silhouette_direction": "Every interactive state remains identifiable without color.",
            "minimum_feature_pixels": 2,
        },
        "typography": {
            "direction": "Use a highly legible interface family with stable metrics.",
            "minimum_text_scale_percent": 200,
        },
        "motion": {
            "direction": "State changes use brief non-essential transitions.",
            "reduced_motion": True,
        },
        "ui": {
            "hierarchy": "Primary action, current state, and outcome feedback remain distinct.",
            "density": presentation["ui_density"],
        },
        "accessibility": {
            "captions": presentation["accessibility"]["captions"],
            "screen_reader_structure": presentation["accessibility"]["screen_reader_structure"],
            "keyboard_only": presentation["accessibility"]["keyboard_only"],
        },
        "localization": {
            "source_locale": presentation["localization"]["source_locale"],
            "supported_locales": presentation["localization"]["supported_locales"],
            "expansion_budget_percent": 35,
        },
    }


def _audio() -> dict[str, str]:
    return {
        "status": "not_applicable",
        "rationale": "No audio asset is required by this bounded release target.",
    }


def _puzzle_bindings() -> list[dict[str, Any]]:
    return [
        {
            "binding_id": "board_texture",
            "asset_id": "board_ui",
            "selected_format": "asset:png",
            "kind": "ui",
            "representation": "2d",
            "outputs": [{"role": "texture", "media_type": "image/png"}],
            "sharing": {"policy": "exclusive", "group_id": None},
        }
    ]


def _narrative_bindings() -> list[dict[str, Any]]:
    common = {
        "asset_id": "narrative_ui_font",
        "selected_format": "asset:font",
        "kind": "font",
        "representation": "2d",
        "outputs": [{"role": "font", "media_type": "font/ttf"}],
        "sharing": {"policy": "shared_exact", "group_id": "narrative_ui"},
    }
    return [
        {"binding_id": "choice_panel", **common},
        {"binding_id": "ending_panel", **common},
    ]


def _fixture_bindings(descriptor: dict[str, Any]) -> list[dict[str, Any]]:
    sharing = (
        {"policy": "shared_exact", "group_id": "narrative_ui"}
        if len(descriptor["binding_ids"]) > 1
        else {"policy": "exclusive", "group_id": None}
    )
    return [
        {
            "binding_id": binding_id,
            "asset_id": descriptor["asset_id"],
            "selected_format": descriptor["selected_format"],
            "kind": descriptor["kind"],
            "representation": "2d",
            "outputs": [
                {
                    "role": descriptor["role"],
                    "media_type": descriptor["media_type"],
                }
            ],
            "sharing": dict(sharing),
        }
        for binding_id in descriptor["binding_ids"]
    ]


def _puzzle_spec(
    gamepack: dict[str, Any],
    subject: dict[str, Any],
    target: dict[str, Any],
    style: dict[str, Any],
    inventory: dict[str, Any],
) -> dict[str, Any]:
    return build_asset_specification(
        gamepack,
        subject,
        target,
        style,
        inventory,
        asset_id="board_ui",
        outputs=[
            {
                "role": "texture",
                "media_type": "image/png",
                "runtime_path": "assets/ui/board.png",
                "expectations": {
                    "kind": "png",
                    "width": 256,
                    "height": 256,
                    "color_type": "rgba8",
                    "max_bytes": 262144,
                },
            }
        ],
        acceptance_criteria=[
            "Every board symbol remains distinguishable without color.",
            "The exact runtime output matches the reviewed target.",
        ],
        production_class="procedural_offline",
        review_requirements={
            "human_review_required": True,
            "qa_profile": "ui_readability",
            "evidence_required": True,
        },
    )


def _narrative_spec(
    gamepack: dict[str, Any],
    subject: dict[str, Any],
    target: dict[str, Any],
    style: dict[str, Any],
    inventory: dict[str, Any],
) -> dict[str, Any]:
    return build_asset_specification(
        gamepack,
        subject,
        target,
        style,
        inventory,
        asset_id="narrative_ui_font",
        outputs=[
            {
                "role": "font",
                "media_type": "font/ttf",
                "runtime_path": "assets/fonts/narrative-ui.ttf",
                "expectations": {
                    "kind": "font",
                    "container": "ttf",
                    "glyph_ranges": ["U+0020-007E"],
                    "max_glyphs": 256,
                    "max_bytes": 524288,
                },
            }
        ],
        acceptance_criteria=list(_NARRATIVE_FONT_ACCEPTANCE_CRITERIA),
        production_class="procedural_offline",
        review_requirements={
            "human_review_required": True,
            "qa_profile": "localized_text",
            "evidence_required": True,
        },
    )


def _fixture_spec(
    descriptor: dict[str, Any],
    gamepack: dict[str, Any],
    subject: dict[str, Any],
    target: dict[str, Any],
    style: dict[str, Any],
    inventory: dict[str, Any],
) -> dict[str, Any]:
    if descriptor["kind"] == "font":
        return _narrative_spec(gamepack, subject, target, style, inventory)
    return build_asset_specification(
        gamepack,
        subject,
        target,
        style,
        inventory,
        asset_id=descriptor["asset_id"],
        outputs=[
            {
                "role": descriptor["role"],
                "media_type": descriptor["media_type"],
                "runtime_path": descriptor["runtime_path"],
                "expectations": {
                    "kind": "png",
                    "width": 256,
                    "height": 256,
                    "color_type": "rgba8",
                    "max_bytes": 262144,
                },
            }
        ],
        acceptance_criteria=sorted(
            descriptor["acceptance_criteria"], key=lambda item: item.encode("utf-8")
        ),
        production_class="procedural_offline",
        review_requirements={
            "human_review_required": True,
            "qa_profile": descriptor["qa_profile"],
            "evidence_required": True,
        },
    )


def _evidence_hash(label: str, *values: bytes) -> str:
    digest = hashlib.sha256(label.encode("utf-8"))
    for value in values:
        digest.update(struct.pack(">I", len(value)))
        digest.update(value)
    return digest.hexdigest()


def _narrative_design_mask(text: str) -> bytes:
    lines = [
        "0".join(_NARRATIVE_GLYPH_ROWS[character][row] for character in text) for row in range(7)
    ]
    return "\n".join(lines).encode("ascii")


def _narrative_rendered_mask_manifest(font_payload: bytes) -> bytes:
    import PIL
    from PIL import ImageFont

    if PIL.__version__ != "12.3.0":
        raise ValueError("narrative font QA requires pinned Pillow 12.3.0")
    font = ImageFont.truetype(io.BytesIO(font_payload), 24)
    observed: dict[str, str] = {}
    for text in NARRATIVE_FONT_FIXTURE_STRINGS:
        rendered = font.getmask(text, mode="L")
        mask = bytes(rendered)
        if rendered.size[0] < 1 or rendered.size[1] < 1 or not any(mask):
            raise ValueError(f"narrative font produced a blank fixture string: {text}")
        observed[text] = hashlib.sha256(struct.pack(">II", *rendered.size) + mask).hexdigest()
    if observed != NARRATIVE_FONT_RENDERED_MASK_SHA256:
        raise ValueError("narrative font rendered masks do not match pinned QA evidence")
    return b"\n".join(
        f"{text}:{observed[text]}".encode("ascii") for text in NARRATIVE_FONT_FIXTURE_STRINGS
    )


def _narrative_qa_evidence(font_payload: bytes) -> tuple[str, ...]:
    critical_pairs = ("O0", "Il", "I1", "l1", "S5", "B8", "Z2", "G6")
    design_source = "\n".join(
        f"{ord(character):02X}:{'/'.join(rows)}"
        for character, rows in sorted(_NARRATIVE_GLYPH_ROWS.items(), key=lambda item: ord(item[0]))
    ).encode("ascii")
    nonblank_source = b"\n".join(
        f"{ord(character):02X}:{'/'.join(_NARRATIVE_GLYPH_ROWS[character])}".encode("ascii")
        for character in (chr(codepoint) for codepoint in range(0x21, 0x7F))
    )
    fixture_masks = _narrative_rendered_mask_manifest(font_payload)
    font_bytes = _evidence_hash("font-bytes-v1", font_payload)
    cmap = _evidence_hash("printable-ascii-cmap-v1", design_source)
    blank_space = _evidence_hash(
        "blank-space-v1",
        "/".join(_NARRATIVE_GLYPH_ROWS[" "]).encode("ascii"),
    )
    nonblank = _evidence_hash("nonblank-outline-source-v1", nonblank_source)
    critical = _evidence_hash(
        "critical-pair-source-v1",
        b"\n".join(
            pair.encode("ascii") + b":" + _narrative_design_mask(pair) for pair in critical_pairs
        ),
    )
    fixture_strings = _evidence_hash("fixture-string-mask-source-v1", fixture_masks)
    return (
        font_bytes,
        critical,
        nonblank,
        fixture_strings,
        cmap,
        blank_space,
    )


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _puzzle_png() -> bytes:
    width = height = 256
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            light = ((x // 32) + (y // 32)) % 2 == 0
            value = 255 if light else 0
            rows.extend((value, value, value, 255))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0),
        )
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows), level=9))
        + _png_chunk(b"IEND", b"")
    )


_SEMANTIC_PNG_SIZE = (256, 256)
_SEMANTIC_PNG_MAX_BYTES = 262_144
_SEMANTIC_PNG_PALETTES: dict[str, dict[str, tuple[int, int, int, int]]] = {
    "action_hud": {
        "background": (12, 18, 28, 255),
        "frame": (230, 236, 242, 255),
        "panel": (38, 54, 72, 255),
        "muted": (104, 122, 142, 255),
        "status": (72, 204, 142, 255),
        "warning": (246, 178, 62, 255),
        "danger": (226, 72, 78, 255),
    },
    "faction_map": {
        "background": (18, 22, 28, 255),
        "frame": (238, 238, 232, 255),
        "north": (48, 80, 132, 255),
        "south": (204, 164, 96, 255),
        "north_hatch": (128, 168, 220, 255),
        "south_hatch": (92, 58, 34, 255),
        "connection": (184, 196, 204, 255),
        "threshold": (246, 212, 72, 255),
    },
    "storylet_cards": {
        "background": (24, 18, 30, 255),
        "frame": (238, 232, 242, 255),
        "card_one": (55, 48, 74, 255),
        "card_two": (82, 68, 104, 255),
        "card_three": (112, 88, 132, 255),
        "eligible": (72, 204, 132, 255),
        "locked": (226, 102, 76, 255),
        "branch": (246, 204, 72, 255),
    },
    "season_dashboard": {
        "background": (14, 26, 32, 255),
        "frame": (234, 240, 242, 255),
        "header": (42, 84, 102, 255),
        "table": (30, 48, 58, 255),
        "alternate": (58, 76, 86, 255),
        "accent": (72, 188, 208, 255),
        "win": (70, 190, 122, 255),
        "draw": (232, 190, 72, 255),
        "loss": (218, 82, 88, 255),
    },
}


def _pinned_pillow() -> tuple[Any, Any]:
    import PIL
    from PIL import Image, ImageDraw

    if PIL.__version__ != "12.3.0":
        raise ValueError("semantic PNG fixtures require Pillow 12.3.0")
    return Image, ImageDraw


def _draw_action_hud(image: Any, draw: Any, palette: dict[str, tuple[int, ...]]) -> None:
    draw.rectangle((8, 8, 247, 247), outline=palette["frame"], width=3)
    draw.rectangle((16, 16, 174, 40), fill=palette["panel"], outline=palette["frame"], width=2)
    draw.rectangle((24, 24, 154, 32), fill=palette["status"])
    for x in (56, 88, 120):
        draw.line((x, 24, x, 32), fill=palette["background"], width=2)
    draw.rectangle(
        (184, 16, 239, 40),
        fill=palette["warning"],
        outline=palette["frame"],
        width=2,
    )
    draw.ellipse((84, 56, 172, 144), outline=palette["muted"], width=4)
    draw.polygon(
        ((128, 64), (164, 100), (128, 136), (92, 100)),
        outline=palette["warning"],
        width=4,
    )
    draw.line((106, 100, 150, 100), fill=palette["danger"], width=4)
    draw.line((128, 78, 128, 122), fill=palette["danger"], width=4)
    panels = (
        ((16, 188, 78, 232), palette["status"]),
        ((96, 188, 158, 232), palette["muted"]),
        ((176, 188, 238, 232), palette["warning"]),
    )
    for (left, top, right, bottom), fill in panels:
        draw.rectangle((left, top, right, bottom), fill=palette["panel"])
        draw.rectangle((left, top, right, bottom), outline=palette["frame"], width=2)
        draw.rectangle((left + 8, top + 10, right - 8, top + 17), fill=fill)
        draw.rectangle((left + 8, top + 25, right - 18, top + 31), fill=palette["frame"])


def _draw_faction_map(image: Any, draw: Any, palette: dict[str, tuple[int, ...]]) -> None:
    draw.polygon(
        ((10, 42), (104, 28), (122, 218), (12, 240)),
        fill=palette["north"],
        outline=palette["frame"],
    )
    draw.polygon(
        ((134, 34), (246, 46), (242, 238), (128, 218)),
        fill=palette["south"],
        outline=palette["frame"],
    )
    for y in (70, 104, 138, 172, 206):
        draw.line((20, y, 96, y - 18), fill=palette["north_hatch"], width=3)
    for x in (154, 180, 206, 232):
        draw.line((x, 58, x, 218), fill=palette["south_hatch"], width=3)
    north_nodes = ((40, 78), (82, 130), (44, 190))
    south_nodes = ((210, 76), (174, 132), (214, 190))
    for start, end in zip(north_nodes[:-1], north_nodes[1:], strict=True):
        draw.line((*start, *end), fill=palette["connection"], width=4)
    for start, end in zip(south_nodes[:-1], south_nodes[1:], strict=True):
        draw.line((*start, *end), fill=palette["connection"], width=4)
    draw.line((82, 130, 174, 132), fill=palette["connection"], width=4)
    for x, y in north_nodes:
        draw.ellipse((x - 9, y - 9, x + 9, y + 9), fill=palette["frame"])
        draw.rectangle((x - 4, y - 4, x + 4, y + 4), fill=palette["north"])
    for x, y in south_nodes:
        draw.ellipse((x - 9, y - 9, x + 9, y + 9), fill=palette["frame"])
        draw.rectangle((x - 4, y - 4, x + 4, y + 4), fill=palette["south_hatch"])
    draw.rectangle((176, 8, 247, 32), fill=palette["background"], outline=palette["frame"], width=2)
    draw.rectangle((184, 16, 226, 24), fill=palette["threshold"])
    draw.line((232, 13, 232, 27), fill=palette["frame"], width=3)


def _draw_storylet_cards(image: Any, draw: Any, palette: dict[str, tuple[int, ...]]) -> None:
    cards = (
        ((18, 30, 86, 154), palette["card_one"], palette["eligible"]),
        ((94, 50, 162, 174), palette["card_two"], palette["branch"]),
        ((170, 30, 238, 154), palette["card_three"], palette["locked"]),
    )
    for (left, top, right, bottom), fill, marker in cards:
        draw.rectangle((left, top, right, bottom), fill=fill, outline=palette["frame"], width=3)
        draw.rectangle((left + 8, top + 10, right - 8, top + 21), fill=marker)
        for offset in (38, 54, 70):
            draw.rectangle(
                (left + 9, top + offset, right - 12, top + offset + 5),
                fill=palette["frame"],
            )
        draw.rectangle((left + 9, bottom - 20, left + 23, bottom - 7), outline=marker, width=3)
    draw.line((128, 174, 88, 218), fill=palette["branch"], width=4)
    draw.line((128, 174, 168, 218), fill=palette["branch"], width=4)
    draw.polygon(((88, 208), (98, 226), (78, 226)), fill=palette["eligible"])
    draw.polygon(((168, 208), (178, 226), (158, 226)), fill=palette["locked"])
    draw.rectangle((18, 238, 238, 247), outline=palette["frame"], width=2)
    for left, right, fill in (
        (22, 84, palette["eligible"]),
        (88, 150, palette["branch"]),
        (154, 234, palette["locked"]),
    ):
        draw.rectangle((left, 241, right, 244), fill=fill)


def _draw_season_dashboard(
    image: Any,
    draw: Any,
    palette: dict[str, tuple[int, ...]],
) -> None:
    draw.rectangle((10, 10, 246, 246), outline=palette["frame"], width=3)
    draw.rectangle((16, 16, 240, 42), fill=palette["header"], outline=palette["frame"], width=2)
    for x in (26, 54, 82):
        draw.rectangle((x, 24, x + 18, 33), fill=palette["accent"])
    draw.rectangle((16, 54, 170, 200), fill=palette["table"], outline=palette["frame"], width=2)
    for top in (82, 142):
        draw.rectangle((18, top, 168, top + 28), fill=palette["alternate"])
    for x in (34, 118, 148):
        draw.line((x, 54, x, 200), fill=palette["frame"], width=2)
    for y in (80, 110, 140, 170, 200):
        draw.line((16, y, 170, y), fill=palette["frame"], width=2)
    draw.rectangle((180, 54, 240, 200), fill=palette["table"], outline=palette["frame"], width=2)
    for y in (88, 134, 180):
        draw.line((180, y, 240, y), fill=palette["frame"], width=2)
    for top, fill in ((64, palette["win"]), (110, palette["draw"]), (156, palette["loss"])):
        draw.rectangle((192, top, 228, top + 14), fill=fill)
    draw.rectangle((16, 214, 240, 240), fill=palette["table"], outline=palette["frame"], width=2)
    draw.rectangle((24, 222, 198, 231), fill=palette["accent"])
    draw.line((206, 218, 206, 235), fill=palette["frame"], width=3)
    draw.rectangle((214, 222, 232, 231), fill=palette["win"])


def _authoring_png(case: str) -> bytes:
    descriptor = ASSET_FIXTURES.get(case)
    if case not in SEMANTIC_PNG_CASES or not isinstance(descriptor, dict):
        raise ValueError(f"{case} has no semantic PNG fixture layout")
    layout = descriptor["png_layout"]
    palette = _SEMANTIC_PNG_PALETTES[layout]
    Image, ImageDraw = _pinned_pillow()
    image = Image.new("RGBA", _SEMANTIC_PNG_SIZE, palette["background"])
    draw = ImageDraw.Draw(image)
    drawers = {
        "action_hud": _draw_action_hud,
        "faction_map": _draw_faction_map,
        "storylet_cards": _draw_storylet_cards,
        "season_dashboard": _draw_season_dashboard,
    }
    drawers[layout](image, draw, palette)
    output = io.BytesIO()
    image.save(
        output,
        format="PNG",
        compress_level=9,
        optimize=False,
    )
    return output.getvalue()


def _pixel_matches(
    image: Any,
    samples: tuple[tuple[tuple[int, int], tuple[int, int, int, int]], ...],
) -> bool:
    return all(image.getpixel(point) == color for point, color in samples)


def _color_count(
    image: Any,
    box: tuple[int, int, int, int],
    color: tuple[int, int, int, int],
) -> int:
    return sum(pixel == color for pixel in image.crop(box).get_flattened_data())


def _luminance(pixel: tuple[int, int, int, int]) -> int:
    red, green, blue, _alpha = pixel
    return (299 * red + 587 * green + 114 * blue) // 1000


def _luminance_gap(
    image: Any,
    points: tuple[tuple[int, int], ...],
    *,
    minimum: int,
) -> bool:
    values = sorted(_luminance(image.getpixel(point)) for point in points)
    return all(right - left >= minimum for left, right in zip(values[:-1], values[1:], strict=True))


def _semantic_layout_checks(layout: str, image: Any) -> tuple[dict[str, bool], ...]:
    palette = _SEMANTIC_PNG_PALETTES[layout]
    if layout == "action_hud":
        return (
            {
                "bounded_frame": _pixel_matches(
                    image,
                    (
                        ((8, 8), palette["frame"]),
                        ((247, 247), palette["frame"]),
                    ),
                ),
                "segmented_progress": _pixel_matches(
                    image,
                    (
                        ((30, 26), palette["status"]),
                        ((56, 26), palette["background"]),
                        ((70, 26), palette["status"]),
                    ),
                ),
                "three_status_regions": _pixel_matches(
                    image,
                    (
                        ((28, 200), palette["status"]),
                        ((108, 200), palette["muted"]),
                        ((188, 200), palette["warning"]),
                    ),
                ),
                "grayscale_hierarchy": _luminance_gap(
                    image,
                    ((30, 26), (108, 200), (188, 200)),
                    minimum=12,
                ),
            },
            {
                "telegraph_diamond": _pixel_matches(
                    image,
                    (
                        ((128, 64), palette["warning"]),
                        ((164, 100), palette["warning"]),
                        ((92, 100), palette["warning"]),
                    ),
                ),
                "telegraph_cross": _pixel_matches(
                    image,
                    (
                        ((128, 100), palette["danger"]),
                        ((110, 100), palette["danger"]),
                        ((128, 118), palette["danger"]),
                    ),
                ),
                "bounded_status_density": (
                    _color_count(image, (16, 188, 239, 233), palette["panel"]) > 3_000
                    and _color_count(image, (16, 188, 239, 233), palette["frame"]) > 700
                ),
            },
        )
    if layout == "faction_map":
        return (
            {
                "territories_present": (
                    _color_count(image, (0, 32, 256, 241), palette["north"]) > 8_000
                    and _color_count(image, (0, 32, 256, 241), palette["south"]) > 8_000
                ),
                "territories_grayscale_distinct": _luminance_gap(
                    image,
                    ((24, 54), (144, 54)),
                    minimum=45,
                ),
                "nodes_and_connections": (
                    _color_count(image, (20, 56, 236, 210), palette["frame"]) > 900
                    and _color_count(image, (20, 56, 236, 210), palette["connection"]) > 500
                ),
                "distinct_hatching": (
                    _color_count(image, (12, 42, 120, 222), palette["north_hatch"]) > 650
                    and _color_count(image, (132, 42, 244, 222), palette["south_hatch"]) > 1_000
                ),
            },
            {
                "threshold_bar": _pixel_matches(
                    image,
                    (
                        ((190, 20), palette["threshold"]),
                        ((220, 20), palette["threshold"]),
                        ((232, 20), palette["frame"]),
                    ),
                ),
                "threshold_bounded": (
                    _color_count(image, (176, 8, 248, 33), palette["threshold"]) > 250
                    and _color_count(image, (176, 8, 248, 33), palette["frame"]) > 250
                ),
            },
        )
    if layout == "storylet_cards":
        return (
            {
                "depth_meter_segments": _pixel_matches(
                    image,
                    (
                        ((30, 242), palette["eligible"]),
                        ((100, 242), palette["branch"]),
                        ((180, 242), palette["locked"]),
                    ),
                ),
                "branch_cue": (
                    _color_count(image, (72, 168, 184, 230), palette["branch"]) > 350
                    and _pixel_matches(
                        image,
                        (
                            ((88, 216), palette["eligible"]),
                            ((168, 216), palette["locked"]),
                        ),
                    )
                ),
            },
            {
                "three_distinct_cards": _pixel_matches(
                    image,
                    (
                        ((30, 115), palette["card_one"]),
                        ((106, 140), palette["card_two"]),
                        ((182, 115), palette["card_three"]),
                    ),
                ),
                "card_luminance_sequence": _luminance_gap(
                    image,
                    ((30, 115), (106, 140), (182, 115)),
                    minimum=14,
                ),
                "eligibility_markers": _pixel_matches(
                    image,
                    (
                        ((30, 44), palette["eligible"]),
                        ((106, 64), palette["branch"]),
                        ((182, 44), palette["locked"]),
                    ),
                ),
                "card_frames": _color_count(
                    image,
                    (16, 28, 240, 176),
                    palette["frame"],
                )
                > 3_500,
            },
        )
    if layout == "season_dashboard":
        return (
            {
                "standings_table": (
                    _color_count(image, (16, 54, 171, 201), palette["frame"]) > 2_000
                    and _color_count(image, (16, 54, 171, 201), palette["alternate"]) > 7_000
                ),
                "season_header": _pixel_matches(
                    image,
                    (
                        ((30, 28), palette["accent"]),
                        ((58, 28), palette["accent"]),
                        ((86, 28), palette["accent"]),
                    ),
                ),
                "table_and_header_grayscale_distinct": _luminance_gap(
                    image,
                    ((20, 60), (20, 90), (30, 28)),
                    minimum=15,
                ),
            },
            {
                "round_results": _pixel_matches(
                    image,
                    (
                        ((200, 70), palette["win"]),
                        ((200, 116), palette["draw"]),
                        ((200, 162), palette["loss"]),
                    ),
                ),
                "result_regions": (
                    _color_count(image, (180, 54, 241, 201), palette["win"]) > 450
                    and _color_count(image, (180, 54, 241, 201), palette["draw"]) > 450
                    and _color_count(image, (180, 54, 241, 201), palette["loss"]) > 450
                ),
                "season_target": _pixel_matches(
                    image,
                    (
                        ((30, 226), palette["accent"]),
                        ((198, 226), palette["accent"]),
                        ((206, 226), palette["frame"]),
                        ((220, 226), palette["win"]),
                    ),
                ),
            },
        )
    raise ValueError(f"unsupported semantic PNG layout {layout}")


def _inspect_semantic_png(
    payload: bytes,
) -> tuple[Any | None, dict[str, bool], dict[str, Any]]:
    Image, _ImageDraw = _pinned_pillow()
    bounded = 0 < len(payload) <= _SEMANTIC_PNG_MAX_BYTES
    signature = payload.startswith(b"\x89PNG\r\n\x1a\n")
    ihdr = len(payload) >= 33 and payload[12:16] == b"IHDR"
    width = height = bit_depth = color_type = None
    if ihdr:
        width, height = struct.unpack(">II", payload[16:24])
        bit_depth = payload[24]
        color_type = payload[25]
    header_exact = (
        bounded
        and signature
        and ihdr
        and (width, height) == _SEMANTIC_PNG_SIZE
        and bit_depth == 8
        and color_type == 6
    )
    image = None
    observations: dict[str, Any] = {
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_size": len(payload),
        "ihdr_width": width,
        "ihdr_height": height,
        "ihdr_bit_depth": bit_depth,
        "ihdr_color_type": color_type,
        "decoded_format": None,
        "decoded_mode": None,
        "decoded_size": None,
        "pixel_sha256": None,
        "metadata_keys": [],
    }
    if header_exact:
        try:
            with Image.open(io.BytesIO(payload)) as decoded:
                decoded.load()
                observations.update(
                    {
                        "decoded_format": decoded.format,
                        "decoded_mode": decoded.mode,
                        "decoded_size": list(decoded.size),
                        "pixel_sha256": hashlib.sha256(decoded.tobytes()).hexdigest(),
                        "metadata_keys": sorted(decoded.info),
                    }
                )
                if decoded.format == "PNG" and decoded.mode == "RGBA":
                    image = decoded.copy()
        except (OSError, SyntaxError, ValueError):
            image = None
    common = {
        "payload_bounded": bounded,
        "png_signature": signature,
        "rgba8_256x256_ihdr": bool(header_exact),
        "pillow_png_decode": image is not None,
        "decoded_dimensions_exact": image is not None and image.size == _SEMANTIC_PNG_SIZE,
        "decoded_mode_rgba": image is not None and image.mode == "RGBA",
        "single_frame": image is not None and getattr(image, "n_frames", 1) == 1,
        "fully_opaque": image is not None and image.getchannel("A").getextrema() == (255, 255),
        "metadata_free": image is not None and not observations["metadata_keys"],
    }
    return image, common, observations


def _evaluate_processed_png_acceptance(
    case: str,
    payload: bytes,
    processing_output: dict[str, Any],
) -> list[dict[str, Any]]:
    descriptor = ASSET_FIXTURES.get(case)
    if case not in SEMANTIC_PNG_CASES or not isinstance(descriptor, dict):
        raise ValueError(f"{case} has no semantic PNG acceptance profile")
    criteria = sorted(descriptor["acceptance_criteria"], key=lambda item: item.encode("utf-8"))
    image, common_checks, observations = _inspect_semantic_png(payload)
    processed_sha256 = hashlib.sha256(payload).hexdigest()
    processed_size = len(payload)
    receipt_sha256 = processing_output.get("sha256")
    receipt_size = processing_output.get("size_bytes")
    common_checks.update(
        {
            "processed_sha256_matches_receipt": receipt_sha256 == processed_sha256,
            "processed_size_matches_receipt": receipt_size == processed_size,
        }
    )
    observations.update(
        {
            "processed_sha256": processed_sha256,
            "processed_size": processed_size,
            "processing_receipt_sha256": receipt_sha256,
            "processing_receipt_size": receipt_size,
        }
    )
    if image is None:
        semantic_checks = tuple({"semantic_layout_decoded": False} for _ in criteria)
    else:
        semantic_checks = _semantic_layout_checks(descriptor["png_layout"], image)
    if len(semantic_checks) != len(criteria):
        raise ValueError(f"{case} semantic checks do not cover every acceptance criterion")
    results = []
    for index, (criterion, case_checks) in enumerate(zip(criteria, semantic_checks, strict=True)):
        checks = {**common_checks, **case_checks}
        status = "passed" if all(checks.values()) else "failed"
        evidence_document = {
            "case": case,
            "criterion_index": index,
            "criterion_sha256": hashlib.sha256(criterion.encode("utf-8")).hexdigest(),
            "status": status,
            "checks": checks,
            "observations": observations,
        }
        evidence_hash = _evidence_hash(
            "semantic-png-processed-acceptance-v2",
            json.dumps(
                evidence_document,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        )
        results.append(
            {
                "criterion_index": index,
                "criterion_sha256": evidence_document["criterion_sha256"],
                "status": status,
                "evidence_hashes": [evidence_hash],
            }
        )
    return results


def _manifest_state_for_acceptance(acceptance_results: list[dict[str, Any]]) -> str:
    return (
        "release_ready"
        if acceptance_results
        and all(result.get("status") == "passed" for result in acceptance_results)
        else "processed"
    )


def _bitmap_outline_glyph(rows: tuple[str, ...]) -> tuple[bytes, int, int]:
    if len(rows) != 7 or any(len(row) != 5 or set(row) - {"0", "1"} for row in rows):
        raise ValueError("narrative glyph source must be an exact 5x7 binary grid")
    points: list[tuple[int, int]] = []
    contour_ends: list[int] = []
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            if value == "0":
                continue
            x_min = 50 + column_index * 100
            x_max = x_min + 80
            y_max = 700 - row_index * 100
            y_min = y_max - 80
            points.extend(
                (
                    (x_min, y_min),
                    (x_min, y_max),
                    (x_max, y_max),
                    (x_max, y_min),
                )
            )
            contour_ends.append(len(points) - 1)
    if not points:
        return struct.pack(">hhhhhH", 0, 0, 0, 0, 0, 0), 0, 0
    x_values = [point[0] for point in points]
    y_values = [point[1] for point in points]
    glyph = bytearray(
        struct.pack(
            ">hhhhh",
            len(contour_ends),
            min(x_values),
            min(y_values),
            max(x_values),
            max(y_values),
        )
    )
    glyph.extend(struct.pack(f">{len(contour_ends)}H", *contour_ends))
    glyph.extend(struct.pack(">H", 0))
    glyph.extend(b"\x01" * len(points))
    previous = 0
    for x_value in x_values:
        glyph.extend(struct.pack(">h", x_value - previous))
        previous = x_value
    previous = 0
    for y_value in y_values:
        glyph.extend(struct.pack(">h", y_value - previous))
        previous = y_value
    glyph.extend(b"\0" * (-len(glyph) % 2))
    return bytes(glyph), len(points), len(contour_ends)


def _font_name_table() -> bytes:
    values = (
        (0, "Copyright waived under CC0-1.0 by World Forge contributors."),
        (1, "World Forge Tiny Fixture"),
        (2, "Regular"),
        (4, "World Forge Tiny Fixture Regular"),
        (5, "Version 1.1.0"),
        (6, "WorldForgeTinyFixture-Regular"),
        (13, "CC0-1.0"),
        (
            14,
            "https://creativecommons.org/publicdomain/zero/1.0/",
        ),
    )
    storage = bytearray()
    records = bytearray()
    for name_id, value in values:
        encoded = value.encode("utf-16-be")
        records.extend(
            struct.pack(
                ">HHHHHH",
                3,
                1,
                0x0409,
                name_id,
                len(encoded),
                len(storage),
            )
        )
        storage.extend(encoded)
    return struct.pack(">HHH", 0, len(values), 6 + len(records)) + records + storage


def _assemble_sfnt(tables: dict[str, bytes]) -> bytes:
    ordered = sorted(tables.items())
    table_count = len(ordered)
    max_power = 1 << (table_count.bit_length() - 1)
    search_range = max_power * 16
    entry_selector = max_power.bit_length() - 1
    range_shift = table_count * 16 - search_range
    offset = 12 + table_count * 16
    records: list[tuple[str, int, int, int]] = []
    for tag, data in ordered:
        offset += -offset % 4
        records.append((tag, sfnt_checksum(data), offset, len(data)))
        offset += len(data)
    font = bytearray(
        struct.pack(
            ">IHHHH",
            0x00010000,
            table_count,
            search_range,
            entry_selector,
            range_shift,
        )
    )
    for tag, checksum, table_offset, length in records:
        font.extend(
            struct.pack(
                ">4sIII",
                tag.encode("ascii"),
                checksum,
                table_offset,
                length,
            )
        )
    for (_, data), (_, _, table_offset, _) in zip(ordered, records, strict=True):
        font.extend(b"\0" * (table_offset - len(font)))
        font.extend(data)
    font.extend(b"\0" * (-len(font) % 4))
    head_offset = next(table_offset for tag, _, table_offset, _ in records if tag == "head")
    adjustment = (0xB1B0AFBA - sfnt_checksum(font)) & 0xFFFFFFFF
    struct.pack_into(">I", font, head_offset + 8, adjustment)
    return bytes(font)


def _narrative_ttf() -> bytes:
    expected_characters = tuple(chr(codepoint) for codepoint in range(0x20, 0x7F))
    if tuple(sorted(_NARRATIVE_GLYPH_ROWS, key=ord)) != expected_characters:
        raise ValueError("narrative glyph source must exactly cover printable ASCII")

    notdef_rows = (
        "11111",
        "10001",
        "10101",
        "10101",
        "10101",
        "10001",
        "11111",
    )
    glyphs: list[bytes] = []
    maximum_points = 0
    maximum_contours = 0
    for rows in (notdef_rows, *(_NARRATIVE_GLYPH_ROWS[char] for char in expected_characters)):
        glyph, point_count, contour_count = _bitmap_outline_glyph(rows)
        glyphs.append(glyph)
        maximum_points = max(maximum_points, point_count)
        maximum_contours = max(maximum_contours, contour_count)
    offsets = [0]
    glyf = bytearray()
    for glyph in glyphs:
        glyf.extend(glyph)
        offsets.append(len(glyf))

    cmap_subtable = struct.pack(">7H", 4, 32, 0, 4, 4, 1, 0)
    cmap_subtable += struct.pack(">2H", 0x007E, 0xFFFF)
    cmap_subtable += struct.pack(">H", 0)
    cmap_subtable += struct.pack(">2H", 0x0020, 0xFFFF)
    cmap_subtable += struct.pack(">2H", 0xFFE1, 1)
    cmap_subtable += struct.pack(">2H", 0, 0)
    cmap = struct.pack(">HHHHI", 0, 1, 3, 1, 12) + cmap_subtable

    glyph_count = len(glyphs)
    os2 = struct.pack(
        ">HhHHH11h",
        0,
        600,
        400,
        5,
        0,
        650,
        600,
        0,
        75,
        650,
        600,
        0,
        350,
        50,
        250,
        0,
    )
    os2 += bytes(10)
    os2 += struct.pack(
        ">4I4sHHHhhhHH",
        1,
        0,
        0,
        0,
        b"WFGE",
        0x0040,
        0x0020,
        0x007E,
        800,
        -200,
        0,
        800,
        200,
    )
    tables = {
        "OS/2": os2,
        "cmap": cmap,
        "glyf": bytes(glyf),
        "head": struct.pack(
            ">IIIIHHQQhhhhHHhhh",
            0x00010000,
            0x0001199A,
            0,
            0x5F0F3CF5,
            0x000B,
            1000,
            0,
            0,
            0,
            0,
            530,
            700,
            0,
            8,
            2,
            1,
            0,
        ),
        "hhea": struct.pack(
            ">IhhhH11hH",
            0x00010000,
            800,
            -200,
            0,
            600,
            0,
            70,
            530,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            glyph_count,
        ),
        "hmtx": b"".join(
            struct.pack(">Hh", 600, 0 if index == 1 else 50) for index in range(glyph_count)
        ),
        "loca": struct.pack(f">{len(offsets)}I", *offsets),
        "maxp": struct.pack(
            ">I14H",
            0x00010000,
            glyph_count,
            maximum_points,
            maximum_contours,
            0,
            0,
            2,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        ),
        "name": _font_name_table(),
        "post": struct.pack(
            ">IIhhIIIII",
            0x00030000,
            0,
            -75,
            50,
            1,
            0,
            0,
            0,
            0,
        ),
    }
    return _assemble_sfnt(tables)


def _identity(document: dict[str, Any], id_field: str) -> dict[str, Any]:
    return {
        "format": document["format"],
        "format_version": document["format_version"],
        "id": document[id_field],
        "content_hash": document["content_hash"],
    }


class _CanonicalFixtureAuthorityResolver:
    """Code-owned resolver used only while deterministically generating known fixtures."""

    def __init__(self, case: str, gamepack: dict[str, Any]) -> None:
        if case not in CASES or gamepack.get("content_hash") != KNOWN_FIXTURE_GAMEPACK_HASHES[case]:
            raise ValueError("fixture authority accepts only an exact canonical gamepack case")
        self.case = case
        self.reviews: dict[tuple[str, str], RetainedAssetQaReviewRecord] = {}
        self.releases: dict[tuple[str, str], RetainedAssetReleaseAuthorityRecord] = {}

    def resolve_asset_qa_review(
        self,
        *,
        review_receipt_id: str,
        content_hash: str,
    ) -> RetainedAssetQaReviewRecord:
        try:
            return self.reviews[(review_receipt_id, content_hash)]
        except KeyError as exc:
            raise GenericAssetAuthorityError(
                "authority_resolver_failed",
                "canonical fixture review authority is not retained",
            ) from exc

    def resolve_asset_release_authority(
        self,
        *,
        release_authority_id: str,
        content_hash: str,
    ) -> RetainedAssetReleaseAuthorityRecord:
        try:
            return self.releases[(release_authority_id, content_hash)]
        except KeyError as exc:
            raise GenericAssetAuthorityError(
                "authority_resolver_failed",
                "canonical fixture release authority is not retained",
            ) from exc


def _fixture_authority_binding(
    case: str,
    gamepack: dict[str, Any],
    *,
    operation: str,
    output_position: int,
    snapshot_hash: str,
) -> dict[str, Any]:
    suffix = "review" if operation == "asset.qa.review" else "release"
    return {
        "workspace_id": f"fixture-{case}",
        "root_generation": 1,
        "source_revision": gamepack["content_hash"],
        "workflow_status_hash": None,
        "artifact_snapshot_hash": snapshot_hash,
        "producer_job_id": f"fixture-{case}-{suffix}",
        "producer_operation": operation,
        "producer_output_position": output_position,
    }


def _verified_fixture_reviews(
    case: str,
    *,
    gamepack: dict[str, Any],
    specification: dict[str, Any],
    processing_receipt: dict[str, Any],
    qa_report: dict[str, Any],
    artifact_root: Path,
) -> tuple[
    list[dict[str, Any]],
    list[VerifiedAssetQaReview],
    _CanonicalFixtureAuthorityResolver,
]:
    resolver = _CanonicalFixtureAuthorityResolver(case, gamepack)
    criteria = specification["acceptance_criteria"]
    documents: list[dict[str, Any]] = []
    handles: list[VerifiedAssetQaReview] = []
    for position, output in enumerate(qa_report["outputs"]):
        retained_output = (artifact_root / output["locator"]).read_bytes()
        snapshot_hash = canonical_creation_hash(
            {
                "case": case,
                "specification": _identity(specification, "spec_id"),
                "processing_receipt": _identity(
                    processing_receipt,
                    "processing_receipt_id",
                ),
                "qa_report": _identity(qa_report, "qa_report_id"),
                "output": {
                    "role": output["role"],
                    "sha256": output["sha256"],
                    "size_bytes": output["size_bytes"],
                },
            }
        )
        binding = _fixture_authority_binding(
            case,
            gamepack,
            operation="asset.qa.review",
            output_position=position,
            snapshot_hash=snapshot_hash,
        )
        review = build_asset_qa_review_receipt(
            qa_report,
            specification,
            processing_receipt,
            review_receipt_id=f"{case.replace('-', '_')}_{output['role']}_qa_review",
            output_role=output["role"],
            decisions=["approved"] * len(criteria),
            blockers=[],
            authority=binding,
            retained_output=retained_output,
        )
        payload = serialize_asset_qa_review_receipt(review)
        resolver.reviews[(review["review_receipt_id"], review["content_hash"])] = (
            RetainedAssetQaReviewRecord(
                document_bytes=payload,
                document_blob_sha256=hashlib.sha256(payload).hexdigest(),
                document_size_bytes=len(payload),
                specification_bytes=canonical_json_bytes(specification),
                processing_receipt_bytes=canonical_json_bytes(processing_receipt),
                qa_report_bytes=canonical_json_bytes(qa_report),
                retained_output_bytes=retained_output,
                retained_output_sha256=hashlib.sha256(retained_output).hexdigest(),
                retained_output_size_bytes=len(retained_output),
                **binding,
            )
        )
        documents.append(review)
        handles.append(verify_asset_qa_review(review, resolver=resolver))
    return documents, handles, resolver


def _verified_fixture_release(
    case: str,
    *,
    gamepack: dict[str, Any],
    manifest: dict[str, Any],
    assetpack: dict[str, Any],
    reviews: list[VerifiedAssetQaReview],
    resolver: _CanonicalFixtureAuthorityResolver,
) -> tuple[dict[str, Any], VerifiedAssetReleaseAuthority]:
    snapshot_hash = canonical_creation_hash(
        {
            "case": case,
            "manifest": _identity(manifest, "manifest_id"),
            "assetpack": _identity(assetpack, "assetpack_id"),
            "reviews": [dict(review.identity) for review in reviews],
        }
    )
    binding = _fixture_authority_binding(
        case,
        gamepack,
        operation="asset.release.authorize",
        output_position=0,
        snapshot_hash=snapshot_hash,
    )
    release = build_asset_release_authority(
        manifest,
        assetpack,
        reviews,
        release_authority_id=f"{case.replace('-', '_')}_asset_release",
        blockers=[],
        authority=binding,
    )
    payload = serialize_asset_release_authority(release)
    resolver.releases[(release["release_authority_id"], release["content_hash"])] = (
        RetainedAssetReleaseAuthorityRecord(
            document_bytes=payload,
            document_blob_sha256=hashlib.sha256(payload).hexdigest(),
            document_size_bytes=len(payload),
            **binding,
        )
    )
    verified = verify_asset_release_authority(
        release,
        manifest=manifest,
        assetpack=assetpack,
        reviews=reviews,
        resolver=resolver,
    )
    return release, verified


def _production_documents(
    *,
    case: str,
    gamepack: dict[str, Any],
    subject: dict[str, Any],
    target: dict[str, Any],
    style: dict[str, Any],
    inventory: dict[str, Any],
    specification: dict[str, Any],
    artifact_root: Path,
    source_root: Path,
) -> tuple[tuple[Path, dict[str, Any] | None, bytes], ...]:
    descriptor = ASSET_FIXTURES[case]
    asset_id = specification["asset"]["asset_id"]
    production_root = Path("assets") / "production" / asset_id
    semantic_png = case in SEMANTIC_PNG_CASES
    semantic_acceptance_results: list[dict[str, Any]] | None = None
    if descriptor["kind"] != "font":
        operation_id = "generate_png"
        operation_version = 2 if semantic_png else 1
        candidate_id = f"{asset_id}_candidate"
        candidate_relative = production_root / "candidates" / descriptor["candidate_name"]
        candidate_payload = _authoring_png(case) if semantic_png else _puzzle_png()
        seed = descriptor.get(
            "seed", int.from_bytes(hashlib.sha256(case.encode("utf-8")).digest()[:4], "big")
        )
        review_profile = descriptor["review_profile"]
        tool_version = "1.1.0" if semantic_png else "1.0.0"
        if semantic_png:
            criterion_evidence = ()
            qa_evidence = (
                _evidence_hash(
                    f"{case}-candidate-generation-log-v2",
                    candidate_payload,
                ),
            )
        else:
            criterion_evidence = ()
            qa_evidence = (
                descriptor.get("qa_evidence", _evidence_hash(f"{case}-qa-v1", candidate_payload)),
            )
        selection_evidence = (
            descriptor.get(
                "selection_evidence", _evidence_hash(f"{case}-selection-v1", candidate_payload)
            ),
        )
        provenance_evidence = descriptor.get(
            "provenance_evidence",
            _evidence_hash(f"{case}-project-authored-design-v1", candidate_payload),
        )
        component_license_evidence = descriptor.get(
            "component_license_evidence",
            _evidence_hash("fixture-generator-license-v1", b"MIT"),
        )
        notice = descriptor.get(
            "notice",
            f"{descriptor['asset_id']} fixture bytes are project-authored and dedicated "
            "to the public domain under CC0-1.0.",
        )
        license_evidence = (
            descriptor.get(
                "license_evidence", _evidence_hash(f"{case}-cc0-notice-v1", notice.encode())
            ),
        )
        started_evidence = descriptor.get(
            "started_evidence",
            _evidence_hash(
                f"{case}-generation-start-v1", specification["content_hash"].encode("ascii")
            ),
        )
        completed_evidence = descriptor.get(
            "completed_evidence",
            _evidence_hash(f"{case}-generation-complete-v1", candidate_payload),
        )
        rights_evidence = (
            descriptor.get(
                "rights_evidence",
                _evidence_hash(
                    f"{case}-project-authorship-rights-v1",
                    b"World Forge contributors; CC0-1.0",
                ),
            ),
        )
    else:
        operation_id = "generate_font"
        operation_version = 2
        candidate_id = "narrative_ui_font_candidate"
        candidate_relative = production_root / "candidates" / "narrative-ui.ttf"
        candidate_payload = _narrative_ttf()
        seed = 20260728
        review_profile = "localized_text"
        tool_version = "1.1.0"
        criterion_evidence = _narrative_qa_evidence(candidate_payload)
        qa_evidence = tuple(sorted(criterion_evidence))
        selection_evidence = (_evidence_hash("narrative-font-selection-v1", candidate_payload),)
        provenance_evidence = _evidence_hash(
            "narrative-font-project-authored-design-v1",
            "\n".join(
                f"{character}:{'/'.join(rows)}"
                for character, rows in sorted(
                    _NARRATIVE_GLYPH_ROWS.items(),
                    key=lambda item: ord(item[0]),
                )
            ).encode("ascii"),
        )
        component_license_evidence = _evidence_hash(
            "fixture-generator-license-v1",
            b"MIT",
        )
        notice = (
            "World Forge Tiny Fixture glyph designs and generated font bytes "
            "are project-authored and dedicated to the public domain under CC0-1.0."
        )
        license_evidence = (_evidence_hash("narrative-font-cc0-notice-v1", notice.encode("utf-8")),)
        started_evidence = _evidence_hash(
            "narrative-font-generation-start-v1",
            specification["content_hash"].encode("ascii"),
        )
        completed_evidence = _evidence_hash(
            "narrative-font-generation-complete-v1",
            candidate_payload,
        )
        rights_evidence = (
            _evidence_hash(
                "narrative-font-project-authorship-rights-v1",
                b"World Forge contributors; CC0-1.0",
            ),
        )
    candidate_path = artifact_root / candidate_relative
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_bytes(candidate_payload)
    toolchain = {
        "production_class": "procedural_offline",
        "tool_id": "world_forge_fixture_generator",
        "tool_version": tool_version,
        "operation_id": operation_id,
        "seed": seed,
    }
    request = build_asset_production_request(
        gamepack,
        subject,
        target,
        style,
        inventory,
        specification,
        request_id=f"{asset_id}_production",
        production_class="procedural_offline",
        operation={"operation_id": operation_id, "version": operation_version},
        input_artifacts=[],
        reproducibility={"mode": "deterministic", "seed_policy": "fixed"},
        rights_requirements={
            "commercial_use_review_required": True,
            "evidence_required": True,
            "human_review_required": True,
            "redistribution_review_required": True,
        },
        toolchain_requirements=toolchain,
    )
    receipt = build_asset_production_receipt(
        request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
        receipt_id=f"{asset_id}_receipt",
        status="completed",
        executed_toolchain=toolchain,
        candidates=[
            {
                "role": specification["outputs"][0]["role"],
                "candidate_artifact_id": candidate_id,
                "locator": candidate_relative.as_posix(),
            }
        ],
        artifact_root=artifact_root,
        execution_evidence={
            "started_evidence_hash": started_evidence,
            "completed_evidence_hash": completed_evidence,
            "sanitized_log_hashes": list(qa_evidence),
        },
        rights_attestation={
            "basis": "fixture_public_domain",
            "evidence_hashes": list(rights_evidence),
        },
    )
    selection = build_asset_selection(
        receipt,
        request=request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
        artifact_root=artifact_root,
        selection_id=f"{asset_id}_selection",
        review={
            "reviewer_id": "fixture_asset_reviewer",
            "rationale": (
                "The exact candidate bytes were selected for deterministic processing and "
                "semantic QA."
                if semantic_png
                else (
                    f"The exact candidate bytes satisfy the reviewed {review_profile} requirements."
                )
            ),
            "evidence_hashes": list(selection_evidence),
        },
    )
    provenance = build_asset_provenance_record(
        selection,
        receipt=receipt,
        request=request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
        artifact_root=artifact_root,
        provenance_id=f"{asset_id}_provenance",
        component_evidence=[
            {
                "scope": "generator_tool",
                "component_id": "world_forge_fixture_generator",
                "component_version": tool_version,
                "evidence_hash": provenance_evidence,
            }
        ],
    )
    license_record = build_asset_license_record(
        provenance,
        selection=selection,
        receipt=receipt,
        request=request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
        artifact_root=artifact_root,
        license_record_id=f"{asset_id}_license",
        candidate_artifact_id=candidate_id,
        license_basis={"kind": "spdx", "identifier": "CC0-1.0"},
        copyright={
            "holder": "World Forge contributors",
            "year_policy": "not_applicable",
            "year": None,
        },
        permissions={
            "commercial_use": True,
            "modification": True,
            "redistribution": True,
        },
        obligations={
            "attribution_required": False,
            "notice_required": True,
            "source_offer_required": False,
        },
        component_licenses=[
            {
                "scope": "generator_tool",
                "component_id": "world_forge_fixture_generator",
                "identifier": "MIT",
                "evidence_hash": component_license_evidence,
            }
        ],
        runtime_notice_text=notice,
        evidence_hashes=list(license_evidence),
    )
    lineage = {
        "gamepack": gamepack,
        "subject": subject,
        "target": target,
        "style": style,
        "inventory": inventory,
        "specification": specification,
        "request": request,
        "receipt": receipt,
        "selection": selection,
        "provenance": provenance,
        "license_records": [license_record],
        "artifact_root": artifact_root,
    }
    recipe = build_asset_processing_recipe(
        recipe_id=f"{asset_id}_recipe",
        **lineage,
    )
    processing_receipt = build_asset_processing_receipt(
        recipe,
        processing_receipt_id=f"{asset_id}_processing_receipt",
        **lineage,
    )
    processed = processing_receipt["outputs"]
    if len(processed) != 1:
        raise ValueError(f"{case} fixture must have exactly one processed output")
    processed_output = processed[0]
    processed_locator = Path(processed_output["locator"])
    processed_payload = (artifact_root / processed_locator).read_bytes()
    if semantic_png:
        semantic_acceptance_results = _evaluate_processed_png_acceptance(
            case,
            processed_payload,
            processed_output,
        )
    if semantic_acceptance_results is not None:
        acceptance_results = semantic_acceptance_results
    else:
        acceptance_results = []
        for index, criterion in enumerate(specification["acceptance_criteria"]):
            if descriptor["kind"] == "font":
                evidence_hashes = [criterion_evidence[index]]
            else:
                evidence_hashes = [
                    hashlib.sha256(f"{case}\0acceptance\0{index}".encode()).hexdigest()
                ]
            acceptance_results.append(
                {
                    "criterion_index": index,
                    "criterion_sha256": hashlib.sha256(str(criterion).encode("utf-8")).hexdigest(),
                    "status": "passed",
                    "evidence_hashes": evidence_hashes,
                }
            )
    qa_report = build_asset_qa_report(
        processing_receipt,
        recipe=recipe,
        qa_report_id=f"{asset_id}_qa",
        acceptance_results=acceptance_results,
        **lineage,
    )
    manifest_state = (
        _manifest_state_for_acceptance(acceptance_results)
        if qa_report["status"] == "passed"
        else "processed"
    )
    if manifest_state != "release_ready":
        raise ValueError(f"{case} canonical fixture QA did not authorize release")
    record = {
        "specification": specification,
        "request": request,
        "receipt": receipt,
        "selection": selection,
        "provenance": provenance,
        "license_records": [license_record],
        "recipe": recipe,
        "processing_receipt": processing_receipt,
        "qa_report": qa_report,
    }
    review_documents, verified_reviews, authority_resolver = _verified_fixture_reviews(
        case,
        gamepack=gamepack,
        specification=specification,
        processing_receipt=processing_receipt,
        qa_report=qa_report,
        artifact_root=artifact_root,
    )
    manifest = build_asset_manifest(
        gamepack,
        subject,
        target,
        style,
        inventory,
        manifest_id=f"{case.replace('-', '_')}_asset_manifest",
        state=manifest_state,
        asset_records=[record],
        artifact_root=artifact_root,
        qa_reviews=verified_reviews,
    )
    assetpack = build_generic_assetpack_manifest(
        manifest,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        asset_records=[record],
        artifact_root=artifact_root,
        qa_reviews=verified_reviews,
    )
    release_authority, verified_release = _verified_fixture_release(
        case,
        gamepack=gamepack,
        manifest=manifest,
        assetpack=assetpack,
        reviews=verified_reviews,
        resolver=authority_resolver,
    )
    if not verified_release.authorized:
        raise ValueError(f"{case} canonical fixture release authority is not authorized")
    case_root = source_root / case
    review_values = tuple(
        (
            case_root / production_root / f"qa-review-{review['reviewed_output']['role']}.json",
            review,
            serialize_asset_qa_review_receipt(review),
        )
        for review in review_documents
    )
    values = (
        (case_root / candidate_relative, None, candidate_payload),
        (case_root / processed_locator, None, processed_payload),
        (
            case_root / production_root / "request.json",
            request,
            serialize_production_contract(request),
        ),
        (
            case_root / production_root / "receipt.json",
            receipt,
            serialize_production_contract(receipt),
        ),
        (
            case_root / production_root / "selection.json",
            selection,
            serialize_production_contract(selection),
        ),
        (
            case_root / production_root / "provenance.json",
            provenance,
            serialize_production_contract(provenance),
        ),
        (
            case_root / production_root / "license.json",
            license_record,
            serialize_production_contract(license_record),
        ),
        (
            case_root / production_root / "recipe.json",
            recipe,
            serialize_asset_processing_contract(recipe),
        ),
        (
            case_root / production_root / "processing-receipt.json",
            processing_receipt,
            serialize_asset_processing_contract(processing_receipt),
        ),
        (
            case_root / production_root / "qa-report.json",
            qa_report,
            serialize_asset_processing_contract(qa_report),
        ),
        (
            case_root / "assets" / "manifest.json",
            manifest,
            serialize_asset_processing_contract(manifest),
        ),
        (
            case_root / "assets" / "release-authority.json",
            release_authority,
            serialize_asset_release_authority(release_authority),
        ),
    )
    return tuple(values[:-2]) + review_values + tuple(values[-2:])


def _build_fixture_plan(
    case: str,
    *,
    source_root: Path | None = None,
) -> tuple[
    tuple[tuple[Path, dict[str, Any], bytes], ...],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    examples_root = EXAMPLES if source_root is None else source_root
    try:
        descriptor = ASSET_FIXTURES[case]
    except KeyError as exc:
        raise ValueError(f"unsupported generic asset fixture: {case}") from exc
    gamepack = load_gamepack(
        examples_root / case / "artifacts" / f"{case}.gamepack.json",
    )
    subject = build_asset_subject(gamepack)
    target = build_asset_target(
        gamepack,
        subject,
        review=_review(
            evidence_id="binding_review",
            content_hash="1" * 64,
            rationale="The binding plan was reviewed against every compiled requirement.",
        ),
        bindings=_fixture_bindings(descriptor),
    )
    style = build_asset_style(
        gamepack,
        subject,
        target,
        reviewer=_review(
            evidence_id="style_review",
            content_hash="2" * 64,
            rationale="The visual and audio directions were reviewed against the runtime target.",
        ),
        visual=_visual(gamepack),
        audio=_audio(),
    )
    inventory = build_asset_inventory(gamepack, subject, target, style)
    specification = _fixture_spec(
        descriptor,
        gamepack,
        subject,
        target,
        style,
        inventory,
    )
    root = examples_root / case / "assets"
    values = (
        (root / "subject.json", subject),
        (root / "target.json", target),
        (root / "style.json", style),
        (root / "inventory.json", inventory),
        (root / "specs" / f"{specification['asset']['asset_id']}.json", specification),
    )
    planning: tuple[tuple[Path, dict[str, Any], bytes], ...] = tuple(
        (path, document, serialize_asset_contract(document)) for path, document in values
    )
    return planning, gamepack, subject, target, style, inventory, specification


def build_fixture_planning_documents(
    case: str,
    *,
    source_root: Path | None = None,
) -> tuple[tuple[Path, dict[str, Any], bytes], ...]:
    """Build the deterministic planning contracts without publishing production artifacts."""

    planning, *_ = _build_fixture_plan(case, source_root=source_root)
    return planning


def build_fixture_documents(
    case: str,
    *,
    artifact_root: Path | None = None,
    source_root: Path | None = None,
) -> tuple[tuple[Path, dict[str, Any] | None, bytes], ...]:
    planning, gamepack, subject, target, style, inventory, specification = _build_fixture_plan(
        case,
        source_root=source_root,
    )
    examples_root = EXAMPLES if source_root is None else source_root
    if artifact_root is not None:
        return planning + _production_documents(
            case=case,
            gamepack=gamepack,
            subject=subject,
            target=target,
            style=style,
            inventory=inventory,
            specification=specification,
            artifact_root=artifact_root,
            source_root=examples_root,
        )
    with tempfile.TemporaryDirectory(prefix="world-forge-generic-assets-") as temporary:
        return planning + _production_documents(
            case=case,
            gamepack=gamepack,
            subject=subject,
            target=target,
            style=style,
            inventory=inventory,
            specification=specification,
            artifact_root=Path(temporary),
            source_root=examples_root,
        )


def _write(path: Path, document: dict[str, Any] | None, payload: bytes) -> None:
    if document is None:
        _write_binary_create_only(path, payload)
        return
    if path.exists():
        current = read_json_object(path)
        expected_content_hash = current.get("content_hash")
        if not isinstance(expected_content_hash, str):
            raise ValueError(f"{path} has no string content_hash")
        write_json_cooperative_replace(
            path,
            document,
            expected_cooperative_content_hash=expected_content_hash,
        )
    else:
        write_json_atomic(path, document)


def _write_binary_create_only(path: Path, payload: bytes) -> None:
    path = prepare_output_path(path)
    try:
        relative = PurePosixPath(path.relative_to(ROOT).as_posix())
    except ValueError as exc:
        raise ValueError(f"Fixture output escapes the repository: {path}") from exc
    parent_path = ROOT.joinpath(*relative.parts[:-1])
    descriptor: int | None = None
    try:
        with _pinned_ancestor_identities(ROOT, context="generic fixture root") as root_ids:
            with _pinned_ancestor_identities(
                parent_path,
                context=f"generic fixture parent {relative.parent}",
            ) as parent_ids:
                with _open_pinned_parent(
                    ROOT,
                    relative,
                    world_identity=root_ids[-1],
                    parent_identity=parent_ids[-1],
                ) as parent:
                    _reject_pinned_collision(
                        parent,
                        relative.name,
                        context=f"generic fixture output {relative}",
                    )
                    if parent.entry_info(relative.name) is not None:
                        current, _ = _safe_entry_snapshot(
                            parent,
                            relative.name,
                            context=f"generic fixture output {relative}",
                            require_standalone=True,
                            require_utf8=False,
                            limit=max(1, len(payload)),
                        )
                        if current != payload:
                            raise ValueError(
                                f"Refusing to replace differing binary fixture: {path}"
                            )
                        return
                    flags = (
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0)
                    )
                    descriptor = parent.open_entry(relative.name, flags, 0o644)
                    created = os.fstat(descriptor)
                    created_identity = (created.st_dev, created.st_ino)
                    view = memoryview(payload)
                    while view:
                        written = os.write(descriptor, view)
                        if written <= 0:
                            raise OSError("binary fixture write made no progress")
                        view = view[written:]
                    os.fsync(descriptor)
                    final = os.fstat(descriptor)
                    if (
                        not stat.S_ISREG(final.st_mode)
                        or final.st_nlink != 1
                        or final.st_size != len(payload)
                        or (final.st_dev, final.st_ino) != created_identity
                    ):
                        raise OSError("binary fixture output identity changed")
                    os.close(descriptor)
                    descriptor = None
                    visible = parent.entry_info(relative.name)
                    if (
                        visible is None
                        or not stat.S_ISREG(visible.st_mode)
                        or visible.st_nlink != 1
                        or (visible.st_dev, visible.st_ino) != created_identity
                    ):
                        raise OSError("binary fixture output is not the created file")
                    parent.flush()
    except StudioError as exc:
        raise ValueError(
            f"Could not securely publish binary fixture {path}: {exc.message}"
        ) from exc
    except OSError as exc:
        raise ValueError(f"Could not securely publish binary fixture {path}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        # A named destination cannot be unlinked conditionally by file identity
        # on every supported platform. Preserve a failed create-only output
        # rather than risk deleting a foreign replacement through a path race.


def _fixture_bytes_match(
    path: Path,
    expected: bytes,
    *,
    root: Path | None = None,
) -> bool:
    fixture_root = ROOT if root is None else root
    try:
        relative = path.relative_to(fixture_root).as_posix()
        actual = _safe_artifact_bytes(
            fixture_root,
            relative,
            limit=max(1, len(expected)),
        )
    except (GenericAssetProductionError, ValueError):
        return False
    return actual == expected


def _resolve_selected_cases(
    parser: argparse.ArgumentParser,
    selections: list[str] | None,
) -> tuple[str, ...]:
    if selections is None:
        return CASES
    selected: set[str] = set()
    for case in selections:
        if not case:
            parser.error("--case CASE_ID cannot be empty")
        if case not in CASES:
            parser.error(f"unknown --case CASE_ID {case!r}")
        if case in selected:
            parser.error(f"duplicate --case CASE_ID {case!r}")
        selected.add(case)
    return tuple(case for case in CASES if case in selected)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate or verify trusted generic gamepack asset fixtures",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--write",
        action="store_true",
        help="write or cooperatively replace canonical fixtures instead of checking them",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="verify canonical fixtures (the default)",
    )
    parser.add_argument(
        "--case",
        dest="selected_cases",
        action="append",
        metavar="CASE_ID",
        help="limit generation or verification to one canonical case; may be repeated",
    )
    args = parser.parse_args(argv)
    selected_cases = _resolve_selected_cases(parser, args.selected_cases)
    mismatches: list[Path] = []
    total = 0
    for case in selected_cases:
        for path, document, expected in build_fixture_documents(case):
            total += 1
            if args.write:
                _write(path, document, expected)
            elif not _fixture_bytes_match(path, expected):
                mismatches.append(path)
    if mismatches:
        for path in mismatches:
            print(f"ERROR fixture differs: {path.relative_to(ROOT)}")
        return 1
    print(f"OK generic_asset_fixtures={total} mode={'write' if args.write else 'check'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
