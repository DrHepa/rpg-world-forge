import { useEffect, useMemo, useRef } from "react";

import { AssetInspection } from "./AssetInspection";
import {
  ASSET_CATALOG_CATEGORY_LABELS,
  assetCatalogPageCategories,
  type AssetCatalogCategory,
  type AssetCatalogListMode,
  type AssetCatalogState,
} from "./asset-catalog-state";

export function AssetsCockpit({
  state,
  active,
  onList,
  onInspect,
  onCategory,
}: {
  state: AssetCatalogState;
  active: boolean;
  onList: (mode: AssetCatalogListMode) => boolean;
  onInspect: (entryId: string) => boolean;
  onCategory: (category: AssetCatalogCategory | null) => void;
}) {
  const rootRef = useRef<HTMLElement>(null);
  const listHeadingRef = useRef<HTMLHeadingElement>(null);
  const focusAfterPageRef = useRef(false);
  const categories = useMemo(() => assetCatalogPageCategories(state), [state]);
  const categoryCounts = useMemo(() => {
    const counts = new Map<AssetCatalogCategory, number>();
    for (const entry of state.entries) {
      counts.set(entry.category, (counts.get(entry.category) ?? 0) + 1);
    }
    return counts;
  }, [state.entries]);
  const visibleEntries = useMemo(
    () =>
      state.selectedCategory
        ? state.entries.filter((entry) => entry.category === state.selectedCategory)
        : state.entries,
    [state.entries, state.selectedCategory],
  );

  useEffect(() => {
    if (!active) {
      focusAfterPageRef.current = false;
    } else if (focusAfterPageRef.current && state.listRequest === null) {
      focusAfterPageRef.current = false;
      listHeadingRef.current?.focus();
    }
  }, [active, state.currentOffset, state.listRequest]);

  function requestPage(
    mode: AssetCatalogListMode,
    event: React.MouseEvent<HTMLButtonElement>,
  ): void {
    const keyboardActivation =
      event.detail === 0 && rootRef.current?.contains(document.activeElement) === true;
    const started = onList(mode);
    focusAfterPageRef.current = started && keyboardActivation;
  }

  const pending = state.listRequest !== null || state.inspectRequest !== null;
  const hasSnapshot = state.manifestRevision !== null && state.consistency === "current";

  return (
    <section
      ref={rootRef}
      className="assets-cockpit"
      aria-labelledby="assets-cockpit-heading"
      aria-busy={pending}
      onBlurCapture={(event) => {
        const next = event.relatedTarget;
        if (
          focusAfterPageRef.current &&
          next instanceof Node &&
          !rootRef.current?.contains(next)
        ) {
          focusAfterPageRef.current = false;
        }
      }}
    >
      <header className="assets-header">
        <div>
          <p className="breadcrumb">Assets / Read-only inspection cockpit</p>
          <h2 id="assets-cockpit-heading">Verified asset catalog</h2>
          <p>
            Revision snapshot only. Categories, counts, filters, and entries describe the
            current page; pages are replaced rather than accumulated.
          </p>
        </div>
        <div className="asset-snapshot-summary">
          <span>Manifest revision</span>
          <strong>
            {state.manifestRevision
              ? `${state.manifestRevision.slice(0, 12)}…`
              : "Not loaded"}
          </strong>
          <small>Offset {state.currentOffset.toLocaleString("en-US")}</small>
        </div>
      </header>

      {!state.workspaceId ? (
        <div className="assets-empty">
          <h3>No workspace selected</h3>
          <p>Choose a registered workspace to inspect its authorized asset catalog.</p>
        </div>
      ) : (
        <>
          <div className="asset-page-toolbar" aria-label="Asset catalog pagination">
            <button
              type="button"
              className="secondary"
              disabled={state.visitedOffsets.length === 0 || pending || !hasSnapshot}
              onClick={(event) => requestPage("previous", event)}
            >
              Previous page
            </button>
            <button
              type="button"
              className="secondary"
              disabled={state.nextOffset === null || pending || !hasSnapshot}
              onClick={(event) => requestPage("next", event)}
            >
              Next page
            </button>
            <button
              type="button"
              className="secondary"
              disabled={pending}
              onClick={(event) => requestPage("refresh", event)}
            >
              Refresh revision snapshot
            </button>
            <span>
              {hasSnapshot
                ? `${String(state.entries.length)} entries on current page`
                : "No manifest snapshot loaded"}
            </span>
          </div>

          {state.status ? (
            <p className="asset-status" role="status" aria-live="polite">
              {state.status}
            </p>
          ) : null}
          {state.error ? (
            <p className="asset-error" role="alert">
              {state.error}
            </p>
          ) : null}
          {state.consistency === "stale" || state.consistency === "conflict" ? (
            <p className="asset-stale-note">
              This page is no longer actionable. Refresh to establish a new revision
              snapshot.
            </p>
          ) : null}

          {!hasSnapshot && !state.listRequest && !state.error ? (
            <div className="assets-empty">
              <h3>No manifest snapshot</h3>
              <p>The current workspace has not returned an asset catalog page.</p>
            </div>
          ) : null}

          {hasSnapshot ? (
            <div className="assets-layout">
              <nav
                className="asset-categories"
                aria-labelledby="asset-categories-heading"
              >
                <div className="section-heading">
                  <div>
                    <p className="eyebrow">Current page only</p>
                    <h2 id="asset-categories-heading">Categories</h2>
                  </div>
                  <span>{String(categories.length)}</span>
                </div>
                <ul>
                  <li>
                    <button
                      type="button"
                      aria-pressed={state.selectedCategory === null}
                      onClick={() => onCategory(null)}
                    >
                      <span>All current-page entries</span>
                      <small>{String(state.entries.length)}</small>
                    </button>
                  </li>
                  {categories.map((category) => (
                    <li key={category}>
                      <button
                        type="button"
                        aria-pressed={state.selectedCategory === category}
                        onClick={() => onCategory(category)}
                      >
                        <span>{ASSET_CATALOG_CATEGORY_LABELS[category]}</span>
                        <small>{String(categoryCounts.get(category) ?? 0)}</small>
                      </button>
                    </li>
                  ))}
                </ul>
              </nav>

              <section className="asset-list-panel" aria-labelledby="asset-list-heading">
                <div className="section-heading">
                  <div>
                    <p className="eyebrow">Page replacement view</p>
                    <h2 id="asset-list-heading" ref={listHeadingRef} tabIndex={-1}>
                      Catalog entries
                    </h2>
                  </div>
                  <span>{String(visibleEntries.length)}</span>
                </div>
                {visibleEntries.length === 0 ? (
                  <p className="empty-state">
                    No entries match this current-page category.
                  </p>
                ) : (
                  <ul className="asset-entry-list">
                    {visibleEntries.map((entry) => {
                      const selected =
                        state.selectedEntry?.entry_id === entry.entry_id;
                      const descriptionId = `asset-entry-${entry.entry_id}`;
                      return (
                        <li key={entry.entry_id}>
                          <button
                            type="button"
                            aria-pressed={selected}
                            aria-describedby={descriptionId}
                            disabled={!entry.inspectable || state.listRequest !== null}
                            onClick={() => onInspect(entry.entry_id)}
                          >
                            <span>
                              <strong>
                                {entry.asset_id ??
                                  ASSET_CATALOG_CATEGORY_LABELS[entry.category]}
                              </strong>
                              <small>{entry.role ?? "No role reported"}</small>
                            </span>
                            <span aria-hidden="true">{selected ? "●" : "○"}</span>
                          </button>
                          <p id={descriptionId}>
                            {entry.path ?? "Identity-only catalog record"} ·{" "}
                            {entry.inspectable ? "Inspectable metadata" : "Not inspectable"}
                          </p>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </section>

              <AssetInspection
                entry={state.selectedEntry}
                inspection={state.inspection}
                pending={state.inspectRequest !== null}
              />
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
