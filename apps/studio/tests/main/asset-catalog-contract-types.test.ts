import { describe, expect, expectTypeOf, it } from "vitest";

import type {
  AssetCatalogInspectRequest,
  AssetCatalogInspectResponse,
  AssetCatalogListRequest,
  AssetCatalogListResponse,
} from "../../src/generated/studio-protocol";
import type {
  StudioRequestParams,
  StudioSuccessForMethod,
} from "../../src/main/ndjson-supervisor";

describe("generated asset catalog contracts", () => {
  it("maps the two exact methods without a legacy transport fallback", () => {
    expectTypeOf<StudioRequestParams<"asset.catalog.list">>().toEqualTypeOf<
      AssetCatalogListRequest["params"]
    >();
    expectTypeOf<StudioRequestParams<"asset.catalog.inspect">>().toEqualTypeOf<
      AssetCatalogInspectRequest["params"]
    >();
    expectTypeOf<StudioSuccessForMethod<"asset.catalog.list">>().toEqualTypeOf<
      AssetCatalogListResponse
    >();
    expectTypeOf<StudioSuccessForMethod<"asset.catalog.inspect">>().toEqualTypeOf<
      AssetCatalogInspectResponse
    >();
  });

  it("requires revision authority after page one and for every inspection", () => {
    const firstPage: AssetCatalogListRequest["params"] = {
      workspace_id: "workspace_01",
    };
    const laterPage: AssetCatalogListRequest["params"] = {
      workspace_id: "workspace_01",
      offset: 64,
      expected_manifest_revision: "a".repeat(64),
    };
    const inspect: AssetCatalogInspectRequest["params"] = {
      workspace_id: "workspace_01",
      entry_id: `asset_${"b".repeat(64)}`,
      expected_manifest_revision: "a".repeat(64),
    };
    // @ts-expect-error later pages require the exact manifest revision.
    const invalidLaterPage: AssetCatalogListRequest["params"] = {
      workspace_id: "workspace_01",
      offset: 64,
      expected_manifest_revision: undefined,
    };
    const rendererPath: AssetCatalogInspectRequest["params"] = {
      ...inspect,
      // @ts-expect-error renderer-supplied paths are not part of inspection authority.
      path: "assets/renderpack/manifest.json",
    };

    expect(firstPage.workspace_id).toBe("workspace_01");
    expect(laterPage.offset).toBe(64);
    expect(inspect.entry_id).toMatch(/^asset_/);
    expect(invalidLaterPage.offset).toBe(64);
    expect(rendererPath.entry_id).toBe(inspect.entry_id);
  });
});
