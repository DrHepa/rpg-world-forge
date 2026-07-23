import type {
  StudioSourceListResult,
  StudioSourceReadResult,
  StudioWorkspaceOverviewResult,
  StudioWorldAnalyzeResult,
  StudioWorldValidateResult,
} from "../shared/studio-api";

export type SourceSummary = StudioSourceListResult["documents"][number];
export type SourceDocument = StudioSourceReadResult["document"];

export interface CachedSourceDocument extends SourceDocument {
  workspaceId: string;
}

export interface SourceDraft {
  workspaceId: string;
  path: string;
  baseSha256: string;
  text: string;
  dirty: boolean;
  jsonSyntaxError: string | null;
}

export interface AuthoringState {
  generation: number;
  workspaceId: string | null;
  loadingWorkspace: boolean;
  overview: StudioWorkspaceOverviewResult["overview"] | null;
  documents: SourceSummary[];
  validation: StudioWorldValidateResult["validation"] | null;
  analysis: StudioWorldAnalyzeResult["analysis"] | null;
  selectedPath: string | null;
  sourcePending: boolean;
  sourceError: string | null;
  workspaceError: string | null;
  cache: Record<string, CachedSourceDocument>;
  drafts: Record<string, SourceDraft>;
}

export type AuthoringAction =
  | { type: "workspace-selected"; workspaceId: string; generation: number }
  | {
      type: "workspace-loaded";
      workspaceId: string;
      generation: number;
      overview: StudioWorkspaceOverviewResult["overview"];
      documents: SourceSummary[];
      validation: StudioWorldValidateResult["validation"];
      analysis: StudioWorldAnalyzeResult["analysis"] | null;
    }
  | { type: "workspace-failed"; workspaceId: string; generation: number; message: string }
  | { type: "source-selected"; path: string }
  | { type: "source-loading"; workspaceId: string; generation: number; path: string }
  | {
      type: "source-loaded";
      workspaceId: string;
      generation: number;
      path: string;
      expectedSha256: string;
      document: SourceDocument;
    }
  | {
      type: "source-failed";
      workspaceId: string;
      generation: number;
      path: string;
      message: string;
    }
  | { type: "draft-changed"; text: string }
  | { type: "draft-discarded" };

export function createInitialAuthoringState(): AuthoringState {
  return {
    generation: 0,
    workspaceId: null,
    loadingWorkspace: false,
    overview: null,
    documents: [],
    validation: null,
    analysis: null,
    selectedPath: null,
    sourcePending: false,
    sourceError: null,
    workspaceError: null,
    cache: {},
    drafts: {},
  };
}

export function authoringReducer(
  state: AuthoringState,
  action: AuthoringAction,
): AuthoringState {
  switch (action.type) {
    case "workspace-selected":
      return {
        ...state,
        generation: action.generation,
        workspaceId: action.workspaceId,
        loadingWorkspace: true,
        overview: null,
        documents: [],
        validation: null,
        analysis: null,
        selectedPath: null,
        sourcePending: false,
        sourceError: null,
        workspaceError: null,
      };
    case "workspace-loaded":
      if (!matchesGeneration(state, action.workspaceId, action.generation)) return state;
      return {
        ...state,
        loadingWorkspace: false,
        overview: action.overview,
        documents: [...action.documents].sort((left, right) =>
          left.path.localeCompare(right.path, "en"),
        ),
        validation: action.validation,
        analysis: action.analysis,
        workspaceError: null,
      };
    case "workspace-failed":
      if (!matchesGeneration(state, action.workspaceId, action.generation)) return state;
      return { ...state, loadingWorkspace: false, workspaceError: boundedMessage(action.message) };
    case "source-selected":
      return {
        ...state,
        selectedPath: action.path,
        sourcePending: false,
        sourceError: null,
      };
    case "source-loading":
      if (!matchesGeneration(state, action.workspaceId, action.generation)) return state;
      if (state.selectedPath !== action.path) return state;
      return { ...state, sourcePending: true, sourceError: null };
    case "source-loaded": {
      if (!matchesGeneration(state, action.workspaceId, action.generation)) return state;
      const selected = selectedSourceSummary(state);
      if (
        state.selectedPath !== action.path ||
        selected?.path !== action.path ||
        selected.sha256 !== action.expectedSha256
      ) {
        return state;
      }
      if (action.document.path !== action.path) {
        return {
          ...state,
          sourcePending: false,
          sourceError: "Source response did not match the requested document.",
        };
      }
      if (action.document.sha256 !== action.expectedSha256) {
        return {
          ...state,
          sourcePending: false,
          sourceError: "Source changed after listing; refresh the workspace before editing.",
        };
      }
      const key = sourceVersionKey(action.workspaceId, action.document.path, action.document.sha256);
      const cached: CachedSourceDocument = { ...action.document, workspaceId: action.workspaceId };
      const draft = state.drafts[key] ?? {
        workspaceId: action.workspaceId,
        path: action.document.path,
        baseSha256: action.document.sha256,
        text: action.document.content,
        dirty: false,
        jsonSyntaxError: jsonSyntaxMessage(action.document.path, action.document.content),
      };
      return {
        ...state,
        sourcePending: false,
        sourceError: null,
        cache: { ...state.cache, [key]: cached },
        drafts: { ...state.drafts, [key]: draft },
      };
    }
    case "source-failed":
      if (!matchesGeneration(state, action.workspaceId, action.generation)) return state;
      if (state.selectedPath !== action.path) return state;
      return { ...state, sourcePending: false, sourceError: boundedMessage(action.message) };
    case "draft-changed": {
      const selection = selectedSourceVersion(state);
      if (!selection) return state;
      const key = sourceVersionKey(selection.workspaceId, selection.path, selection.sha256);
      const cached = state.cache[key];
      if (!cached) return state;
      return {
        ...state,
        drafts: {
          ...state.drafts,
          [key]: {
            workspaceId: selection.workspaceId,
            path: selection.path,
            baseSha256: selection.sha256,
            text: action.text,
            dirty: action.text !== cached.content,
            jsonSyntaxError: jsonSyntaxMessage(selection.path, action.text),
          },
        },
      };
    }
    case "draft-discarded": {
      const selection = selectedSourceVersion(state);
      if (!selection) return state;
      const key = sourceVersionKey(selection.workspaceId, selection.path, selection.sha256);
      const cached = state.cache[key];
      if (!cached) return state;
      return {
        ...state,
        drafts: {
          ...state.drafts,
          [key]: {
            workspaceId: selection.workspaceId,
            path: selection.path,
            baseSha256: selection.sha256,
            text: cached.content,
            dirty: false,
            jsonSyntaxError: jsonSyntaxMessage(selection.path, cached.content),
          },
        },
      };
    }
  }
}

export function selectedSourceSummary(state: AuthoringState): SourceSummary | null {
  return state.documents.find((document) => document.path === state.selectedPath) ?? null;
}

export function selectedSourceVersion(
  state: AuthoringState,
): { workspaceId: string; path: string; sha256: string } | null {
  const summary = selectedSourceSummary(state);
  return state.workspaceId && summary
    ? { workspaceId: state.workspaceId, path: summary.path, sha256: summary.sha256 }
    : null;
}

export function selectedCachedDocument(state: AuthoringState): CachedSourceDocument | null {
  const selected = selectedSourceVersion(state);
  return selected
    ? state.cache[sourceVersionKey(selected.workspaceId, selected.path, selected.sha256)] ?? null
    : null;
}

export function selectedDraft(state: AuthoringState): SourceDraft | null {
  const selected = selectedSourceVersion(state);
  return selected
    ? state.drafts[sourceVersionKey(selected.workspaceId, selected.path, selected.sha256)] ?? null
    : null;
}

export function sourceVersionKey(workspaceId: string, path: string, sha256: string): string {
  return `${workspaceId}\u0000${path}\u0000${sha256}`;
}

export function jsonSyntaxMessage(path: string, text: string): string | null {
  if (!path.toLowerCase().endsWith(".json")) return null;
  try {
    JSON.parse(text);
    return null;
  } catch (error) {
    const message = error instanceof SyntaxError ? error.message : "Invalid JSON";
    return boundedMessage(message, 180);
  }
}

export function boundedMessage(value: string, limit = 240): string {
  const printable = Array.from(value, (character) => {
    const code = character.codePointAt(0) ?? 0;
    return code < 32 || code === 127 ? " " : character;
  }).join("");
  const normalized = printable.replace(/\s+/gu, " ").trim();
  return normalized.length <= limit ? normalized : `${normalized.slice(0, limit - 1)}…`;
}

export class RequestLimiter {
  readonly #maximum: number;
  #active = 0;
  readonly #waiters: Array<() => void> = [];

  constructor(maximum = 4) {
    if (!Number.isSafeInteger(maximum) || maximum < 1) {
      throw new TypeError("Request concurrency must be a positive integer");
    }
    this.#maximum = maximum;
  }

  async run<T>(operation: () => Promise<T>): Promise<T> {
    if (this.#active >= this.#maximum) {
      await new Promise<void>((resolve) => this.#waiters.push(resolve));
    }
    this.#active += 1;
    try {
      return await operation();
    } finally {
      this.#active -= 1;
      this.#waiters.shift()?.();
    }
  }
}

function matchesGeneration(
  state: AuthoringState,
  workspaceId: string,
  generation: number,
): boolean {
  return state.workspaceId === workspaceId && state.generation === generation;
}
