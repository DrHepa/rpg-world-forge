import { describe, expect, expectTypeOf, it } from "vitest";

import type {
  AssetPreviewCloseRequest,
  AssetPreviewCloseResponse,
  AssetPreviewOpenRequest,
  AssetPreviewOpenResponse,
  AssetPreviewReadRequest,
  AssetPreviewReadResponse,
} from "../../src/generated/studio-protocol";
import type {
  StudioRequestParams,
  StudioSuccessForMethod,
} from "../../src/main/ndjson-supervisor";
import {
  IPC_CHANNELS,
  STUDIO_METHODS,
  STUDIO_READ_METHODS,
  type ForgeStudioApi,
  type StudioAssetPreviewChunkReply,
  type StudioAssetPreviewCloseReply,
  type StudioAssetPreviewOpenReply,
  type StudioClientResult,
} from "../../src/shared/studio-api";

describe("generated asset preview contracts", () => {
  it("maps all three exact methods without a legacy transport fallback", () => {
    expectTypeOf<StudioRequestParams<"asset.preview.open">>().toEqualTypeOf<
      AssetPreviewOpenRequest["params"]
    >();
    expectTypeOf<StudioRequestParams<"asset.preview.read">>().toEqualTypeOf<
      AssetPreviewReadRequest["params"]
    >();
    expectTypeOf<StudioRequestParams<"asset.preview.close">>().toEqualTypeOf<
      AssetPreviewCloseRequest["params"]
    >();
    expectTypeOf<StudioSuccessForMethod<"asset.preview.open">>().toEqualTypeOf<
      AssetPreviewOpenResponse
    >();
    expectTypeOf<StudioSuccessForMethod<"asset.preview.read">>().toEqualTypeOf<
      AssetPreviewReadResponse
    >();
    expectTypeOf<StudioSuccessForMethod<"asset.preview.close">>().toEqualTypeOf<
      AssetPreviewCloseResponse
    >();
  });

  it("exposes only three named renderer capabilities and typed bytes", () => {
    expectTypeOf<ForgeStudioApi["openAssetPreview"]>().parameters.toEqualTypeOf<
      [workspaceId: string, manifestRevision: string, entryId: string]
    >();
    expectTypeOf<ForgeStudioApi["openAssetPreview"]>().returns.toEqualTypeOf<
      Promise<StudioClientResult<StudioAssetPreviewOpenReply>>
    >();
    expectTypeOf<ForgeStudioApi["readAssetPreviewChunk"]>().parameters.toEqualTypeOf<
      [handle: string, sequence: number]
    >();
    expectTypeOf<ForgeStudioApi["readAssetPreviewChunk"]>().returns.toEqualTypeOf<
      Promise<StudioClientResult<StudioAssetPreviewChunkReply>>
    >();
    expectTypeOf<ForgeStudioApi["closeAssetPreview"]>().returns.toEqualTypeOf<
      Promise<StudioClientResult<StudioAssetPreviewCloseReply>>
    >();

    expect(IPC_CHANNELS.openAssetPreview).toBe("studio:open-asset-preview");
    expect(IPC_CHANNELS.readAssetPreviewChunk).toBe(
      "studio:read-asset-preview-chunk",
    );
    expect(IPC_CHANNELS.closeAssetPreview).toBe("studio:close-asset-preview");
    expect(STUDIO_METHODS.has("asset.preview.open")).toBe(true);
    expect(STUDIO_METHODS.has("asset.preview.read")).toBe(true);
    expect(STUDIO_METHODS.has("asset.preview.close")).toBe(true);
    expect([...STUDIO_READ_METHODS]).not.toContain("asset.preview.read");
  });
});
