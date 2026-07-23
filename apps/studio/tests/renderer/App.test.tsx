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

beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(canvasContext());
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
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
    expect(screen.getByRole("button", { name: "Assets" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Game" })).toBeDisabled();
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
      validateWorld,
      analyzeWorld,
      bindCodexWorkspace,
      startCodexThread,
      startCodexTurn,
      interruptCodexTurn,
      answerCodexUserInput,
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
