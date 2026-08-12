import { describe, expect, it } from "vitest";

import {
  creationProfilePreview,
  isCreationProfileDirty,
  parseCreationFacetJson,
  replaceCreationFacet,
  summarizeCreationFacet,
  validateCreationProfileDocument,
} from "../../src/renderer/creation-state";

const HASH = "a".repeat(64);

describe("generic creation profile state", () => {
  it("strictly parses a facet object and rejects duplicate keys or numeric overflow", () => {
    expect(
      parseCreationFacetJson(
        '{"presence":"none","simulated_domains":[]}',
        "world",
      ),
    ).toEqual({ presence: "none", simulated_domains: [] });
    expect(() =>
      parseCreationFacetJson('{"presence":"none","presence":"abstract"}', "world"),
    ).toThrow(/duplicate object key/u);
    expect(() =>
      parseCreationFacetJson('{"weight":1e400}', "production"),
    ).toThrow(/finite/u);
  });

  it("requires an object root and rejects prototype-pollution keys at any depth", () => {
    expect(() => parseCreationFacetJson("[]", "fiction")).toThrow(/object root/u);
    expect(() =>
      parseCreationFacetJson('{"nested":{"__proto__":{}}}', "fiction"),
    ).toThrow(/unsupported object key/u);
  });

  it("accepts only JSON grammar whitespace", () => {
    expect(parseCreationFacetJson("\t{\r\n}\n", "fiction")).toEqual({});
    for (const source of ["\u00a0{}", "{}\u2028", "\ufeff{}"] as const) {
      expect(() => parseCreationFacetJson(source, "fiction")).toThrow(/JSON/u);
    }
  });

  it("replaces only the selected facet while preserving no-world and narrative-none", () => {
    const profile = creationProfile();
    const next = replaceCreationFacet(profile, "fiction", {
      genres: ["mystery"],
      tones: ["focused"],
      tags: [],
    });

    expect(next.fiction).toEqual({
      genres: ["mystery"],
      tones: ["focused"],
      tags: [],
    });
    expect(next.world).toEqual(profile.world);
    expect(next.narrative).toEqual(profile.narrative);
    expect(next).not.toBe(profile);
  });

  it("normalizes a stable whole-profile preview and detects meaningful drafts", () => {
    const profile = creationProfile();
    const reordered = { ...profile, title: profile.title, format: profile.format };
    const preview = creationProfilePreview(reordered);

    expect(preview.startsWith('{\n  "content_hash"')).toBe(true);
    expect(preview.endsWith("\n")).toBe(true);
    expect(isCreationProfileDirty(profile, reordered)).toBe(false);
    expect(
      isCreationProfileDirty(
        profile,
        replaceCreationFacet(profile, "experience", {
          ...profile.experience,
          player_promise: "A different promise.",
        }),
      ),
    ).toBe(true);
  });

  it("validates the exact eight canonical facets and produces truthful summaries", () => {
    const profile = validateCreationProfileDocument(creationProfile());

    expect(summarizeCreationFacet("gameplay", profile.gameplay)).toMatch(/puzzle/iu);
    expect(summarizeCreationFacet("world", profile.world)).toBe("No world");
    expect(summarizeCreationFacet("narrative", profile.narrative)).toBe("No narrative");
    expect(summarizeCreationFacet("presentation", profile.presentation)).toContain("2D");
    expect(summarizeCreationFacet("runtime_target", profile.runtime_target)).toContain(
      "gamepack_raylib_2d_puzzle",
    );
    expect(() =>
      validateCreationProfileDocument({ ...creationProfile(), world: null }),
    ).toThrow(/world/u);
  });
});

function creationProfile() {
  return {
    content_hash: HASH,
    experience: {
      player_promise: "Solve a compact deterministic puzzle.",
      audiences: ["puzzle players"],
      experience_goals: ["clarity"],
    },
    extensions: [],
    fiction: { genres: [], tones: ["focused"], tags: [] },
    format: "world-forge.creation_profile" as const,
    format_version: 1 as const,
    gameplay: {
      primary_family: "puzzle",
      secondary_families: [],
      mechanic_tags: ["puzzle:swap"],
      player_role: "solver",
      core_verbs: [{ id: "swap", description: "Swap adjacent symbols." }],
      core_loop: ["inspect", "swap"],
      rule_model: "deterministic",
      goal_model: "match the target",
      challenge_model: "finite reasoning",
      failure_recovery: "restart",
      progression: "single challenge",
      teleology: "finite",
      session_structure: "short session",
      social_topology: "single_player",
      dependencies: { authored: [], systemic: [], procedural: [] },
    },
    narrative: {
      requirement: "none",
      authorship_mode: "none",
      topology: "none",
      delivery_channels: [],
      protagonist_model: "none",
      agency: "none",
      focalization: "none",
      canon_variability: "none",
      pacing: "none",
      endings: "none",
      information_model: "none",
    },
    presentation: {
      mode: "2d",
      camera: "fixed",
      perspective: "orthographic",
      visual_language: "geometric",
      ui_density: "low",
      audio_role: "feedback",
      input_assumptions: ["input:keyboard"],
      accessibility: {
        remapping: true,
        keyboard_only: true,
        captions: true,
        text_scaling: true,
        high_contrast: true,
        color_independence: true,
        reduced_motion: true,
        timing_alternatives: true,
        screen_reader_structure: true,
      },
      localization: {
        source_locale: "en",
        supported_locales: ["en"],
        externalized_text: true,
      },
    },
    production: {
      content_modes: {
        gameplay: "authored",
        world: "not_applicable",
        narrative: "not_applicable",
        assets: "authored",
      },
      seed_policy: "none",
      reproducibility: "content addressed",
      selection_policy: "reviewed",
      human_review: true,
      provenance_required: true,
      licensing_required: true,
      qa_required: true,
    },
    profile_id: "puzzle_profile",
    project_id: "puzzle_project",
    runtime_target: {
      requested_adapter: "gamepack_raylib_2d_puzzle",
      accepted_logic_formats: [{ format: "world-forge.gamepack", versions: [1] }],
      required_features: ["logic:finite_state"],
      optional_features: [],
      presentation_mode: "2d",
      platforms: ["platform:linux_x86_64"],
      renderer: "raylib",
      input_capabilities: ["input:keyboard"],
      asset_formats: ["asset:png"],
      save_expected: true,
      replay_expected: true,
      packaging_target: "standalone desktop directory",
    },
    title: "Abstract puzzle",
    world: {
      presence: "none",
      spatial_topology: "none",
      scale: "none",
      time_model: "none",
      simulation_depth: "none",
      simulated_domains: [],
      persistence: "none",
      spatial_structure: "none",
    },
  };
}
