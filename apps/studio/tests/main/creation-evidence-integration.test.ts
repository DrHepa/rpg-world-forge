// @vitest-environment jsdom

import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { resolveStudioEnvironmentValue } from "../../scripts/studio-environment.mjs";
import { ForgeServiceSupervisor } from "../../src/main/forge-service";
import { registerStudioIpc } from "../../src/main/ipc";
import { createStudioApi } from "../../src/preload/api";
import { CreationWorkspace } from "../../src/renderer/CreationWorkspace";
import {
  IPC_CHANNELS,
  type StudioCreationWorkspace,
} from "../../src/shared/studio-api";

const repoRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../..",
);
const python = findTestPython();
const services: ForgeServiceSupervisor[] = [];
const temporaryRoots: string[] = [];

afterEach(async () => {
  cleanup();
  Reflect.deleteProperty(window, "forgeStudio");
  vi.restoreAllMocks();
  await Promise.all(services.splice(0).map(async (service) => service.stop()));
  await Promise.all(
    temporaryRoots.splice(0).map(async (root) => rm(root, { recursive: true, force: true })),
  );
});

describe("production Studio v4 evidence boundary", () => {
  it(
    "crosses Python, NDJSON/Ajv, IPC, preload, and renderer without service mocks",
    { timeout: 30_000 },
    async () => {
      const temporary = await mkdtemp(path.join(tmpdir(), "world-forge-studio-v4-"));
      temporaryRoots.push(temporary);
      const service = new ForgeServiceSupervisor({
        executable: python,
        args: [
          "-m",
          "worldforge.studio",
          "--data-dir",
          path.join(temporary, "studio-data"),
        ],
        cwd: repoRoot,
        env: realServiceEnvironment(),
      });
      services.push(service);
      await service.initialize();

      const grantReply = await service.request(
        "integration-grant",
        "creation_root_grant.create",
        {
          grant_id: "grant_integration_project",
          role: "new_target",
          display_name: "Integration project",
          path: path.join(temporary, "project"),
          expected_project_hash: null,
        },
        10_000,
        3,
      );
      expect(grantReply.kind).toBe("response");
      if (grantReply.kind !== "response") throw new Error(grantReply.error.message);
      const grant = (grantReply.result as {
        grant: { grant_id: string; generation: number };
      }).grant;

      const workspaceReply = await service.request(
        "integration-workspace",
        "creation_workspace.create",
        {
          workspace_id: "workspace_integration_project",
          grant_id: grant.grant_id,
          expected_grant_generation: grant.generation,
          project_kind: "universe_library",
          project_id: "integration_project",
          title: "Integration project",
          default_locale: "en",
          project_version: "0.1.0",
        },
        10_000,
        3,
      );
      expect(workspaceReply.kind).toBe("response");
      if (workspaceReply.kind !== "response") throw new Error(workspaceReply.error.message);
      const workspace = (workspaceReply.result as {
        workspace: StudioCreationWorkspace;
      }).workspace;

      const harness = registerRealServiceIpc(service);
      try {
        const api = createStudioApi(harness);
        Object.defineProperty(window, "forgeStudio", {
          configurable: true,
          value: api,
        });
        const inspected = await api.inspectCreationEvidence({
          workspaceId: workspace.workspace_id,
          expectedRootGeneration: workspace.root_generation,
          expectedSourceRevision: workspace.source_revision,
          expectedWorkflowStatusHash: workspace.workflow_status_hash,
          expectedArtifactSnapshotHash: null,
        });
        expect(inspected).toMatchObject({
          ok: true,
          value: {
            protocol_version: 4,
            kind: "response",
            method: "creation_evidence.inspect",
            result: {
              authority: {
                workspace_id: workspace.workspace_id,
                root_generation: workspace.root_generation,
                source_revision: workspace.source_revision,
                workflow_status_hash: workspace.workflow_status_hash,
              },
              evidence: {
                format: "world-forge.studio_creation_evidence",
                format_version: 1,
              },
            },
          },
        });
        const envelope = (inspected as {
          value: {
            result: {
              artifact_snapshot_hash: string;
              evidence: { artifact_counts: { active: number } };
            };
          };
        }).value;
        const listed = await api.listCreationArtifacts({
          workspaceId: workspace.workspace_id,
          expectedRootGeneration: workspace.root_generation,
          expectedSourceRevision: workspace.source_revision,
          expectedWorkflowStatusHash: workspace.workflow_status_hash,
          expectedArtifactSnapshotHash: envelope.result.artifact_snapshot_hash,
          lifecycle: "active",
          cursor: null,
          limit: 64,
        });
        expect(listed).toMatchObject({
          ok: true,
          value: {
            protocol_version: 4,
            kind: "response",
            method: "creation_artifact.list",
            result: {
              artifact_snapshot_hash: envelope.result.artifact_snapshot_hash,
              next_cursor: null,
            },
          },
        });
        const serialized = JSON.stringify({ inspected, listed });
        expect(serialized).not.toContain(temporary);
        expect(envelope.result.evidence.artifact_counts.active).toBeGreaterThan(0);

        render(
          createElement(CreationWorkspace, {
            workspaceId: workspace.workspace_id,
            generation: workspace.root_generation,
            onNavigationStateChange: vi.fn(),
          }),
        );
        expect(
          await screen.findByRole("heading", {
            name: "Integration project creation profile",
          }),
        ).toBeInTheDocument();

        fireEvent.click(screen.getByRole("tab", { name: "Assets" }));
        expect(
          await screen.findByRole("heading", { name: "Asset evidence" }),
        ).toBeInTheDocument();
        await waitFor(() => {
          expect(screen.queryByText("Loading creation evidence…")).not.toBeInTheDocument();
        });
        expect(document.body.textContent).not.toContain(temporary);

        fireEvent.click(screen.getByRole("tab", { name: "Compatibility" }));
        expect(
          await screen.findByRole("heading", { name: "Runtime compatibility evidence" }),
        ).toBeInTheDocument();
        expect(document.body.textContent).not.toContain(temporary);

        const rendererInspect = harness.invocations
          .filter((invocation) => invocation.channel === IPC_CHANNELS.inspectCreationEvidence)
          .at(-1);
        const rendererLists = harness.invocations
          .filter((invocation) => invocation.channel === IPC_CHANNELS.listCreationArtifacts)
          .slice(-2);
        expect(rendererInspect?.arguments).toEqual([
          {
            workspaceId: workspace.workspace_id,
            expectedRootGeneration: workspace.root_generation,
            expectedSourceRevision: workspace.source_revision,
            expectedWorkflowStatusHash: workspace.workflow_status_hash,
            expectedArtifactSnapshotHash: null,
          },
        ]);
        expect(rendererLists.map((invocation) => invocation.arguments)).toEqual([
          [
            {
              workspaceId: workspace.workspace_id,
              expectedRootGeneration: workspace.root_generation,
              expectedSourceRevision: workspace.source_revision,
              expectedWorkflowStatusHash: workspace.workflow_status_hash,
              expectedArtifactSnapshotHash: envelope.result.artifact_snapshot_hash,
              lifecycle: "active",
              cursor: null,
              limit: 64,
            },
          ],
          [
            {
              workspaceId: workspace.workspace_id,
              expectedRootGeneration: workspace.root_generation,
              expectedSourceRevision: workspace.source_revision,
              expectedWorkflowStatusHash: workspace.workflow_status_hash,
              expectedArtifactSnapshotHash: envelope.result.artifact_snapshot_hash,
              lifecycle: "candidate",
              cursor: null,
              limit: 64,
            },
          ],
        ]);
      } finally {
        harness.dispose();
      }
    },
  );
});

function registerRealServiceIpc(service: ForgeServiceSupervisor) {
  const handlers = new Map<string, (event: unknown, ...args: unknown[]) => unknown>();
  const invocations: Array<{ channel: string; arguments: unknown[] }> = [];
  const ipcMain = {
    handle: (channel: string, handler: (event: unknown, ...args: unknown[]) => unknown) => {
      handlers.set(channel, handler);
    },
    removeHandler: (channel: string) => handlers.delete(channel),
  };
  const mainFrame = { url: "rwf-studio://app/index.html" };
  const webContents = {
    mainFrame,
    isDestroyed: () => false,
    send: vi.fn(),
  };
  const window = { webContents, isDestroyed: () => false };
  const codex = {
    status: { state: "unbound", message: "unbound", pid: null, workspaceId: null },
    subscribe: () => () => undefined,
  };
  const dialogs = {
    showOpenDialog: () => Promise.resolve({ canceled: true, filePaths: [] }),
    showSaveDialog: () => Promise.resolve({ canceled: true, filePath: undefined }),
  };
  const dispose = registerStudioIpc(
    ipcMain as never,
    window as never,
    service,
    codex as never,
    dialogs as never,
  );
  return {
    dispose,
    invocations,
    async invoke(channel: string, ...args: unknown[]): Promise<unknown> {
      const handler = handlers.get(channel);
      if (!handler) throw new Error(`Missing production IPC handler for ${channel}`);
      invocations.push({ channel, arguments: args });
      return await handler({ sender: webContents, senderFrame: mainFrame }, ...args);
    },
    on: () => undefined,
    removeListener: () => undefined,
  };
}

function realServiceEnvironment(): Record<string, string> {
  const result: Record<string, string> = {
    PYTHONPATH: path.join(repoRoot, "src"),
    PYTHONUTF8: "1",
  };
  for (const name of ["LANG", "SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP"]) {
    const value = process.env[name];
    if (value) result[name] = value;
  }
  return result;
}

function findTestPython(): string {
  const configured = resolveStudioEnvironmentValue(process.env, "TEST_PYTHON");
  if (configured && path.isAbsolute(configured) && existsSync(configured)) {
    return configured;
  }
  if (process.platform !== "win32") {
    for (const candidate of ["/usr/bin/python3", "/usr/local/bin/python3"]) {
      if (existsSync(candidate)) return candidate;
    }
  }
  const command = process.platform === "win32" ? "where.exe" : "command";
  const args = process.platform === "win32" ? ["python.exe"] : ["-v", "python3"];
  const discovered = execFileSync(command, args, { encoding: "utf8" })
    .split(/\r?\n/u)[0]
    ?.trim();
  if (!discovered || !path.isAbsolute(discovered)) {
    throw new Error("A Python interpreter is required for Studio integration tests");
  }
  return discovered;
}
