import { describe, expect, it } from "vitest";

import type {
  StudioAssetCatalogEntry,
  StudioAssetInspection,
} from "../../src/shared/studio-api";
import {
  ASSET_CATALOG_CATEGORY_LABELS,
  ASSET_INSPECTION_KIND_LABELS,
  MAX_VISITED_CATALOG_OFFSETS,
  assetCatalogPageCategories,
  beginAssetCatalogInspection,
  beginAssetCatalogList,
  bindAssetCatalogWorkspace,
  createInitialAssetCatalogState,
  decodeAssetCatalogInspectReply,
  decodeAssetCatalogListReply,
  receiveAssetCatalogInspection,
  receiveAssetCatalogList,
  selectAssetCatalogCategory,
  type AssetCatalogCategory,
  type AssetCatalogListIntent,
  type AssetCatalogState,
} from "../../src/renderer/asset-catalog-state";

const REVISION_A = "a".repeat(64);
const REVISION_B = "b".repeat(64);
const ENTRY_1 = `asset_${"1".repeat(64)}`;
const ENTRY_2 = `asset_${"2".repeat(64)}`;

describe("revision-guarded asset catalog state", () => {
  it("ignores stale workspace, generation, and superseded list requests", () => {
    const state = boundState();
    const first = requireListTransition(state, "initial");
    const second = requireListTransition(first.state, "refresh");

    const superseded = receiveAssetCatalogList(
      second.state,
      first.intent,
      listReply({ entries: [entry()] }),
    );
    expect(superseded).toBe(second.state);

    const wrongGeneration = receiveAssetCatalogList(
      second.state,
      { ...second.intent, generation: 2 },
      listReply({ entries: [entry()] }),
    );
    expect(wrongGeneration).toBe(second.state);

    const moved = bindAssetCatalogWorkspace(second.state, "workspace_02", 2);
    expect(
      receiveAssetCatalogList(moved, second.intent, listReply({ entries: [entry()] })),
    ).toBe(moved);
    expect(second.intent.token).toBeGreaterThan(first.intent.token);
  });

  it("owns immutable list intents and rejects every forged correlation field", () => {
    const loaded = loadPage(boundState(), "initial", {
      entries: [entry()],
      nextOffset: 64,
    });
    const transition = requireListTransition(loaded, "next");
    const stored = transition.state.listRequest;
    if (!stored) throw new Error("Expected stored list request");

    expect(stored).not.toBe(transition.intent);
    expect(stored.page).not.toBe(transition.intent.page);
    expect(stored.visitedOffsetsAfterSuccess).not.toBe(
      transition.intent.visitedOffsetsAfterSuccess,
    );
    expect(Object.isFrozen(stored)).toBe(true);
    expect(Object.isFrozen(stored.page)).toBe(true);
    expect(Object.isFrozen(stored.visitedOffsetsAfterSuccess)).toBe(true);
    expect(Object.isFrozen(transition.intent)).toBe(true);
    expect(() => {
      (transition.intent as unknown as { mode: string }).mode = "previous";
    }).toThrow(TypeError);

    const forged: AssetCatalogListIntent[] = [
      { ...transition.intent, workspaceId: "workspace_02" },
      { ...transition.intent, generation: 2 },
      { ...transition.intent, token: transition.intent.token + 1 },
      { ...transition.intent, mode: "previous" },
      { ...transition.intent, offset: 128 },
      { ...transition.intent, expectedManifestRevision: REVISION_B },
      {
        ...transition.intent,
        page: { offset: 128, manifestRevision: REVISION_A },
      },
      {
        ...transition.intent,
        page: { offset: 64, manifestRevision: REVISION_B },
      },
      { ...transition.intent, visitedOffsetsAfterSuccess: [999] },
    ];
    const reply = listReply({
      offset: 64,
      entries: [entry(ENTRY_2)],
      nextOffset: null,
    });
    for (const intent of forged) {
      expect(receiveAssetCatalogList(transition.state, intent, reply)).toBe(
        transition.state,
      );
    }
    expect(
      receiveAssetCatalogList(
        transition.state,
        forged.at(-1) as AssetCatalogListIntent,
        protocolErrorReply("conflict", {}),
      ),
    ).toBe(transition.state);

    const exactCopy: AssetCatalogListIntent = {
      ...transition.intent,
      page: transition.intent.page ? { ...transition.intent.page } : undefined,
      visitedOffsetsAfterSuccess: [
        ...transition.intent.visitedOffsetsAfterSuccess,
      ],
    };
    const accepted = receiveAssetCatalogList(transition.state, exactCopy, reply);
    expect(accepted.currentOffset).toBe(64);
    expect(accepted.visitedOffsets).toEqual([0]);
  });

  it("binds forward and previous pages to one exact revision", () => {
    let state = loadPage(boundState(), "initial", {
      offset: 0,
      entries: [entry()],
      nextOffset: 64,
    });

    const next = requireListTransition(state, "next");
    expect(next.intent.page).toEqual({ offset: 64, manifestRevision: REVISION_A });
    state = receiveAssetCatalogList(
      next.state,
      next.intent,
      listReply({
        offset: 64,
        entries: [entry(ENTRY_2, { category: "audio_bible" })],
        nextOffset: 128,
      }),
    );
    expect(state.currentOffset).toBe(64);
    expect(state.visitedOffsets).toEqual([0]);

    const previous = requireListTransition(state, "previous");
    expect(previous.intent.page).toEqual({ offset: 0, manifestRevision: REVISION_A });
    state = receiveAssetCatalogList(
      previous.state,
      previous.intent,
      listReply({ offset: 0, entries: [entry()], nextOffset: 64 }),
    );
    expect(state.currentOffset).toBe(0);
    expect(state.visitedOffsets).toEqual([]);
  });

  it("bounds back history and evicts only the oldest offsets", () => {
    let state = loadPage(boundState(), "initial", {
      entries: [entry()],
      nextOffset: 1,
    });
    const forwardCount = MAX_VISITED_CATALOG_OFFSETS + 6;
    for (let offset = 1; offset <= forwardCount; offset += 1) {
      const next = requireListTransition(state, "next");
      state = receiveAssetCatalogList(
        next.state,
        next.intent,
        listReply({
          offset,
          entries: [entry()],
          nextOffset: offset + 1,
        }),
      );
      expect(state.visitedOffsets.length).toBeLessThanOrEqual(
        MAX_VISITED_CATALOG_OFFSETS,
      );
    }
    expect(state.currentOffset).toBe(forwardCount);
    expect(state.visitedOffsets).toHaveLength(MAX_VISITED_CATALOG_OFFSETS);
    expect(state.visitedOffsets[0]).toBe(6);

    for (let remaining = MAX_VISITED_CATALOG_OFFSETS; remaining > 0; remaining -= 1) {
      const previous = requireListTransition(state, "previous");
      state = receiveAssetCatalogList(
        previous.state,
        previous.intent,
        listReply({
          offset: previous.intent.offset,
          entries: [entry()],
          nextOffset: previous.intent.offset + 1,
        }),
      );
    }
    expect(state.currentOffset).toBe(6);
    expect(state.visitedOffsets).toEqual([]);
    expect(beginAssetCatalogList(state, "previous")).toBeNull();
  });

  it("refresh atomically clears page history, selection, and inspection", () => {
    let state = loadPage(boundState(), "initial", {
      entries: [entry()],
      nextOffset: 64,
    });
    state = selectAssetCatalogCategory(state, "visual_bible");
    const inspect = requireInspectionTransition(state, ENTRY_1);
    state = receiveAssetCatalogInspection(
      inspect.state,
      inspect.intent,
      inspectReply({ inspection: pngInspection() }),
    );
    expect(state.inspection?.kind).toBe("png");

    const next = requireListTransition(state, "next");
    state = receiveAssetCatalogList(
      next.state,
      next.intent,
      listReply({ offset: 64, entries: [entry()], nextOffset: null }),
    );
    expect(state.visitedOffsets).toEqual([0]);

    const refresh = requireListTransition(state, "refresh");
    expect(refresh.intent.offset).toBe(0);
    expect(refresh.intent.page).toBeUndefined();
    expect(refresh.state).toMatchObject({
      consistency: "unbound",
      manifestRevision: null,
      currentOffset: 0,
      visitedOffsets: [],
      entries: [],
      nextOffset: null,
      selectedCategory: null,
      selectedEntry: null,
      inspection: null,
      inspectRequest: null,
    });
  });

  it("clears actionable data on unexpected revision and protocol conflict", () => {
    let state = loadPage(boundState(), "initial", {
      entries: [entry()],
      nextOffset: 64,
    });
    const next = requireListTransition(state, "next");
    state = receiveAssetCatalogList(
      next.state,
      next.intent,
      listReply({
        revision: REVISION_B,
        offset: 64,
        entries: [entry()],
        nextOffset: null,
      }),
    );
    expect(state.consistency).toBe("stale");
    expect(state.entries).toEqual([]);
    expect(state.manifestRevision).toBeNull();
    expect(state.staleMessage).toMatch(/Refresh/u);

    state = loadPage(bindAssetCatalogWorkspace(state, "workspace_01", 2), "initial", {
      entries: [entry()],
    });
    const refresh = requireListTransition(state, "refresh");
    state = receiveAssetCatalogList(
      refresh.state,
      refresh.intent,
      protocolErrorReply("conflict", {
        absolute_path: "/home/private/world/assets.json",
      }),
    );
    expect(state.consistency).toBe("conflict");
    expect(state.entries).toEqual([]);
    expect(state.staleMessage).toMatch(/revision conflicted/u);
    expect(state.staleMessage).not.toContain("/home/private");
  });

  it("accepts inspections only for the exact workspace, generation, token, revision, and entry", () => {
    const loaded = loadPage(boundState(), "initial", {
      entries: [entry(), entry(ENTRY_2)],
    });
    const first = requireInspectionTransition(loaded, ENTRY_1);
    const second = requireInspectionTransition(first.state, ENTRY_1);

    expect(
      receiveAssetCatalogInspection(
        second.state,
        first.intent,
        inspectReply({ inspection: pngInspection() }),
      ),
    ).toBe(second.state);
    expect(
      receiveAssetCatalogInspection(
        second.state,
        { ...second.intent, generation: 2 },
        inspectReply({ inspection: pngInspection() }),
      ),
    ).toBe(second.state);

    const mismatchedEntry = receiveAssetCatalogInspection(
      second.state,
      second.intent,
      inspectReply({ entry: entry(ENTRY_2), inspection: pngInspection() }),
    );
    expect(mismatchedEntry.inspection).toBeNull();
    expect(mismatchedEntry.selectedEntry).toBeNull();
    expect(mismatchedEntry.error).toMatch(/did not match/u);

    const revisionRequest = requireInspectionTransition(loaded, ENTRY_1);
    const stale = receiveAssetCatalogInspection(
      revisionRequest.state,
      revisionRequest.intent,
      inspectReply({ revision: REVISION_B, inspection: pngInspection() }),
    );
    expect(stale.consistency).toBe("stale");
    expect(stale.entries).toEqual([]);

    const exactRequest = requireInspectionTransition(loaded, ENTRY_1);
    const exact = receiveAssetCatalogInspection(
      exactRequest.state,
      exactRequest.intent,
      inspectReply({ inspection: pngInspection() }),
    );
    expect(exact.inspection).toEqual(pngInspection());
    expect(exact.selectedEntry?.entry_id).toBe(ENTRY_1);
  });

  it("keeps category selection page-local and preserves backend enum values", () => {
    let state = loadPage(boundState(), "initial", {
      entries: [
        entry(ENTRY_1, { category: "visual_bible" }),
        entry(ENTRY_2, { category: "production_output", selected: true }),
      ],
      nextOffset: 64,
    });
    expect(assetCatalogPageCategories(state)).toEqual([
      "visual_bible",
      "production_output",
    ]);
    state = selectAssetCatalogCategory(state, "production_output");
    expect(state.selectedCategory).toBe("production_output");
    expect(selectAssetCatalogCategory(state, "audio_bible")).toBe(state);

    const next = requireListTransition(state, "next");
    state = receiveAssetCatalogList(
      next.state,
      next.intent,
      listReply({
        offset: 64,
        entries: [entry(ENTRY_2, { category: "qa" })],
      }),
    );
    expect(state.selectedCategory).toBeNull();
    expect(assetCatalogPageCategories(state)).toEqual(["qa"]);
  });

  it("decodes every generated category without coercion", () => {
    const categories = Object.keys(
      ASSET_CATALOG_CATEGORY_LABELS,
    ) as AssetCatalogCategory[];
    for (const category of categories) {
      const decoded = decodeAssetCatalogListReply(
        listReply({ entries: [entry(ENTRY_1, { category })] }),
      );
      expect(decoded.ok, category).toBe(true);
      if (decoded.ok) {
        expect(decoded.value.entries[0]?.category).toBe(category);
      }
    }
    expect(categories).toHaveLength(15);

    const boxedCategory = {
      ...entry(),
      category: new String("qa"),
    } as unknown as StudioAssetCatalogEntry;
    expect(
      decodeAssetCatalogListReply(listReply({ entries: [boxedCategory] })),
    ).toMatchObject({ ok: false, kind: "invalid-response" });
  });

  it("decodes every inspection kind and rejects a malformed branch variant", () => {
    const cases: Array<{
      kind: keyof typeof ASSET_INSPECTION_KIND_LABELS;
      valid: unknown;
      malformed: unknown;
    }> = [
      {
        kind: "json",
        valid: {
          kind: "json",
          encoding: "utf-8",
          content: '{"name":"asset","nested":{"ok":true}}',
          value: { nested: { ok: true }, name: "asset" },
        },
        malformed: {
          kind: "json",
          encoding: "utf-8",
          content: '{"name":"asset"}',
          value: { name: "different" },
        },
      },
      {
        kind: "glsl",
        valid: {
          kind: "glsl",
          encoding: "utf-8",
          content: "#version 330\nvoid main() {}",
        },
        malformed: {
          kind: "glsl",
          encoding: "utf-16",
          content: "void main() {}",
        },
      },
      {
        kind: "png",
        valid: pngInspection(),
        malformed: { ...pngInspection(), width: 0 },
      },
      {
        kind: "wav",
        valid: {
          kind: "wav",
          channels: 2,
          sample_rate: 48_000,
          sample_width_bits: 16,
          frame_count: 96_000,
          duration_ms: 2_000,
        },
        malformed: {
          kind: "wav",
          channels: 0,
          sample_rate: 48_000,
          sample_width_bits: 16,
          frame_count: 0,
          duration_ms: 0,
        },
      },
      {
        kind: "font",
        valid: { kind: "font", flavor: "opentype", table_count: 12 },
        malformed: { kind: "font", flavor: "woff", table_count: 12 },
      },
      {
        kind: "glb",
        valid: {
          kind: "glb",
          byte_length: 1_024,
          json_chunk_bytes: 512,
          bin_chunk_bytes: 512,
          extensions_used: ["KHR_materials_unlit"],
          extensions_required: [],
          external_uris: ["textures/albedo.png"],
          embedded_uris: 0,
          max_texture_dimension: 1_024,
          metrics: glbMetrics(),
        },
        malformed: {
          kind: "glb",
          byte_length: 1,
          json_chunk_bytes: 1,
          bin_chunk_bytes: 0,
          extensions_used: [],
          extensions_required: [],
          external_uris: ["/home/private/texture.png"],
          embedded_uris: 0,
          max_texture_dimension: 0,
          metrics: glbMetrics(),
        },
      },
      {
        kind: "unavailable",
        valid: { kind: "unavailable", reason: "identity_only" },
        malformed: { kind: "unavailable", reason: "private_path" },
      },
    ];

    expect(cases.map(({ kind }) => kind)).toEqual(
      Object.keys(ASSET_INSPECTION_KIND_LABELS),
    );
    for (const testCase of cases) {
      const decoded = decodeAssetCatalogInspectReply(
        inspectReply({ inspection: testCase.valid }),
      );
      expect(decoded.ok, testCase.kind).toBe(true);
      if (decoded.ok) {
        expect(decoded.value.inspection.kind).toBe(testCase.kind);
      }
      expect(
        decodeAssetCatalogInspectReply(
          inspectReply({ inspection: testCase.malformed }),
        ),
        testCase.kind,
      ).toMatchObject({ ok: false, kind: "invalid-response" });
    }

    expect(
      decodeAssetCatalogInspectReply(
        inspectReply({ inspection: { kind: new String("png") } }),
      ),
    ).toMatchObject({ ok: false, kind: "invalid-response" });
  });

  it("derives bounded JSON state from content and rejects hidden semantic payloads", () => {
    const supplied = { b: [true, null], a: 1 };
    const decoded = decodeAssetCatalogInspectReply(
      inspectReply({
        inspection: {
          kind: "json",
          encoding: "utf-8",
          content: '{"a":1,"b":[true,null]}',
          value: supplied,
        },
      }),
    );
    expect(decoded.ok).toBe(true);
    if (decoded.ok && decoded.value.inspection.kind === "json") {
      expect(decoded.value.inspection.value).toEqual({
        a: 1,
        b: [true, null],
      });
      expect(decoded.value.inspection.value).not.toBe(supplied);
    }

    const hidden = "x".repeat(300_000);
    expect(
      decodeAssetCatalogInspectReply(
        inspectReply({
          inspection: {
            kind: "json",
            encoding: "utf-8",
            content: '{"safe":true}',
            value: { safe: true, hidden },
          },
        }),
      ),
    ).toMatchObject({ ok: false, kind: "invalid-response" });

    const tooManyNodes = {
      values: Array.from({ length: 2_000 }, (_, index) => index),
    };
    expect(
      decodeAssetCatalogInspectReply(
        inspectReply({
          inspection: {
            kind: "json",
            encoding: "utf-8",
            content: JSON.stringify(tooManyNodes),
            value: tooManyNodes,
          },
        }),
      ),
    ).toMatchObject({ ok: false, kind: "invalid-response" });

    let tooDeep: Record<string, unknown> = { value: true };
    for (let depth = 0; depth < 13; depth += 1) {
      tooDeep = { nested: tooDeep };
    }
    expect(
      decodeAssetCatalogInspectReply(
        inspectReply({
          inspection: {
            kind: "json",
            encoding: "utf-8",
            content: JSON.stringify(tooDeep),
            value: tooDeep,
          },
        }),
      ),
    ).toMatchObject({ ok: false, kind: "invalid-response" });

    expect(
      decodeAssetCatalogInspectReply(
        inspectReply({
          inspection: {
            kind: "json",
            encoding: "utf-8",
            content: '{"number":null}',
            value: { number: Number.NaN },
          },
        }),
      ),
    ).toMatchObject({ ok: false, kind: "invalid-response" });
  });

  it("enforces closed envelopes and maximum page bounds", () => {
    const maximum = Array.from({ length: 64 }, (_, index) =>
      entry(`asset_${index.toString(16).padStart(64, "0")}`),
    );
    expect(decodeAssetCatalogListReply(listReply({ entries: maximum })).ok).toBe(true);
    expect(
      decodeAssetCatalogListReply(listReply({ entries: [...maximum, entry()] })),
    ).toMatchObject({ ok: false, kind: "invalid-response" });
    expect(
      decodeAssetCatalogListReply(listReply({ entries: [], limit: 65 })),
    ).toMatchObject({ ok: false, kind: "invalid-response" });
    expect(
      decodeAssetCatalogListReply(listReply({ entries: [], offset: 64, nextOffset: 64 })),
    ).toMatchObject({ ok: false, kind: "invalid-response" });

    const reply = listReply({ entries: [] }) as {
      ok: true;
      value: Record<string, unknown>;
    };
    expect(
      decodeAssetCatalogListReply({
        ...reply,
        value: { ...reply.value, raw_diagnostic: "/tmp/private" },
      }),
    ).toMatchObject({ ok: false, kind: "invalid-response" });
  });

  it("does not transition or emit an intent for non-inspectable entries", () => {
    const state = loadPage(boundState(), "initial", {
      entries: [
        entry(ENTRY_1, {
          category: "processing_recipe",
          path: null,
          inspectable: false,
        }),
      ],
    });
    expect(beginAssetCatalogInspection(state, ENTRY_1)).toBeNull();
    expect(state.inspectToken).toBe(0);
    expect(state.selectedEntry).toBeNull();
  });

  it("bounds safe errors without exposing diagnostics or absolute paths", () => {
    const started = requireListTransition(boundState(), "initial");
    const rawDiagnostic = `/home/private/${"x".repeat(2_000)}\u0000`;
    const failed = receiveAssetCatalogList(started.state, started.intent, {
      ok: false,
      error: { code: "internal_error", message: rawDiagnostic },
    });
    expect(failed.error).toBe("The asset catalog request failed.");
    expect(failed.error?.length).toBeLessThanOrEqual(240);
    expect(failed.error).not.toContain("/home/private");

    expect(
      decodeAssetCatalogInspectReply(
        inspectReply({
          inspection: {
            kind: "glb",
            byte_length: 1,
            json_chunk_bytes: 1,
            bin_chunk_bytes: 0,
            extensions_used: [],
            extensions_required: [],
            external_uris: ["C:\\private\\texture.png"],
            embedded_uris: 0,
            max_texture_dimension: 0,
            metrics: glbMetrics(),
          },
        }),
      ),
    ).toMatchObject({ ok: false, kind: "invalid-response" });
  });

  it("replaces pages without accumulating entries across replies", () => {
    let state = loadPage(boundState(), "initial", {
      entries: [entry(ENTRY_1), entry(ENTRY_2)],
      nextOffset: 64,
    });
    const firstEntries = state.entries;
    const next = requireListTransition(state, "next");
    state = receiveAssetCatalogList(
      next.state,
      next.intent,
      listReply({
        offset: 64,
        entries: [entry(ENTRY_2, { category: "runtime_output" })],
      }),
    );
    expect(state.entries).toHaveLength(1);
    expect(state.entries[0]?.category).toBe("runtime_output");
    expect(state.entries).not.toBe(firstEntries);
  });
});

function boundState(): AssetCatalogState {
  return bindAssetCatalogWorkspace(createInitialAssetCatalogState(), "workspace_01", 1);
}

function loadPage(
  state: AssetCatalogState,
  mode: "initial" | "refresh" | "next" | "previous",
  options: {
    revision?: string;
    offset?: number;
    entries?: StudioAssetCatalogEntry[];
    nextOffset?: number | null;
  } = {},
): AssetCatalogState {
  const transition = requireListTransition(state, mode);
  return receiveAssetCatalogList(
    transition.state,
    transition.intent,
    listReply(options),
  );
}

function requireListTransition(
  state: AssetCatalogState,
  mode: "initial" | "refresh" | "next" | "previous",
) {
  const transition = beginAssetCatalogList(state, mode);
  if (!transition) throw new Error(`Expected ${mode} list transition`);
  return transition;
}

function requireInspectionTransition(state: AssetCatalogState, entryId: string) {
  const transition = beginAssetCatalogInspection(state, entryId);
  if (!transition) throw new Error("Expected inspection transition");
  return transition;
}

function entry(
  entryId = ENTRY_1,
  overrides: Partial<StudioAssetCatalogEntry> = {},
): StudioAssetCatalogEntry {
  return {
    entry_id: entryId,
    asset_id: "asset_01",
    category: "visual_bible",
    role: "concept",
    path: "assets/concept.png",
    sha256: "c".repeat(64),
    media_type: "image/png",
    selected: false,
    inspectable: true,
    ...overrides,
  };
}

function listReply({
  revision = REVISION_A,
  offset = 0,
  limit = 64,
  entries = [],
  nextOffset = null,
}: {
  revision?: string;
  offset?: number;
  limit?: number;
  entries?: StudioAssetCatalogEntry[];
  nextOffset?: number | null;
} = {}): unknown {
  return {
    ok: true,
    value: {
      protocol: "rpg-world-forge.studio_protocol",
      protocol_version: 1,
      kind: "response",
      request_id: "request_01",
      method: "asset.catalog.list",
      result: {
        manifest_revision: revision,
        offset,
        limit,
        entries,
        next_offset: nextOffset,
      },
    },
  };
}

function inspectReply({
  revision = REVISION_A,
  entry: inspectedEntry = entry(),
  inspection = { kind: "unavailable", reason: "identity_only" },
}: {
  revision?: string;
  entry?: StudioAssetCatalogEntry;
  inspection?: unknown;
} = {}): unknown {
  return {
    ok: true,
    value: {
      protocol: "rpg-world-forge.studio_protocol",
      protocol_version: 1,
      kind: "response",
      request_id: "request_02",
      method: "asset.catalog.inspect",
      result: {
        manifest_revision: revision,
        entry: inspectedEntry,
        inspection,
      },
    },
  };
}

function protocolErrorReply(
  code: "invalid_request" | "not_found" | "conflict" | "invalid_state" | "internal_error",
  details: Record<string, unknown>,
): unknown {
  return {
    ok: true,
    value: {
      protocol: "rpg-world-forge.studio_protocol",
      protocol_version: 1,
      kind: "error",
      request_id: "request_03",
      error: {
        code,
        message: "Raw backend diagnostic must not be exposed",
        details,
      },
    },
  };
}

function pngInspection(): Extract<StudioAssetInspection, { kind: "png" }> {
  return {
    kind: "png",
    width: 64,
    height: 64,
    bit_depth: 8,
    color_type: 6,
    interlaced: false,
  };
}

function glbMetrics(): Extract<
  StudioAssetInspection,
  { kind: "glb" }
>["metrics"] {
  return {
    nodes: 0,
    meshes: 0,
    materials: 0,
    textures: 0,
    skins: 0,
    bones: 0,
    influences: 0,
    animations: 0,
    vertices: 0,
    triangles: 0,
    external_uris: 1,
  };
}
