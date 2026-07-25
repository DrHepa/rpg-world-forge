import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { EventEmitter } from "node:events";
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
import { PassThrough } from "node:stream";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  asarLookupPath,
  enumerateRawAsarEntries,
  parseWindowsSnapshotError,
  SHELL_MANIFEST_PATH,
  ShellPackageError,
  staticFuseFixture,
  targetFixtureLayout,
  verifyPackagedShell as verifyPackagedShellCore,
  withWindowsBackend,
  writeShellPackageManifest as writeShellPackageManifestCore,
} from "../../scripts/shell-package-verifier.mjs";
import { parseShellPackageArguments } from "../../scripts/verify-shell-package.mjs";
import {
  isWithin,
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
const maxBackendReportLineBytes = 16 * 1024 * 1024;
const maxBackendFinalLineBytes = 64;
const maxBackendTrailingBytes = 64;
const verifyPackagedShell = (options) =>
  verifyPackagedShellCore({ ...options, pythonExecutable: testPython });
const writeShellPackageManifest = (options) =>
  writeShellPackageManifestCore({
    ...options,
    pythonExecutable: testPython,
  });

function createHungBackend({
  closeAfterFinal = false,
  closeAfterKillErrors = [],
  finalOutput,
  initialOutput = '{"status":"ready"}\n',
  killErrorSignals = [],
  reapOnKill = true,
} = {}) {
  const child = new EventEmitter();
  child.exitCode = null;
  child.killSignals = [];
  child.killed = false;
  child.signalCode = null;
  child.stderr = new PassThrough();
  child.stdin = new PassThrough();
  child.stdout = new PassThrough();
  const close = (status, signal) => {
    if (child.exitCode !== null || child.signalCode !== null) {
      return;
    }
    child.exitCode = status;
    child.signalCode = signal;
    child.stderr.end();
    child.stdout.end();
    queueMicrotask(() => child.emit("close", status, signal));
  };
  child.kill = (signal = "SIGTERM") => {
    child.killed = true;
    child.killSignals.push(signal);
    if (killErrorSignals.includes(signal)) {
      const error = new Error(`kill ${signal} denied`);
      error.code = "EPERM";
      child.emit("error", error);
      if (closeAfterKillErrors.includes(signal)) {
        queueMicrotask(() => close(null, signal));
      }
      return false;
    }
    if (reapOnKill && child.signalCode === null) {
      close(null, signal);
    }
    return true;
  };
  child.stdin.once("finish", () => {
    if (finalOutput !== undefined) {
      child.stdout.write(finalOutput);
    }
    if (closeAfterFinal) {
      close(0, null);
    }
  });
  child.start = () => {
    if (!child.started) {
      child.started = true;
      queueMicrotask(() => {
        child.emit("spawn");
        if (initialOutput !== undefined) {
          child.stdout.write(initialOutput);
        }
      });
    }
    return child;
  };
  return child;
}

function expectBackendLifecycleDetached(child) {
  for (const event of ["close", "error", "spawn"]) {
    expect(child.listenerCount(event)).toBe(0);
  }
}

function shellPackageFailure(operation) {
  try {
    operation();
  } catch (error) {
    expect(error).toBeInstanceOf(ShellPackageError);
    return error;
  }
  throw new Error("expected shell package verification to fail");
}

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

describe("raw ASAR header enumeration", () => {
  it("builds deterministic slash paths from literal nested keys", () => {
    expect(
      enumerateRawAsarEntries({
        files: {
          "z.txt": { size: 3 },
          assets: {
            files: {
              "two.txt": { size: 2 },
              "one.txt": { size: 1 },
            },
          },
        },
      }),
    ).toEqual({
      directories: ["assets"],
      files: [
        { path: "assets/one.txt", size: 1 },
        { path: "assets/two.txt", size: 2 },
        { path: "z.txt", size: 3 },
      ],
    });
  });

  it("adapts canonical paths only at the host-sensitive lookup boundary", () => {
    const canonical = enumerateRawAsarEntries({
      files: {
        "dist-electron": {
          files: {
            main: {
              files: {
                "index.cjs": { size: 1 },
              },
            },
          },
        },
      },
    }).files[0].path;
    expect(canonical).toBe("dist-electron/main/index.cjs");
    expect(asarLookupPath(canonical, path.posix)).toBe(canonical);

    const windowsLookup = asarLookupPath(canonical, path.win32);
    expect(windowsLookup).toBe("dist-electron\\main\\index.cjs");
    expect(path.win32.dirname(windowsLookup).split(path.win32.sep)).toEqual([
      "dist-electron",
      "main",
    ]);
    expect(path.win32.basename(windowsLookup)).toBe("index.cjs");
  });

  it("rejects literal backslash keys instead of normalizing them", () => {
    const error = shellPackageFailure(() =>
      enumerateRawAsarEntries({
        files: {
          "dist\\renderer": { size: 1 },
        },
      }),
    );
    expect(error.code).toBe("nonportable_package_path");
  });

  it("rejects case-insensitive aliases from literal header keys", () => {
    const error = shellPackageFailure(() =>
      enumerateRawAsarEntries({
        files: {
          Assets: { files: {} },
          assets: { files: {} },
        },
      }),
    );
    expect(error.code).toBe("app_asar_path_alias");
  });

  it("preserves link and unpacked-entry rejection", () => {
    for (const entry of [{ link: "target.txt" }, { size: 1, unpacked: true }]) {
      const error = shellPackageFailure(() =>
        enumerateRawAsarEntries({ files: { entry } }),
      );
      expect(error.code).toBe("app_asar_non_regular_entry");
    }
  });

  it("enforces the existing tree and file budgets", () => {
    let nested = { "payload.bin": { size: 0 } };
    for (let depth = 0; depth < 34; depth += 1) {
      nested = { [`d${String(depth)}`]: { files: nested } };
    }
    expect(
      shellPackageFailure(() =>
        enumerateRawAsarEntries({ files: nested }),
      ).code,
    ).toBe("package_tree_too_deep");

    expect(
      shellPackageFailure(() =>
        enumerateRawAsarEntries({
          files: { "oversized.bin": { size: 1_073_741_825 } },
        }),
      ).code,
    ).toBe("package_file_too_large");

    expect(
      shellPackageFailure(() =>
        enumerateRawAsarEntries({
          files: {
            "one.bin": { size: 1_073_741_824 },
            "two.bin": { size: 1_073_741_824 },
            "three.bin": { size: 1_073_741_824 },
            "four.bin": { size: 1 },
          },
        }),
      ).code,
    ).toBe("package_tree_too_large");

    const tooManyFiles = {};
    for (let index = 0; index < 20_001; index += 1) {
      tooManyFiles[`f${String(index).padStart(5, "0")}`] = { size: 0 };
    }
    expect(
      shellPackageFailure(() =>
        enumerateRawAsarEntries({ files: tooManyFiles }),
      ).code,
    ).toBe("package_tree_too_large");
  });
});

describe("Windows boundary helpers", () => {
  it("treats cross-volume Windows paths as external", () => {
    expect(
      isWithin(
        "D:\\a\\rpg-world-forge",
        "C:\\runner-temp\\shell-package",
        path.win32,
      ),
    ).toBe(false);
    expect(
      isWithin(
        "D:\\A\\RPG-World-Forge",
        "d:\\a\\rpg-world-forge\\nested",
        path.win32,
      ),
    ).toBe(true);
  });

  it("accepts only bounded exact allowlisted snapshot errors", () => {
    expect(
      parseWindowsSnapshotError(
        Buffer.from(
          "Studio shell snapshot failed: packaged_resource_mismatch\r\n",
          "utf8",
        ),
      ),
    ).toBe("packaged_resource_mismatch");
    for (const invalid of [
      Buffer.from("Studio shell snapshot failed: backend_failure\n", "utf8"),
      Buffer.from(
        "Studio shell snapshot failed: invalid_backend_command\n",
        "utf8",
      ),
      Buffer.from(
        "Studio shell snapshot failed: packaged_resource_mismatch\ntrailing\n",
        "utf8",
      ),
      Buffer.from([
        0xef,
        0xbb,
        0xbf,
        ...Buffer.from(
          "Studio shell snapshot failed: packaged_resource_mismatch\n",
          "utf8",
        ),
      ]),
      Buffer.alloc(1025, 0x61),
      Buffer.from([0xff]),
    ]) {
      expect(parseWindowsSnapshotError(invalid)).toBeNull();
    }
  });

  it("maps a missing backend executable without an unhandled rejection", async () => {
    let child;
    const unhandled = [];
    const listener = (reason) => unhandled.push(reason);
    process.on("unhandledRejection", listener);
    try {
      await expect(
        withWindowsBackend(
          {
            outputPath: temporaryRoot,
            pythonExecutable: path.join(
              temporaryRoot,
              "missing-python-backend",
            ),
            sourceRoot: studioRoot,
            targetId: "linux-x64",
          },
          async () => {
            throw new Error("callback must not run");
          },
          {
            spawnBackend: (...arguments_) => {
              child = spawn(...arguments_);
              return child;
            },
            timeoutMs: 50,
            terminationTimeoutMs: 50,
          },
        ),
      ).rejects.toMatchObject({
        code: "windows_python_backend_unavailable",
      });
      await new Promise((resolve) => setImmediate(resolve));
      expect(unhandled).toEqual([]);
      expect(child).toBeDefined();
      expectBackendLifecycleDetached(child);
    } finally {
      process.off("unhandledRejection", listener);
    }
  });

  it("times out and reaps a backend that stops after ready", async () => {
    let callbackRan = false;
    const child = createHungBackend();
    await expect(
      withWindowsBackend(
        {
          outputPath: temporaryRoot,
          pythonExecutable: process.execPath,
          sourceRoot: studioRoot,
          targetId: "linux-x64",
        },
        async () => {
          callbackRan = true;
          return { action: "finalize", result: null };
        },
        {
          parseEvidence: (report) => report,
          spawnBackend: () => child.start(),
          timeoutMs: 20,
          terminationTimeoutMs: 20,
        },
      ),
    ).rejects.toMatchObject({ code: "windows_backend_timeout" });
    expect(callbackRan).toBe(true);
    expect(child.killed).toBe(true);
    expect(child.exitCode !== null || child.signalCode !== null).toBe(true);
  });

  it("escalates to SIGKILL when SIGTERM errors without close", async () => {
    const original = new ShellPackageError("electron_root_layout_mismatch");
    const child = createHungBackend({
      killErrorSignals: ["SIGTERM"],
    });
    await expect(
      withWindowsBackend(
        {
          outputPath: temporaryRoot,
          pythonExecutable: process.execPath,
          sourceRoot: studioRoot,
          targetId: "linux-x64",
        },
        async () => {
          throw original;
        },
        {
          parseEvidence: (report) => report,
          spawnBackend: () => child.start(),
          timeoutMs: 20,
          terminationTimeoutMs: 20,
        },
      ),
    ).rejects.toBe(original);
    expect(child.killSignals).toEqual(["SIGTERM", "SIGKILL"]);
    expect(child.signalCode).toBe("SIGKILL");
    expectBackendLifecycleDetached(child);
  });

  it("bounds cleanup when SIGTERM and SIGKILL both error without close", async () => {
    const original = new ShellPackageError("electron_root_layout_mismatch");
    const child = createHungBackend({
      killErrorSignals: ["SIGTERM", "SIGKILL"],
    });
    const started = Date.now();
    await expect(
      withWindowsBackend(
        {
          outputPath: temporaryRoot,
          pythonExecutable: process.execPath,
          sourceRoot: studioRoot,
          targetId: "linux-x64",
        },
        async () => {
          throw original;
        },
        {
          parseEvidence: (report) => report,
          spawnBackend: () => child.start(),
          timeoutMs: 20,
          terminationTimeoutMs: 20,
        },
      ),
    ).rejects.toBe(original);
    expect(Date.now() - started).toBeLessThan(1000);
    expect(child.killSignals).toEqual(["SIGTERM", "SIGKILL"]);
    expect(child.exitCode).toBeNull();
    expect(child.signalCode).toBeNull();
    expectBackendLifecycleDetached(child);
  });

  it("accepts close as reaping proof after a SIGTERM error", async () => {
    const original = new ShellPackageError("electron_root_layout_mismatch");
    const child = createHungBackend({
      closeAfterKillErrors: ["SIGTERM"],
      killErrorSignals: ["SIGTERM"],
    });
    await expect(
      withWindowsBackend(
        {
          outputPath: temporaryRoot,
          pythonExecutable: process.execPath,
          sourceRoot: studioRoot,
          targetId: "linux-x64",
        },
        async () => {
          throw original;
        },
        {
          parseEvidence: (report) => report,
          spawnBackend: () => child.start(),
          timeoutMs: 20,
          terminationTimeoutMs: 20,
        },
      ),
    ).rejects.toBe(original);
    expect(child.killSignals).toEqual(["SIGTERM"]);
    expect(child.signalCode).toBe("SIGTERM");
    expectBackendLifecycleDetached(child);
  });

  it("rejects an oversized unterminated first line at the CRLF lookahead bound", async () => {
    const child = createHungBackend({
      initialOutput: Buffer.concat([
        Buffer.alloc(maxBackendReportLineBytes, 0x61),
        Buffer.from("\r", "ascii"),
      ]),
    });
    await expect(
      withWindowsBackend(
        {
          outputPath: temporaryRoot,
          pythonExecutable: process.execPath,
          sourceRoot: studioRoot,
          targetId: "linux-x64",
        },
        async () => {
          throw new Error("callback must not run");
        },
        {
          parseEvidence: (report) => report,
          spawnBackend: () => child.start(),
          timeoutMs: 100,
          terminationTimeoutMs: 20,
        },
      ),
    ).rejects.toMatchObject({ code: "windows_backend_invalid" });
    expect(child.killed).toBe(true);
  });

  it("rejects an oversized unterminated final line", async () => {
    const child = createHungBackend({
      finalOutput: Buffer.concat([
        Buffer.alloc(maxBackendFinalLineBytes, 0x20),
        Buffer.from("\r", "ascii"),
      ]),
    });
    await expect(
      withWindowsBackend(
        {
          outputPath: temporaryRoot,
          pythonExecutable: process.execPath,
          sourceRoot: studioRoot,
          targetId: "linux-x64",
        },
        async () => ({ action: "finalize", result: null }),
        {
          parseEvidence: (report) => report,
          spawnBackend: () => child.start(),
          timeoutMs: 100,
          terminationTimeoutMs: 20,
        },
      ),
    ).rejects.toMatchObject({ code: "windows_backend_invalid" });
    expect(child.killed).toBe(true);
  });

  it("rejects oversized trailing whitespace after the final line", async () => {
    const child = createHungBackend({
      closeAfterFinal: true,
      finalOutput: Buffer.concat([
        Buffer.from('{"status":"finalized"}\n', "utf8"),
        Buffer.alloc(maxBackendTrailingBytes + 1, 0x20),
      ]),
    });
    await expect(
      withWindowsBackend(
        {
          outputPath: temporaryRoot,
          pythonExecutable: process.execPath,
          sourceRoot: studioRoot,
          targetId: "linux-x64",
        },
        async () => ({ action: "finalize", result: null }),
        {
          parseEvidence: (report) => report,
          spawnBackend: () => child.start(),
          timeoutMs: 100,
          terminationTimeoutMs: 20,
        },
      ),
    ).rejects.toMatchObject({ code: "windows_backend_invalid" });
  });

  it("rejects a third protocol line", async () => {
    const child = createHungBackend({
      closeAfterFinal: true,
      finalOutput:
        '{"status":"finalized"}\n{"status":"unexpected"}\n',
    });
    await expect(
      withWindowsBackend(
        {
          outputPath: temporaryRoot,
          pythonExecutable: process.execPath,
          sourceRoot: studioRoot,
          targetId: "linux-x64",
        },
        async () => ({ action: "finalize", result: null }),
        {
          parseEvidence: (report) => report,
          spawnBackend: () => child.start(),
          timeoutMs: 100,
          terminationTimeoutMs: 20,
        },
      ),
    ).rejects.toMatchObject({ code: "windows_backend_invalid" });
  });

  it("accepts exact report and acknowledgement byte boundaries", async () => {
    const report = Buffer.from('{"status":"ready"}', "utf8");
    const final = Buffer.from('{"status":"finalized"}', "utf8");
    const child = createHungBackend({
      closeAfterFinal: true,
      finalOutput: Buffer.concat([
        final,
        Buffer.alloc(maxBackendFinalLineBytes - final.length, 0x20),
        Buffer.from("\n", "ascii"),
        Buffer.alloc(maxBackendTrailingBytes, 0x20),
      ]),
      initialOutput: Buffer.concat([
        report,
        Buffer.alloc(maxBackendReportLineBytes - report.length, 0x20),
        Buffer.from("\r\n", "ascii"),
      ]),
    });
    await expect(
      withWindowsBackend(
        {
          outputPath: temporaryRoot,
          pythonExecutable: process.execPath,
          sourceRoot: studioRoot,
          targetId: "linux-x64",
        },
        async () => ({ action: "finalize", result: "bounded" }),
        {
          parseEvidence: (value) => value,
          spawnBackend: () => child.start(),
          timeoutMs: 1000,
          terminationTimeoutMs: 20,
        },
      ),
    ).resolves.toBe("bounded");
  });

  it("preserves a callback error while reaping a hung backend", async () => {
    const original = new ShellPackageError("electron_root_layout_mismatch");
    const child = createHungBackend();
    await expect(
      withWindowsBackend(
        {
          outputPath: temporaryRoot,
          pythonExecutable: process.execPath,
          sourceRoot: studioRoot,
          targetId: "linux-x64",
        },
        async () => {
          throw original;
        },
        {
          parseEvidence: (report) => report,
          spawnBackend: () => child.start(),
          timeoutMs: 20,
          terminationTimeoutMs: 20,
        },
      ),
    ).rejects.toBe(original);
    expect(child.killed).toBe(true);
    expect(child.exitCode !== null || child.signalCode !== null).toBe(true);
  });

  it("preserves a callback error when bounded cleanup cannot observe exit", async () => {
    const original = new ShellPackageError("electron_root_layout_mismatch");
    const child = createHungBackend({ reapOnKill: false });

    await expect(
      withWindowsBackend(
        {
          outputPath: temporaryRoot,
          pythonExecutable: process.execPath,
          sourceRoot: studioRoot,
          targetId: "linux-x64",
        },
        async () => {
          throw original;
        },
        {
          parseEvidence: (report) => report,
          spawnBackend: () => child.start(),
          timeoutMs: 20,
          terminationTimeoutMs: 20,
        },
      ),
    ).rejects.toBe(original);
    expect(child.killSignals).toEqual(["SIGTERM", "SIGKILL"]);
    expect(child.stdout.destroyed).toBe(true);
    for (const event of ["close", "data", "end", "error"]) {
      expect(child.stdout.listenerCount(event)).toBe(0);
    }
  });
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
        expect(isWithin(studioRoot, packageRoot)).toBe(false);
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
