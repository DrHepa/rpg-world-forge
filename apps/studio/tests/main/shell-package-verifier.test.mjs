import { createHash } from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import { EventEmitter } from "node:events";
import {
  chmod,
  copyFile,
  cp,
  link,
  mkdir,
  mkdtemp,
  readFile,
  realpath,
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
import {
  parseShellPackageArguments,
  verifyRetainedAsarContracts,
} from "../../scripts/verify-shell-package.mjs";
import {
  isWithin,
  PackageShellError,
  runShellPackage,
} from "../../scripts/package-shell.mjs";
import { verifyGenericAssetRuntimeSnapshot } from "../../scripts/verify-generic-asset-runtime.mjs";
import { cleanProcessOutput } from "../../scripts/build-processes.mjs";
import { resolveStudioEnvironmentValue } from "../../scripts/studio-environment.mjs";

const require = createRequire(import.meta.url);
const asar = require("@electron/asar");
const testRoot = path.dirname(fileURLToPath(import.meta.url));
const studioRoot = path.resolve(testRoot, "../..");
const repositoryRoot = path.resolve(studioRoot, "../..");
const canVerifySecurely = ["linux", "win32"].includes(process.platform);
const fixtureFuseReader = async () => staticFuseFixture();
const testPython =
  resolveStudioEnvironmentValue(process.env, "BUILD_PYTHON") ??
  process.env.PYTHON ??
  (process.env.pythonLocation
    ? path.join(process.env.pythonLocation, "python.exe")
    : undefined);
const maxBackendReportLineBytes = 16 * 1024 * 1024;
const maxBackendFinalLineBytes = 64;
const maxBackendTrailingBytes = 64;
const maxBuilderStdoutBytes = 64 * 1024;
const verifyPackagedShell = (options) =>
  verifyPackagedShellCore({ ...options, pythonExecutable: testPython });
const writeShellPackageManifest = (options) =>
  writeShellPackageManifestCore({
    ...options,
    pythonExecutable: testPython,
  });
const verifyRetainedGenericAssetRuntime = ({ bytes, sha256, size }) =>
  verifyGenericAssetRuntimeSnapshot({
    artifactBytes: bytes,
    expectedSha256: sha256,
    expectedSize: size,
  });

function electronBuilderEbusyOutput(
  boundPath,
  {
    lineEnding = "\n",
    operation = "rename",
    reportedBoundPath = boundPath,
    stackBoundPath = reportedBoundPath,
  } = {},
) {
  const reportedTemporary = path.join(
    reportedBoundPath,
    "win-unpacked.tmp",
  );
  const reportedFinal = path.join(reportedBoundPath, "win-unpacked");
  const stackTemporary = path.join(stackBoundPath, "win-unpacked.tmp");
  const stackFinal = path.join(stackBoundPath, "win-unpacked");
  return Buffer.from(
    `  ⨯ EBUSY: resource busy or locked, ${operation} '${reportedTemporary}' -> '${reportedFinal}'  failedTask=build stackTrace=Error: EBUSY: resource busy or locked, ${operation} '${stackTemporary}' -> '${stackFinal}'${lineEnding}`,
    "utf8",
  );
}

function createPackageRetryHarness({
  builderResults,
  finalizerError,
  manifestError,
  pythonExecutable = testPython,
  verifierStatus = 0,
}) {
  const calls = [];
  const delayCalls = [];
  const events = [];
  const manifestCalls = [];
  const reservations = [];
  let builderIndex = 0;
  const reservationFactory = async (outputPath) => {
    const record = {
      boundPath: outputPath,
      closeCount: 0,
      finalizeCount: 0,
      outputPath,
    };
    reservations.push(record);
    events.push({ outputPath, type: "reserve" });
    return {
      boundPath: record.boundPath,
      close: async () => {
        record.closeCount += 1;
        events.push({ outputPath, type: "close" });
      },
      finalize: async () => {
        record.finalizeCount += 1;
        events.push({ outputPath, type: "finalize" });
        if (finalizerError) {
          throw finalizerError;
        }
      },
    };
  };
  const runner = async (executable, args, options) => {
    const call = { args, executable, options };
    calls.push(call);
    if (args[1] === "--dir") {
      const result = builderResults[builderIndex];
      builderIndex += 1;
      const resolved = typeof result === "function"
        ? result(options.env.RWF_STUDIO_PACKAGE_OUTPUT)
        : result;
      events.push({ type: "builder-exit" });
      return resolved;
    }
    if (
      args[0] ===
      path.join(studioRoot, "scripts/verify-shell-package.mjs")
    ) {
      events.push({ type: "verify" });
      return verifierStatus;
    }
    return 0;
  };
  const manifestWriter = async (options) => {
    manifestCalls.push(options);
    events.push({ type: "manifest" });
    if (manifestError) {
      throw manifestError;
    }
  };
  const delay = async (milliseconds) => {
    delayCalls.push(milliseconds);
    events.push({ milliseconds, type: "delay" });
  };
  return {
    calls,
    delay,
    delayCalls,
    events,
    manifestCalls,
    reservations,
    tools: {
      builderCli: path.join(temporaryRoot, "electron-builder.js"),
      delay,
      manifestWriter,
      npmCli: path.join(temporaryRoot, "npm-cli.js"),
      pythonExecutable,
      reservationFactory,
      runner,
    },
  };
}

function createHungBackend({
  closeAfterFinal = false,
  closeAfterKillErrors = [],
  exitAfterStart = false,
  exitStatus = 1,
  finalExitStatus = 0,
  finalOutput,
  initialOutput = '{"status":"ready"}\n',
  killErrorSignals = [],
  reapOnKill = true,
  stderrOutput,
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
      close(finalExitStatus, null);
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
        if (stderrOutput !== undefined) {
          child.stderr.write(stderrOutput);
        }
        if (exitAfterStart) {
          close(exitStatus, null);
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

async function packageShellFailure(operation) {
  try {
    await operation();
  } catch (error) {
    expect(error).toBeInstanceOf(PackageShellError);
    return error;
  }
  throw new Error("expected Studio shell packaging to fail");
}

async function shellBackendFailure(operation) {
  try {
    await operation();
  } catch (error) {
    expect(error).toBeInstanceOf(ShellPackageError);
    return error;
  }
  throw new Error("expected Studio shell snapshot backend to fail");
}

let temporaryRoot;
let originalTemporaryEnvironment;
const temporaryEnvironmentKeys = ["TEMP", "TMP", "TMPDIR"];
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
        name: "@world-forge/studio",
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
  originalTemporaryEnvironment = Object.fromEntries(
    temporaryEnvironmentKeys.map((key) => [
      key,
      {
        present: Object.hasOwn(process.env, key),
        value: process.env[key],
      },
    ]),
  );
  for (const key of temporaryEnvironmentKeys) {
    process.env[key] = temporaryRoot;
  }
  if (canVerifySecurely) {
    await createBase("linux-x64");
    await createBase("win32-x64");
  }
}, 60_000);

afterAll(async () => {
  if (originalTemporaryEnvironment) {
    for (const key of temporaryEnvironmentKeys) {
      const original = originalTemporaryEnvironment[key];
      if (original.present) {
        process.env[key] = original.value;
      } else {
        delete process.env[key];
      }
    }
  }
  if (temporaryRoot) {
    await rm(temporaryRoot, { force: true, recursive: true });
  }
});

describe("suite temporary root hygiene", () => {
  it("contains Node and backend snapshot roots under the suite root", async () => {
    expect(os.tmpdir()).toBe(temporaryRoot);

    let backendArguments;
    let backendOptions;
    const child = createHungBackend({
      exitAfterStart: true,
      initialOutput: Buffer.alloc(0),
    });
    await expect(
      withWindowsBackend(
        {
          outputPath: temporaryRoot,
          pythonExecutable: process.execPath,
          sourceRoot: studioRoot,
          targetId: "win32-x64",
        },
        async () => {
          throw new Error("callback must not run");
        },
        {
          spawnBackend: (_executable, arguments_, options) => {
            backendArguments = arguments_;
            backendOptions = options;
            return child.start();
          },
          timeoutMs: 100,
          terminationTimeoutMs: 20,
        },
      ),
    ).rejects.toMatchObject({ code: "windows_backend_ready_failed" });

    const snapshotFlag = backendArguments.indexOf("--snapshot-dir");
    expect(snapshotFlag).toBeGreaterThanOrEqual(0);
    const snapshotRoot = backendArguments[snapshotFlag + 1];
    expect(path.dirname(snapshotRoot)).toBe(temporaryRoot);
    expect(isWithin(temporaryRoot, snapshotRoot)).toBe(true);
    expect(backendOptions.env).toMatchObject({
      TEMP: temporaryRoot,
      TMP: temporaryRoot,
    });
  });
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
  it.each([
    "windows_snapshot_package_sharing_conflict",
    "windows_snapshot_source_sharing_conflict",
  ])("retries one exact %s on the same publication identity", async (code) => {
    const backendCalls = [];
    const events = [];
    const verificationCalls = [];
    const result = await writeShellPackageManifestCore(
      {
        fuseReader: fixtureFuseReader,
        outputPath: path.join(temporaryRoot, "retained-publication-retry"),
        pythonExecutable: testPython ?? process.execPath,
        sourceRoot: studioRoot,
        targetId: "win32-x64",
      },
      {
        buildWindowsPublicationCommand: async () => {
          events.push("publish");
          return {
            action: "publish",
            payload: "e30K",
            result: null,
          };
        },
        hostPlatform: "win32",
        verifyPackage: async (options) => {
          events.push("verification");
          verificationCalls.push(options);
          return { status: "verified" };
        },
        windowsBackend: async (options, callback) => {
          backendCalls.push(options);
          events.push(`publication-${backendCalls.length}`);
          if (backendCalls.length === 1) {
            throw new ShellPackageError(code);
          }
          const command = await callback({ evidence: {} });
          expect(command).toMatchObject({ action: "publish" });
        },
      },
    );

    expect(result).toEqual({ status: "verified" });
    expect(backendCalls).toHaveLength(2);
    expect(backendCalls[1]).toBe(backendCalls[0]);
    expect(backendCalls[0]).toEqual({
      outputPath: path.join(
        temporaryRoot,
        "retained-publication-retry",
      ),
      pythonExecutable: testPython ?? process.execPath,
      sourceRoot: studioRoot,
      targetId: "win32-x64",
    });
    expect(verificationCalls).toHaveLength(1);
    expect(events).toEqual([
      "publication-1",
      "publication-2",
      "publish",
      "verification",
    ]);
  });

  it.each([
    "windows_snapshot_package_sharing_conflict",
    "windows_snapshot_source_sharing_conflict",
  ])("stops after a second exact %s", async (code) => {
    const failures = [
      new ShellPackageError(code),
      new ShellPackageError(code),
    ];
    let publicationCalls = 0;
    let verificationCalls = 0;
    await expect(
      writeShellPackageManifestCore(
        {
          outputPath: path.join(
            temporaryRoot,
            "retained-publication-exhausted",
          ),
          pythonExecutable: testPython ?? process.execPath,
          sourceRoot: studioRoot,
          targetId: "win32-x64",
        },
        {
          buildWindowsPublicationCommand: async () => {
            throw new Error("publication callback must not run");
          },
          hostPlatform: "win32",
          verifyPackage: async () => {
            verificationCalls += 1;
          },
          windowsBackend: async () => {
            const failure = failures[publicationCalls];
            publicationCalls += 1;
            throw failure;
          },
        },
      ),
    ).rejects.toBe(failures[1]);
    expect(publicationCalls).toBe(2);
    expect(verificationCalls).toBe(0);
  });

  it.each([
    [
      "duck typed package sharing",
      Object.assign(new Error("private duck failure"), {
        code: "windows_snapshot_package_sharing_conflict",
      }),
    ],
    [
      "duck typed source sharing",
      Object.assign(new Error("private duck failure"), {
        code: "windows_snapshot_source_sharing_conflict",
      }),
    ],
    [
      "legacy common sharing",
      new ShellPackageError("windows_snapshot_sharing_conflict"),
    ],
    [
      "setup sharing",
      new ShellPackageError("windows_snapshot_setup_sharing_conflict"),
    ],
    [
      "setup failure",
      new ShellPackageError("windows_snapshot_setup_failed"),
    ],
    [
      "timeout",
      new ShellPackageError("windows_backend_timeout"),
    ],
    [
      "protocol",
      new ShellPackageError("windows_backend_invalid"),
    ],
    [
      "publication",
      new ShellPackageError("shell_manifest_publish_failed"),
    ],
    [
      "finalization",
      new ShellPackageError("windows_snapshot_finalize_failed"),
    ],
    [
      "cleanup",
      new ShellPackageError("windows_snapshot_cleanup_failed"),
    ],
  ])("does not retry a %s publication failure", async (_label, failure) => {
    let publicationCalls = 0;
    let verificationCalls = 0;
    await expect(
      writeShellPackageManifestCore(
        {
          outputPath: path.join(
            temporaryRoot,
            `publication-no-retry-${_label.replaceAll(" ", "-")}`,
          ),
          pythonExecutable: testPython ?? process.execPath,
          sourceRoot: studioRoot,
          targetId: "win32-x64",
        },
        {
          hostPlatform: "win32",
          verifyPackage: async () => {
            verificationCalls += 1;
          },
          windowsBackend: async () => {
            publicationCalls += 1;
            throw failure;
          },
        },
      ),
    ).rejects.toBe(failure);
    expect(publicationCalls).toBe(1);
    expect(verificationCalls).toBe(0);
  });

  it.each([
    "windows_snapshot_package_sharing_conflict",
    "windows_snapshot_source_sharing_conflict",
  ])("does not retry %s thrown by the publication callback", async (code) => {
    const failure = new ShellPackageError(code);
    let publicationCalls = 0;
    let callbackCalls = 0;
    await expect(
      writeShellPackageManifestCore(
        {
          outputPath: path.join(
            temporaryRoot,
            "publication-callback-no-retry",
          ),
          pythonExecutable: testPython ?? process.execPath,
          sourceRoot: studioRoot,
          targetId: "win32-x64",
        },
        {
          buildWindowsPublicationCommand: async () => {
            callbackCalls += 1;
            throw failure;
          },
          hostPlatform: "win32",
          verifyPackage: async () => {
            throw new Error("verification must not run");
          },
          windowsBackend: async (_options, callback) => {
            publicationCalls += 1;
            await callback({ evidence: {} });
          },
        },
      ),
    ).rejects.toBe(failure);
    expect(publicationCalls).toBe(1);
    expect(callbackCalls).toBe(1);
  });

  it("fails closed when the retry observes any existing manifest", async () => {
    let publicationCalls = 0;
    let verificationCalls = 0;
    await expect(
      writeShellPackageManifestCore(
        {
          outputPath: path.join(
            temporaryRoot,
            "publication-existing-manifest",
          ),
          pythonExecutable: testPython ?? process.execPath,
          sourceRoot: studioRoot,
          targetId: "win32-x64",
        },
        {
          hostPlatform: "win32",
          verifyPackage: async () => {
            verificationCalls += 1;
          },
          windowsBackend: async (_options, callback) => {
            publicationCalls += 1;
            if (publicationCalls === 1) {
              throw new ShellPackageError(
                "windows_snapshot_package_sharing_conflict",
              );
            }
            await callback({
              evidence: {
                tree: {
                  files: new Map([[SHELL_MANIFEST_PATH, {}]]),
                },
              },
            });
          },
        },
      ),
    ).rejects.toMatchObject({
      code: "shell_manifest_already_exists",
    });
    expect(publicationCalls).toBe(2);
    expect(verificationCalls).toBe(0);
  });

  it.each([
    "windows_snapshot_package_sharing_conflict",
    "windows_snapshot_source_sharing_conflict",
  ])("never retries %s from the verification backend", async (code) => {
    const failure = new ShellPackageError(code);
    let publicationCalls = 0;
    let verificationCalls = 0;
    await expect(
      writeShellPackageManifestCore(
        {
          outputPath: path.join(
            temporaryRoot,
            "verification-sharing-no-retry",
          ),
          pythonExecutable: testPython ?? process.execPath,
          sourceRoot: studioRoot,
          targetId: "win32-x64",
        },
        {
          buildWindowsPublicationCommand: async () => ({
            action: "publish",
            payload: "e30K",
            result: null,
          }),
          hostPlatform: "win32",
          verifyPackage: async () => {
            verificationCalls += 1;
            throw failure;
          },
          windowsBackend: async (_options, callback) => {
            publicationCalls += 1;
            await callback({ evidence: {} });
          },
        },
      ),
    ).rejects.toBe(failure);
    expect(publicationCalls).toBe(1);
    expect(verificationCalls).toBe(1);
  });

  it("keeps the successful Windows path to one publication and one verification", async () => {
    const events = [];
    await expect(
      writeShellPackageManifestCore(
        {
          outputPath: path.join(
            temporaryRoot,
            "publication-first-success",
          ),
          pythonExecutable: testPython ?? process.execPath,
          sourceRoot: studioRoot,
          targetId: "win32-x64",
        },
        {
          buildWindowsPublicationCommand: async () => {
            events.push("publish");
            return {
              action: "publish",
              payload: "e30K",
              result: null,
            };
          },
          hostPlatform: "win32",
          verifyPackage: async () => {
            events.push("verification");
            return { status: "verified" };
          },
          windowsBackend: async (_options, callback) => {
            events.push("publication");
            await callback({ evidence: {} });
          },
        },
      ),
    ).resolves.toEqual({ status: "verified" });
    expect(events).toEqual([
      "publication",
      "publish",
      "verification",
    ]);
  });

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
    for (const code of [
      "packaged_resource_mismatch",
      "windows_snapshot_cleanup_failed",
      "windows_snapshot_finalize_failed",
      "windows_snapshot_package_failed",
      "windows_snapshot_package_sharing_conflict",
      "windows_snapshot_setup_failed",
      "windows_snapshot_setup_sharing_conflict",
      "windows_snapshot_source_failed",
      "windows_snapshot_source_sharing_conflict",
    ]) {
      expect(
        parseWindowsSnapshotError(
          Buffer.from(
            `Studio shell snapshot failed: ${code}\r\n`,
            "utf8",
          ),
        ),
      ).toBe(code);
    }
    for (const invalid of [
      Buffer.from("Studio shell snapshot failed: backend_failure\n", "utf8"),
      Buffer.from(
        "Studio shell snapshot failed: invalid_backend_command\n",
        "utf8",
      ),
      Buffer.from(
        "Studio shell snapshot failed: windows_snapshot_sharing_conflict\n",
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

  it.each([
    [
      "shell_manifest_publish_failed",
      "shell_manifest_publish_failed",
    ],
    [
      "windows_snapshot_setup_sharing_conflict",
      "windows_snapshot_setup_sharing_conflict",
    ],
    [
      "windows_snapshot_package_sharing_conflict",
      "windows_snapshot_package_sharing_conflict",
    ],
    [
      "windows_snapshot_source_sharing_conflict",
      "windows_snapshot_source_sharing_conflict",
    ],
    ["private_backend_path", "windows_backend_ready_failed"],
  ])(
    "maps backend %s evidence to redacted %s",
    async (backendCode, expectedCode) => {
      let callbackRan = false;
      const child = createHungBackend({
        exitAfterStart: true,
        initialOutput: Buffer.alloc(0),
        stderrOutput: Buffer.from(
          `Studio shell snapshot failed: ${backendCode}\n`,
          "utf8",
        ),
      });
      const observed = await shellBackendFailure(() =>
        withWindowsBackend(
          {
            outputPath: temporaryRoot,
            pythonExecutable: process.execPath,
            sourceRoot: studioRoot,
            targetId: "win32-x64",
          },
          async () => {
            callbackRan = true;
            return { action: "finalize", result: null };
          },
          {
            parseEvidence: (report) => report,
            spawnBackend: () => child.start(),
            timeoutMs: 100,
            terminationTimeoutMs: 20,
          },
        ),
      );

      expect(callbackRan).toBe(false);
      expect(observed).toMatchObject({
        code: expectedCode,
        message: expectedCode,
        name: "ShellPackageError",
      });
      expect(observed.message).not.toContain(backendCode === expectedCode
        ? "private"
        : backendCode);
      expectBackendLifecycleDetached(child);
    },
  );

  it.each([
    {
      code: "windows_backend_ready_failed",
      createChild: () =>
        createHungBackend({
          exitAfterStart: true,
          initialOutput: Buffer.alloc(0),
          stderrOutput: Buffer.from("private ready failure\n", "utf8"),
        }),
    },
    {
      code: "windows_backend_command_failed",
      createChild: () => {
        const child = createHungBackend();
        child.stdin.end = () => {
          throw new Error("private command failure");
        };
        return child;
      },
    },
    {
      code: "windows_backend_final_failed",
      createChild: () =>
        createHungBackend({
          closeAfterFinal: true,
          finalExitStatus: 1,
          stderrOutput: Buffer.from("private final failure\n", "utf8"),
        }),
    },
    {
      code: "windows_backend_exit_failed",
      createChild: () =>
        createHungBackend({
          closeAfterFinal: true,
          finalExitStatus: 1,
          finalOutput: '{"status":"finalized"}\n',
          stderrOutput: Buffer.from("private exit failure\n", "utf8"),
        }),
    },
  ])("reports the exact $code transport stage", async ({ code, createChild }) => {
    const child = createChild();
    const observed = await shellBackendFailure(() =>
      withWindowsBackend(
        {
          outputPath: temporaryRoot,
          pythonExecutable: process.execPath,
          sourceRoot: studioRoot,
          targetId: "win32-x64",
        },
        async () => ({ action: "finalize", result: null }),
        {
          parseEvidence: (report) => report,
          spawnBackend: () => child.start(),
          timeoutMs: 100,
          terminationTimeoutMs: 20,
        },
      ),
    );

    expect(observed).toMatchObject({
      code,
      message: code,
      name: "ShellPackageError",
    });
    expect(observed.message).not.toContain("private");
    expectBackendLifecycleDetached(child);
  });

  it("reports cleanup failure only when no earlier failure exists", async () => {
    const child = createHungBackend({
      closeAfterFinal: true,
      finalOutput: '{"status":"finalized"}\n',
    });
    const observed = await shellBackendFailure(() =>
      withWindowsBackend(
        {
          outputPath: temporaryRoot,
          pythonExecutable: process.execPath,
          sourceRoot: studioRoot,
          targetId: "win32-x64",
        },
        async () => ({ action: "finalize", result: null }),
        {
          parseEvidence: (report) => report,
          protocolReaderFactory: () => ({
            cancel() {
              throw new Error("private cleanup failure");
            },
            end: Promise.resolve({ kind: "end" }),
            hasPendingLineOverflow: () => false,
            lines: [
              Promise.resolve({
                kind: "line",
                value: Buffer.from('{"status":"ready"}', "utf8"),
              }),
              Promise.resolve({
                kind: "line",
                value: Buffer.from('{"status":"finalized"}', "utf8"),
              }),
            ],
          }),
          spawnBackend: () => child.start(),
          timeoutMs: 100,
          terminationTimeoutMs: 20,
        },
      ),
    );

    expect(observed).toMatchObject({
      code: "windows_backend_cleanup_failed",
      message: "windows_backend_cleanup_failed",
      name: "ShellPackageError",
    });
    expect(observed.message).not.toContain("private");
    expectBackendLifecycleDetached(child);
  });

  it("does not expose a retryable sharing code when backend cleanup fails", async () => {
    const child = createHungBackend({
      exitAfterStart: true,
      initialOutput: Buffer.alloc(0),
      stderrOutput: Buffer.from(
        "Studio shell snapshot failed: windows_snapshot_package_sharing_conflict\n",
        "utf8",
      ),
    });
    const observed = await shellBackendFailure(() =>
      withWindowsBackend(
        {
          outputPath: temporaryRoot,
          pythonExecutable: process.execPath,
          sourceRoot: studioRoot,
          targetId: "win32-x64",
        },
        async () => {
          throw new Error("callback must not run");
        },
        {
          protocolReaderFactory: () => ({
            cancel() {
              throw new Error("private cleanup failure");
            },
            end: Promise.resolve({ kind: "end" }),
            hasPendingLineOverflow: () => false,
            lines: [
              Promise.resolve({ kind: "done" }),
              Promise.resolve({ kind: "done" }),
            ],
          }),
          spawnBackend: () => child.start(),
          timeoutMs: 100,
          terminationTimeoutMs: 20,
        },
      ),
    );

    expect(observed).toMatchObject({
      code: "windows_backend_cleanup_failed",
      message: "windows_backend_cleanup_failed",
      name: "ShellPackageError",
    });
    expectBackendLifecycleDetached(child);
  });

  it.each([
    new ShellPackageError("electron_root_layout_mismatch"),
    new Error("private callback validation"),
  ])("preserves callback failures without transport relabeling", async (original) => {
    const child = createHungBackend();
    await expect(
      withWindowsBackend(
        {
          outputPath: temporaryRoot,
          pythonExecutable: process.execPath,
          sourceRoot: studioRoot,
          targetId: "win32-x64",
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
    expectBackendLifecycleDetached(child);
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
  it("pins the literal Electron 43.2.0 Windows root inventory", () => {
    expect(targetFixtureLayout("win32-x64").rootFiles).toEqual([
      "LICENSE.electron.txt",
      "LICENSES.chromium.html",
      "World Forge Studio.exe",
      "chrome_100_percent.pak",
      "chrome_200_percent.pak",
      "d3dcompiler_47.dll",
      "dxcompiler.dll",
      "dxil.dll",
      "ffmpeg.dll",
      "icudtl.dat",
      "libEGL.dll",
      "libGLESv2.dll",
      "resources.pak",
      "snapshot_blob.bin",
      "v8_context_snapshot.bin",
      "vk_swiftshader.dll",
      "vk_swiftshader_icd.json",
      "vulkan-1.dll",
    ]);
  });

  it("opens the Windows package snapshot reader with guard-compatible sharing", () => {
    const pythonExecutable =
      testPython ?? (process.platform === "win32" ? "python" : "python3");
    const probe = spawnSync(
      pythonExecutable,
      [
        "-B",
        "-c",
        `
import json
import sys
import types
from pathlib import Path

from apps.studio.scripts import shell_package_snapshot as snapshot

calls = []

class FakeApi:
    @staticmethod
    def state(_handle, _field):
        return types.SimpleNamespace(
            identity=(7, 11),
            is_directory=True,
            is_reparse=False,
        )

class FakeChain:
    def __init__(self, *args, **kwargs):
        calls.append({
            "args": [
                value.as_posix() if isinstance(value, Path) else str(value)
                for value in args
            ],
            "kwargs": kwargs,
        })
        if kwargs != {"share_write": True, "writable_leaf": False}:
            raise PermissionError("guard-incompatible package reader")
        self.api = FakeApi()
        self.leaf = 1

    def close(self):
        pass

module = types.ModuleType("scripts.studio_runtime_assembly")
module._WindowsDirectoryChain = FakeChain
sys.modules[module.__name__] = module
snapshot._WindowsReader = lambda api: types.SimpleNamespace(api=api)
original_scan = snapshot._WindowsPinnedTree._scan
snapshot._WindowsPinnedTree._scan = lambda *_args: None
try:
    snapshot._WindowsPinnedTree(
        Path("C:/guarded-package"),
        share_write=True,
        writable_leaf=False,
    )
finally:
    snapshot._WindowsPinnedTree._scan = original_scan
print(json.dumps(calls, sort_keys=True))
`,
      ],
      {
        cwd: repositoryRoot,
        encoding: "utf8",
        env: process.env,
        maxBuffer: 64 * 1024,
      },
    );
    expect({
      signal: probe.signal,
      status: probe.status,
      stderr: probe.stderr,
    }).toEqual({
      signal: null,
      status: 0,
      stderr: "",
    });
    expect(JSON.parse(probe.stdout)).toEqual([
      {
        args: ["C:/guarded-package", "package"],
        kwargs: {
          share_write: true,
          writable_leaf: false,
        },
      },
    ]);
  });

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
          retainedAsarVerifier: verifyRetainedGenericAssetRuntime,
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

  it.skipIf(process.platform !== "linux")(
    "smokes the retained ASAR bytes and rejects pathname replacement afterward",
    async () => {
      const root = await cloneBase("linux-x64", "retained-asar-smoke");
      const archive = path.join(root, "resources/app.asar");
      const original = await readFile(archive);
      let retainedSnapshot;
      let runtimeEvidence;
      let failure;
      try {
        await verifyPackagedShell({
          beforeFinalBinding: async () => {
            const replacement = `${archive}.replacement`;
            await copyFile(archive, replacement);
            await rename(replacement, archive);
          },
          fuseReader: fixtureFuseReader,
          outputPath: root,
          retainedAsarVerifier: async (snapshot) => {
            retainedSnapshot = snapshot;
            expect(snapshot.bytes.equals(original)).toBe(true);
            runtimeEvidence =
              await verifyRetainedAsarContracts(snapshot);
            return runtimeEvidence;
          },
          targetId: "linux-x64",
        });
      } catch (error) {
        failure = error;
      }
      expect(retainedSnapshot).toMatchObject({
        logical_path: "resources/app.asar",
        sha256: createHash("sha256").update(original).digest("hex"),
        size: original.length,
      });
      expect(runtimeEvidence).toMatchObject({
        artifact_sha256: retainedSnapshot.sha256,
        artifact_size_bytes: retainedSnapshot.size,
        game_package: {
          artifact_sha256: retainedSnapshot.sha256,
          artifact_size_bytes: retainedSnapshot.size,
          manifests_verified: 1,
          status: "verified",
          tamper_rejections: 8,
        },
        generic_materialization_contracts: {
          artifact_sha256: retainedSnapshot.sha256,
          artifact_size_bytes: retainedSnapshot.size,
          invalid_documents_rejected: 7,
          status: "verified",
          valid_documents_accepted: 6,
        },
        status: "verified",
      });
      expect(failure).toMatchObject({ code: "package_entry_replaced" });
    },
    30_000,
  );

  it.skipIf(!canVerifySecurely)(
    "rejects missing and extra Windows Electron root files",
    async () => {
      const missing = await cloneBase("win32-x64", "missing-dxcompiler");
      await rm(path.join(missing, "dxcompiler.dll"));
      await expect(
        verifyPackagedShell({
          fuseReader: fixtureFuseReader,
          outputPath: missing,
          targetId: "win32-x64",
        }),
      ).rejects.toMatchObject({ code: "electron_root_layout_mismatch" });

      const extra = await cloneBase("win32-x64", "extra-root-dll");
      await writeFile(path.join(extra, "dxcompiler-copy.dll"), "extra\n");
      await expect(
        verifyPackagedShell({
          fuseReader: fixtureFuseReader,
          outputPath: extra,
          targetId: "win32-x64",
        }),
      ).rejects.toMatchObject({ code: "electron_root_layout_mismatch" });
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
    const cleanBuildFiles = [
      "dist-electron/main/generic-asset-runtime.cjs",
      "dist-electron/main/generic-game-package.cjs",
      "dist-electron/main/generic-game-persistence.cjs",
      "dist-electron/main/generic-game-runtime-bundle.cjs",
      "dist-electron/main/generic-headless-evidence.cjs",
      "dist-electron/main/generic-materialization-contracts.cjs",
      "dist-electron/main/generic-runtime-contracts.cjs",
      "dist-electron/main/index.cjs",
      "dist-electron/authority-modal/index.html",
      "dist-electron/authority-modal/preload.cjs",
      "dist-electron/authority-modal/renderer.js",
      "dist-electron/authority-modal/style.css",
      "dist-electron/preload/index.cjs",
      "dist-renderer/index.html",
      "dist-renderer/assets/index.css",
      "dist-renderer/assets/index.js",
      "dist-renderer/assets/vendor.js",
      "package.json",
      "!node_modules/**/*",
    ];
    expect(packageDocument.build.files).toEqual(cleanBuildFiles);
    expect(
      cleanBuildFiles.filter(
        (entry) => !entry.startsWith("!") && entry !== "package.json",
      ),
    ).toHaveLength(17);
    expect(cleanBuildFiles.filter((entry) => !entry.startsWith("!"))).toHaveLength(18);
    expect(packageDocument.build.directories.output).toBe(
      "${env.WORLD_FORGE_STUDIO_PACKAGE_OUTPUT}",
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
    const manifestCalls = [];
    let reservedOutputReal;
    const runner = async (executable, args, options) => {
      calls.push({ args, executable, options });
      if (args[1] === "--dir") {
        reservedOutputReal = await realpath(
          options.env.RWF_STUDIO_PACKAGE_OUTPUT,
        );
      }
      return 0;
    };
    const tools = {
      builderCli: path.join(temporaryRoot, "electron-builder.js"),
      manifestWriter: async (options) => {
        manifestCalls.push(options);
      },
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
    const canonicalOutput = path.join(
      await realpath(path.dirname(output)),
      path.basename(output),
    );
    const result = await runShellPackage({
      ...tools,
      argv: ["--output", output, "--target", "linux-x64"],
    });
    expect(result).toEqual({
      output_path: canonicalOutput,
      package_path: path.join(canonicalOutput, "linux-unpacked"),
      target_id: "linux-x64",
    });
    expect(reservedOutputReal).toBe(canonicalOutput);
    expect(calls).toHaveLength(3);
    const boundOutput = calls[1].options.env.RWF_STUDIO_PACKAGE_OUTPUT;
    expect(
      calls[1].options.env.WORLD_FORGE_STUDIO_PACKAGE_OUTPUT,
    ).toBe(boundOutput);
    expect(path.isAbsolute(boundOutput)).toBe(true);
    expect(calls[1].args).toEqual([
      tools.builderCli,
      "--dir",
      "--linux",
      "--x64",
      "--publish=never",
      `--config.directories.output=${boundOutput}`,
    ]);
    expect(calls[1].options.captureStdoutBytes).toBe(
      maxBuilderStdoutBytes,
    );
    expect(calls[2].args).toEqual([
      path.join(studioRoot, "scripts/verify-shell-package.mjs"),
      "--path",
      path.join(boundOutput, "linux-unpacked"),
      "--target",
      "linux-x64",
    ]);
    expect(manifestCalls).toEqual([
      {
        outputPath: path.join(boundOutput, "linux-unpacked"),
        pythonExecutable: testPython,
        targetId: "linux-x64",
      },
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

  it("rejects conflicting package aliases before invoking any build process", async () => {
    const calls = [];
    await expect(
      runShellPackage({
        argv: [
          "--output",
          path.join(temporaryRoot, "conflicting-env-output"),
          "--target",
          "linux-x64",
        ],
        builderCli: path.join(temporaryRoot, "electron-builder.js"),
        environment: {
          WORLD_FORGE_STUDIO_BUILD_PYTHON: "/canonical/python",
          RWF_STUDIO_BUILD_PYTHON: "/legacy/python",
        },
        npmCli: path.join(temporaryRoot, "npm-cli.js"),
        runner: async (...args) => {
          calls.push(args);
          return 0;
        },
      }),
    ).rejects.toThrow(/conflicting Studio environment variables/u);
    expect(calls).toHaveLength(0);
  });

  it("retries one exact CRLF EBUSY rename on a fresh reservation and returns its package path", async () => {
    const pythonExecutable = path.join(temporaryRoot, "python.exe");
    const harness = createPackageRetryHarness({
      builderResults: [
        (boundPath) => ({
          status: 1,
          stdout: electronBuilderEbusyOutput(boundPath, {
            lineEnding: "\r\n",
          }),
          stdoutOverflow: false,
        }),
        { status: 0, stdout: Buffer.alloc(0), stdoutOverflow: false },
      ],
      pythonExecutable,
    });
    const output = path.join(temporaryRoot, "retry-success");
    const result = await runShellPackage({
      ...harness.tools,
      argv: ["--output", output, "--target", "win32-x64"],
    });

    const builderCalls = harness.calls.filter(
      ({ args }) => args[1] === "--dir",
    );
    const verifierCalls = harness.calls.filter(
      ({ args }) =>
        args[0] ===
        path.join(studioRoot, "scripts/verify-shell-package.mjs"),
    );
    expect(builderCalls).toHaveLength(2);
    expect(harness.reservations).toHaveLength(2);
    expect(harness.reservations[0].outputPath).not.toBe(
      harness.reservations[1].outputPath,
    );
    expect(harness.delayCalls).toEqual([1000]);
    expect(harness.events.map(({ type }) => type)).toEqual([
      "reserve",
      "builder-exit",
      "close",
      "delay",
      "reserve",
      "builder-exit",
      "manifest",
      "verify",
      "finalize",
      "close",
    ]);
    expect(harness.reservations[0]).toMatchObject({
      closeCount: 1,
      finalizeCount: 0,
    });
    expect(harness.reservations[1]).toMatchObject({
      closeCount: 1,
      finalizeCount: 1,
    });
    for (const call of builderCalls) {
      expect(call.args).toContain("--publish=never");
      expect(call.options.captureStdoutBytes).toBe(
        maxBuilderStdoutBytes,
      );
    }
    expect(verifierCalls).toHaveLength(1);
    expect(verifierCalls[0].args).toEqual([
      path.join(studioRoot, "scripts/verify-shell-package.mjs"),
      "--path",
      path.join(harness.reservations[1].boundPath, "win-unpacked"),
      "--target",
      "win32-x64",
    ]);
    expect(harness.manifestCalls).toEqual([
      {
        outputPath: path.join(
          harness.reservations[1].boundPath,
          "win-unpacked",
        ),
        pythonExecutable,
        targetId: "win32-x64",
      },
    ]);
    expect(result).toEqual({
      output_path: harness.reservations[1].outputPath,
      package_path: path.join(
        harness.reservations[1].outputPath,
        "win-unpacked",
      ),
      target_id: "win32-x64",
    });
  });

  it("stops after two exact LF EBUSY rename failures", async () => {
    const exactFailure = (boundPath) => ({
      status: 1,
      stdout: electronBuilderEbusyOutput(boundPath),
      stdoutOverflow: false,
    });
    const harness = createPackageRetryHarness({
      builderResults: [exactFailure, exactFailure],
    });
    await expect(
      runShellPackage({
        ...harness.tools,
        argv: [
          "--output",
          path.join(temporaryRoot, "retry-exhausted"),
          "--target",
          "win32-x64",
        ],
      }),
    ).rejects.toMatchObject({ code: "shell_package_failed" });

    expect(
      harness.calls.filter(({ args }) => args[1] === "--dir"),
    ).toHaveLength(2);
    expect(harness.delayCalls).toEqual([1000]);
    expect(harness.reservations).toHaveLength(2);
    expect(
      harness.reservations.map(
        ({ closeCount, finalizeCount }) => ({
          closeCount,
          finalizeCount,
        }),
      ),
    ).toEqual([
      { closeCount: 1, finalizeCount: 0 },
      { closeCount: 1, finalizeCount: 0 },
    ]);
  });

  it.each([
    [
      "invalid UTF-8",
      (boundPath) => ({
        status: 1,
        stdout: Buffer.concat([
          Buffer.from([0xff]),
          electronBuilderEbusyOutput(boundPath),
        ]),
        stdoutOverflow: false,
      }),
    ],
    [
      "stdout overflow",
      (boundPath) => ({
        status: 1,
        stdout: electronBuilderEbusyOutput(boundPath),
        stdoutOverflow: true,
      }),
    ],
    [
      "ANSI-altered output",
      (boundPath) => ({
        status: 1,
        stdout: Buffer.concat([
          Buffer.from("\u001b[31m", "utf8"),
          electronBuilderEbusyOutput(boundPath),
        ]),
        stdoutOverflow: false,
      }),
    ],
    [
      "partial line",
      (boundPath) => ({
        status: 1,
        stdout: electronBuilderEbusyOutput(boundPath, {
          lineEnding: "",
        }),
        stdoutOverflow: false,
      }),
    ],
    [
      "duplicate lines",
      (boundPath) => ({
        status: 1,
        stdout: Buffer.concat([
          electronBuilderEbusyOutput(boundPath),
          electronBuilderEbusyOutput(boundPath),
        ]),
        stdoutOverflow: false,
      }),
    ],
    [
      "ambiguous lines",
      (boundPath) => ({
        status: 1,
        stdout: Buffer.concat([
          electronBuilderEbusyOutput(boundPath),
          electronBuilderEbusyOutput(boundPath, {
            reportedBoundPath: `${boundPath}-other`,
          }),
        ]),
        stdoutOverflow: false,
      }),
    ],
    [
      "ambiguous operations",
      (boundPath) => ({
        status: 1,
        stdout: Buffer.concat([
          electronBuilderEbusyOutput(boundPath),
          electronBuilderEbusyOutput(boundPath, {
            operation: "copy",
          }),
        ]),
        stdoutOverflow: false,
      }),
    ],
    [
      "stderr-only signature",
      (boundPath) => ({
        status: 1,
        stderr: electronBuilderEbusyOutput(boundPath),
        stdout: Buffer.alloc(0),
        stdoutOverflow: false,
      }),
    ],
    [
      "wrong reported path",
      (boundPath) => ({
        status: 1,
        stdout: electronBuilderEbusyOutput(boundPath, {
          reportedBoundPath: `${boundPath}-other`,
        }),
        stdoutOverflow: false,
      }),
    ],
    [
      "wrong operation",
      (boundPath) => ({
        status: 1,
        stdout: electronBuilderEbusyOutput(boundPath, {
          operation: "copy",
        }),
        stdoutOverflow: false,
      }),
    ],
    [
      "wrong stack path",
      (boundPath) => ({
        status: 1,
        stdout: electronBuilderEbusyOutput(boundPath, {
          stackBoundPath: `${boundPath}-other`,
        }),
        stdoutOverflow: false,
      }),
    ],
  ])("does not retry a %s", async (_label, builderResult) => {
    const harness = createPackageRetryHarness({
      builderResults: [builderResult],
    });
    await expect(
      runShellPackage({
        ...harness.tools,
        argv: [
          "--output",
          path.join(
            temporaryRoot,
            `no-retry-${_label.replaceAll(" ", "-")}`,
          ),
          "--target",
          "win32-x64",
        ],
      }),
    ).rejects.toMatchObject({ code: "shell_package_failed" });

    expect(
      harness.calls.filter(({ args }) => args[1] === "--dir"),
    ).toHaveLength(1);
    expect(harness.delayCalls).toEqual([]);
    expect(harness.reservations).toHaveLength(1);
    expect(harness.reservations[0]).toMatchObject({
      closeCount: 1,
      finalizeCount: 0,
    });
  });

  it("does not retry a verifier failure", async () => {
    const harness = createPackageRetryHarness({
      builderResults: [
        { status: 0, stdout: Buffer.alloc(0), stdoutOverflow: false },
      ],
      verifierStatus: 1,
    });
    await expect(
      runShellPackage({
        ...harness.tools,
        argv: [
          "--output",
          path.join(temporaryRoot, "verifier-no-retry"),
          "--target",
          "win32-x64",
        ],
      }),
    ).rejects.toMatchObject({
      code: "shell_package_verification_failed",
    });

    expect(
      harness.calls.filter(({ args }) => args[1] === "--dir"),
    ).toHaveLength(1);
    expect(harness.delayCalls).toEqual([]);
    expect(harness.reservations[0]).toMatchObject({
      closeCount: 1,
      finalizeCount: 0,
    });
  });

  it.each(["linux-x64", "win32-x64"])(
    "fails closed without retrying when %s post-builder manifest publication fails",
    async (targetId) => {
      const manifestError = Object.assign(
        new Error("private manifest path must stay redacted"),
        { code: "shell_manifest_publish_failed" },
      );
      const harness = createPackageRetryHarness({
        builderResults: [
          { status: 0, stdout: Buffer.alloc(0), stdoutOverflow: false },
        ],
        manifestError,
      });
      const observed = await packageShellFailure(() =>
        runShellPackage({
          ...harness.tools,
          argv: [
            "--output",
            path.join(
              temporaryRoot,
              `manifest-no-retry-${targetId}`,
            ),
            "--target",
            targetId,
          ],
        }),
      );
      expect(observed).toMatchObject({
        code: "package_failed",
        message: "package_failed",
        name: "PackageShellError",
      });
      expect(observed).not.toHaveProperty(
        "message",
        manifestError.message,
      );

      expect(
        harness.calls.filter(({ args }) => args[1] === "--dir"),
      ).toHaveLength(1);
      expect(
        harness.calls.filter(
          ({ args }) =>
            args[0] ===
            path.join(studioRoot, "scripts/verify-shell-package.mjs"),
        ),
      ).toHaveLength(0);
      expect(harness.delayCalls).toEqual([]);
      expect(harness.manifestCalls).toHaveLength(1);
      expect(harness.events.map(({ type }) => type)).toEqual([
        "reserve",
        "builder-exit",
        "manifest",
        "close",
      ]);
      expect(harness.reservations[0]).toMatchObject({
        closeCount: 1,
        finalizeCount: 0,
      });
    },
  );

  it.each([
    "windows_backend_failed",
    "shell_manifest_publish_failed",
  ])(
    "preserves bounded %s manifest failure evidence without its message",
    async (code) => {
      const manifestError = new ShellPackageError(
        code,
        `private manifest detail for ${code}`,
      );
      const harness = createPackageRetryHarness({
        builderResults: [
          { status: 0, stdout: Buffer.alloc(0), stdoutOverflow: false },
        ],
        manifestError,
      });
      const observed = await packageShellFailure(() =>
        runShellPackage({
          ...harness.tools,
          argv: [
            "--output",
            path.join(temporaryRoot, `bounded-manifest-${code}`),
            "--target",
            "win32-x64",
          ],
        }),
      );

      expect(observed).toMatchObject({
        code,
        message: code,
        name: "PackageShellError",
      });
      expect(observed).not.toHaveProperty(
        "message",
        manifestError.message,
      );
      expect(harness.events.map(({ type }) => type)).toEqual([
        "reserve",
        "builder-exit",
        "manifest",
        "close",
      ]);
      expect(harness.reservations[0]).toMatchObject({
        closeCount: 1,
        finalizeCount: 0,
      });
    },
  );

  it("does not retry a finalizer failure", async () => {
    const harness = createPackageRetryHarness({
      builderResults: [
        { status: 0, stdout: Buffer.alloc(0), stdoutOverflow: false },
      ],
      finalizerError: new PackageShellError("package_output_changed"),
    });
    await expect(
      runShellPackage({
        ...harness.tools,
        argv: [
          "--output",
          path.join(temporaryRoot, "finalizer-no-retry"),
          "--target",
          "win32-x64",
        ],
      }),
    ).rejects.toMatchObject({ code: "package_output_changed" });

    expect(
      harness.calls.filter(({ args }) => args[1] === "--dir"),
    ).toHaveLength(1);
    expect(harness.delayCalls).toEqual([]);
    expect(harness.reservations[0]).toMatchObject({
      closeCount: 1,
      finalizeCount: 1,
    });
  });

  it("reverifies the exact Windows package path reported by the wrapper", async () => {
    const workflow = await readFile(
      path.resolve(studioRoot, "../../.github/workflows/ci.yml"),
      "utf8",
    );
    expect(workflow).toContain(
      "npm run package:dir -- --output $output --target win32-x64 | Tee-Object -Variable packageOutput",
    );
    expect(workflow).toContain(
      "$packageReport = @($packageOutput)[-1] | ConvertFrom-Json",
    );
    expect(workflow).toContain(
      "$unpacked = [string]$packageReport.package_path",
    );
    expect(workflow).not.toContain(
      '$unpacked = Join-Path $output "win-unpacked"',
    );
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
