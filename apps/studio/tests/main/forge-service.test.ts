import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it } from "vitest";

import { ForgeServiceSupervisor } from "../../src/main/forge-service";

const fixture = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../fixtures/fake_forge_service.py",
);
const python = findTestPython();
const services: ForgeServiceSupervisor[] = [];

afterEach(async () => {
  await Promise.all(services.splice(0).map(async (service) => service.stop()));
});

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

describe("ForgeServiceSupervisor", () => {
  it("finishes stopped when stop races the startup handshake", async () => {
    const service = new ForgeServiceSupervisor({
      executable: python,
      args: [fixture, "delayed"],
      cwd: path.dirname(fixture),
      env: { LANG: "C.UTF-8" },
    });
    services.push(service);
    const initializing = service.initialize();
    await expect.poll(() => service.status.state).toBe("starting");

    const firstStop = service.stop();
    const secondStop = service.stop();

    expect(secondStop).toBe(firstStop);
    await expect(initializing).rejects.toThrow();
    await firstStop;
    expect(service.status).toEqual({
      state: "stopped",
      message: "Forge Studio service is stopped",
      pid: null,
    });
  });
});
