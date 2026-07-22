import { chmod, mkdir, mkdtemp, readFile, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { prepareCodexLaunch } from "../../src/main/codex-config";
import { CODEX_VERSION, type CodexRuntime } from "../../src/main/runtime-manifest";

const roots: string[] = [];

afterEach(async () => {
  const { rm } = await import("node:fs/promises");
  await Promise.all(roots.splice(0).map(async (root) => rm(root, { recursive: true, force: true })));
});

describe("Codex workspace configuration", () => {
  it("writes a private, closed Forge-only config and strips inherited injection", async () => {
    const fixture = await makeFixture();
    const launch = await prepareCodexLaunch({
      ...fixture,
      environment: {
        PATH: "/attacker",
        NODE_OPTIONS: "--inspect",
        RUST_LOG: "trace",
        LANG: "C.UTF-8",
      },
    });
    const config = await readFile(launch.configPath, "utf8");

    expect(launch.args).toEqual(["app-server", "--stdio", "--strict-config"]);
    expect(launch.cwd).toBe(fixture.workspaceRoot);
    expect(launch.env).toMatchObject({
      CODEX_HOME: fixture.codexHome,
      HOME: fixture.codexHome,
      USERPROFILE: fixture.codexHome,
      LANG: "C.UTF-8",
    });
    expect(launch.env).not.toHaveProperty("PATH");
    expect(launch.env).not.toHaveProperty("NODE_OPTIONS");
    expect(config).toContain('approval_policy = "never"');
    expect(config).toContain('sandbox_mode = "read-only"');
    expect(config).toContain('web_search = "disabled"');
    expect(config).toContain('inherit = "none"');
    expect(config).toContain('[mcp_servers.forge]');
    expect(config).toContain('"forge_stage_changeset"');
    expect(config).toContain('"forge_get_changeset"');
    expect(config).toContain('"forge_list_changesets"');
    expect(config).toContain('"--workspace-id", "workspace_01"');
    expect(config).not.toContain("apply_changeset");
    expect(config).not.toContain("network_access");
    expect(config).not.toContain("/attacker");
  });

  it("rejects invalid workspace IDs and non-canonical workspace roots", async () => {
    const fixture = await makeFixture();
    await expect(
      prepareCodexLaunch({ ...fixture, workspaceId: "../escape" }),
    ).rejects.toThrow(/workspace ID/u);

    const alias = path.join(fixture.root, "workspace-alias");
    await symlink(fixture.workspaceRoot, alias, "dir");
    await expect(
      prepareCodexLaunch({ ...fixture, workspaceRoot: alias }),
    ).rejects.toThrow(/standalone directory/u);
  });

  it("refuses to replace a linked config", async () => {
    const fixture = await makeFixture();
    const target = path.join(fixture.root, "attacker.toml");
    await writeFile(target, "attacker", "utf8");
    await symlink(target, path.join(fixture.codexHome, "config.toml"));
    await expect(prepareCodexLaunch(fixture)).rejects.toThrow(/identity is unsafe/u);
    expect(await readFile(target, "utf8")).toBe("attacker");
  });
});

async function makeFixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), "rwf-codex-config-"));
  roots.push(root);
  const workspaceRoot = path.join(root, "workspace");
  const dataDir = path.join(root, "service");
  const codexHome = path.join(root, "codex-home");
  const pythonExecutable = path.join(root, "python");
  const codexExecutable = path.join(root, "codex");
  await mkdir(workspaceRoot);
  await mkdir(dataDir);
  await mkdir(codexHome);
  await writeFile(pythonExecutable, "fixture", "utf8");
  await writeFile(codexExecutable, "fixture", "utf8");
  await chmod(pythonExecutable, 0o755);
  await chmod(codexExecutable, 0o755);
  const runtime: CodexRuntime = { pythonExecutable, codexExecutable, version: CODEX_VERSION };
  return {
    root,
    runtime,
    codexHome,
    dataDir,
    workspaceId: "workspace_01",
    workspaceRoot,
  };
}
