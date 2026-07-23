import { createHash } from "node:crypto";
import {
  chmod,
  copyFile,
  cp,
  link,
  mkdir,
  mkdtemp,
  readFile,
  rename,
  rm,
  stat,
  symlink,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  SHELL_MANIFEST_PATH,
  ShellPackageError,
  staticFuseFixture,
  targetFixtureLayout,
  verifyPackagedShell as verifyPackagedShellCore,
  writeShellPackageManifest as writeShellPackageManifestCore,
} from "../../scripts/shell-package-verifier.mjs";
import { parseShellPackageArguments } from "../../scripts/verify-shell-package.mjs";
import {
  PackageShellError,
  runShellPackage,
} from "../../scripts/package-shell.mjs";
import { cleanProcessOutput } from "../../scripts/build-processes.mjs";

const require = createRequire(import.meta.url);
const asar = require("@electron/asar");
const testRoot = path.dirname(fileURLToPath(import.meta.url));
const studioRoot = path.resolve(testRoot, "../..");
const canVerifySecurely = ["linux", "win32"].includes(process.platform);
const fixtureFuseReader = async () => staticFuseFixture();
const testPython =
  process.env.RWF_STUDIO_BUILD_PYTHON ??
  process.env.PYTHON ??
  (process.env.pythonLocation
    ? path.join(process.env.pythonLocation, "python.exe")
    : undefined);
const verifyPackagedShell = (options) =>
  verifyPackagedShellCore({ ...options, pythonExecutable: testPython });
const writeShellPackageManifest = (options) =>
  writeShellPackageManifestCore({
    ...options,
    pythonExecutable: testPython,
  });

let temporaryRoot;
const bases = new Map();

async function makeAsar(destination, { extraPath } = {}) {
  const source = await mkdtemp(path.join(temporaryRoot, "asar-source-"));
  await cp(
    path.join(studioRoot, "dist-electron"),
    path.join(source, "dist-electron"),
    { recursive: true },
  );
  await cp(
    path.join(studioRoot, "dist-renderer"),
    path.join(source, "dist-renderer"),
    { recursive: true },
  );
  await writeFile(
    path.join(source, "package.json"),
    `${JSON.stringify(
      {
        dependencies: {
          ajv: "8.20.0",
          react: "19.2.8",
          "react-dom": "19.2.8",
        },
        main: "dist-electron/main/index.cjs",
        name: "@rpg-world-forge/studio",
        private: true,
        type: "module",
        version: "0.1.0",
      },
      null,
      2,
    )}\n`,
  );
  if (extraPath) {
    const extra = path.join(source, ...extraPath.split("/"));
    await mkdir(path.dirname(extra), { recursive: true });
    await writeFile(extra, "unauthorized packaged payload\n");
  }
  try {
    await asar.createPackage(source, destination);
  } finally {
    await rm(source, { force: true, recursive: true });
  }
}

async function createBase(targetId) {
  const root = path.join(temporaryRoot, `base-${targetId}`);
  const layout = targetFixtureLayout(targetId);
  await mkdir(path.join(root, "locales"), { recursive: true });
  await mkdir(path.join(root, "resources/packaging"), { recursive: true });
  for (const relative of layout.rootFiles) {
    await writeFile(path.join(root, relative), `fixture:${relative}\n`);
  }
  await chmod(path.join(root, layout.executable), 0o755);
  for (const relative of layout.locales) {
    await writeFile(path.join(root, relative), "");
  }
  await makeAsar(path.join(root, "resources/app.asar"));
  await copyFile(
    path.join(studioRoot, "resources/runtime-manifest.json"),
    path.join(root, "resources/runtime-manifest.json"),
  );
  for (const filename of [
    "runtime-package-manifest.schema.json",
    "runtime-sources.json",
    "runtime-sources.schema.json",
    "shell-package-manifest.schema.json",
  ]) {
    await copyFile(
      path.join(studioRoot, "packaging", filename),
      path.join(root, "resources/packaging", filename),
    );
  }
  await cp(
    path.join(studioRoot, "protocol/codex-app-server-0.144.6"),
    path.join(root, "resources/protocol/codex-app-server-0.144.6"),
    { recursive: true },
  );
  await writeShellPackageManifest({
    fuseReader: fixtureFuseReader,
    outputPath: root,
    targetId,
  });
  bases.set(targetId, root);
}

async function cloneBase(targetId, label) {
  const destination = path.join(temporaryRoot, `${targetId}-${label}`);
  await cp(bases.get(targetId), destination, { recursive: true });
  return destination;
}

async function cloneCommittedSource(label) {
  const destination = path.join(temporaryRoot, `source-${label}`);
  await cp(
    path.join(studioRoot, "packaging"),
    path.join(destination, "packaging"),
    { recursive: true },
  );
  await cp(
    path.join(studioRoot, "resources"),
    path.join(destination, "resources"),
    { recursive: true },
  );
  await cp(
    path.join(studioRoot, "protocol"),
    path.join(destination, "protocol"),
    { recursive: true },
  );
  await cp(
    path.join(studioRoot, "dist-electron"),
    path.join(destination, "dist-electron"),
    { recursive: true },
  );
  await cp(
    path.join(studioRoot, "dist-renderer"),
    path.join(destination, "dist-renderer"),
    { recursive: true },
  );
  return destination;
}

beforeAll(async () => {
  temporaryRoot = await mkdtemp(path.join(os.tmpdir(), "rwf-shell-verifier-"));
  if (canVerifySecurely) {
    await createBase("linux-x64");
    await createBase("win32-x64");
  }
}, 60_000);

afterAll(async () => {
  if (temporaryRoot) {
    await rm(temporaryRoot, { force: true, recursive: true });
  }
});

describe(
  "Studio packaged shell verifier",
  { timeout: process.platform === "win32" ? 60_000 : 10_000 },
  () => {
  it.skipIf(!canVerifySecurely)(
    "verifies exact Linux and Windows x64 shell-only layouts",
    async () => {
      for (const targetId of ["linux-x64", "win32-x64"]) {
        const packageRoot = bases.get(targetId);
        expect(path.relative(studioRoot, packageRoot).startsWith("..")).toBe(true);
        expect(
          JSON.parse(
            await readFile(
              path.join(packageRoot, ...SHELL_MANIFEST_PATH.split("/")),
              "utf8",
            ),
          ),
        ).toMatchObject({
          package_kind: "shell_only",
          target_id: targetId,
        });
        const result = await verifyPackagedShell({
          fuseReader: fixtureFuseReader,
          outputPath: packageRoot,
          targetId,
        });
        expect(result).toMatchObject({
          package_kind: "shell_only",
          redistribution_status: "blocked",
          release_ready: false,
          target_id: targetId,
        });
        expect(result.verified_files).toBeGreaterThan(900);
      }
    },
  );

  it.skipIf(!canVerifySecurely)(
    "rejects missing, extra, and altered committed resources",
    async () => {
      const missing = await cloneBase("linux-x64", "missing");
      await rm(path.join(missing, "resources/runtime-manifest.json"));
      await expect(
        verifyPackagedShell({
          fuseReader: fixtureFuseReader,
          outputPath: missing,
          targetId: "linux-x64",
        }),
      ).rejects.toMatchObject({ code: "packaged_resource_mismatch" });

      const extra = await cloneBase("linux-x64", "extra");
      await writeFile(path.join(extra, "resources/unexpected.json"), "{}\n");
      await expect(
        verifyPackagedShell({
          fuseReader: fixtureFuseReader,
          outputPath: extra,
          targetId: "linux-x64",
        }),
      ).rejects.toMatchObject({ code: "shell_resource_extra" });

      const altered = await cloneBase("linux-x64", "altered");
      await writeFile(
        path.join(altered, "resources/runtime-manifest.json"),
        '{"format":"altered"}\n',
      );
      await expect(
        verifyPackagedShell({
          fuseReader: fixtureFuseReader,
          outputPath: altered,
          targetId: "linux-x64",
        }),
      ).rejects.toMatchObject({ code: "packaged_resource_mismatch" });
    },
  );

  it.skipIf(!canVerifySecurely)(
    "rejects every ASAR entry outside the pinned clean build inventory",
    async () => {
      const root = await cloneBase("linux-x64", "asar-extra");
      await rm(path.join(root, ...SHELL_MANIFEST_PATH.split("/")));
      const archive = path.join(root, "resources/app.asar");
      await rm(archive);
      await makeAsar(archive, { extraPath: "runtime/python.exe" });
      await expect(
        writeShellPackageManifest({
          fuseReader: fixtureFuseReader,
          outputPath: root,
          targetId: "linux-x64",
        }),
      ).rejects.toMatchObject({ code: "app_asar_inventory_mismatch" });
    },
  );

  it("removes stale process output before an esbuild process build", async () => {
    const root = path.join(temporaryRoot, "stale-process-build");
    const stale = path.join(root, "dist-electron/runtime/python.exe");
    await mkdir(path.dirname(stale), { recursive: true });
    await writeFile(stale, "stale runtime payload\n");
    await cleanProcessOutput(root);
    await expect(stat(path.join(root, "dist-electron"))).rejects.toMatchObject({
      code: "ENOENT",
    });
  });

  it("locks electron-builder to the exact clean shell build files", async () => {
    const packageDocument = JSON.parse(
      await readFile(path.join(studioRoot, "package.json"), "utf8"),
    );
    expect(packageDocument.build.files).toEqual([
      "dist-electron/main/index.cjs",
      "dist-electron/preload/index.cjs",
      "dist-renderer/index.html",
      "dist-renderer/assets/index.css",
      "dist-renderer/assets/index.js",
      "package.json",
      "!node_modules/**/*",
    ]);
    expect(packageDocument.build.directories.output).toBe(
      "${env.RWF_STUDIO_PACKAGE_OUTPUT}",
    );
  });

  it.skipIf(!canVerifySecurely)(
    "denies or detects a same-byte resource replacement before final binding",
    async () => {
      const root = await cloneBase("linux-x64", "replaced");
      const resource = path.join(
        root,
        "resources/packaging/runtime-sources.json",
      );
      const moved = `${resource}.moved`;
      const bytes = await readFile(resource);
      if (process.platform === "win32") {
        let denied = false;
        const result = await verifyPackagedShell({
          beforeFinalBinding: async () => {
            try {
              await rename(resource, moved);
            } catch {
              denied = true;
            }
            expect(denied).toBe(true);
          },
          fuseReader: fixtureFuseReader,
          outputPath: root,
          targetId: "linux-x64",
        });
        expect(result.release_ready).toBe(false);
      } else {
        await expect(
          verifyPackagedShell({
            beforeFinalBinding: async () => {
              await rename(resource, moved);
              await writeFile(resource, bytes);
            },
            fuseReader: fixtureFuseReader,
            outputPath: root,
            targetId: "linux-x64",
          }),
        ).rejects.toMatchObject({ code: "package_entry_replaced" });
      }
    },
  );

  it.skipIf(process.platform !== "linux")(
    "rejects same-size in-place package tampering before final binding",
    async () => {
      const root = await cloneBase("linux-x64", "in-place-tampered");
      const target = path.join(root, "chrome_100_percent.pak");
      const altered = Buffer.from(await readFile(target));
      altered[0] ^= 1;
      await expect(
        verifyPackagedShell({
          beforeFinalBinding: async () => {
            await writeFile(target, altered);
          },
          fuseReader: fixtureFuseReader,
          outputPath: root,
          targetId: "linux-x64",
        }),
      ).rejects.toMatchObject({ code: "package_entry_replaced" });
    },
  );

  it.skipIf(process.platform !== "linux")(
    "retains committed source identities through final binding",
    async () => {
      const root = await cloneBase("linux-x64", "source-replaced");
      const sourceRoot = await cloneCommittedSource("replaced");
      const source = path.join(
        sourceRoot,
        "packaging/runtime-sources.json",
      );
      const moved = `${source}.moved`;
      const bytes = await readFile(source);
      await expect(
        verifyPackagedShell({
          beforeFinalBinding: async () => {
            await rename(source, moved);
            await writeFile(source, bytes);
          },
          fuseReader: fixtureFuseReader,
          outputPath: root,
          sourceRoot,
          targetId: "linux-x64",
        }),
      ).rejects.toMatchObject({ code: "source_resource_changed" });
    },
  );

  it.skipIf(process.platform !== "win32")(
    "retains the native Windows package root against parent replacement",
    async () => {
      const root = await cloneBase("win32-x64", "parent-replaced");
      const moved = `${root}-moved`;
      let denied = false;
      const result = await verifyPackagedShell({
        beforeFinalBinding: async () => {
          try {
            await rename(root, moved);
          } catch {
            denied = true;
          }
          expect(denied).toBe(true);
        },
        fuseReader: fixtureFuseReader,
        outputPath: root,
        targetId: "win32-x64",
      });
      expect(result).toMatchObject({
        package_kind: "shell_only",
        target_id: "win32-x64",
      });
    },
  );

  it.skipIf(!canVerifySecurely)(
    "rejects symlinks and hardlinks before trusting inventory bytes",
    async () => {
      const symbolic = await cloneBase("linux-x64", "symlink");
      await symlink(
        "runtime-manifest.json",
        path.join(symbolic, "resources/alias.json"),
      );
      await expect(
        verifyPackagedShell({
          fuseReader: fixtureFuseReader,
          outputPath: symbolic,
          targetId: "linux-x64",
        }),
      ).rejects.toMatchObject({ code: "package_non_regular_entry" });

      const hard = await cloneBase("linux-x64", "hardlink");
      await link(
        path.join(hard, "resources/runtime-manifest.json"),
        path.join(hard, "resources/alias.json"),
      );
      await expect(
        verifyPackagedShell({
          fuseReader: fixtureFuseReader,
          outputPath: hard,
          targetId: "linux-x64",
        }),
      ).rejects.toMatchObject({ code: "package_non_regular_entry" });
    },
  );

  it.skipIf(!canVerifySecurely)(
    "rejects altered shell evidence and a wrong target",
    async () => {
      const altered = await cloneBase("win32-x64", "manifest-altered");
      const manifestPath = path.join(
        altered,
        ...SHELL_MANIFEST_PATH.split("/"),
      );
      const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
      manifest.release_ready = true;
      await writeFile(manifestPath, `${JSON.stringify(manifest)}\n`);
      await expect(
        verifyPackagedShell({
          fuseReader: fixtureFuseReader,
          outputPath: altered,
          targetId: "win32-x64",
        }),
      ).rejects.toBeInstanceOf(ShellPackageError);

      await expect(
        verifyPackagedShell({
          fuseReader: fixtureFuseReader,
          outputPath: bases.get("win32-x64"),
          targetId: "linux-x64",
        }),
      ).rejects.toMatchObject({ code: "electron_root_layout_mismatch" });
    },
  );

  it("uses exact CLI flags and fails closed without a secure host primitive", async () => {
    expect(() =>
      parseShellPackageArguments([
        "--pa",
        path.join(os.tmpdir(), "package"),
        "--target",
        "linux-x64",
      ]),
    ).toThrowError(
      expect.objectContaining({
        code: "invalid_arguments",
      }),
    );

    if (!canVerifySecurely) {
      await expect(
        verifyPackagedShell({
          fuseReader: fixtureFuseReader,
          outputPath: path.join(os.tmpdir(), "package"),
          targetId: "win32-x64",
        }),
      ).rejects.toMatchObject({ code: "secure_primitive_unavailable" });
    }
  });

  it("rejects unsafe package outputs before spawning and binds the exact external output", async () => {
    const calls = [];
    const runner = async (executable, args, options) => {
      calls.push({ args, executable, options });
      return 0;
    };
    const tools = {
      builderCli: path.join(temporaryRoot, "electron-builder.js"),
      npmCli: path.join(temporaryRoot, "npm-cli.js"),
      pythonExecutable: testPython,
      runner,
    };
    await expect(
      runShellPackage({ ...tools, argv: [] }),
    ).rejects.toBeInstanceOf(PackageShellError);
    await expect(
      runShellPackage({
        ...tools,
        argv: ["--output", "relative", "--target", "linux-x64"],
      }),
    ).rejects.toMatchObject({ code: "invalid_package_output" });

    const inside = path.join(studioRoot, `.unsafe-shell-output-${process.pid}`);
    await expect(
      runShellPackage({
        ...tools,
        argv: ["--output", inside, "--target", "linux-x64"],
      }),
    ).rejects.toMatchObject({ code: "package_output_inside_repository" });
    await expect(stat(inside)).rejects.toMatchObject({ code: "ENOENT" });

    const alias = path.join(temporaryRoot, "studio-alias");
    await symlink(
      studioRoot,
      alias,
      process.platform === "win32" ? "junction" : "dir",
    );
    await expect(
      runShellPackage({
        ...tools,
        argv: [
          "--output",
          path.join(alias, "unsafe-shell-output"),
          "--target",
          "linux-x64",
        ],
      }),
    ).rejects.toMatchObject({ code: "package_output_inside_repository" });
    expect(calls).toHaveLength(0);

    const output = path.join(temporaryRoot, "external-shell-output");
    const result = await runShellPackage({
      ...tools,
      argv: ["--output", output, "--target", "linux-x64"],
    });
    expect(result).toEqual({
      output_path: output,
      package_path: path.join(output, "linux-unpacked"),
      target_id: "linux-x64",
    });
    expect(calls).toHaveLength(3);
    const boundOutput = calls[1].options.env.RWF_STUDIO_PACKAGE_OUTPUT;
    expect(path.isAbsolute(boundOutput)).toBe(true);
    expect(calls[1].args).toEqual([
      tools.builderCli,
      "--dir",
      "--linux",
      "--x64",
      `--config.directories.output=${boundOutput}`,
    ]);
    expect(calls[2].args).toEqual([
      path.join(studioRoot, "scripts/verify-shell-package.mjs"),
      "--path",
      path.join(boundOutput, "linux-unpacked"),
      "--target",
      "linux-x64",
    ]);
    expect((await stat(output)).isDirectory()).toBe(true);

    const racedOutput = path.join(temporaryRoot, "raced-shell-output");
    const movedOutput = `${racedOutput}.moved`;
    let callsForRace = 0;
    const raceRunner = async () => {
      callsForRace += 1;
      if (callsForRace === 1) {
        await symlink(
          studioRoot,
          racedOutput,
          process.platform === "win32" ? "junction" : "dir",
        );
      }
      return 0;
    };
    const raced = runShellPackage({
      ...tools,
      argv: ["--output", racedOutput, "--target", "linux-x64"],
      runner: raceRunner,
    });
    await expect(raced).rejects.toMatchObject({
      code: "package_output_exists",
    });
    expect(callsForRace).toBe(1);
    await expect(stat(movedOutput)).rejects.toMatchObject({ code: "ENOENT" });
  });

  it.skipIf(!canVerifySecurely)(
    "binds the inventory to exact bytes rather than file names alone",
    async () => {
      const root = await cloneBase("linux-x64", "same-size-altered");
      const target = path.join(root, "resources/runtime-manifest.json");
      const bytes = await readFile(target);
      const altered = Buffer.from(bytes);
      altered[0] ^= 1;
      expect(
        createHash("sha256").update(altered).digest("hex"),
      ).not.toBe(createHash("sha256").update(bytes).digest("hex"));
      await writeFile(target, altered);
      await expect(
        verifyPackagedShell({
          fuseReader: fixtureFuseReader,
          outputPath: root,
          targetId: "linux-x64",
        }),
      ).rejects.toMatchObject({ code: "packaged_resource_mismatch" });
    },
  );
  },
);
