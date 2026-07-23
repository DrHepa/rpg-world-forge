// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AssetsCockpit } from "../../src/renderer/AssetsCockpit";
import {
  bindAssetCatalogWorkspace,
  createInitialAssetCatalogState,
  type AssetCatalogState,
} from "../../src/renderer/asset-catalog-state";
import type { StudioAssetCatalogEntry } from "../../src/shared/studio-api";

afterEach(cleanup);

describe("AssetsCockpit", () => {
  it("uses current-page semantic categories, list buttons, and DOM order", () => {
    const onCategory = vi.fn();
    const state = loadedState();
    const { container } = render(
      <AssetsCockpit
        state={state}
        active
        onList={() => false}
        onInspect={() => false}
        onCategory={onCategory}
      />,
    );

    expect(screen.getByText(/Revision snapshot only/u)).toBeInTheDocument();
    expect(screen.getByText("2 entries on current page")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Categories" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /All current-page entries/u })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: /Visual bible/u })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /QA/u })).toBeInTheDocument();
    const nonInspectable = screen.getByRole("button", { name: /asset_02/u });
    expect(nonInspectable).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /QA/u }));
    expect(onCategory).toHaveBeenCalledWith("qa");

    const categories = container.querySelector(".asset-categories");
    const list = container.querySelector(".asset-list-panel");
    const detail = container.querySelector(".asset-detail");
    if (!categories || !list || !detail) throw new Error("Expected three cockpit columns");
    expect(
      categories.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      list.compareDocumentPosition(detail) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("moves focus after keyboard pagination only while focus remains in the active cockpit", () => {
    const onList = vi.fn().mockReturnValue(true);
    const state = loadedState();
    const { rerender } = render(
      <>
        <AssetsCockpit
          state={state}
          active
          onList={onList}
          onInspect={() => false}
          onCategory={() => undefined}
        />
        <button type="button">Outside focus</button>
      </>,
    );
    const next = screen.getByRole("button", { name: "Next page" });
    next.focus();
    fireEvent.click(next, { detail: 0 });
    expect(onList).toHaveBeenCalledWith("next");
    rerender(
      <>
        <AssetsCockpit
          state={{ ...state, currentOffset: 64 }}
          active
          onList={onList}
          onInspect={() => false}
          onCategory={() => undefined}
        />
        <button type="button">Outside focus</button>
      </>,
    );
    expect(screen.getByRole("heading", { name: "Catalog entries" })).toHaveFocus();

    const refreshed = screen.getByRole("button", {
      name: "Refresh revision snapshot",
    });
    refreshed.focus();
    fireEvent.click(refreshed, { detail: 0 });
    screen.getByRole("button", { name: "Outside focus" }).focus();
    rerender(
      <>
        <AssetsCockpit
          state={{ ...state, currentOffset: 0 }}
          active
          onList={onList}
          onInspect={() => false}
          onCategory={() => undefined}
        />
        <button type="button">Outside focus</button>
      </>,
    );
    expect(screen.getByRole("button", { name: "Outside focus" })).toHaveFocus();
  });

  it("shows honest no-workspace, stale, status, and bounded error states", () => {
    const { rerender } = render(
      <AssetsCockpit
        state={createInitialAssetCatalogState()}
        active
        onList={() => false}
        onInspect={() => false}
        onCategory={() => undefined}
      />,
    );
    expect(screen.getByRole("heading", { name: "No workspace selected" })).toBeInTheDocument();

    rerender(
      <AssetsCockpit
        state={{
          ...bindAssetCatalogWorkspace(
            createInitialAssetCatalogState(),
            "workspace_01",
            1,
          ),
          consistency: "conflict",
          error: "The asset catalog revision conflicted with this request.",
          staleMessage: "The asset catalog revision conflicted with this request.",
          status: null,
        }}
        active
        onList={() => false}
        onInspect={() => false}
        onCategory={() => undefined}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/revision conflicted/u);
    expect(screen.getByText(/no longer actionable/u)).toBeInTheDocument();
    expect(screen.queryByText(/absolute|stderr|details/iu)).not.toBeInTheDocument();
  });

  it("keeps responsive layout in semantic DOM order without CSS reordering", () => {
    const styles = readFileSync(
      resolve(process.cwd(), "src/renderer/styles.css"),
      "utf8",
    );
    expect(styles).toMatch(/\.assets-layout\s*\{[\s\S]*grid-template-columns:/u);
    expect(styles).toContain("@media (max-width: 1180px)");
    expect(styles).toContain("@media (max-width: 860px)");
    expect(styles).toContain("@media (max-width: 600px)");
    expect(styles).toMatch(
      /@media \(max-width: 1180px\)[\s\S]*\.asset-categories\s*\{[\s\S]*grid-column: 1 \/ -1/u,
    );
    expect(styles).toMatch(
      /@media \(max-width: 860px\)[\s\S]*\.assets-layout\s*\{\s*grid-template-columns: 1fr/u,
    );
    expect(styles).not.toMatch(/\border\s*:/u);
  });
});

function loadedState(): AssetCatalogState {
  return {
    ...bindAssetCatalogWorkspace(
      createInitialAssetCatalogState(),
      "workspace_01",
      1,
    ),
    consistency: "current",
    status: "Asset catalog page loaded.",
    manifestRevision: "a".repeat(64),
    currentOffset: 0,
    entries: [
      entry(`asset_${"1".repeat(64)}`),
      entry(`asset_${"2".repeat(64)}`, {
        asset_id: "asset_02",
        category: "qa",
        path: "assets/qa.json",
        inspectable: false,
      }),
    ],
    nextOffset: 64,
  };
}

function entry(
  entryId: string,
  overrides: Partial<StudioAssetCatalogEntry> = {},
): StudioAssetCatalogEntry {
  return {
    entry_id: entryId,
    asset_id: "asset_01",
    category: "visual_bible",
    role: "concept",
    path: "assets/concept.png",
    sha256: "b".repeat(64),
    media_type: "image/png",
    selected: false,
    inspectable: true,
    ...overrides,
  };
}
