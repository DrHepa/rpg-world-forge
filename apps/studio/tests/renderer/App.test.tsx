// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../../src/renderer/App";
import type {
  CodexActivityEvent,
  ForgeStudioApi,
  StudioActivityEvent,
} from "../../src/shared/studio-api";

const SHA_WORLD = "a".repeat(64);
const SHA_MAP = "b".repeat(64);
const SHA_PROPOSED = "6cd86327e443282ef8b2e4109125f8fc9c43c64951ba100f47665f62c468577e";
const SHA_REVIEW = "d".repeat(64);
const ASSET_REVISION = "e".repeat(64);
const ASSET_ENTRY_ID = `asset_${"1".repeat(64)}`;
const UPDATED_WORLD_CONTENT = '{"id":"world_01","title":"A quieter world"}';
const COMPACT_DISCIPLINE_TABS_QUERY = "(max-width: 860px)";

const originalMatchMediaDescriptor = Object.getOwnPropertyDescriptor(window, "matchMedia");

beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(canvasContext());
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  if (originalMatchMediaDescriptor) {
    Object.defineProperty(window, "matchMedia", originalMatchMediaDescriptor);
  } else {
    Reflect.deleteProperty(window, "matchMedia");
  }
});

describe("Studio World authoring cockpit", () => {
  it("loads registered workspaces and the four named World resources on selection", async () => {
    const { api, mocks } = createApi();
    installApi(api);
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /workspace_01/u }));

    await waitFor(() => {
      expect(mocks.getWorkspaceOverview).toHaveBeenCalledWith("workspace_01");
      expect(mocks.listSourceDocuments).toHaveBeenCalledWith("workspace_01");
      expect(mocks.validateWorld).toHaveBeenCalledWith("workspace_01");
      expect(mocks.analyzeWorld).toHaveBeenCalledWith("workspace_01");
    });
    expect(await screen.findByRole("heading", { name: "Neutral World" })).toBeInTheDocument();
    expect(await screen.findByLabelText("In-memory source draft")).toHaveValue(
      WORLD_DOCUMENT.content,
    );
    expect(screen.getByText("foundation")).toBeInTheDocument();
    expect(screen.getByText("Release validation passed · 7 objects")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Assets" })).toBeEnabled();
    expect(screen.getByRole("tab", { name: "Game" })).toBeDisabled();
  });

  it("uses roving tabs, lazy-loads Assets, and preserves the exact dirty World draft", async () => {
    const { api, mocks } = createApi();
    installApi(api);
    render(<App />);
    expect(mocks.listAssetCatalog).not.toHaveBeenCalled();

    fireEvent.click(await screen.findByRole("button", { name: /workspace_01/u }));
    const editor = await screen.findByLabelText("In-memory source draft");
    fireEvent.change(editor, { target: { value: UPDATED_WORLD_CONTENT } });
    expect(mocks.listAssetCatalog).not.toHaveBeenCalled();

    const worldTab = screen.getByRole("tab", { name: "World" });
    const assetsTab = screen.getByRole("tab", { name: "Assets" });
    const gameTab = screen.getByRole("tab", { name: "Game" });
    expect(worldTab).toHaveAttribute("tabindex", "0");
    expect(assetsTab).toHaveAttribute("tabindex", "-1");
    expect(gameTab).toHaveAttribute("tabindex", "-1");
    worldTab.focus();
    fireEvent.keyDown(worldTab, { key: "End" });
    await waitFor(() => expect(assetsTab).toHaveFocus());
    expect(assetsTab).toHaveAttribute("aria-selected", "true");
    expect(gameTab).toBeDisabled();
    await waitFor(() =>
      expect(mocks.listAssetCatalog).toHaveBeenCalledWith("workspace_01"),
    );
    expect(mocks.listAssetCatalog).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("heading", { name: "Verified asset catalog" })).toBeInTheDocument();
    expect(document.querySelector("#world-workbench")).toHaveAttribute("hidden");
    expect(document.querySelector("#assets-workbench")).not.toHaveAttribute("hidden");
    expect(document.querySelector<HTMLTextAreaElement>("#source-draft")).toHaveValue(
      UPDATED_WORLD_CONTENT,
    );
    expect(screen.getByRole("link", { name: "Skip to Assets workbench" })).toHaveAttribute(
      "href",
      "#assets-workbench",
    );

    expect(screen.getByRole("tablist", { name: "Forge disciplines" })).toHaveAttribute(
      "aria-orientation",
      "vertical",
    );
    expect(fireEvent.keyDown(assetsTab, { key: "ArrowRight" })).toBe(true);
    expect(assetsTab).toHaveFocus();
    expect(assetsTab).toHaveAttribute("aria-selected", "true");

    expect(fireEvent.keyDown(assetsTab, { key: "ArrowDown" })).toBe(false);
    await waitFor(() => expect(worldTab).toHaveFocus());
    expect(document.querySelector("#assets-workbench")).toHaveAttribute("hidden");
    expect(document.querySelector<HTMLTextAreaElement>("#source-draft")).toHaveValue(
      UPDATED_WORLD_CONTENT,
    );
    expect(mocks.getWorkspaceOverview).toHaveBeenCalledTimes(1);
    expect(mocks.listSourceDocuments).toHaveBeenCalledTimes(1);
    expect(mocks.readSourceDocument).toHaveBeenCalledTimes(1);
  });

  it("uses horizontal arrow keys at the compact breakpoint and skips disabled Game", async () => {
    const media = installMatchMedia(true);
    const { api } = createApi();
    installApi(api);
    const view = render(<App />);

    const tablist = screen.getByRole("tablist", { name: "Forge disciplines" });
    const worldTab = screen.getByRole("tab", { name: "World" });
    const assetsTab = screen.getByRole("tab", { name: "Assets" });
    const gameTab = screen.getByRole("tab", { name: "Game" });
    expect(tablist).toHaveAttribute("aria-orientation", "horizontal");
    expect(gameTab).toBeDisabled();

    worldTab.focus();
    expect(fireEvent.keyDown(worldTab, { key: "ArrowDown" })).toBe(true);
    expect(worldTab).toHaveFocus();
    expect(worldTab).toHaveAttribute("aria-selected", "true");

    expect(fireEvent.keyDown(worldTab, { key: "ArrowRight" })).toBe(false);
    await waitFor(() => expect(assetsTab).toHaveFocus());
    expect(assetsTab).toHaveAttribute("aria-selected", "true");

    expect(fireEvent.keyDown(assetsTab, { key: "ArrowRight" })).toBe(false);
    await waitFor(() => expect(worldTab).toHaveFocus());
    expect(worldTab).toHaveAttribute("aria-selected", "true");
    expect(gameTab).toHaveAttribute("tabindex", "-1");

    act(() => media.setMatches(false));
    expect(tablist).toHaveAttribute("aria-orientation", "vertical");
    view.unmount();
    expect(media.removeEventListener).toHaveBeenCalledWith(
      "change",
      media.changeListener,
    );
  });

  it("uses exact revision-bound list and inspect API calls with page replacement", async () => {
    const secondEntryId = `asset_${"2".repeat(64)}`;
    const listAssetCatalog = vi
      .fn()
      .mockResolvedValueOnce(
        assetCatalogListResponse({ entries: [assetEntry()], nextOffset: 64 }),
      )
      .mockResolvedValueOnce(
        assetCatalogListResponse({
          offset: 64,
          entries: [
            assetEntry({
              entry_id: secondEntryId,
              asset_id: "asset_02",
              category: "qa",
              path: "assets/qa.json",
              media_type: "application/json",
            }),
          ],
        }),
      )
      .mockResolvedValueOnce(
        assetCatalogListResponse({ entries: [assetEntry()], nextOffset: 64 }),
      )
      .mockResolvedValueOnce(
        assetCatalogListResponse({
          revision: "9".repeat(64),
          entries: [],
        }),
      );
    const inspectAssetCatalogEntry = vi.fn().mockResolvedValue(
      assetCatalogInspectResponse({
        entry: assetEntry({
          entry_id: secondEntryId,
          asset_id: "asset_02",
          category: "qa",
          path: "assets/qa.json",
          media_type: "application/json",
        }),
        inspection: {
          kind: "json",
          encoding: "utf-8",
          content: '{"valid":true}',
          value: { valid: true },
        },
      }),
    );
    const { api } = createApi({ listAssetCatalog, inspectAssetCatalogEntry });
    installApi(api);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /workspace_01/u }));
    fireEvent.click(screen.getByRole("tab", { name: "Assets" }));
    await waitFor(() => expect(listAssetCatalog).toHaveBeenNthCalledWith(1, "workspace_01"));
    expect(await screen.findByRole("button", { name: /asset_01/u })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() =>
      expect(listAssetCatalog).toHaveBeenNthCalledWith(2, "workspace_01", {
        offset: 64,
        manifestRevision: ASSET_REVISION,
      }),
    );
    expect(await screen.findByRole("button", { name: /asset_02/u })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /asset_01/u })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /asset_02/u }));
    await waitFor(() =>
      expect(inspectAssetCatalogEntry).toHaveBeenCalledWith(
        "workspace_01",
        ASSET_REVISION,
        secondEntryId,
      ),
    );
    expect(await screen.findByRole("heading", { name: "Semantic JSON tree" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
    await waitFor(() =>
      expect(listAssetCatalog).toHaveBeenNthCalledWith(3, "workspace_01", {
        offset: 0,
        manifestRevision: ASSET_REVISION,
      }),
    );
    expect(await screen.findByRole("button", { name: /asset_01/u })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /asset_02/u })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Refresh revision snapshot" }));
    await waitFor(() => expect(listAssetCatalog).toHaveBeenCalledTimes(4));
    expect(listAssetCatalog.mock.calls[3]).toEqual(["workspace_01"]);
    expect(await screen.findByText("0 entries on current page")).toBeInTheDocument();
  });

  it("fails closed on a catalog conflict and refreshes without exposing diagnostics", async () => {
    const listAssetCatalog = vi
      .fn()
      .mockResolvedValueOnce(
        assetCatalogListResponse({ entries: [assetEntry()], nextOffset: 64 }),
      )
      .mockResolvedValueOnce({
        ok: true,
        value: {
          protocol: "rpg-world-forge.studio_protocol",
          protocol_version: 1,
          kind: "error",
          request_id: "catalog-conflict",
          error: {
            code: "conflict",
            message: "SECRET /home/private/catalog.json",
            details: { absolute_root: "/home/private" },
          },
        },
      })
      .mockResolvedValueOnce(
        assetCatalogListResponse({
          revision: "8".repeat(64),
          entries: [assetEntry({ category: "runtime_output" })],
        }),
      );
    const { api } = createApi({ listAssetCatalog });
    installApi(api);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /workspace_01/u }));
    fireEvent.click(screen.getByRole("tab", { name: "Assets" }));
    await screen.findByRole("button", { name: /asset_01/u });
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/revision conflicted/u);
    expect(screen.queryByRole("button", { name: /asset_01/u })).not.toBeInTheDocument();
    expect(screen.queryByText(/SECRET|\/home\/private/u)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Refresh revision snapshot" }));
    await waitFor(() => expect(listAssetCatalog.mock.calls[2]).toEqual(["workspace_01"]));
    expect(await screen.findByRole("button", { name: /asset_01/u })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Runtime output/u })).toBeInTheDocument();
  });

  it("keeps dirty-workspace confirmation active when selection starts from Assets", async () => {
    const { api, mocks } = createApi({
      listWorkspaces: vi.fn().mockResolvedValue(
        legacyResponse("workspace.list", {
          workspaces: [
            { workspace_id: "workspace_01" },
            { workspace_id: "workspace_02" },
          ],
        }),
      ),
    });
    installApi(api);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /workspace_01/u }));
    fireEvent.change(await screen.findByLabelText("In-memory source draft"), {
      target: { value: UPDATED_WORLD_CONTENT },
    });
    fireEvent.click(screen.getByRole("tab", { name: "Assets" }));
    await waitFor(() => expect(mocks.listAssetCatalog).toHaveBeenCalledWith("workspace_01"));

    const workspaceTwo = screen.getByRole("button", { name: /workspace_02/u });
    fireEvent.click(workspaceTwo);
    expect(
      screen.getByRole("dialog", { name: /Discard this in-memory draft/u }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Stay here" }));
    await waitFor(() => expect(workspaceTwo).toHaveFocus());
    expect(screen.getByRole("tab", { name: "Assets" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(document.querySelector<HTMLTextAreaElement>("#source-draft")).toHaveValue(
      UPDATED_WORLD_CONTENT,
    );
  });

  it("reports JSON syntax and confirms dirty source navigation with focus restoration", async () => {
    const { api, mocks } = createApi();
    installApi(api);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /workspace_01/u }));
    const editor = await screen.findByLabelText("In-memory source draft");
    fireEvent.change(editor, { target: { value: '{"broken":' } });
    expect(await screen.findByText(/JSON syntax:/u)).toBeInTheDocument();
    expect(screen.getByText("Draft — not staged")).toBeInTheDocument();

    const mapButton = screen.getByRole("button", { name: /garden\.json/u });
    fireEvent.click(mapButton);
    expect(screen.getByRole("dialog", { name: /Discard this in-memory draft/u })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Stay here" }));
    await waitFor(() => expect(mapButton).toHaveFocus());
    expect(screen.getByLabelText("In-memory source draft")).toHaveValue('{"broken":');

    fireEvent.click(mapButton);
    fireEvent.click(screen.getByRole("button", { name: "Discard draft" }));
    expect(await screen.findByText(/Neutral garden: 3 × 2 cells/u)).toBeInTheDocument();
    expect(mocks.readSourceDocument).toHaveBeenCalledWith("workspace_01", "source/maps/garden.json");
    expect(screen.getByText("Draft preview — non-authoritative")).toBeInTheDocument();
  });

  it("rejects a source read whose SHA no longer matches the authorized list", async () => {
    const { api } = createApi({
      readSourceDocument: vi.fn().mockResolvedValue(
        namedResponse("source.read", {
          document: { ...WORLD_DOCUMENT, sha256: "c".repeat(64) },
        }),
      ),
    });
    installApi(api);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /workspace_01/u }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Source changed after listing/u);
    expect(screen.queryByLabelText("In-memory source draft")).not.toBeInTheDocument();
  });

  it("polls each bounded dock category once per cycle", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api, mocks } = createApi();
    installApi(api);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /workspace_01/u }));
    await waitFor(() => {
      expect(mocks.listEvents).toHaveBeenCalledTimes(1);
      expect(mocks.listChangesets).toHaveBeenCalledTimes(1);
      expect(mocks.listJobs).toHaveBeenCalledTimes(1);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    await waitFor(() => {
      expect(mocks.listEvents).toHaveBeenCalledTimes(2);
      expect(mocks.listChangesets).toHaveBeenCalledTimes(2);
      expect(mocks.listJobs).toHaveBeenCalledTimes(2);
    });
  });

  it("retains named Codex bind, thread, turn, interrupt, and user-input controls", async () => {
    let codexListener: ((event: CodexActivityEvent) => void) | undefined;
    const { api, mocks } = createApi({
      onCodexEvent: (listener) => {
        codexListener = listener;
        return vi.fn();
      },
    });
    installApi(api);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /workspace_01/u }));
    await screen.findByRole("heading", { name: "Neutral World" });
    fireEvent.click(screen.getByRole("button", { name: "Assistant" }));
    fireEvent.click(screen.getByRole("button", { name: "Bind Codex" }));
    await waitFor(() => expect(mocks.bindCodexWorkspace).toHaveBeenCalledWith("workspace_01"));
    fireEvent.click(screen.getByRole("button", { name: "New thread" }));
    await waitFor(() => expect(mocks.startCodexThread).toHaveBeenCalledOnce());
    fireEvent.change(screen.getByLabelText("Turn message"), { target: { value: "Review this lore" } });
    fireEvent.click(screen.getByRole("button", { name: "Send turn" }));
    await waitFor(() =>
      expect(mocks.startCodexTurn).toHaveBeenCalledWith("thread-1", "Review this lore"),
    );
    fireEvent.click(screen.getByRole("button", { name: "Interrupt turn" }));
    await waitFor(() =>
      expect(mocks.interruptCodexTurn).toHaveBeenCalledWith("thread-1", "turn-1"),
    );

    act(() => {
      codexListener?.({
        type: "codex-user-input",
        token: "token-1",
        threadId: "thread-1",
        turnId: "turn-2",
        questions: [
          {
            id: "tone",
            header: "Tone",
            question: "Choose a neutral tone",
            isOther: false,
            isSecret: false,
            options: [{ label: "Quiet", description: "Restrained" }],
          },
        ],
      });
    });
    fireEvent.change(screen.getByLabelText("Choose a neutral tone"), {
      target: { value: "Quiet" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Submit answers" }));
    await waitFor(() =>
      expect(mocks.answerCodexUserInput).toHaveBeenCalledWith("token-1", { tone: ["Quiet"] }),
    );
  });

  it("summarizes service diagnostics without injecting raw stderr", () => {
    let activityListener: ((event: StudioActivityEvent) => void) | undefined;
    const { api } = createApi({
      onEvent: (listener) => {
        activityListener = listener;
        return vi.fn();
      },
    });
    installApi(api);
    render(<App />);
    act(() => activityListener?.({ type: "service-stderr", text: "SECRET absolute/path" }));
    expect(screen.queryByText(/SECRET absolute\/path/u)).not.toBeInTheDocument();
    expect(screen.getByText(/1 live updates/u)).toBeInTheDocument();
  });

  it("stages an exact draft, approves without applying, then separately applies and refreshes", async () => {
    const appliedSha = "e".repeat(64);
    const listSourceDocuments = vi
      .fn()
      .mockResolvedValueOnce(sourceListResponse(SHA_WORLD))
      .mockResolvedValueOnce(sourceListResponse(appliedSha));
    const readSourceDocument = vi.fn().mockImplementation((_workspaceId: string, path: string) =>
      Promise.resolve(
        namedResponse("source.read", {
          document:
            path === "source/maps/garden.json"
              ? MAP_DOCUMENT
              : listSourceDocuments.mock.calls.length > 1
                ? { ...WORLD_DOCUMENT, sha256: appliedSha, content: UPDATED_WORLD_CONTENT }
                : WORLD_DOCUMENT,
        }),
      ),
    );
    const { api, mocks } = createApi({ listSourceDocuments, readSourceDocument });
    installApi(api);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /workspace_01/u }));
    const editor = await screen.findByLabelText("In-memory source draft");
    fireEvent.change(editor, { target: { value: UPDATED_WORLD_CONTENT } });
    expect(mocks.stageSourceDocument).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Stage for review" }));
    await waitFor(() =>
      expect(mocks.stageSourceDocument).toHaveBeenCalledWith(
        "workspace_01",
        "source/world.json",
        SHA_WORLD,
        UPDATED_WORLD_CONTENT,
      ),
    );
    expect(await screen.findByRole("dialog", { name: "Changeset review" })).toBeInTheDocument();
    await waitFor(() => {
      expect(mocks.getChangeset).toHaveBeenCalledWith("changeset_01");
      expect(mocks.readChangesetDiff).toHaveBeenCalledWith("changeset_01");
    });
    expect(screen.getByText(SHA_REVIEW)).toBeInTheDocument();
    expect(screen.getByText("/title")).toBeInTheDocument();
    expect(screen.getByText('"A quieter world"')).toBeInTheDocument();
    expect(document.querySelector("pre")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Approve review" }));
    expect(screen.getByRole("dialog", { name: /Approve this reviewed changeset/u })).toBeInTheDocument();
    expect(mocks.approveChangeset).not.toHaveBeenCalled();
    expect(mocks.applyChangeset).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Approve only" }));
    await waitFor(() =>
      expect(mocks.approveChangeset).toHaveBeenCalledWith("changeset_01", SHA_REVIEW),
    );
    expect(mocks.applyChangeset).not.toHaveBeenCalled();

    fireEvent.click(await screen.findByRole("button", { name: "Apply approved changeset" }));
    expect(screen.getByRole("dialog", { name: /Apply this approved changeset/u })).toBeInTheDocument();
    expect(screen.getByText(/draft based on a changed source will no longer be active/u)).toBeInTheDocument();
    expect(mocks.applyChangeset).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm apply" }));
    await waitFor(() =>
      expect(mocks.applyChangeset).toHaveBeenCalledWith("changeset_01", SHA_REVIEW),
    );
    await waitFor(() => expect(listSourceDocuments).toHaveBeenCalledTimes(2));
    expect(await screen.findByLabelText("In-memory source draft")).toHaveValue(UPDATED_WORLD_CONTENT);
    expect(screen.queryByRole("dialog", { name: "Changeset review" })).not.toBeInTheDocument();
  });

  it("opens a v2 dock proposal, confirms rejection, and restores focus", async () => {
    const { api, mocks } = createApi({
      listChangesets: vi.fn().mockResolvedValue(
        legacyResponse("changeset.list", { changesets: [V2_STAGED] }),
      ),
    });
    installApi(api);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /workspace_01/u }));
    fireEvent.click(screen.getByRole("tab", { name: "Changesets" }));
    const openButton = await screen.findByRole("button", { name: "Open review" });
    fireEvent.click(openButton);
    expect(await screen.findByRole("button", { name: "Close changeset review" })).toHaveFocus();
    const rejectButton = await screen.findByRole("button", { name: "Reject changeset" });
    fireEvent.click(rejectButton);
    fireEvent.click(screen.getByRole("button", { name: "Return to review" }));
    await waitFor(() => expect(rejectButton).toHaveFocus());
    fireEvent.click(rejectButton);
    expect(mocks.rejectChangeset).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm rejection" }));
    await waitFor(() =>
      expect(mocks.rejectChangeset).toHaveBeenCalledWith("changeset_01", SHA_REVIEW),
    );
    expect(mocks.applyChangeset).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Close changeset review" }));
    await waitFor(() => expect(openButton).toHaveFocus());
  });

  it("exposes only the top review dialog and focuses a stable pending action status", async () => {
    let resolveApproval: ((value: ReturnType<typeof approvalResponse>) => void) | undefined;
    const approveChangeset = vi.fn().mockImplementation(
      () =>
        new Promise<ReturnType<typeof approvalResponse>>((resolve) => {
          resolveApproval = resolve;
        }),
    );
    const { api } = createApi({
      listChangesets: vi.fn().mockResolvedValue(
        legacyResponse("changeset.list", { changesets: [V2_STAGED] }),
      ),
      approveChangeset,
    });
    installApi(api);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /workspace_01/u }));
    fireEvent.click(screen.getByRole("tab", { name: "Changesets" }));
    fireEvent.click(await screen.findByRole("button", { name: "Open review" }));
    const reviewDialog = await screen.findByRole("dialog", { name: "Changeset review" });
    fireEvent.click(screen.getByRole("button", { name: "Approve review" }));

    const confirmation = screen.getByRole("dialog", { name: /Approve this reviewed changeset/u });
    expect(screen.getAllByRole("dialog")).toEqual([confirmation]);
    expect(reviewDialog).toHaveAttribute("aria-hidden", "true");
    expect(reviewDialog).not.toHaveAttribute("aria-modal");
    expect(reviewDialog).toHaveAttribute("inert");
    expect(document.querySelector(".studio-content")).toHaveAttribute("inert");
    expect(screen.getByRole("button", { name: "Return to review" })).toHaveFocus();

    fireEvent.click(screen.getByRole("button", { name: "Approve only" }));
    const pending = await screen.findByText("Approval request pending. Source files remain unchanged.");
    expect(pending).toHaveFocus();
    expect(reviewDialog).toHaveAttribute("aria-modal", "true");
    expect(reviewDialog).not.toHaveAttribute("aria-hidden");
    expect(reviewDialog).not.toHaveAttribute("inert");
    expect(screen.getAllByRole("dialog")).toEqual([reviewDialog]);
    expect(approveChangeset).toHaveBeenCalledWith("changeset_01", SHA_REVIEW);

    await act(async () => {
      resolveApproval?.(approvalResponse());
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Close changeset review" })).toHaveFocus(),
    );
  });

  it("keeps legacy v1 exact diff unavailable and permits only confirmed rejection", async () => {
    const rejectChangeset = vi.fn().mockResolvedValue(
      namedResponse("changeset.reject", { changeset: { ...V1_STAGED, status: "rejected" } }),
    );
    const { api } = createApi({
      listChangesets: vi.fn().mockResolvedValue(
        legacyResponse("changeset.list", { changesets: [V1_STAGED] }),
      ),
      getChangeset: vi.fn().mockResolvedValue(
        namedResponse("changeset.get", { changeset: V1_STAGED }),
      ),
      readChangesetDiff: vi.fn().mockResolvedValue(
        namedResponse("changeset.diff", { diff: V1_DIFF }),
      ),
      rejectChangeset,
    });
    installApi(api);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /workspace_01/u }));
    fireEvent.click(screen.getByRole("tab", { name: "Changesets" }));
    fireEvent.click(await screen.findByRole("button", { name: "Open review" }));
    expect(await screen.findByRole("heading", { name: "Exact diff unavailable" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve review" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Apply approved changeset" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reject changeset" }));
    expect(rejectChangeset).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm rejection" }));
    await waitFor(() => expect(rejectChangeset).toHaveBeenCalledWith("legacy_01", undefined));
  });

  it("surfaces staging failures without opening hidden review or write flows", async () => {
    const { api, mocks } = createApi({
      stageSourceDocument: vi.fn().mockResolvedValue({
        ok: false,
        error: { code: "service_unavailable", message: "Review service unavailable" },
      }),
    });
    installApi(api);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /workspace_01/u }));
    fireEvent.change(await screen.findByLabelText("In-memory source draft"), {
      target: { value: UPDATED_WORLD_CONTENT },
    });
    fireEvent.click(screen.getByRole("button", { name: "Stage for review" }));
    expect(await screen.findByText("Review service unavailable")).toBeInTheDocument();
    expect(mocks.getChangeset).not.toHaveBeenCalled();
    expect(mocks.readChangesetDiff).not.toHaveBeenCalled();
    expect(mocks.approveChangeset).not.toHaveBeenCalled();
    expect(mocks.applyChangeset).not.toHaveBeenCalled();
  });

  it("keeps reviewed evidence open when an action fails and never advances to apply", async () => {
    const approveChangeset = vi.fn().mockResolvedValue({
      ok: false,
      error: { code: "invalid_request", message: "Approval review identity is stale" },
    });
    const { api, mocks } = createApi({
      listChangesets: vi.fn().mockResolvedValue(
        legacyResponse("changeset.list", { changesets: [V2_STAGED] }),
      ),
      approveChangeset,
    });
    installApi(api);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /workspace_01/u }));
    fireEvent.click(screen.getByRole("tab", { name: "Changesets" }));
    fireEvent.click(await screen.findByRole("button", { name: "Open review" }));
    fireEvent.click(await screen.findByRole("button", { name: "Approve review" }));
    fireEvent.click(screen.getByRole("button", { name: "Approve only" }));
    expect(await screen.findAllByText("Approval review identity is stale")).not.toHaveLength(0);
    expect(screen.getByRole("dialog", { name: "Changeset review" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Apply approved changeset" })).not.toBeInTheDocument();
    expect(mocks.applyChangeset).not.toHaveBeenCalled();
    expect(mocks.listSourceDocuments).toHaveBeenCalledTimes(1);
  });
});

function createApi(overrides: Partial<ForgeStudioApi> = {}) {
  const unavailable = vi.fn().mockResolvedValue({
    ok: false,
    error: { code: "service_unavailable", message: "Unavailable in fixture" },
  });
  const listEvents = vi.fn().mockResolvedValue(legacyResponse("events.list", { events: [] }));
  const listChangesets = vi.fn().mockResolvedValue(
    legacyResponse("changeset.list", { changesets: [] }),
  );
  const listJobs = vi.fn().mockResolvedValue(legacyResponse("job.list", { jobs: [] }));
  const getWorkspaceOverview = vi.fn().mockResolvedValue(
    namedResponse("workspace.overview", { overview: OVERVIEW }),
  );
  const listSourceDocuments = vi.fn().mockResolvedValue(
    namedResponse("source.list", {
      documents: [
        { path: "source/world.json", kind: "world", size: 24, sha256: SHA_WORLD },
        { path: "source/maps/garden.json", kind: "maps", size: 120, sha256: SHA_MAP },
      ],
    }),
  );
  const readSourceDocument = vi.fn().mockImplementation((_workspaceId: string, path: string) =>
    Promise.resolve(
      namedResponse("source.read", {
        document: path === "source/maps/garden.json" ? MAP_DOCUMENT : WORLD_DOCUMENT,
      }),
    ),
  );
  const listAssetCatalog = vi.fn().mockResolvedValue(
    assetCatalogListResponse({
      entries: [assetEntry()],
      nextOffset: 64,
    }),
  );
  const inspectAssetCatalogEntry = vi.fn().mockResolvedValue(
    assetCatalogInspectResponse({
      inspection: {
        kind: "png",
        width: 64,
        height: 32,
        bit_depth: 8,
        color_type: 6,
        interlaced: false,
      },
    }),
  );
  const validateWorld = vi.fn().mockResolvedValue(
    namedResponse("world.validate", { validation: VALIDATION }),
  );
  const analyzeWorld = vi.fn().mockResolvedValue(
    namedResponse("world.analyze", {
      validation: VALIDATION,
      analysis: {
        format: "rpg-world-forge.narrative_analysis",
        format_version: 1,
        world_id: "world_01",
        summary: { finding_count: 1 },
        findings: [
          {
            severity: "info",
            code: "quiet_start",
            path: "/lore",
            message: "Opening is restrained",
          },
        ],
      },
    }),
  );
  const bindCodexWorkspace = vi.fn().mockResolvedValue({
    ok: true,
    value: { state: "ready", message: "Codex is bound", pid: 456, workspaceId: "workspace_01" },
  });
  const startCodexThread = vi.fn().mockResolvedValue({
    ok: true,
    value: { threadId: "thread-1" },
  });
  const startCodexTurn = vi.fn().mockResolvedValue({
    ok: true,
    value: { turnId: "turn-1", status: "inProgress" },
  });
  const interruptCodexTurn = vi.fn().mockResolvedValue({ ok: true, value: undefined });
  const answerCodexUserInput = vi.fn().mockResolvedValue({ ok: true, value: undefined });
  const stageSourceDocument = vi.fn().mockResolvedValue(
    namedResponse("changeset.create", { changeset: V2_STAGED }),
  );
  const getChangeset = vi.fn().mockResolvedValue(
    namedResponse("changeset.get", { changeset: V2_STAGED }),
  );
  const readChangesetDiff = vi.fn().mockResolvedValue(
    namedResponse("changeset.diff", { diff: V2_DIFF }),
  );
  const approveChangeset = vi.fn().mockResolvedValue(
    namedResponse("changeset.approve", { changeset: { ...V2_STAGED, status: "approved" } }),
  );
  const rejectChangeset = vi.fn().mockResolvedValue(
    namedResponse("changeset.reject", { changeset: { ...V2_STAGED, status: "rejected" } }),
  );
  const applyChangeset = vi.fn().mockResolvedValue(
    namedResponse("changeset.apply", { changeset: { ...V2_STAGED, status: "applied" } }),
  );
  const api: ForgeStudioApi = {
    initialize: vi.fn().mockResolvedValue(legacyResponse("service.initialize", { service: "ready" })),
    getServiceStatus: vi.fn().mockResolvedValue({
      ok: true,
      value: { state: "ready", message: "Forge Studio service is ready", pid: 123 },
    }),
    listWorkspaces: vi.fn().mockResolvedValue(
      legacyResponse("workspace.list", { workspaces: [{ workspace_id: "workspace_01" }] }),
    ),
    listEvents,
    listChangesets,
    listJobs,
    getWorkspaceOverview,
    listSourceDocuments,
    readSourceDocument,
    listAssetCatalog,
    inspectAssetCatalogEntry,
    stageSourceDocument,
    getChangeset,
    readChangesetDiff,
    approveChangeset,
    rejectChangeset,
    applyChangeset,
    validateWorld,
    analyzeWorld,
    validateAssetReceipt: unavailable,
    verifyAssetpack: unavailable,
    runHeadless: unavailable,
    runReplay: unavailable,
    cancelJob: unavailable,
    onEvent: () => vi.fn(),
    getCodexStatus: vi.fn().mockResolvedValue({
      ok: true,
      value: { state: "unbound", message: "Not bound", pid: null, workspaceId: null },
    }),
    bindCodexWorkspace,
    readCodexAccount: unavailable,
    startCodexLogin: unavailable,
    startCodexThread,
    resumeCodexThread: unavailable,
    forkCodexThread: unavailable,
    startCodexTurn,
    steerCodexTurn: unavailable,
    interruptCodexTurn,
    answerCodexUserInput,
    onCodexEvent: () => vi.fn(),
    ...overrides,
  };
  return {
    api,
    mocks: {
      listEvents,
      listChangesets,
      listJobs,
      getWorkspaceOverview,
      listSourceDocuments,
      readSourceDocument,
      listAssetCatalog,
      inspectAssetCatalogEntry,
      validateWorld,
      analyzeWorld,
      bindCodexWorkspace,
      startCodexThread,
      startCodexTurn,
      interruptCodexTurn,
      answerCodexUserInput,
      stageSourceDocument,
      getChangeset,
      readChangesetDiff,
      approveChangeset,
      rejectChangeset,
      applyChangeset,
    },
  };
}

function legacyResponse(method: string, result: Record<string, unknown>) {
  return {
    ok: true as const,
    value: {
      protocol: "rpg-world-forge.studio_protocol" as const,
      protocol_version: 1 as const,
      kind: "response" as const,
      request_id: "fixture-request",
      method,
      result,
    },
  };
}

function namedResponse<M extends string, R>(method: M, result: R) {
  return {
    ok: true as const,
    value: {
      protocol: "rpg-world-forge.studio_protocol" as const,
      protocol_version: 1 as const,
      kind: "response" as const,
      request_id: "fixture-request",
      method,
      result,
    },
  };
}

function approvalResponse() {
  return namedResponse("changeset.approve", {
    changeset: { ...V2_STAGED, status: "approved" as const },
  });
}

function sourceListResponse(worldSha: string) {
  return namedResponse("source.list", {
    documents: [
      { path: "source/world.json", kind: "world", size: 24, sha256: worldSha },
      { path: "source/maps/garden.json", kind: "maps", size: 120, sha256: SHA_MAP },
    ],
  });
}

function assetCatalogListResponse({
  revision = ASSET_REVISION,
  offset = 0,
  entries = [assetEntry()],
  nextOffset = null,
}: {
  revision?: string;
  offset?: number;
  entries?: ReturnType<typeof assetEntry>[];
  nextOffset?: number | null;
} = {}) {
  return namedResponse("asset.catalog.list", {
    manifest_revision: revision,
    offset,
    limit: 64,
    entries,
    next_offset: nextOffset,
  });
}

function assetCatalogInspectResponse({
  revision = ASSET_REVISION,
  entry = assetEntry(),
  inspection = {
    kind: "unavailable" as const,
    reason: "identity_only" as const,
  },
}: {
  revision?: string;
  entry?: ReturnType<typeof assetEntry>;
  inspection?: Record<string, unknown>;
} = {}) {
  return namedResponse("asset.catalog.inspect", {
    manifest_revision: revision,
    entry,
    inspection,
  });
}

function assetEntry(
  overrides: Partial<{
    entry_id: string;
    asset_id: string | null;
    category:
      | "manifest"
      | "target"
      | "visual_bible"
      | "audio_bible"
      | "inventory"
      | "specification"
      | "production_receipt"
      | "production_request"
      | "production_output"
      | "processing_receipt"
      | "processing_recipe"
      | "processing_output"
      | "license"
      | "qa"
      | "runtime_output";
    role: string | null;
    path: string | null;
    sha256: string;
    media_type: string | null;
    selected: boolean;
    inspectable: boolean;
  }> = {},
) {
  return {
    entry_id: ASSET_ENTRY_ID,
    asset_id: "asset_01",
    category: "visual_bible" as const,
    role: "concept",
    path: "assets/concept.png",
    sha256: "f".repeat(64),
    media_type: "image/png",
    selected: false,
    inspectable: true,
    ...overrides,
  };
}

const OVERVIEW = {
  workspace_id: "workspace_01",
  project: { world_id: "world_01", title: "Neutral World", world_version: "1.0.0" },
  status: { current_phase: "foundation", revision: 4, canon_locked: false, worldpack_hash: null },
  repositories: { game_registered: false, bundle_registered: false },
  capabilities: {
    providers: false,
    source_inspection: true,
    world_validation: true,
    narrative_analysis: true,
    staged_changesets: true,
    asset_catalog_inspection: true,
  },
};

const VALIDATION = {
  valid: true,
  profile: "release",
  world_id: "world_01",
  object_count: 7,
  diagnostics: [],
  diagnostics_truncated: false,
};

const WORLD_DOCUMENT = {
  path: "source/world.json",
  kind: "world",
  size: 24,
  sha256: SHA_WORLD,
  encoding: "utf-8",
  content: '{"id":"world_01","title":"Neutral World"}',
  json: { id: "world_01", title: "Neutral World" },
};

const MAP_DOCUMENT = {
  path: "source/maps/garden.json",
  kind: "maps",
  size: 120,
  sha256: SHA_MAP,
  encoding: "utf-8",
  content: JSON.stringify({
    id: "garden",
    display_name: "Neutral garden",
    width: 3,
    height: 2,
    legend: { ".": "ground", "#": "rock" },
    rows: ["...", ".#."],
  }),
  json: {},
};

const V2_STAGED = {
  format: "rpg-world-forge.studio_changeset" as const,
  format_version: 2 as const,
  changeset_id: "changeset_01",
  workspace_id: "workspace_01",
  status: "staged" as const,
  operations: [
    {
      path: "source/world.json",
      operation: "replace" as const,
      base_sha256: SHA_WORLD,
      base_size: 24,
      proposed_sha256: SHA_PROPOSED,
      size: new TextEncoder().encode(UPDATED_WORLD_CONTENT).byteLength,
    },
  ] as const,
  review_sha256: SHA_REVIEW,
  created_at: "2026-07-23T00:00:00Z",
  updated_at: "2026-07-23T00:00:00Z",
};

const V2_DIFF = {
  changeset_id: "changeset_01",
  changeset_format_version: 2 as const,
  available: true as const,
  unavailable_reason: null,
  review_sha256: SHA_REVIEW,
  operations: [
    {
      path: "source/world.json",
      operation: "replace" as const,
      base_sha256: SHA_WORLD,
      base_size: 24,
      proposed_sha256: SHA_PROPOSED,
      size: new TextEncoder().encode(UPDATED_WORLD_CONTENT).byteLength,
      text_hunks: [
        {
          base_start: 1,
          base_count: 1,
          proposed_start: 1,
          proposed_count: 1,
          lines: [
            { kind: "remove" as const, text: WORLD_DOCUMENT.content },
            { kind: "add" as const, text: UPDATED_WORLD_CONTENT },
          ],
        },
      ],
      json_pointer_changes: [
        {
          operation: "replace" as const,
          pointer: "/title",
          old_value: "Neutral World",
          value: "A quieter world",
        },
      ],
    },
  ] as const,
};

const V1_STAGED = {
  format: "rpg-world-forge.studio_changeset" as const,
  format_version: 1 as const,
  changeset_id: "legacy_01",
  workspace_id: "workspace_01",
  status: "staged" as const,
  operations: [
    {
      path: "source/world.json",
      operation: "replace" as const,
      base_sha256: SHA_WORLD,
      proposed_sha256: SHA_PROPOSED,
      size: 24,
    },
  ] as const,
  created_at: "2026-07-22T00:00:00Z",
  updated_at: "2026-07-22T00:00:00Z",
};

const V1_DIFF = {
  changeset_id: "legacy_01",
  changeset_format_version: 1 as const,
  available: false as const,
  unavailable_reason: "legacy_base_bytes_not_retained" as const,
  review_sha256: null,
  operations: [] as const,
};

function installMatchMedia(initialMatches: boolean): {
  setMatches: (matches: boolean) => void;
  removeEventListener: ReturnType<typeof vi.fn>;
  readonly changeListener: EventListenerOrEventListenerObject | null;
} {
  let matches = initialMatches;
  let changeListener: EventListenerOrEventListenerObject | null = null;
  const addEventListener = vi.fn(
    (type: string, listener: EventListenerOrEventListenerObject): void => {
      if (type === "change") changeListener = listener;
    },
  );
  const removeEventListener = vi.fn();
  const mediaQueryList = {
    get matches() {
      return matches;
    },
    media: COMPACT_DISCIPLINE_TABS_QUERY,
    onchange: null,
    addEventListener,
    removeEventListener,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  } as unknown as MediaQueryList;
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn((query: string) => {
      expect(query).toBe(COMPACT_DISCIPLINE_TABS_QUERY);
      return mediaQueryList;
    }),
  });
  return {
    setMatches(nextMatches: boolean): void {
      matches = nextMatches;
      const event = Object.assign(new Event("change"), {
        matches,
        media: COMPACT_DISCIPLINE_TABS_QUERY,
      }) as MediaQueryListEvent;
      if (typeof changeListener === "function") {
        changeListener(event);
      } else {
        changeListener?.handleEvent(event);
      }
    },
    removeEventListener,
    get changeListener() {
      return changeListener;
    },
  };
}

function installApi(api: ForgeStudioApi): void {
  Object.defineProperty(window, "forgeStudio", { configurable: true, value: api });
}

function canvasContext(): CanvasRenderingContext2D {
  return {
    beginPath: vi.fn(),
    clearRect: vi.fn(),
    closePath: vi.fn(),
    fill: vi.fn(),
    fillRect: vi.fn(),
    lineTo: vi.fn(),
    moveTo: vi.fn(),
    setTransform: vi.fn(),
    stroke: vi.fn(),
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 1,
  } as unknown as CanvasRenderingContext2D;
}
