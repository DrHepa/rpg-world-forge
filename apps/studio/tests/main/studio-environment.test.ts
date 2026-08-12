import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  resolveStudioEnvironment,
  resolveStudioEnvironmentValue,
} from "../../scripts/studio-environment.mjs";

describe("Studio environment compatibility bridge", () => {
  it("accepts canonical, deprecated, and equal dual values", () => {
    expect(
      resolveStudioEnvironmentValue(
        { WORLD_FORGE_STUDIO_DEV_PYTHON: "/canonical/python" },
        "DEV_PYTHON",
      ),
    ).toBe("/canonical/python");
    expect(
      resolveStudioEnvironmentValue(
        { RWF_STUDIO_DEV_PYTHON: "/legacy/python" },
        "DEV_PYTHON",
      ),
    ).toBe("/legacy/python");
    expect(
      resolveStudioEnvironmentValue(
        {
          WORLD_FORGE_STUDIO_DEV_PYTHON: "/same/python",
          RWF_STUDIO_DEV_PYTHON: "/same/python",
        },
        "DEV_PYTHON",
      ),
    ).toBe("/same/python");
  });

  it("rejects conflicts and unknown names without mutating the source", () => {
    const source = {
      WORLD_FORGE_STUDIO_DEV_CODEX: "/canonical/codex",
      RWF_STUDIO_DEV_CODEX: "/legacy/codex",
      UNRELATED: "keep",
    };
    expect(() => resolveStudioEnvironment(source)).toThrow(
      /conflicting Studio environment variables/u,
    );
    expect(source).toEqual({
      WORLD_FORGE_STUDIO_DEV_CODEX: "/canonical/codex",
      RWF_STUDIO_DEV_CODEX: "/legacy/codex",
      UNRELATED: "keep",
    });
    expect(() =>
      resolveStudioEnvironmentValue(source, "NOT_REGISTERED" as "DEV_CODEX"),
    ).toThrow(/unsupported Studio environment variable/u);
  });

  it("treats empty aliases as unset and returns normalized path values", () => {
    expect(
      resolveStudioEnvironmentValue(
        {
          WORLD_FORGE_STUDIO_DEV_PYTHON: "",
          RWF_STUDIO_DEV_PYTHON: "/legacy/python",
        },
        "DEV_PYTHON",
        { cwd: "/workspace", pathFlavor: path.posix },
      ),
    ).toBe("/legacy/python");
    expect(
      resolveStudioEnvironmentValue(
        {
          WORLD_FORGE_STUDIO_DEV_PYTHON: "",
          RWF_STUDIO_DEV_PYTHON: "  ",
        },
        "DEV_PYTHON",
        { cwd: "/workspace", pathFlavor: path.posix },
      ),
    ).toBeUndefined();
    expect(
      resolveStudioEnvironmentValue(
        { WORLD_FORGE_STUDIO_DEV_PYTHON: "tools/python" },
        "DEV_PYTHON",
        { cwd: "/workspace", pathFlavor: path.posix },
      ),
    ).toBe("/workspace/tools/python");
  });

  it("accepts semantically equal relative and platform-specific aliases", () => {
    expect(
      resolveStudioEnvironmentValue(
        {
          WORLD_FORGE_STUDIO_DEV_CODEX: "/workspace/tools/codex",
          RWF_STUDIO_DEV_CODEX: "tools/./codex",
        },
        "DEV_CODEX",
        { cwd: "/workspace", pathFlavor: path.posix },
      ),
    ).toBe("/workspace/tools/codex");
    expect(
      resolveStudioEnvironmentValue(
        {
          WORLD_FORGE_STUDIO_BUILD_PYTHON: "C:\\Tools\\Python.exe",
          RWF_STUDIO_BUILD_PYTHON: "c:/tools/./python.exe",
        },
        "BUILD_PYTHON",
        { cwd: "C:\\workspace", pathFlavor: path.win32 },
      ),
    ).toBe("C:\\Tools\\Python.exe");
    expect(() =>
      resolveStudioEnvironmentValue(
        {
          WORLD_FORGE_STUDIO_DEV_CODEX: "/workspace/one",
          RWF_STUDIO_DEV_CODEX: "/workspace/two",
        },
        "DEV_CODEX",
        { cwd: "/workspace", pathFlavor: path.posix },
      ),
    ).toThrow(/conflicting Studio environment variables/u);
  });
});
