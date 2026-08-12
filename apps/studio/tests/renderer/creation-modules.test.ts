import { describe, expect, it } from "vitest";

import {
  creationModulePreview,
  parseCreationModuleJson,
  resolveCreationModuleReferences,
  validateCreationModuleId,
  validateCreationModuleDocument,
} from "../../src/renderer/creation-modules";

describe("generic creation module contracts", () => {
  it("derives typed module paths only from the canonical source manifest", () => {
    const references = resolveCreationModuleReferences("source/manifest.json", {
      format: "world-forge.creation_source_manifest",
      format_version: 1,
      project_id: "neutral_game",
      content_hash: "a".repeat(64),
      extensions: [],
      profile: {
        format: "world-forge.creation_profile",
        format_version: 1,
        id: "neutral_profile",
        path: "profile.json",
        content_hash: "b".repeat(64),
      },
      modules: {
        world_modules: [],
        activity_modules: [],
        narrative_modules: [],
        system_modules: [],
        logic_modules: [{
          format: "world-forge.logic_module",
          format_version: 1,
          id: "core",
          path: "logic/core.json",
          content_hash: "c".repeat(64),
        }],
      },
    });

    expect(references).toEqual([expect.objectContaining({
      collection: "logic_modules",
      format: "world-forge.logic_module",
      id: "core",
      manifestPath: "logic/core.json",
      projectPath: "source/logic/core.json",
    })]);
  });

  it("rejects duplicate JSON keys, non-finite overflow, unsafe paths, and NFC/case collisions", () => {
    expect(() => parseCreationModuleJson('{"module_id":"a","module_id":"b"}', "logic module")).toThrow(/duplicate/u);
    expect(() => parseCreationModuleJson('{"value":1e999}', "logic module")).toThrow(/finite/u);
    expect(() => parseCreationModuleJson("[]", "logic module")).toThrow(/object root/u);
    const manifest = baseManifest();
    manifest.modules.logic_modules.push({
      format: "world-forge.logic_module",
      format_version: 1,
      id: "CORE",
      path: "logic/../logic/case.json",
      content_hash: "d".repeat(64),
    });
    expect(() => resolveCreationModuleReferences("source/manifest.json", manifest)).toThrow();
  });

  it("binds edited document identity to its manifest reference and renders canonical preview", () => {
    const reference = resolveCreationModuleReferences(
      "source/manifest.json",
      baseManifest(),
    )[0];
    const document = validateCreationModuleDocument({
      format: "world-forge.logic_module",
      format_version: 1,
      module_id: "core",
      project_id: "neutral_game",
      title: "Core",
      content_hash: "c".repeat(64),
    }, reference, "neutral_game");
    expect(creationModulePreview(document)).toBe(
      `${JSON.stringify({
        content_hash: "c".repeat(64),
        format: "world-forge.logic_module",
        format_version: 1,
        module_id: "core",
        project_id: "neutral_game",
        title: "Core",
      }, null, 2)}\n`,
    );
    expect(() => validateCreationModuleDocument(
      { ...document, module_id: "other" },
      reference,
      "neutral_game",
    )).toThrow(/identity/u);
  });

  it("uses the same lowercase module ID contract as trusted IPC and Python", () => {
    expect(validateCreationModuleId("core_v1")).toBe("core_v1");
    for (const value of ["Core", "core.v1", "core:v1", "a".repeat(129)] as const) {
      expect(() => validateCreationModuleId(value)).toThrow(/ID/u);
    }
  });
});

interface TestManifest extends Record<string, unknown> {
  modules: {
    world_modules: Record<string, unknown>[];
    activity_modules: Record<string, unknown>[];
    narrative_modules: Record<string, unknown>[];
    system_modules: Record<string, unknown>[];
    logic_modules: Record<string, unknown>[];
  };
}

function baseManifest(): TestManifest {
  return {
    format: "world-forge.creation_source_manifest",
    format_version: 1,
    project_id: "neutral_game",
    content_hash: "a".repeat(64),
    extensions: [],
    profile: {
      format: "world-forge.creation_profile",
      format_version: 1,
      id: "neutral_profile",
      path: "profile.json",
      content_hash: "b".repeat(64),
    },
    modules: {
      world_modules: [],
      activity_modules: [],
      narrative_modules: [],
      system_modules: [],
      logic_modules: [{
        format: "world-forge.logic_module",
        format_version: 1,
        id: "core",
        path: "logic/core.json",
        content_hash: "c".repeat(64),
      }],
    },
  };
}
