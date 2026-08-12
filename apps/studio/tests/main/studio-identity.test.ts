import { readFile } from "node:fs/promises";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  LEGACY_STUDIO_APP_ID,
  LEGACY_STUDIO_PROTOCOL,
  LEGACY_STUDIO_USER_DATA_DIRECTORY,
  STUDIO_EXECUTABLE_NAME,
  STUDIO_PRODUCT_NAME,
  legacyStudioUserDataPath,
  pinLegacyStudioUserDataRoot,
} from "../../src/main/studio-identity";

describe("Studio identity bridge", () => {
  it("uses new visible identity while pinning legacy application storage identities", async () => {
    const packageDocument = JSON.parse(
      await readFile(path.resolve(import.meta.dirname, "../../package.json"), "utf8"),
    ) as {
      name: string;
      build: {
        appId: string;
        productName: string;
        linux: { executableName: string };
      };
    };

    expect(packageDocument.name).toBe("@world-forge/studio");
    expect(packageDocument.build).toMatchObject({
      appId: LEGACY_STUDIO_APP_ID,
      productName: STUDIO_PRODUCT_NAME,
      linux: { executableName: STUDIO_EXECUTABLE_NAME },
    });
    expect(LEGACY_STUDIO_PROTOCOL).toBe("rwf-studio");
    expect(LEGACY_STUDIO_USER_DATA_DIRECTORY).toBe("RPG World Forge Studio");
    expect(legacyStudioUserDataPath("/app-data")).toBe(
      path.join("/app-data", "RPG World Forge Studio"),
    );
  });

  it("pins the legacy user-data root before startup without copying or merging data", () => {
    const calls: Array<[string, string?]> = [];
    pinLegacyStudioUserDataRoot({
      getPath(name) {
        calls.push(["get", name]);
        return "/app-data";
      },
      setPath(name, value) {
        calls.push([name, value]);
      },
    });
    expect(calls).toEqual([
      ["get", "appData"],
      ["userData", path.join("/app-data", "RPG World Forge Studio")],
    ]);
  });

  it("validates environment before protocol registration and every startup mutation", async () => {
    const source = await readFile(
      path.resolve(import.meta.dirname, "../../src/main/index.ts"),
      "utf8",
    );
    const pin = source.indexOf("pinLegacyStudioUserDataRoot(app);");
    const environment = source.indexOf("resolveStudioEnvironment(process.env);");
    const protocol = source.indexOf("protocol.registerSchemesAsPrivileged([");
    const sandbox = source.indexOf("app.enableSandbox();");
    const lock = source.indexOf("app.requestSingleInstanceLock()");
    expect(environment).toBeGreaterThan(-1);
    expect(protocol).toBeGreaterThan(-1);
    expect(environment).toBeLessThan(protocol);
    expect(environment).toBeLessThan(pin);
    expect(pin).toBeGreaterThan(-1);
    expect(pin).toBeLessThan(sandbox);
    expect(sandbox).toBeLessThan(lock);
  });
});
