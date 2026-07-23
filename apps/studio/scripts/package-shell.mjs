import { spawn } from "node:child_process";
import { constants } from "node:fs";
import {
  lstat,
  mkdir,
  open,
  realpath,
} from "node:fs/promises";
import path from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

const SCRIPT_ROOT = path.dirname(fileURLToPath(import.meta.url));
export const STUDIO_ROOT = path.resolve(SCRIPT_ROOT, "..");
export const REPOSITORY_ROOT = path.resolve(STUDIO_ROOT, "../..");

export class PackageShellError extends Error {
  constructor(code, exitCode = 1) {
    super(code);
    this.code = code;
    this.exitCode = exitCode;
    this.name = "PackageShellError";
  }
}

function fail(code, exitCode = 1) {
  throw new PackageShellError(code, exitCode);
}

function normalizedKey(value) {
  return process.platform === "win32" ? value.toLowerCase() : value;
}

function isWithin(parent, candidate) {
  const relative = path.relative(
    normalizedKey(parent),
    normalizedKey(candidate),
  );
  return (
    relative === "" ||
    (!path.isAbsolute(relative) &&
      relative !== ".." &&
      !relative.startsWith(`..${path.sep}`))
  );
}

async function canonicalFuturePath(outputPath) {
  try {
    await lstat(outputPath);
    fail("package_output_exists", 2);
  } catch (error) {
    if (error instanceof PackageShellError) {
      throw error;
    }
    if (error?.code !== "ENOENT") {
      fail("package_output_parent_invalid", 2);
    }
  }
  const parent = path.dirname(outputPath);
  try {
    const resolved = await realpath(parent);
    if (!(await lstat(resolved)).isDirectory()) {
      fail("package_output_parent_invalid", 2);
    }
    return path.join(resolved, path.basename(outputPath));
  } catch (error) {
    if (error instanceof PackageShellError) {
      throw error;
    }
    fail("package_output_parent_invalid", 2);
  }
}

export function parsePackageShellArguments(argv) {
  if (
    argv.length !== 4 ||
    argv[0] !== "--output" ||
    argv[2] !== "--target" ||
    !["linux-x64", "win32-x64"].includes(argv[3])
  ) {
    fail("invalid_arguments", 2);
  }
  const outputPath = argv[1];
  if (
    typeof outputPath !== "string" ||
    !path.isAbsolute(outputPath) ||
    path.normalize(outputPath) !== outputPath
  ) {
    fail("invalid_package_output", 2);
  }
  return { outputPath, targetId: argv[3] };
}

export async function validatePackageOutput(
  outputPath,
  {
    repositoryRoot = REPOSITORY_ROOT,
  } = {},
) {
  const repositoryReal = await realpath(repositoryRoot);
  if (isWithin(repositoryRoot, outputPath)) {
    fail("package_output_inside_repository", 2);
  }
  const canonicalOutput = await canonicalFuturePath(outputPath);
  if (isWithin(repositoryReal, canonicalOutput)) {
    fail("package_output_inside_repository", 2);
  }
  return canonicalOutput;
}

function sameIdentity(left, right) {
  return left.dev === right.dev && left.ino === right.ino;
}

function descriptorPath(handle, child = "") {
  const root = `/proc/${process.pid}/fd/${handle.fd}`;
  return child ? path.join(root, child) : root;
}

async function reserveLinuxOutput(outputPath, repositoryRoot) {
  const repositoryReal = await realpath(repositoryRoot);
  let parentHandle;
  let outputHandle;
  try {
    parentHandle = await open(
      path.dirname(outputPath),
      constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW,
    );
    const openedParent = await realpath(descriptorPath(parentHandle));
    if (isWithin(repositoryReal, openedParent)) {
      fail("package_output_inside_repository", 2);
    }
    const name = path.basename(outputPath);
    try {
      await mkdir(descriptorPath(parentHandle, name), { mode: 0o700 });
    } catch {
      fail("package_output_reservation_failed", 2);
    }
    outputHandle = await open(
      descriptorPath(parentHandle, name),
      constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW,
    );
    const retained = await outputHandle.stat({ bigint: true });
    const named = await lstat(outputPath, { bigint: true });
    if (
      !retained.isDirectory() ||
      !named.isDirectory() ||
      !sameIdentity(retained, named)
    ) {
      fail("package_output_changed");
    }
    let closed = false;
    return {
      boundPath: descriptorPath(outputHandle),
      close: async () => {
        if (closed) {
          return;
        }
        closed = true;
        await outputHandle.close();
        await parentHandle.close();
      },
      finalize: async () => {
        const finalRetained = await outputHandle.stat({ bigint: true });
        const finalNamed = await lstat(outputPath, { bigint: true });
        const finalReal = await realpath(outputPath);
        if (
          !finalRetained.isDirectory() ||
          !finalNamed.isDirectory() ||
          !sameIdentity(retained, finalRetained) ||
          !sameIdentity(retained, finalNamed) ||
          isWithin(repositoryReal, finalReal)
        ) {
          fail("package_output_changed");
        }
      },
    };
  } catch (error) {
    await outputHandle?.close().catch(() => undefined);
    await parentHandle?.close().catch(() => undefined);
    throw error;
  }
}

function windowsPythonExecutable(explicit) {
  const executable =
    explicit ??
    process.env.RWF_STUDIO_BUILD_PYTHON ??
    process.env.PYTHON ??
    (process.env.pythonLocation
      ? path.join(process.env.pythonLocation, "python.exe")
      : undefined);
  return requireAbsoluteTool(executable);
}

async function reserveWindowsOutput(
  outputPath,
  repositoryRoot,
  pythonExecutable,
) {
  const executable = windowsPythonExecutable(pythonExecutable);
  const backend = path.join(SCRIPT_ROOT, "shell_package_snapshot.py");
  const repoRoot = path.resolve(STUDIO_ROOT, "../..");
  const child = spawn(
    executable,
    [
      backend,
      "guard-output",
      "--path",
      outputPath,
      "--source-root",
      STUDIO_ROOT,
      "--repository-root",
      repositoryRoot,
    ],
    {
      cwd: STUDIO_ROOT,
      env: Object.fromEntries(
        Object.entries({
          PYTHONDONTWRITEBYTECODE: "1",
          PYTHONIOENCODING: "utf-8",
          PYTHONNOUSERSITE: "1",
          PYTHONPATH: [path.join(repoRoot, "src"), repoRoot].join(path.delimiter),
          PYTHONUTF8: "1",
          SYSTEMROOT: process.env.SYSTEMROOT,
          TEMP: process.env.TEMP,
          TMP: process.env.TMP,
          WINDIR: process.env.WINDIR,
        }).filter(([, value]) => typeof value === "string"),
      ),
      shell: false,
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    },
  );
  child.stderr.resume();
  const exited = new Promise((resolve, reject) => {
    child.once("error", () => reject(new PackageShellError("package_output_guard_failed")));
    child.once("close", (status) => resolve(status));
  });
  const lines = createInterface({ crlfDelay: Infinity, input: child.stdout });
  const iterator = lines[Symbol.asyncIterator]();
  let completed = false;
  try {
    const first = await iterator.next();
    let report;
    try {
      report = first.done ? null : JSON.parse(first.value);
    } catch {
      fail("package_output_guard_failed");
    }
    if (
      JSON.stringify(report) !==
      JSON.stringify({ output_path: outputPath, status: "ready" })
    ) {
      fail("package_output_guard_failed");
    }
    return {
      boundPath: outputPath,
      close: async () => {
        lines.close();
        if (!completed) {
          child.stdin.end();
          await exited.catch(() => undefined);
        }
      },
      finalize: async () => {
        child.stdin.end('{"action":"finalize"}\n');
        const final = await iterator.next();
        let document;
        try {
          document = final.done ? null : JSON.parse(final.value);
        } catch {
          fail("package_output_guard_failed");
        }
        const status = await exited;
        if (
          status !== 0 ||
          JSON.stringify(document) !== JSON.stringify({ status: "finalized" })
        ) {
          fail("package_output_changed");
        }
        completed = true;
      },
    };
  } catch (error) {
    lines.close();
    child.stdin.end();
    await exited.catch(() => undefined);
    throw error;
  }
}

export async function reservePackageOutput(
  outputPath,
  {
    pythonExecutable,
    repositoryRoot = REPOSITORY_ROOT,
  } = {},
) {
  if (process.platform === "linux") {
    return reserveLinuxOutput(outputPath, repositoryRoot);
  }
  if (process.platform === "win32") {
    return reserveWindowsOutput(
      outputPath,
      repositoryRoot,
      pythonExecutable,
    );
  }
  fail("secure_primitive_unavailable");
}

async function defaultRunner(executable, args, options) {
  const child = spawn(executable, args, {
    ...options,
    shell: false,
    stdio: "inherit",
  });
  return new Promise((resolve, reject) => {
    child.once("error", () => reject(new PackageShellError("package_tool_unavailable")));
    child.once("close", (status) => resolve(status));
  });
}

function requireAbsoluteTool(value) {
  if (
    typeof value !== "string" ||
    !path.isAbsolute(value) ||
    path.normalize(value) !== value
  ) {
    fail("package_tool_unavailable");
  }
  return value;
}

export async function runShellPackage({
  argv,
  builderCli = path.join(STUDIO_ROOT, "node_modules/electron-builder/cli.js"),
  npmCli = process.env.npm_execpath,
  pythonExecutable,
  repositoryRoot = REPOSITORY_ROOT,
  reservationFactory = reservePackageOutput,
  runner = defaultRunner,
} = {}) {
  const { outputPath, targetId } = parsePackageShellArguments(argv ?? []);
  const canonicalOutput = await validatePackageOutput(outputPath, {
    repositoryRoot,
  });
  const nodeExecutable = requireAbsoluteTool(process.execPath);
  const buildTool = requireAbsoluteTool(npmCli);
  const packageTool = requireAbsoluteTool(builderCli);
  const common = {
    cwd: STUDIO_ROOT,
    env: process.env,
  };
  if (
    (await runner(nodeExecutable, [buildTool, "run", "build"], common)) !== 0
  ) {
    fail("package_build_failed");
  }
  const reboundOutput = await validatePackageOutput(outputPath, {
    repositoryRoot,
  });
  if (reboundOutput !== canonicalOutput) {
    fail("package_output_changed");
  }
  const reservation = await reservationFactory(reboundOutput, {
    pythonExecutable,
    repositoryRoot,
  });
  try {
    const platformFlag = targetId === "linux-x64" ? "--linux" : "--win";
    const packageEnvironment = {
      ...process.env,
      RWF_STUDIO_PACKAGE_OUTPUT: reservation.boundPath,
    };
    if (
      (await runner(
        nodeExecutable,
        [
          packageTool,
          "--dir",
          platformFlag,
          "--x64",
          `--config.directories.output=${reservation.boundPath}`,
        ],
        {
          cwd: STUDIO_ROOT,
          env: packageEnvironment,
        },
      )) !== 0
    ) {
      fail("shell_package_failed");
    }
    const boundUnpacked = path.join(
      reservation.boundPath,
      targetId === "linux-x64" ? "linux-unpacked" : "win-unpacked",
    );
    if (
      (await runner(
        nodeExecutable,
        [
          path.join(SCRIPT_ROOT, "verify-shell-package.mjs"),
          "--path",
          boundUnpacked,
          "--target",
          targetId,
        ],
        common,
      )) !== 0
    ) {
      fail("shell_package_verification_failed");
    }
    await reservation.finalize();
  } finally {
    await reservation.close();
  }
  const unpacked = path.join(
    canonicalOutput,
    targetId === "linux-x64" ? "linux-unpacked" : "win-unpacked",
  );
  return Object.freeze({
    output_path: canonicalOutput,
    package_path: unpacked,
    target_id: targetId,
  });
}

async function main() {
  try {
    const result = await runShellPackage({ argv: process.argv.slice(2) });
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } catch (error) {
    if (error instanceof PackageShellError) {
      process.stderr.write(`Studio shell packaging failed: ${error.code}\n`);
      process.exitCode = error.exitCode;
      return;
    }
    process.stderr.write("Studio shell packaging failed: package_failed\n");
    process.exitCode = 1;
  }
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  await main();
}
