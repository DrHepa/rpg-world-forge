import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  StudioCreationChangeset,
  StudioCreationChangesetDiffResult,
  StudioCreationWorkflowResult,
  StudioCreationWorkspace,
} from "../shared/studio-api";
import {
  CREATION_MODULE_GROUPS,
  creationModuleGroupLabel,
  creationModulePreview,
  parseCreationModuleJson,
  resolveCreationModuleReferences,
  validateCreationModuleId,
  validateCreationModuleDocument,
  type CreationModuleDocument,
  type CreationModuleFormat,
  type CreationModuleReference,
} from "./creation-modules";
import {
  creationChangesetNavigationKind,
  reportCreationNavigation,
  requireCreationChangeset,
  requireCreationChangesetDiff,
  requireCreationRecoveryTerminal,
  type RequiredCreationOperation,
} from "./creation-review";
import { CreationServiceError, expectCreationResult } from "./creation-service";
import type { CreationNavigationState } from "./creation-state";

interface LoadedCreationModule {
  reference: CreationModuleReference;
  fileSha256: string;
  document: CreationModuleDocument;
}

interface LoadedModuleCatalog {
  projectId: string;
  modules: LoadedCreationModule[];
}

interface CreationModuleWorkbenchProps {
  workspace: StudioCreationWorkspace;
  workflow: StudioCreationWorkflowResult["workflow"];
  onNavigationStateChange: (state: CreationNavigationState) => void;
  onAuthorityRefresh: () => Promise<void>;
  onApplied: () => Promise<void>;
}

type PendingAction = "load" | "stage" | "approve" | "apply" | "recover" | null;
type EditorMode = "edit" | "create" | "delete" | null;

export function CreationModuleWorkbench({
  workspace,
  workflow,
  onNavigationStateChange,
  onAuthorityRefresh,
  onApplied,
}: CreationModuleWorkbenchProps) {
  const [catalog, setCatalog] = useState<LoadedModuleCatalog | null>(null);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [mode, setMode] = useState<EditorMode>(null);
  const [editorText, setEditorText] = useState("");
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);
  const [createFormat, setCreateFormat] = useState<CreationModuleFormat>("world-forge.logic_module");
  const [createPath, setCreatePath] = useState("source/logic/new-module.json");
  const [editorError, setEditorError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [changeset, setChangeset] = useState<StudioCreationChangeset | null>(null);
  const [diff, setDiff] = useState<StudioCreationChangesetDiffResult["diff"] | null>(null);
  const [pending, setPending] = useState<PendingAction>("load");
  const tokenRef = useRef(0);
  const reviewHeadingRef = useRef<HTMLHeadingElement>(null);

  const selected = catalog?.modules.find((module) => module.reference.projectPath === selectedPath) ?? null;
  const draftDirty = Boolean(
    mode === "create" || mode === "delete" ||
      (mode === "edit" && selected && draft && creationModulePreview(selected.document) !== creationModulePreview(draft)),
  );
  const bufferDirty = mode !== null && editorText.trim().length > 0 && draft === null && mode !== "delete";
  const navigationKind = creationChangesetNavigationKind(changeset?.status) ??
    (bufferDirty ? "facet_buffer" : draftDirty ? "draft" : "clean");

  const load = useCallback(async (): Promise<void> => {
    const token = tokenRef.current + 1;
    tokenRef.current = token;
    setPending("load");
    setActionError(null);
    try {
      const next = await loadModuleCatalog(workspace.workspace_id, workspace.source_revision);
      if (tokenRef.current !== token) return;
      setCatalog(next);
      setSelectedPath((current) =>
        current && next.modules.some((module) => module.reference.projectPath === current)
          ? current
          : null,
      );
    } catch (error) {
      if (tokenRef.current === token) setActionError(describeError(error));
    } finally {
      if (tokenRef.current === token) setPending(null);
    }
  }, [workspace.source_revision, workspace.workspace_id]);

  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (active) void load();
    });
    return () => {
      active = false;
      tokenRef.current += 1;
    };
  }, [load]);

  useEffect(() => {
    reportCreationNavigation(onNavigationStateChange, navigationKind);
  }, [navigationKind, onNavigationStateChange]);

  useEffect(() => {
    if (changeset) reviewHeadingRef.current?.focus();
  }, [changeset]);

  function selectModule(module: LoadedCreationModule): void {
    if (changeset || draftDirty || bufferDirty || pending) return;
    setSelectedPath(module.reference.projectPath);
    setMode(null);
    setDraft(null);
    setEditorText("");
    setEditorError(null);
  }

  function beginEdit(): void {
    if (!selected || changeset || pending) return;
    setMode("edit");
    setDraft(null);
    setEditorText(creationModulePreview(selected.document));
    setEditorError(null);
  }

  function beginCreate(): void {
    if (changeset || pending || draftDirty || bufferDirty) return;
    const seed = {
      format: createFormat,
      format_version: 1,
      module_id: "new_module",
      project_id: catalog?.projectId ?? workspace.project.id,
      title: "New module",
      content_hash: "0".repeat(64),
    };
    setMode("create");
    setDraft(null);
    setEditorText(`${JSON.stringify(seed, null, 2)}\n`);
    setEditorError(null);
  }

  function updateDraft(): void {
    if (!mode || mode === "delete") return;
    try {
      const parsed = parseCreationModuleJson(editorText, "module");
      if (mode === "edit") {
        if (!selected || !catalog) throw new Error("Selected creation module is unavailable");
        setDraft(validateCreationModuleDocument(parsed, selected.reference, catalog.projectId));
      } else {
        validateCreationModuleId(parsed.module_id);
        if (
          parsed.format !== createFormat || parsed.format_version !== 1 ||
          parsed.project_id !== catalog?.projectId ||
          typeof parsed.content_hash !== "string" || !/^[0-9a-f]{64}$/u.test(parsed.content_hash)
        ) {
          throw new Error("New module identity must match the selected format and project");
        }
        setDraft(parsed);
      }
      setEditorError(null);
    } catch (error) {
      setDraft(null);
      setEditorError(describeError(error));
    }
  }

  function cancelDraft(): void {
    if (changeset || pending) return;
    setMode(null);
    setDraft(null);
    setEditorText("");
    setEditorError(null);
  }

  async function stage(): Promise<void> {
    if (!catalog || !mode || changeset || pending) return;
    const operation = moduleOperation();
    if (!operation) return;
    setPending("stage");
    setActionError(null);
    try {
      const stageParams = mode === "delete" && selected
        ? {
            operation: "delete" as const,
            path: selected.reference.projectPath,
            format: selected.reference.format,
            expectedBaseFileSha256: selected.fileSha256,
          }
        : mode === "edit" && selected && draft
          ? {
              operation: "replace" as const,
              path: selected.reference.projectPath,
              format: selected.reference.format,
              expectedBaseFileSha256: selected.fileSha256,
              proposedModule: draft,
            }
          : mode === "create" && draft
            ? {
                operation: "create" as const,
                path: createPath,
                format: createFormat,
                expectedBaseFileSha256: null,
                proposedModule: draft,
              }
            : null;
      if (!stageParams) return;
      const stagedResult = await expectCreationResult(
        window.forgeStudio.stageCreationModuleChange({
          workspaceId: workspace.workspace_id,
          expectedRootGeneration: workspace.root_generation,
          expectedSourceRevision: workspace.source_revision,
          expectedWorkflowStatusHash: workflow.status_hash,
          ...stageParams,
        }),
        "creation_changeset.create",
      );
      const staged = requireCreationChangeset(stagedResult.changeset, {
        workspaceId: workspace.workspace_id,
        requiredOperation: operation,
        expectedRootGeneration: workspace.root_generation,
        expectedSourceRevision: workspace.source_revision,
        expectedWorkflowStatusHash: workflow.status_hash,
      });
      const [recordResult, diffResult] = await Promise.all([
        expectCreationResult(
          window.forgeStudio.getCreationChangeset(staged.changeset_id),
          "creation_changeset.get",
        ),
        expectCreationResult(
          window.forgeStudio.diffCreationChangeset(staged.changeset_id),
          "creation_changeset.diff",
        ),
      ]);
      const record = requireCreationChangeset(recordResult.changeset, {
        workspaceId: workspace.workspace_id,
        requiredOperation: operation,
        status: staged.status,
        immutable: staged,
      });
      setChangeset(record);
      setDiff(requireCreationChangesetDiff(diffResult.diff, record));
      setMode(null);
      setDraft(null);
      setEditorText("");
    } catch (error) {
      setActionError(describeError(error));
      if (error instanceof CreationServiceError && error.code === "conflict") {
        await onAuthorityRefresh();
      }
    } finally {
      setPending(null);
    }
  }

  function moduleOperation(): RequiredCreationOperation | null {
    if (mode === "create" && draft) {
      return { operation: "create", path: createPath, expectedBaseFileSha256: null };
    }
    if (!selected) return null;
    if (mode === "delete") {
      return { operation: "delete", path: selected.reference.projectPath, expectedBaseFileSha256: selected.fileSha256 };
    }
    if (mode === "edit" && draft && draftDirty) {
      return { operation: "replace", path: selected.reference.projectPath, expectedBaseFileSha256: selected.fileSha256 };
    }
    return null;
  }

  function currentRequiredOperation(): RequiredCreationOperation {
    const operation = changeset?.operations.find((candidate) =>
      selectedPath === null || candidate.path === selectedPath || candidate.path === createPath,
    );
    if (!operation) throw new Error("Creation module changeset lost its module operation");
    return {
      operation: operation.operation,
      path: operation.path,
      expectedBaseFileSha256: operation.expected_base_file_sha256,
    };
  }

  async function approve(): Promise<void> {
    if (!changeset || changeset.status !== "staged" || pending) return;
    setPending("approve");
    setActionError(null);
    try {
      const result = await expectCreationResult(
        window.forgeStudio.approveCreationChangeset(changeset.changeset_id, changeset.record_hash, changeset.review_sha256),
        "creation_changeset.approve",
      );
      setChangeset(requireCreationChangeset(result.changeset, {
        workspaceId: workspace.workspace_id,
        requiredOperation: currentRequiredOperation(),
        status: "approved",
        immutable: changeset,
      }));
    } catch (error) {
      setActionError(describeError(error));
      await refreshChangeset();
    } finally {
      setPending(null);
    }
  }

  async function apply(): Promise<void> {
    if (!changeset || changeset.status !== "approved" || pending) return;
    setPending("apply");
    setActionError(null);
    try {
      const result = await expectCreationResult(
        window.forgeStudio.applyCreationChangeset(
          changeset.changeset_id,
          changeset.record_hash,
          changeset.review_sha256,
          workspace.root_generation,
        ),
        "creation_changeset.apply",
      );
      requireCreationChangeset(result.changeset, {
        workspaceId: workspace.workspace_id,
        requiredOperation: currentRequiredOperation(),
        immutable: changeset,
        terminal: true,
      });
      await onApplied();
      setChangeset(null);
      setDiff(null);
    } catch (error) {
      setActionError(describeError(error));
      await refreshChangeset();
      if (error instanceof CreationServiceError && error.code === "conflict") await onAuthorityRefresh();
    } finally {
      setPending(null);
    }
  }

  async function recover(modeValue: "resume" | "rollback"): Promise<void> {
    if (!changeset || (changeset.status !== "applying" && changeset.status !== "recovery_required") || pending) return;
    setPending("recover");
    setActionError(null);
    try {
      const result = await expectCreationResult(
        window.forgeStudio.recoverCreationChangeset(
          changeset.changeset_id,
          modeValue,
          changeset.record_hash,
          changeset.review_sha256,
          workspace.root_generation,
        ),
        "creation_changeset.recover",
      );
      const record = requireCreationChangeset(result.changeset, {
        workspaceId: workspace.workspace_id,
        requiredOperation: currentRequiredOperation(),
        immutable: changeset,
      });
      requireCreationRecoveryTerminal(result.outcome, record.status, modeValue);
      await onApplied();
      setChangeset(null);
      setDiff(null);
    } catch (error) {
      setActionError(describeError(error));
      await refreshChangeset();
    } finally {
      setPending(null);
    }
  }

  async function refreshChangeset(): Promise<void> {
    if (!changeset) return;
    try {
      const result = await expectCreationResult(
        window.forgeStudio.getCreationChangeset(changeset.changeset_id),
        "creation_changeset.get",
      );
      setChangeset(requireCreationChangeset(result.changeset, {
        workspaceId: workspace.workspace_id,
        requiredOperation: currentRequiredOperation(),
        immutable: changeset,
      }));
    } catch {
      // Preserve the last immutable evidence and the original actionable error.
    }
  }

  const grouped = useMemo(() => CREATION_MODULE_GROUPS.map((group) => ({
    ...group,
    modules: catalog?.modules.filter((module) => module.reference.collection === group.collection) ?? [],
  })), [catalog]);

  return (
    <div className="creation-module-workbench">
      <header className="creation-section-heading">
        <div>
          <p className="eyebrow">Manifest-authoritative source</p>
          <h3>Typed modules</h3>
          <p>Edit discriminated world, activity, narrative, system, and logic modules without forcing irrelevant content.</p>
        </div>
        <button type="button" disabled={pending !== null || changeset !== null || draftDirty || bufferDirty} onClick={beginCreate}>
          Add module
        </button>
      </header>

      {pending === "load" ? <p role="status">Loading typed module graph…</p> : null}
      {actionError ? <p role="alert" className="inline-error">{actionError}</p> : null}

      <div className="creation-module-layout">
        <nav aria-label="Typed module collections" className="creation-module-nav">
          {grouped.filter((group) => group.modules.length > 0).map((group) => (
            <section key={group.collection} aria-labelledby={`module-group-${group.collection}`}>
              <h4 id={`module-group-${group.collection}`}>{group.label}</h4>
              <ul>
                {group.modules.map((module) => (
                  <li key={module.reference.projectPath}>
                    <button
                      type="button"
                      aria-current={selectedPath === module.reference.projectPath ? "true" : undefined}
                      onClick={() => selectModule(module)}
                    >
                      <strong>{module.reference.id}</strong>
                      <span>{module.reference.projectPath}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ))}
          {catalog && catalog.modules.length === 0 ? <p>No typed modules are referenced by this manifest.</p> : null}
          {grouped.find((group) => group.collection === "world_modules")?.modules.length === 0 ? (
            <p className="creation-module-absence">No world modules are referenced by this profile-valid manifest.</p>
          ) : null}
          {grouped.find((group) => group.collection === "narrative_modules")?.modules.length === 0 ? (
            <p className="creation-module-absence">No narrative modules are referenced by this profile-valid manifest.</p>
          ) : null}
        </nav>

        <section className="creation-module-detail" aria-live="polite">
          {selected ? (
            <>
              <header>
                <p className="eyebrow">{creationModuleGroupLabel(selected.reference.collection)}</p>
                <h4>{selected.reference.id}</h4>
                <div className="actions">
                  <button type="button" disabled={pending !== null || changeset !== null || mode !== null} onClick={beginEdit}>Edit module</button>
                  <button type="button" disabled={pending !== null || changeset !== null || mode !== null} onClick={() => { setMode("delete"); setDraft(null); setEditorText(""); }}>Prepare deletion</button>
                </div>
              </header>
              <dl className="creation-module-metadata">
                <div><dt>Format</dt><dd><code>{selected.reference.format}</code></dd></div>
                <div><dt>Path</dt><dd><code>{selected.reference.projectPath}</code></dd></div>
                <div><dt>Content hash</dt><dd><code>{selected.reference.contentHash}</code></dd></div>
                <div><dt>File hash</dt><dd><code>{selected.fileSha256}</code></dd></div>
              </dl>
              {mode === null ? <pre className="creation-json-preview">{creationModulePreview(selected.document)}</pre> : null}
            </>
          ) : mode !== "create" ? <p>Select a manifest-referenced module to inspect its typed source.</p> : null}

          {mode === "create" ? (
            <div className="creation-module-create-fields">
              <label>Module type<select value={createFormat} onChange={(event) => setCreateFormat(event.target.value as CreationModuleFormat)}>
                {CREATION_MODULE_GROUPS.map((group) => <option key={group.format} value={group.format}>{group.label}</option>)}
              </select></label>
              <label>Project-relative path<input value={createPath} onChange={(event) => setCreatePath(event.target.value)} /></label>
            </div>
          ) : null}

          {mode === "edit" || mode === "create" ? (
            <div className="creation-module-editor">
              <label htmlFor="creation-module-json">Module JSON</label>
              <textarea id="creation-module-json" rows={20} value={editorText} onChange={(event) => { setEditorText(event.target.value); setDraft(null); setEditorError(null); }} />
              {editorError ? <p role="alert" className="inline-error">{editorError}</p> : null}
              <div className="actions">
                <button type="button" disabled={pending !== null} onClick={updateDraft}>Update draft</button>
                <button type="button" disabled={pending !== null} onClick={cancelDraft}>Discard editor</button>
              </div>
              {draft ? <><h5>Normalized preview</h5><pre className="creation-json-preview">{creationModulePreview(draft)}</pre></> : null}
            </div>
          ) : null}

          {mode === "delete" ? (
            <div className="creation-danger-zone" role="group" aria-label="Module deletion review">
              <p>The module stays intact until the aggregate module, manifest, and project changeset is approved and applied.</p>
              <button type="button" disabled={pending !== null} onClick={() => void stage()}>Stage module deletion</button>
              <button type="button" disabled={pending !== null} onClick={cancelDraft}>Cancel deletion</button>
            </div>
          ) : null}

          {(mode === "edit" || mode === "create") && draft && draftDirty ? (
            <button type="button" disabled={pending !== null} onClick={() => void stage()}>
              {mode === "create" ? "Stage new module" : "Stage module changes"}
            </button>
          ) : null}
        </section>
      </div>

      {changeset ? (
        <section className="creation-review" aria-labelledby="module-review-heading">
          <h4 id="module-review-heading" ref={reviewHeadingRef} tabIndex={-1}>Reviewed module changeset</h4>
          <p><strong>{changeset.status}</strong> · <code>{changeset.changeset_id}</code></p>
          {diff ? (
            <div className="creation-diff">
              <strong>{diff.operations.length} operation{diff.operations.length === 1 ? "" : "s"}</strong>
              <ul>{diff.operations.map((operation) => <li key={`${operation.operation}:${operation.path}`}><code>{operation.path}</code><span>{operation.operation} · {formatDelta(operation.size_delta)} bytes</span></li>)}</ul>
            </div>
          ) : null}
          <div className="actions">
            {changeset.status === "staged" ? <button type="button" disabled={pending !== null} onClick={() => void approve()}>Approve module changeset</button> : null}
            {changeset.status === "approved" ? <button type="button" disabled={pending !== null} onClick={() => void apply()}>Apply approved module changes</button> : null}
            {changeset.status === "applying" || changeset.status === "recovery_required" ? <>
              <button type="button" disabled={pending !== null} onClick={() => void recover("resume")}>Resume module apply</button>
              <button type="button" disabled={pending !== null} onClick={() => void recover("rollback")}>Roll back module apply</button>
            </> : null}
          </div>
        </section>
      ) : null}
    </div>
  );
}

async function loadModuleCatalog(
  workspaceId: string,
  sourceRevision: string,
): Promise<LoadedModuleCatalog> {
  const listed = await expectCreationResult(
    window.forgeStudio.listCreationDocuments(workspaceId, sourceRevision),
    "creation_document.list",
  );
  if (listed.source_revision !== sourceRevision || !Array.isArray(listed.documents)) {
    throw new Error("Forge Studio returned stale creation document authority");
  }
  const summaries = listed.documents.map(requireSummary);
  const projects = summaries.filter((summary) => summary.format === "world-forge.project" && summary.formatVersion === 1);
  if (projects.length !== 1) throw new Error("Creation workspace must expose exactly one generic project document");
  const project = await readDocument(workspaceId, sourceRevision, projects[0]);
  const manifestReference = requireReference(project.document.source_manifest, "project source manifest");
  const manifestSummary = requireReferencedSummary(summaries, manifestReference);
  const manifest = await readDocument(workspaceId, sourceRevision, manifestSummary);
  const references = resolveCreationModuleReferences(manifest.path, manifest.document);
  const modules = await Promise.all(references.map(async (reference) => {
    const summary = summaries.find((candidate) => candidate.path === reference.projectPath);
    if (!summary || summary.format !== reference.format || summary.contentHash !== reference.contentHash || summary.id !== reference.id) {
      throw new Error("Creation module reference is unresolved");
    }
    const loaded = await readDocument(workspaceId, sourceRevision, summary);
    return {
      reference,
      fileSha256: loaded.fileSha256,
      document: validateCreationModuleDocument(loaded.document, reference, String(project.document.project_id)),
    };
  }));
  return { projectId: String(project.document.project_id), modules };
}

interface DocumentSummary {
  path: string;
  format: string;
  formatVersion: number;
  id: string;
  contentHash: string;
  fileSha256: string;
}

function requireSummary(value: unknown): DocumentSummary {
  if (!isRecord(value) || typeof value.path !== "string" || typeof value.format !== "string" ||
    typeof value.id !== "string" || typeof value.content_hash !== "string" || typeof value.file_sha256 !== "string") {
    throw new Error("Forge Studio returned an invalid creation document summary");
  }
  return { path: value.path, format: value.format, formatVersion: Number(value.format_version), id: value.id, contentHash: value.content_hash, fileSha256: value.file_sha256 };
}

async function readDocument(workspaceId: string, sourceRevision: string, summary: DocumentSummary) {
  const result = await expectCreationResult(
    window.forgeStudio.readCreationDocument(workspaceId, sourceRevision, summary.path),
    "creation_document.read",
  );
  if (result.source_revision !== sourceRevision || !isRecord(result.document)) throw new Error("Forge Studio returned stale creation document authority");
  const wire = result.document;
  if (wire.path !== summary.path || wire.format !== summary.format || wire.format_version !== summary.formatVersion || wire.id !== summary.id || wire.content_hash !== summary.contentHash || wire.file_sha256 !== summary.fileSha256 || !isRecord(wire.document)) {
    throw new Error("Forge Studio returned mismatched creation document evidence");
  }
  return { ...summary, document: wire.document };
}

function requireReference(value: unknown, context: string) {
  if (!isRecord(value) || typeof value.path !== "string" || typeof value.format !== "string" || typeof value.id !== "string" || typeof value.content_hash !== "string") {
    throw new Error(`Creation ${context} reference is invalid`);
  }
  return { path: value.path, format: value.format, formatVersion: Number(value.format_version), id: value.id, contentHash: value.content_hash };
}

function requireReferencedSummary(summaries: DocumentSummary[], reference: ReturnType<typeof requireReference>): DocumentSummary {
  const summary = summaries.find((candidate) => candidate.path === reference.path);
  if (!summary || summary.format !== reference.format || summary.formatVersion !== reference.formatVersion || summary.id !== reference.id || summary.contentHash !== reference.contentHash) {
    throw new Error("Creation project reference is unresolved");
  }
  return summary;
}

function formatDelta(value: number): string {
  return value > 0 ? `+${String(value)}` : String(value);
}

function describeError(value: unknown): string {
  return value instanceof Error ? value.message : "Creation module operation failed";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
