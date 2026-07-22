import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it } from "vitest";

import type { CodexLaunchSpec } from "../../src/main/codex-config";
import {
  CodexProtocolError,
  CodexRequestTimeoutError,
  CodexSupervisor,
  type CodexEvent,
} from "../../src/main/codex-supervisor";
import { CODEX_VERSION } from "../../src/main/runtime-manifest";

const fixtures = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../fixtures");
const python = findTestPython();
const supervisors: CodexSupervisor[] = [];

afterEach(async () => {
  await Promise.all(supervisors.splice(0).map(async (supervisor) => supervisor.stop()));
});

function create(mode = "normal", options: ConstructorParameters<typeof CodexSupervisor>[1] = {}) {
  const spec: CodexLaunchSpec = {
    executable: python,
    args: ["app-server", "--stdio", "--strict-config"],
    cwd: fixtures,
    env: {
      CODEX_HOME: fixtures,
      HOME: fixtures,
      USERPROFILE: fixtures,
      RWF_FAKE_CODEX_MODE: mode,
    },
    codexHome: fixtures,
    configPath: path.join(fixtures, "config.toml"),
    workspaceId: "workspace_01",
    expectedVersion: CODEX_VERSION,
  };
  const supervisor = new CodexSupervisor(spec, {
    verifyVersion: () => Promise.resolve(),
    ...options,
  });
  supervisors.push(supervisor);
  return supervisor;
}

function findTestPython(): string {
  const configured = process.env.RWF_STUDIO_TEST_PYTHON;
  if (configured && path.isAbsolute(configured) && existsSync(configured)) return configured;
  if (process.platform !== "win32") {
    for (const candidate of ["/usr/bin/python3", "/usr/local/bin/python3"]) {
      if (existsSync(candidate)) return candidate;
    }
  }
  const command = process.platform === "win32" ? "where.exe" : "command";
  const args = process.platform === "win32" ? ["python.exe"] : ["-v", "python3"];
  const discovered = execFileSync(command, args, { encoding: "utf8" }).split(/\r?\n/u)[0]?.trim();
  if (!discovered || !path.isAbsolute(discovered)) {
    throw new Error("A Python interpreter is required for fake Codex app-server tests");
  }
  return discovered;
}

describe("CodexSupervisor", () => {
  it("performs initialize/initialized and accepts only allowlisted client methods", async () => {
    const supervisor = create();
    const initialized = await supervisor.start();

    expect(initialized).toMatchObject({ userAgent: "codex-cli/0.144.6" });
    expect(supervisor.state).toBe("ready");
    await expect(supervisor.request("account/read", { refreshToken: false })).resolves.toMatchObject({
      requiresOpenaiAuth: true,
    });
    await expect(supervisor.request("command/exec", {})).rejects.toBeInstanceOf(CodexProtocolError);
  });

  it("coalesces sanitized deltas and preserves authoritative turn completion", async () => {
    const supervisor = create("workflow");
    const events: CodexEvent[] = [];
    supervisor.subscribe((event) => events.push(event));
    await supervisor.start();
    await supervisor.request("turn/start", { threadId: "thread-1", input: [] });
    await new Promise((resolve) => setTimeout(resolve, 60));

    const deltas = events.filter(
      (event) => event.type === "notification" && event.method === "item/agentMessage/delta",
    );
    expect(deltas).toHaveLength(1);
    expect(deltas[0]).toMatchObject({ params: { delta: "hello world", cwd: "[redacted]" } });
    expect(events).toContainEqual(
      expect.objectContaining({
        type: "notification",
        method: "turn/completed",
        authoritative: true,
      }),
    );
  });

  it("answers user input only through an opaque pending token", async () => {
    const supervisor = create("user-input");
    let prompt: Extract<CodexEvent, { type: "user-input" }> | undefined;
    supervisor.subscribe((event) => {
      if (event.type === "user-input") prompt = event;
    });
    await supervisor.start();
    await supervisor.request("turn/start", { threadId: "thread-1", input: [] });
    await expect.poll(() => prompt?.token).toBeTruthy();

    await expect(supervisor.answerUserInput("not-a-token", {})).rejects.toThrow();
    await expect(supervisor.answerUserInput(prompt!.token, { wrong: ["North"] })).rejects.toThrow();
    await expect(
      supervisor.answerUserInput(prompt!.token, { choice: ["North"] }),
    ).resolves.toBeUndefined();
    await expect(
      supervisor.answerUserInput(prompt!.token, { choice: ["North"] }),
    ).rejects.toThrow(/no longer pending/u);
  });

  it("deterministically denies every escalation-shaped server request", async () => {
    const supervisor = create("denials");
    const denials: CodexEvent[] = [];
    supervisor.subscribe((event) => {
      if (event.type === "notification" && event.method === "fixture/denial") denials.push(event);
    });
    await supervisor.start();
    await supervisor.request("account/read", { refreshToken: false });
    await expect.poll(() => denials.length).toBe(7);
    expect(JSON.stringify(denials)).toContain("decline");
    expect(JSON.stringify(denials)).toContain("denied");
    expect(JSON.stringify(denials)).toContain('"success":false');
  });

  it("rejects unknown server requests and recycles the child fail-closed", async () => {
    const supervisor = create("unknown-request");
    await supervisor.start();
    await supervisor.request("account/read", { refreshToken: false });
    await expect.poll(() => supervisor.state).toBe("crashed");
  });

  it("fails closed on malformed and oversized lines", async () => {
    const malformed = create("malformed");
    await expect(malformed.start()).rejects.toBeInstanceOf(CodexProtocolError);

    const oversized = create("oversized", { maxLineBytes: 256 });
    await expect(oversized.start()).rejects.toBeInstanceOf(CodexProtocolError);
  });

  it("bounds pending requests and safely ignores a timed-out late reply", async () => {
    const supervisor = create("delayed", { maxPendingRequests: 1 });
    await supervisor.start();
    await expect(
      supervisor.request("account/read", { refreshToken: false }, 100),
    ).rejects.toBeInstanceOf(CodexRequestTimeoutError);
    await new Promise((resolve) => setTimeout(resolve, 350));
    expect(supervisor.state).toBe("ready");
    expect(supervisor.diagnostics.pendingRequests).toBe(0);
  });
});
