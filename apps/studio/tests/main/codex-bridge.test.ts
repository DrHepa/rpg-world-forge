import { chmod, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  WorkspaceCodexBridge,
  type CodexSupervisorClient,
} from "../../src/main/codex-bridge";
import type { CodexLaunchSpec } from "../../src/main/codex-config";
import type { CodexEvent } from "../../src/main/codex-supervisor";
import type { ForgeServiceClient } from "../../src/main/forge-service";
import { CODEX_VERSION, type CodexRuntime } from "../../src/main/runtime-manifest";

const roots: string[] = [];

afterEach(async () => {
  await Promise.all(roots.splice(0).map(async (root) => rm(root, { recursive: true, force: true })));
});

class FakeSupervisor implements CodexSupervisorClient {
  public readonly requests: Array<{ method: string; params: Record<string, unknown> }> = [];
  public state = "stopped";
  public pid: number | null = 123;
  public stopped = false;
  readonly #listeners = new Set<(event: CodexEvent) => void>();
  public subscribe(listener: (event: CodexEvent) => void) {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }
  public start(): Promise<unknown> { this.state = "ready"; return Promise.resolve({}); }
  public request(method: string, params: Record<string, unknown>): Promise<unknown> {
    this.requests.push({ method, params });
    if (method === "account/read") return Promise.resolve({ account: null, requiresOpenaiAuth: true });
    if (method === "account/login/start") {
      return Promise.resolve({
        type: "chatgptDeviceCode",
        loginId: "login-1",
        verificationUrl: "https://example.test/device",
        userCode: "ABCD",
      });
    }
    if (method.startsWith("thread/")) return Promise.resolve({ thread: { id: "thread-1" } });
    if (method === "turn/start") return Promise.resolve({ turn: { id: "turn-1", status: "inProgress" } });
    return Promise.resolve({});
  }
  public answerUserInput(): Promise<void> { return Promise.resolve(); }
  public stop(): Promise<void> { this.stopped = true; this.state = "stopped"; return Promise.resolve(); }
}

describe("WorkspaceCodexBridge", () => {
  it("binds only a registered canonical workspace and injects read-only turn boundaries", async () => {
    const fixture = await createFixture();
    const supervisors: FakeSupervisor[] = [];
    const specs: CodexLaunchSpec[] = [];
    const service = forgeService({ workspace_01: fixture.worldOne, workspace_02: fixture.worldTwo });
    const bridge = new WorkspaceCodexBridge({
      service,
      runtime: fixture.runtime,
      codexHome: fixture.codexHome,
      dataDir: fixture.dataDir,
      environment: {},
      createSupervisor: (spec) => {
        specs.push(spec);
        const supervisor = new FakeSupervisor();
        supervisors.push(supervisor);
        return supervisor;
      },
    });

    await bridge.bindWorkspace("workspace_01");
    expect(bridge.status).toMatchObject({ state: "ready", workspaceId: "workspace_01" });
    expect(specs[0]?.cwd).toBe(fixture.worldOne);
    expect(await bridge.readAccount()).toEqual({ account: null, requiresOpenaiAuth: true });
    expect(await bridge.startLogin("device-code")).toMatchObject({ type: "chatgptDeviceCode" });
    await bridge.startThread();
    await bridge.startTurn("thread-1", "Draft a safe changeset");
    expect(supervisors[0]?.requests.at(-1)).toEqual({
      method: "turn/start",
      params: {
        threadId: "thread-1",
        input: [{ type: "text", text: "Draft a safe changeset", text_elements: [] }],
        cwd: fixture.worldOne,
        approvalPolicy: "never",
        sandboxPolicy: { type: "readOnly", networkAccess: false },
      },
    });

    await bridge.bindWorkspace("workspace_02");
    expect(supervisors[0]?.stopped).toBe(true);
    expect(specs[1]?.cwd).toBe(fixture.worldTwo);
  });

  it("rejects unregistered workspaces and malformed renderer identifiers", async () => {
    const fixture = await createFixture();
    const service = forgeService({ workspace_01: fixture.worldOne });
    const bridge = new WorkspaceCodexBridge({
      service,
      runtime: fixture.runtime,
      codexHome: fixture.codexHome,
      dataDir: fixture.dataDir,
      createSupervisor: () => new FakeSupervisor(),
    });
    await expect(bridge.bindWorkspace("missing_01")).rejects.toThrow(/not registered/u);
    await expect(bridge.bindWorkspace("../escape")).rejects.toThrow(/workspace ID/u);
    await bridge.bindWorkspace("workspace_01");
    await expect(bridge.startTurn("../thread", "text")).rejects.toThrow(/thread ID/u);
    await expect(bridge.startTurn("thread-1", "")).rejects.toThrow(/turn text/u);
  });
});

function forgeService(workspaces: Record<string, string>): ForgeServiceClient {
  return {
    status: { state: "ready", message: "ready", pid: 1 },
    subscribe: () => () => undefined,
    initialize: vi.fn(),
    request: vi.fn(),
    getWorkspace: (workspaceId) => {
      const worldRoot = workspaces[workspaceId];
      return worldRoot
        ? Promise.resolve({ workspaceId, worldRoot })
        : Promise.reject(new Error("Workspace is not registered"));
    },
    stop: vi.fn(),
  };
}

async function createFixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), "rwf-codex-bridge-"));
  roots.push(root);
  const worldOne = path.join(root, "world-one");
  const worldTwo = path.join(root, "world-two");
  const dataDir = path.join(root, "service");
  const codexHome = path.join(root, "codex-home");
  const pythonExecutable = path.join(root, "python");
  const codexExecutable = path.join(root, "codex");
  await Promise.all([worldOne, worldTwo, dataDir, codexHome].map(async (directory) => mkdir(directory)));
  await writeFile(pythonExecutable, "fixture", "utf8");
  await writeFile(codexExecutable, "fixture", "utf8");
  await chmod(pythonExecutable, 0o755);
  await chmod(codexExecutable, 0o755);
  const runtime: CodexRuntime = { pythonExecutable, codexExecutable, version: CODEX_VERSION };
  return {
    worldOne,
    worldTwo,
    dataDir,
    codexHome,
    runtime,
  };
}
