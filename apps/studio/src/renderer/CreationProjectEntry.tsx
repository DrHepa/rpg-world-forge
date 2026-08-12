import { useEffect, useRef, useState } from "react";

import {
  CREATION_CONTENT_MODES,
  DEFAULT_CREATION_CONTENT_MODE,
  type CreationContentMode,
} from "../generated/creation-content-modes";
import type {
    StudioClientResult,
    StudioCreationProjectCreateParams,
    StudioCreationWorkspace,
  StudioCreationWorkspaceReplyEnvelope,
} from "../shared/studio-api";

export interface CreationProjectEntryProps {
  onWorkspaceReady: (workspace: StudioCreationWorkspace) => void;
}

type StudioCreationGameParams = Extract<
  StudioCreationProjectCreateParams,
  { projectKind: "game" }
>;
type StudioCreationNarrativeGameParams = Extract<
  StudioCreationGameParams,
  { narrativeRequirement: "optional" | "required" }
>;

const GAMEPLAY_FAMILIES = [
  "action",
  "adventure",
  "educational",
  "narrative",
  "puzzle",
  "rhythm",
  "role_playing",
  "sandbox",
  "simulation",
  "sports",
  "strategy",
] as const;
const NARRATIVE_AUTHORSHIP = [
  "authored",
  "emergent",
  "procedural",
  "player_authored",
  "social",
  "hybrid",
] as const;
const NARRATIVE_TOPOLOGIES = [
  "linear",
  "foldback",
  "branching",
  "branch_and_bottleneck",
  "hub_and_spoke",
  "modular",
  "storylet",
  "loop_reset",
  "episodic",
  "seasonal",
  "open_ended",
] as const;
const PRESENTATION_MODES = ["text", "2d", "2_5d", "3d", "mixed", "vr", "ar"] as const;

export function CreationProjectEntry({ onWorkspaceReady }: CreationProjectEntryProps) {
  const [formOpen, setFormOpen] = useState(false);
  const [pending, setPending] = useState<"register" | "create" | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [projectId, setProjectId] = useState("");
  const [title, setTitle] = useState("");
  const [projectKind, setProjectKind] = useState<"game" | "asset_library" | "universe_library">(
    "universe_library",
  );
  const [gameplayFamily, setGameplayFamily] = useState("");
  const [initialCoreVerb, setInitialCoreVerb] = useState("");
  const [initialCoreLoop, setInitialCoreLoop] = useState("");
  const [worldPresence, setWorldPresence] = useState("none");
  const [narrativeRequirement, setNarrativeRequirement] = useState("none");
  const [narrativeAuthorship, setNarrativeAuthorship] = useState("none");
  const [narrativeTopology, setNarrativeTopology] = useState("none");
  const [presentationMode, setPresentationMode] = useState("");
  const [runtimeSupportIntent, setRuntimeSupportIntent] = useState("authoring_only");
  const [assetContentMode, setAssetContentMode] = useState<CreationContentMode>(
    DEFAULT_CREATION_CONTENT_MODE,
  );
  const registerTrigger = useRef<HTMLButtonElement>(null);
  const createTrigger = useRef<HTMLButtonElement>(null);
  const projectIdInput = useRef<HTMLInputElement>(null);
  const focusAfterPending = useRef<"register" | "create" | null>(null);

  useEffect(() => {
    if (formOpen) projectIdInput.current?.focus();
  }, [formOpen]);

  useEffect(() => {
    if (pending !== null || focusAfterPending.current === null) return;
    const target = focusAfterPending.current;
    focusAfterPending.current = null;
    (target === "register" ? registerTrigger : createTrigger).current?.focus();
  }, [pending]);

  async function registerExisting(): Promise<void> {
    if (pending) return;
    setPending("register");
    setStatus(null);
    setError(null);
    try {
      const reply = await window.forgeStudio.registerCreationProject();
      if (!reply.ok && reply.error.code === "cancelled") {
        setStatus("Selection cancelled. No project was registered.");
        return;
      }
      const workspace = workspaceFromReply(reply, ["creation_workspace.register"]);
      onWorkspaceReady(workspace);
      setStatus("Creation project registered.");
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      focusAfterPending.current = "register";
      setPending(null);
    }
  }

  async function createProject(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (pending) return;
    setPending("create");
    setStatus(null);
    setError(null);
    try {
      const base = {
        projectId,
        title,
        defaultLocale: "en",
        projectVersion: "0.1.0",
      };
      let params: StudioCreationProjectCreateParams;
      if (projectKind === "game") {
        const gameBase = {
          ...base,
          projectKind: "game" as const,
          gameplayFamily: gameplayFamily as StudioCreationGameParams["gameplayFamily"],
          initialCoreVerb,
          initialCoreLoop,
          worldPresence: worldPresence as StudioCreationGameParams["worldPresence"],
          presentationMode: presentationMode as StudioCreationGameParams["presentationMode"],
          runtimeSupportIntent:
            runtimeSupportIntent as StudioCreationGameParams["runtimeSupportIntent"],
          assetContentMode,
        };
        if (narrativeRequirement === "none") {
          params = {
            ...gameBase,
            narrativeRequirement: "none",
            narrativeAuthorship: "none",
            narrativeTopology: "none",
          };
        } else {
          if (narrativeAuthorship === "none" || narrativeTopology === "none") {
            throw new Error("Narrative games require authorship and topology selections");
          }
          params = {
            ...gameBase,
            narrativeRequirement:
              narrativeRequirement as StudioCreationNarrativeGameParams["narrativeRequirement"],
            narrativeAuthorship:
              narrativeAuthorship as StudioCreationNarrativeGameParams["narrativeAuthorship"],
            narrativeTopology:
              narrativeTopology as StudioCreationNarrativeGameParams["narrativeTopology"],
          };
        }
      } else {
        params = { ...base, projectKind };
      }
      const reply = await window.forgeStudio.createCreationProject(params);
      if (!reply.ok && reply.error.code === "cancelled") {
        setStatus("Selection cancelled. No project was created.");
        return;
      }
      const workspace = workspaceFromReply(reply, [
        "creation_workspace.create",
        "creation_workspace.recover",
      ]);
      onWorkspaceReady(workspace);
      setStatus(`${projectKindLabel(projectKind)} registered.`);
      setFormOpen(false);
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      focusAfterPending.current = "create";
      setPending(null);
    }
  }

  return (
    <section className="creation-project-entry" aria-labelledby="creation-entry-heading">
      <h2 id="creation-entry-heading" className="sr-only">Creation projects</h2>
      <div className="creation-entry-actions">
        <button
          ref={registerTrigger}
          type="button"
          className="secondary compact"
          disabled={pending !== null}
          onClick={() => void registerExisting()}
        >
          {pending === "register" ? "Registering…" : "Register existing"}
        </button>
        <button
          ref={createTrigger}
          type="button"
          className="secondary compact"
          disabled={pending !== null}
          aria-expanded={formOpen}
          aria-controls="new-creation-project-form"
          onClick={() => {
            setFormOpen((current) => !current);
            setStatus(null);
            setError(null);
          }}
        >
          New creation project
        </button>
      </div>

      {formOpen ? (
        <form
          id="new-creation-project-form"
          aria-label="New creation project"
          className="creation-project-form"
          onSubmit={(event) => void createProject(event)}
        >
          <fieldset className="creation-kind-options">
            <legend>Project kind</legend>
            <label>
              <input
                type="radio"
                name="project-kind"
                checked={projectKind === "universe_library"}
                onChange={() => setProjectKind("universe_library")}
              />
              Universe library
            </label>
            <label>
              <input
                type="radio"
                name="project-kind"
                checked={projectKind === "game"}
                onChange={() => setProjectKind("game")}
              />
              Game project
            </label>
            <label>
              <input
                type="radio"
                name="project-kind"
                checked={projectKind === "asset_library"}
                onChange={() => setProjectKind("asset_library")}
              />
              Asset library
            </label>
            <small>Each kind emits only its applicable typed authoring seed.</small>
          </fieldset>
          <label htmlFor="creation-project-id">
            Project ID
            <input
              ref={projectIdInput}
              id="creation-project-id"
              value={projectId}
              required
              pattern="[a-z][a-z0-9_-]+"
              maxLength={64}
              autoComplete="off"
              onChange={(event) => setProjectId(event.target.value)}
            />
          </label>
          <label htmlFor="creation-project-title">
            Project title
            <input
              id="creation-project-title"
              value={title}
              required
              maxLength={256}
              autoComplete="off"
              onChange={(event) => setTitle(event.target.value)}
            />
          </label>
          {projectKind === "game" ? (
            <fieldset className="creation-game-profile">
              <legend>Initial game profile</legend>
              <label htmlFor="creation-gameplay-family">
                Gameplay family
                <select
                  id="creation-gameplay-family"
                  value={gameplayFamily}
                  required
                  onChange={(event) => setGameplayFamily(event.target.value)}
                >
                  <option value="" disabled>Select a gameplay family</option>
                  {GAMEPLAY_FAMILIES.map((family) => (
                    <option key={family} value={family}>{family.replaceAll("_", " ")}</option>
                  ))}
                </select>
              </label>
              <label htmlFor="creation-core-verb">
                Initial core verb
                <input
                  id="creation-core-verb"
                  value={initialCoreVerb}
                  required
                  pattern="[a-z][a-z0-9_]+"
                  maxLength={64}
                  autoComplete="off"
                  onChange={(event) => setInitialCoreVerb(event.target.value)}
                />
              </label>
              <label htmlFor="creation-core-loop">
                Initial core loop
                <textarea
                  id="creation-core-loop"
                  value={initialCoreLoop}
                  required
                  maxLength={512}
                  onChange={(event) => setInitialCoreLoop(event.target.value)}
                />
              </label>
              <label htmlFor="creation-world-presence">
                World presence
                <select
                  id="creation-world-presence"
                  value={worldPresence}
                  onChange={(event) => setWorldPresence(event.target.value)}
                >
                  <option value="none">None</option>
                  <option value="abstract">Abstract</option>
                  <option value="symbolic">Symbolic or board-like</option>
                  <option value="diegetic">Diegetic</option>
                </select>
              </label>
              <label htmlFor="creation-narrative-requirement">
                Narrative requirement
                <select
                  id="creation-narrative-requirement"
                  value={narrativeRequirement}
                  onChange={(event) => {
                    const next = event.target.value;
                    setNarrativeRequirement(next);
                    if (next === "none") {
                      setNarrativeAuthorship("none");
                      setNarrativeTopology("none");
                    }
                  }}
                >
                  <option value="none">None</option>
                  <option value="optional">Optional</option>
                  <option value="required">Required</option>
                </select>
              </label>
              {narrativeRequirement !== "none" ? (
                <>
                  <label htmlFor="creation-narrative-authorship">
                    Narrative authorship
                    <select
                      id="creation-narrative-authorship"
                      value={narrativeAuthorship}
                      required
                      onChange={(event) => setNarrativeAuthorship(event.target.value)}
                    >
                      <option value="none" disabled>Select authorship</option>
                      {NARRATIVE_AUTHORSHIP.map((mode) => (
                        <option key={mode} value={mode}>{mode.replaceAll("_", " ")}</option>
                      ))}
                    </select>
                  </label>
                  <label htmlFor="creation-narrative-topology">
                    Narrative topology
                    <select
                      id="creation-narrative-topology"
                      value={narrativeTopology}
                      required
                      onChange={(event) => setNarrativeTopology(event.target.value)}
                    >
                      <option value="none" disabled>Select topology</option>
                      {NARRATIVE_TOPOLOGIES.map((topology) => (
                        <option key={topology} value={topology}>{topology.replaceAll("_", " ")}</option>
                      ))}
                    </select>
                  </label>
                </>
              ) : null}
              <label htmlFor="creation-presentation-mode">
                Presentation mode
                <select
                  id="creation-presentation-mode"
                  value={presentationMode}
                  required
                  onChange={(event) => setPresentationMode(event.target.value)}
                >
                  <option value="" disabled>Select a presentation mode</option>
                  {PRESENTATION_MODES.map((mode) => (
                    <option key={mode} value={mode}>{mode.replace("_", ".")}</option>
                  ))}
                </select>
              </label>
              <label htmlFor="creation-runtime-intent">
                Runtime support intent
                <select
                  id="creation-runtime-intent"
                  value={runtimeSupportIntent}
                  onChange={(event) => setRuntimeSupportIntent(event.target.value)}
                >
                  <option value="authoring_only">Authoring only</option>
                  <option value="compatibility_assessment">Request compatibility assessment</option>
                </select>
              </label>
              <label htmlFor="creation-asset-content-mode">
                Asset content mode
                <select
                  id="creation-asset-content-mode"
                  value={assetContentMode}
                  onChange={(event) => setAssetContentMode(event.target.value as CreationContentMode)}
                >
                  {CREATION_CONTENT_MODES.map((mode) => (
                    <option key={mode} value={mode}>{mode.replaceAll("_", " ")}</option>
                  ))}
                </select>
              </label>
              <small>No adapter is inferred. Compatibility assessment remains unsupported until an adapter is explicitly selected and verified later.</small>
            </fieldset>
          ) : null}
          <div className="actions">
            <button
              type="submit"
              disabled={
                pending !== null ||
                !projectId ||
                !title.trim() ||
                (projectKind === "game" &&
                  (!gameplayFamily || !initialCoreVerb || !initialCoreLoop.trim() || !presentationMode ||
                    (narrativeRequirement !== "none" &&
                      (narrativeAuthorship === "none" || narrativeTopology === "none"))))
              }
            >
              {pending === "create" ? "Creating…" : "Choose target and create"}
            </button>
            <button
              type="button"
              className="secondary"
              onClick={() => {
                setFormOpen(false);
                createTrigger.current?.focus();
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : null}

      <p className="creation-entry-status" role="status" aria-live="polite">
        {status ?? (pending ? "Native selection is in progress." : "")}
      </p>
      {error ? <p className="inline-error" role="alert">{error}</p> : null}
    </section>
  );
}

function workspaceFromReply(
  reply: StudioClientResult<StudioCreationWorkspaceReplyEnvelope>,
  acceptedMethods: readonly string[],
): StudioCreationWorkspace {
  if (!reply.ok) throw new Error(reply.error.message);
  if (reply.value.kind === "error") throw new Error(reply.value.error.message);
  if (!acceptedMethods.includes(reply.value.method) || !isRecord(reply.value.result)) {
    throw new Error("Forge Studio returned an invalid creation workspace response");
  }
  const workspace = reply.value.result.workspace;
  if (
    !isRecord(workspace) ||
    workspace.format !== "world-forge.studio_creation_workspace" ||
    workspace.format_version !== 1 ||
    !["game", "asset_library", "universe_library"].includes(workspace.project_kind as string) ||
    typeof workspace.workspace_id !== "string" ||
    !isRecord(workspace.project)
  ) {
    throw new Error("Forge Studio returned an invalid creation workspace record");
  }
  return workspace as unknown as StudioCreationWorkspace;
}

function projectKindLabel(kind: "game" | "asset_library" | "universe_library"): string {
  if (kind === "game") return "Game project";
  if (kind === "asset_library") return "Asset library";
  return "Universe library";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function describeError(error: unknown): string {
  return error instanceof Error ? error.message : "Creation project operation failed";
}
