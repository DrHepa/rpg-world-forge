import { describe, expect, it, vi } from "vitest";

import {
  ApplicationLifecycleCoordinator,
  ApplicationQuitGate,
} from "../../src/main/app-lifecycle";

describe("ApplicationLifecycleCoordinator", () => {
  it("closes acquired resources once in dependency order", async () => {
    const order: string[] = [];
    const lifecycle = new ApplicationLifecycleCoordinator();
    lifecycle.ownForge({
      stop: vi.fn(() => {
        order.push("forge");
        return Promise.resolve();
      }),
    });
    lifecycle.ownCodex({
      stop: vi.fn(() => {
        order.push("codex");
        return Promise.resolve();
      }),
    });
    lifecycle.ownIpc(() => {
      order.push("ipc");
    });

    const firstClose = lifecycle.close();
    const secondClose = lifecycle.close();

    expect(secondClose).toBe(firstClose);
    await expect(firstClose).resolves.toEqual([]);
    expect(order).toEqual(["ipc", "codex", "forge"]);
  });

  it("continues shutdown after failures and handles partial startup", async () => {
    const order: string[] = [];
    const lifecycle = new ApplicationLifecycleCoordinator();
    lifecycle.ownForge({
      stop: vi.fn(() => {
        order.push("forge");
        return Promise.resolve();
      }),
    });
    lifecycle.ownCodex({
      stop: vi.fn(() => {
        order.push("codex");
        return Promise.reject(new Error("codex failed"));
      }),
    });
    lifecycle.ownIpc(() => {
      order.push("ipc");
      throw new Error("ipc failed");
    });

    const failures = await lifecycle.close();

    expect(order).toEqual(["ipc", "codex", "forge"]);
    expect(failures).toHaveLength(2);
    expect(failures[0]).toBeInstanceOf(Error);
    expect(failures.map((failure) => failure.message)).toEqual([
      "ipc failed",
      "codex failed",
    ]);
  });
});

describe("ApplicationQuitGate", () => {
  it("prevents repeated quit attempts until owned cleanup settles", async () => {
    let releaseCodex!: () => void;
    const lifecycle = new ApplicationLifecycleCoordinator();
    lifecycle.ownCodex({
      stop: vi.fn(
        () =>
          new Promise<void>((resolve) => {
            releaseCodex = resolve;
          }),
      ),
    });
    const requestQuit = vi.fn();
    const gate = new ApplicationQuitGate(lifecycle, requestQuit);
    const firstEvent = { preventDefault: vi.fn() };
    const repeatedEvent = { preventDefault: vi.fn() };

    gate.handle(firstEvent);
    gate.handle(repeatedEvent);

    expect(firstEvent.preventDefault).toHaveBeenCalledOnce();
    expect(repeatedEvent.preventDefault).toHaveBeenCalledOnce();
    expect(requestQuit).not.toHaveBeenCalled();

    releaseCodex();
    await lifecycle.close();
    expect(requestQuit).toHaveBeenCalledOnce();

    const finalEvent = { preventDefault: vi.fn() };
    gate.handle(finalEvent);
    expect(finalEvent.preventDefault).not.toHaveBeenCalled();
  });
});
