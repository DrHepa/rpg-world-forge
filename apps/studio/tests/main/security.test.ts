import path from "node:path";

import { describe, expect, it, vi } from "vitest";

import {
  CONTENT_SECURITY_POLICY,
  installSessionDenials,
  installWebContentsDenials,
  isTrustedStudioSender,
  resolveStudioResource,
} from "../../src/main/security";

describe("desktop security boundary", () => {
  it("accepts IPC only from the expected top-level Studio document", () => {
    const mainFrame = { url: "rwf-studio://app/index.html" };
    const contents = { mainFrame };

    expect(isTrustedStudioSender({ sender: contents, senderFrame: mainFrame }, contents)).toBe(true);
    expect(
      isTrustedStudioSender(
        { sender: contents, senderFrame: { url: "rwf-studio://app/frame.html" } },
        contents,
      ),
    ).toBe(false);
    expect(
      isTrustedStudioSender(
        { sender: contents, senderFrame: { url: "https://attacker.invalid/" } },
        contents,
      ),
    ).toBe(false);
    expect(
      isTrustedStudioSender(
        { sender: contents, senderFrame: { url: "rwf-studio://app:123/index.html" } },
        contents,
      ),
    ).toBe(false);
    expect(
      isTrustedStudioSender({ sender: {}, senderFrame: mainFrame }, contents),
    ).toBe(false);
  });

  it("maps only contained custom-protocol resources", () => {
    const root = path.resolve("/tmp/studio-renderer");

    expect(resolveStudioResource(root, "rwf-studio://app/")).toBe(
      path.join(root, "index.html"),
    );
    expect(resolveStudioResource(root, "rwf-studio://app/assets/main.js")).toBe(
      path.join(root, "assets/main.js"),
    );
    expect(resolveStudioResource(root, "https://app/index.html")).toBeNull();
    expect(resolveStudioResource(root, "rwf-studio://evil/index.html")).toBeNull();
    expect(resolveStudioResource(root, "rwf-studio://app/..%2fsecret")).toBeNull();
    expect(resolveStudioResource(root, "rwf-studio://app/assets%5csecret")).toBeNull();
  });

  it("sets deny-by-default permission, navigation, popup, and webview policies", () => {
    let permissionCheck: (() => boolean) | undefined;
    let permissionRequest: ((...args: unknown[]) => void) | undefined;
    const targetSession = {
      setPermissionCheckHandler: vi.fn((handler: () => boolean) => {
        permissionCheck = handler;
      }),
      setPermissionRequestHandler: vi.fn((handler: (...args: unknown[]) => void) => {
        permissionRequest = handler;
      }),
    };
    installSessionDenials(targetSession as never);
    expect(permissionCheck?.()).toBe(false);
    const callback = vi.fn();
    permissionRequest?.({}, "camera", callback);
    expect(callback).toHaveBeenCalledWith(false);

    const handlers = new Map<string, (event: { preventDefault(): void }) => void>();
    let windowHandler: (() => { action: string }) | undefined;
    const contents = {
      setWindowOpenHandler: vi.fn((handler: () => { action: string }) => {
        windowHandler = handler;
      }),
      on: vi.fn((name: string, handler: (event: { preventDefault(): void }) => void) => {
        handlers.set(name, handler);
      }),
    };
    installWebContentsDenials(contents as never);
    expect(windowHandler?.()).toEqual({ action: "deny" });
    for (const name of ["will-navigate", "will-redirect", "will-attach-webview"]) {
      const preventDefault = vi.fn();
      handlers.get(name)?.({ preventDefault });
      expect(preventDefault).toHaveBeenCalledOnce();
    }
  });

  it("uses a CSP that forbids network connections and inline execution", () => {
    expect(CONTENT_SECURITY_POLICY).toContain("connect-src 'none'");
    expect(CONTENT_SECURITY_POLICY).toContain("script-src 'self'");
    expect(CONTENT_SECURITY_POLICY).not.toContain("'unsafe-inline'");
    expect(CONTENT_SECURITY_POLICY).not.toContain("http:");
  });
});
