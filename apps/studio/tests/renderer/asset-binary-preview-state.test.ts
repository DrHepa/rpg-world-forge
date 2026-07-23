import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  ASSET_PREVIEW_CHUNK_BYTES,
  ASSET_PREVIEW_MAX_CHUNKS,
  assetBinaryPreviewIdentity,
  decodeAssetPreviewChunk,
  decodeAssetPreviewClose,
  decodeAssetPreviewOpen,
  type AssetBinaryPreviewContext,
  type AssetBinaryPreviewIdentity,
  type AssetPreviewChunkExpectation,
} from "../../src/renderer/asset-binary-preview-state";
import type {
  StudioAssetCatalogEntry,
  StudioAssetInspection,
} from "../../src/shared/studio-api";

const ENTRY_ID = `asset_${"1".repeat(64)}`;
const HANDLE = "H".repeat(43);
const REVISION = "a".repeat(64);
const SHA256 = "b".repeat(64);

describe("asset binary preview state", () => {
  it("creates an exact identity only for active current authorized PNG/WAV outputs", () => {
    expect(
      assetBinaryPreviewIdentity(context(), entry(), pngInspection()),
    ).toEqual({
      workspaceId: "workspace_01",
      generation: 7,
      manifestRevision: REVISION,
      entryId: ENTRY_ID,
      kind: "png",
      mediaType: "image/png",
      category: "production_output",
      sha256: SHA256,
    });
    for (const category of [
      "production_output",
      "processing_output",
      "runtime_output",
    ] as const) {
      expect(
        assetBinaryPreviewIdentity(
          context(),
          entry({ category }),
          pngInspection(),
        )?.category,
      ).toBe(category);
    }

    const forbiddenContexts: AssetBinaryPreviewContext[] = [
      context({ active: false }),
      context({ catalogCurrent: false }),
      context({ workspaceId: null }),
      context({ generation: -1 }),
      context({ manifestRevision: null }),
    ];
    for (const forbidden of forbiddenContexts) {
      expect(assetBinaryPreviewIdentity(forbidden, entry(), pngInspection())).toBeNull();
    }
    for (const category of [
      "manifest",
      "target",
      "visual_bible",
      "audio_bible",
      "inventory",
      "specification",
      "production_receipt",
      "production_request",
      "processing_receipt",
      "processing_recipe",
      "license",
      "qa",
    ] as const) {
      expect(
        assetBinaryPreviewIdentity(
          context(),
          entry({ category }),
          pngInspection(),
        ),
      ).toBeNull();
    }
    expect(
      assetBinaryPreviewIdentity(
        context(),
        entry({ media_type: "audio/wav" }),
        pngInspection(),
      ),
    ).toBeNull();
    for (const inspection of [
      { kind: "json", encoding: "utf-8", content: "{}", value: {} },
      { kind: "glsl", encoding: "utf-8", content: "void main() {}" },
      { kind: "font", flavor: "truetype", table_count: 1 },
      {
        kind: "unavailable",
        reason: "unsupported_media_type",
      },
    ] satisfies StudioAssetInspection[]) {
      expect(
        assetBinaryPreviewIdentity(context(), entry(), inspection),
      ).toBeNull();
    }
  });

  it("correlates open identity, MIME, size, SHA, handle, and fixed chunk size", () => {
    const identity = pngIdentity();
    expect(
      decodeAssetPreviewOpen(
        response("asset.preview.open", openResult()),
        identity,
      ),
    ).toEqual({
      ok: true,
      value: {
        handle: HANDLE,
        byteLength: 4,
        sha256: SHA256,
        chunkBytes: ASSET_PREVIEW_CHUNK_BYTES,
      },
    });

    for (const mutation of [
      { manifest_revision: "c".repeat(64) },
      { entry_id: `asset_${"2".repeat(64)}` },
      { media_type: "audio/wav" },
      { sha256: "d".repeat(64) },
      { chunk_bytes: 1 },
      { byte_length: 0 },
      { handle: "bad" },
    ]) {
      expect(
        decodeAssetPreviewOpen(
          response("asset.preview.open", { ...openResult(), ...mutation }),
          identity,
        ).ok,
      ).toBe(false);
    }
  });

  it("accepts exact sequential chunks and rejects malformed stream correlation", () => {
    const firstBytes = new Uint8Array(ASSET_PREVIEW_CHUNK_BYTES);
    const expectation = chunkExpectation({
      declaredBytes: ASSET_PREVIEW_CHUNK_BYTES + 3,
    });
    const first = decodeAssetPreviewChunk(
      response("asset.preview.read", {
        handle: HANDLE,
        sequence: 0,
        bytes: firstBytes,
        byte_length: ASSET_PREVIEW_CHUNK_BYTES,
        cumulative_bytes: ASSET_PREVIEW_CHUNK_BYTES,
        cumulative_sha256: "c".repeat(64),
        eof: false,
      }),
      expectation,
    );
    expect(first.ok).toBe(true);
    if (!first.ok) throw new Error("Expected first chunk");
    expect(first.value.bytes).not.toBe(firstBytes);

    const second = decodeAssetPreviewChunk(
      response("asset.preview.read", {
        handle: HANDLE,
        sequence: 1,
        bytes: new Uint8Array([1, 2, 3]),
        byte_length: 3,
        cumulative_bytes: ASSET_PREVIEW_CHUNK_BYTES + 3,
        cumulative_sha256: SHA256,
        eof: true,
      }),
      chunkExpectation({
        sequence: 1,
        cumulativeBytes: ASSET_PREVIEW_CHUNK_BYTES,
        declaredBytes: ASSET_PREVIEW_CHUNK_BYTES + 3,
        seenViews: expectation.seenViews,
        seenBuffers: expectation.seenBuffers,
      }),
    );
    expect(second.ok).toBe(true);
    expect(
      decodeAssetPreviewChunk(
        response("asset.preview.read", {
          handle: HANDLE,
          sequence: 0,
          bytes: new Uint8Array([1, 2, 3]),
          byte_length: 3,
          cumulative_bytes: ASSET_PREVIEW_CHUNK_BYTES + 3,
          cumulative_sha256: SHA256,
          eof: true,
        }),
        chunkExpectation({
          sequence: 1,
          cumulativeBytes: ASSET_PREVIEW_CHUNK_BYTES,
          declaredBytes: ASSET_PREVIEW_CHUNK_BYTES + 3,
        }),
      ).ok,
    ).toBe(false);

    const validShape = {
      handle: HANDLE,
      sequence: 0,
      bytes: new Uint8Array([1, 2, 3, 4]),
      byte_length: 4,
      cumulative_bytes: 4,
      cumulative_sha256: SHA256,
      eof: true,
    };
    for (const mutation of [
      { handle: "J".repeat(43) },
      { sequence: 1 },
      { sequence: ASSET_PREVIEW_MAX_CHUNKS },
      { bytes: new Uint8Array(0), byte_length: 0, cumulative_bytes: 0, eof: false },
      { bytes: new Uint8Array(5), byte_length: 5, cumulative_bytes: 5 },
      { byte_length: 3 },
      { cumulative_bytes: 3 },
      { cumulative_sha256: "d".repeat(64) },
      { eof: false },
    ]) {
      expect(
        decodeAssetPreviewChunk(
          response("asset.preview.read", { ...validShape, ...mutation }),
          chunkExpectation(),
        ).ok,
      ).toBe(false);
    }
  });

  it("rejects reused views and backing buffers as non-fresh chunks", () => {
    const bytes = new Uint8Array([1, 2, 3, 4]);
    const expectation = chunkExpectation();
    expectation.seenViews.add(bytes);
    expectation.seenBuffers.add(bytes.buffer);
    expect(
      decodeAssetPreviewChunk(
        response("asset.preview.read", {
          handle: HANDLE,
          sequence: 0,
          bytes,
          byte_length: 4,
          cumulative_bytes: 4,
          cumulative_sha256: SHA256,
          eof: true,
        }),
        expectation,
      ).ok,
    ).toBe(false);
  });

  it("requires exact close correlation", () => {
    expect(
      decodeAssetPreviewClose(
        response("asset.preview.close", { handle: HANDLE, closed: true }),
        HANDLE,
      ),
    ).toBe(true);
    expect(
      decodeAssetPreviewClose(
        response("asset.preview.close", {
          handle: "J".repeat(43),
          closed: true,
        }),
        HANDLE,
      ),
    ).toBe(false);
  });

  it("keeps the renderer preview free of encoded, path, and executable media capabilities", () => {
    const source = [
      "AssetBinaryPreview.tsx",
      "asset-binary-preview-state.ts",
      "useAssetBinaryPreview.ts",
    ]
      .map((file) =>
        readFileSync(resolve(process.cwd(), "src/renderer", file), "utf8"),
      )
      .join("\n");
    expect(source).not.toMatch(
      /\b(?:Buffer|btoa|atob|FontFace|WebGLRenderingContext|THREE)\b|base64|data:|dangerouslySetInnerHTML|\.play\(/u,
    );
  });
});

function context(
  overrides: Partial<AssetBinaryPreviewContext> = {},
): AssetBinaryPreviewContext {
  return {
    active: true,
    catalogCurrent: true,
    workspaceId: "workspace_01",
    generation: 7,
    manifestRevision: REVISION,
    ...overrides,
  };
}

function entry(
  overrides: Partial<StudioAssetCatalogEntry> = {},
): StudioAssetCatalogEntry {
  return {
    entry_id: ENTRY_ID,
    asset_id: "asset_01",
    category: "production_output",
    role: "sprite",
    path: "assets/sprite.png",
    sha256: SHA256,
    media_type: "image/png",
    selected: true,
    inspectable: true,
    ...overrides,
  };
}

function pngInspection(): Extract<StudioAssetInspection, { kind: "png" }> {
  return {
    kind: "png",
    width: 64,
    height: 32,
    bit_depth: 8,
    color_type: 6,
    interlaced: false,
  };
}

function pngIdentity(): AssetBinaryPreviewIdentity {
  const identity = assetBinaryPreviewIdentity(context(), entry(), pngInspection());
  if (!identity) throw new Error("Expected preview identity");
  return identity;
}

function openResult() {
  return {
    handle: HANDLE,
    manifest_revision: REVISION,
    entry_id: ENTRY_ID,
    media_type: "image/png",
    byte_length: 4,
    sha256: SHA256,
    chunk_bytes: ASSET_PREVIEW_CHUNK_BYTES,
  };
}

function chunkExpectation(
  overrides: Partial<AssetPreviewChunkExpectation> = {},
): AssetPreviewChunkExpectation {
  return {
    handle: HANDLE,
    sequence: 0,
    cumulativeBytes: 0,
    declaredBytes: 4,
    declaredSha256: SHA256,
    seenViews: new WeakSet(),
    seenBuffers: new WeakSet(),
    ...overrides,
  };
}

function response(method: string, result: Record<string, unknown>) {
  return {
    ok: true,
    value: {
      protocol: "rpg-world-forge.studio_protocol",
      protocol_version: 1,
      kind: "response",
      request_id: "00000000-0000-4000-8000-000000000001",
      method,
      result,
    },
  };
}
