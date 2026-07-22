import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it } from "vitest";

import {
  NdjsonSupervisor,
  StudioProtocolError,
  StudioOverloadError,
  StudioRequestCancelledError,
  StudioRequestTimeoutError,
  StudioTransportError,
} from "../../src/main/ndjson-supervisor";

const fixture = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../fixtures/fake_forge_service.py",
);
const python = findTestPython();

const supervisors: NdjsonSupervisor[] = [];
const temporaryRoots: string[] = [];

afterEach(async () => {
  await Promise.all(supervisors.splice(0).map(async (supervisor) => supervisor.stop()));
  await Promise.all(
    temporaryRoots.splice(0).map(async (root) => rm(root, { recursive: true, force: true })),
  );
});

function create(mode: string, options: ConstructorParameters<typeof NdjsonSupervisor>[1] = {}) {
  const supervisor = new NdjsonSupervisor(
    {
      executable: python,
      args: [fixture, mode],
      cwd: path.dirname(fixture),
      env: { LANG: "C.UTF-8" },
    },
    options,
  );
  supervisors.push(supervisor);
  return supervisor;
}

function findTestPython(): string {
  const configured = process.env.RWF_STUDIO_TEST_PYTHON;
  if (configured && path.isAbsolute(configured) && existsSync(configured)) {
    return configured;
  }
  if (process.platform !== "win32") {
    for (const candidate of ["/usr/bin/python3", "/usr/local/bin/python3"]) {
      if (existsSync(candidate)) {
        return candidate;
      }
    }
  }
  const command = process.platform === "win32" ? "where.exe" : "command";
  const args = process.platform === "win32" ? ["python.exe"] : ["-v", "python3"];
  const discovered = execFileSync(command, args, { encoding: "utf8" }).split(/\r?\n/u)[0]?.trim();
  if (!discovered || !path.isAbsolute(discovered)) {
    throw new Error("A Python interpreter is required for the fake Forge service tests");
  }
  return discovered;
}

describe("NdjsonSupervisor", () => {
  it("correlates a response split across stdout chunks", async () => {
    const supervisor = create("split");
    await supervisor.start();

    const response = await supervisor.request("split-1", "service.initialize", {}, 2_000);

    expect(response.kind).toBe("response");
    expect(response.request_id).toBe("split-1");
  });

  it("forwards valid service event envelopes", async () => {
    const supervisor = create("event");
    const events: string[] = [];
    supervisor.subscribe((event) => {
      if (event.type === "event") {
        events.push(String(event.envelope.event.type));
      }
    });
    await supervisor.start();

    await supervisor.request("event-1", "service.initialize", {}, 2_000);

    expect(events).toEqual(["fixture.ready"]);
  });

  it("fails closed on malformed service output", async () => {
    const supervisor = create("malformed");
    await supervisor.start();

    await expect(
      supervisor.request("malformed-1", "service.initialize", {}, 2_000),
    ).rejects.toBeInstanceOf(StudioProtocolError);
  });

  it("fails closed on oversized service output", async () => {
    const supervisor = create("oversized", { maxLineBytes: 128 });
    await supervisor.start();

    await expect(
      supervisor.request("oversized-1", "service.initialize", {}, 2_000),
    ).rejects.toBeInstanceOf(StudioProtocolError);
  });

  it("times out a request and safely ignores its late reply", async () => {
    const supervisor = create("delayed");
    await supervisor.start();

    await expect(
      supervisor.request("timeout-1", "service.initialize", {}, 100),
    ).rejects.toBeInstanceOf(StudioRequestTimeoutError);
    await new Promise((resolve) => setTimeout(resolve, 500));
    expect(supervisor.state).toBe("running");
  });

  it("cancels a pending request without exposing a wire-level fake cancellation", async () => {
    const supervisor = create("delayed");
    await supervisor.start();
    const pending = supervisor.request("cancel-1", "service.initialize", {}, 2_000);

    expect(supervisor.cancelRequest("cancel-1")).toBe(true);
    expect(supervisor.cancelRequest("cancel-1")).toBe(false);
    await expect(pending).rejects.toBeInstanceOf(StudioRequestCancelledError);
  });

  it("rejects pending work when the child crashes", async () => {
    const supervisor = create("crash");
    await supervisor.start();

    await expect(
      supervisor.request("crash-1", "service.initialize", {}, 2_000),
    ).rejects.toBeInstanceOf(StudioTransportError);
    await expect.poll(() => supervisor.state).toBe("crashed");
  });

  it("bounds retained stderr", async () => {
    const supervisor = create("stderr", { maxStderrBytes: 64 });
    await supervisor.start();

    await supervisor.request("stderr-1", "service.initialize", {}, 2_000);

    expect(Buffer.byteLength(supervisor.stderrTail)).toBeLessThanOrEqual(64);
    expect(supervisor.stderrTail).toBe("e".repeat(64));
  });

  it("rejects deterministic pending-request overload", async () => {
    const supervisor = create("delayed", { maxPendingRequests: 1 });
    await supervisor.start();
    const first = supervisor.request("pending-1", "service.initialize", {}, 2_000);

    await expect(
      supervisor.request("pending-2", "service.initialize", {}, 2_000),
    ).rejects.toBeInstanceOf(StudioOverloadError);
    expect(supervisor.diagnostics.pendingRequests).toBe(1);
    expect(supervisor.cancelRequest("pending-1")).toBe(true);
    await expect(first).rejects.toBeInstanceOf(StudioRequestCancelledError);
  });

  it("bounds total queued and outstanding request bytes", async () => {
    const supervisor = create("delayed", { maxOutstandingRequestBytes: 1_024 });
    await supervisor.start();
    const params = { blob: "x".repeat(600) };
    const first = supervisor.request("bytes-1", "service.initialize", params, 2_000);

    await expect(
      supervisor.request("bytes-2", "service.initialize", params, 2_000),
    ).rejects.toBeInstanceOf(StudioOverloadError);
    expect(supervisor.diagnostics.outstandingRequestBytes).toBeGreaterThan(600);
    expect(supervisor.cancelRequest("bytes-1")).toBe(true);
    await expect(first).rejects.toBeInstanceOf(StudioRequestCancelledError);
    expect(supervisor.diagnostics.outstandingRequestBytes).toBe(0);
  });

  it("serializes large writes and waits for stdin backpressure to drain", async () => {
    const supervisor = create("backpressure", {
      maxPendingRequests: 4,
      maxOutstandingRequestBytes: 2 * 1024 * 1024,
    });
    await supervisor.start();
    const params = { blob: "x".repeat(300_000) };

    const replies = await Promise.all([
      supervisor.request("pressure-1", "service.initialize", params, 3_000),
      supervisor.request("pressure-2", "service.initialize", params, 3_000),
      supervisor.request("pressure-3", "service.initialize", params, 3_000),
    ]);

    expect(replies.map((reply) => reply.request_id)).toEqual([
      "pressure-1",
      "pressure-2",
      "pressure-3",
    ]);
    expect(supervisor.diagnostics.backpressureWaits).toBeGreaterThan(0);
    expect(supervisor.diagnostics.pendingRequests).toBe(0);
    expect(supervisor.diagnostics.outstandingRequestBytes).toBe(0);
  });

  it("removes never-written churn and fails an abandoned permanently backpressured write", async () => {
    const supervisor = create("stalled", {
      maxPendingRequests: 4,
      maxOutstandingRequestBytes: 2 * 1024 * 1024,
      maxIgnoredRequestIds: 4,
    });
    await supervisor.start();
    const active = supervisor.request(
      "stalled-active",
      "service.initialize",
      { blob: "x".repeat(300_000) },
      3_000,
    );
    await expect.poll(() => supervisor.diagnostics.backpressureWaits).toBeGreaterThan(0);

    for (let index = 0; index < 6; index += 1) {
      const requestId = `queued-${String(index)}`;
      const queued = supervisor.request(requestId, "service.initialize", {}, 100);
      if (index % 2 === 0) {
        expect(supervisor.cancelRequest(requestId)).toBe(true);
        await expect(queued).rejects.toBeInstanceOf(StudioRequestCancelledError);
      } else {
        await expect(queued).rejects.toBeInstanceOf(StudioRequestTimeoutError);
      }
      expect(supervisor.diagnostics).toMatchObject({
        pendingRequests: 1,
        queuedWrites: 1,
        ignoredReplyIds: 0,
      });
      expect(supervisor.diagnostics.outstandingRequestBytes).toBeLessThan(400_000);
    }

    expect(supervisor.cancelRequest("stalled-active")).toBe(true);
    await expect(active).rejects.toBeInstanceOf(StudioRequestCancelledError);
    expect(supervisor.diagnostics.ignoredReplyIds).toBeLessThanOrEqual(1);
    await expect.poll(() => supervisor.pid).toBeNull();
    expect(supervisor.state).toBe("crashed");
    expect(supervisor.diagnostics).toMatchObject({
      pendingRequests: 0,
      outstandingRequestBytes: 0,
      queuedWrites: 0,
      ignoredReplyIds: 0,
    });
  });

  it("recycles the transport instead of evicting sent-request reply tombstones", async () => {
    const supervisor = create("silent", { maxIgnoredRequestIds: 2 });
    await supervisor.start();

    for (let index = 0; index < 3; index += 1) {
      const requestId = `ignored-${String(index)}`;
      const pending = supervisor.request(requestId, "service.initialize", {}, 2_000);
      await expect.poll(() => supervisor.diagnostics.queuedWrites).toBe(0);
      expect(supervisor.cancelRequest(requestId)).toBe(true);
      await expect(pending).rejects.toBeInstanceOf(StudioRequestCancelledError);
      expect(supervisor.diagnostics.ignoredReplyIds).toBeLessThanOrEqual(2);
    }

    expect(supervisor.state).toBe("crashed");
    await expect.poll(() => supervisor.pid).toBeNull();
    expect(supervisor.diagnostics.ignoredReplyIds).toBe(0);
  });

  it("clears failed spawn state so the same supervisor can restart", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "rwf-spawn-recovery-"));
    temporaryRoots.push(root);
    const missingCwd = path.join(root, "appears-later");
    const supervisor = new NdjsonSupervisor({
      executable: python,
      args: [fixture, "normal"],
      cwd: missingCwd,
      env: { LANG: "C.UTF-8" },
    });
    supervisors.push(supervisor);

    await expect(supervisor.start()).rejects.toThrow();
    expect(supervisor.state).toBe("crashed");
    expect(supervisor.pid).toBeNull();

    await mkdir(missingCwd);
    await supervisor.start();
    const response = await supervisor.request(
      "recovered-1",
      "service.initialize",
      {},
      2_000,
    );
    expect(response.request_id).toBe("recovered-1");
  });

  it("rejects relative executables instead of consulting PATH", () => {
    expect(
      () =>
        new NdjsonSupervisor({
          executable: "python",
          args: [],
          env: {},
        }),
    ).toThrow(/absolute path/u);
  });
});
