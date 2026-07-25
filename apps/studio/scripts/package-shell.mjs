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
import { TextDecoder } from "node:util";
import { fileURLToPath } from "node:url";

const SCRIPT_ROOT = path.dirname(fileURLToPath(import.meta.url));
export const STUDIO_ROOT = path.resolve(SCRIPT_ROOT, "..");
export const REPOSITORY_ROOT = path.resolve(STUDIO_ROOT, "../..");
const MAX_BUILDER_ATTEMPTS = 2;
const MAX_BUILDER_STDOUT_BYTES = 64 * 1024;
const BUILDER_RETRY_DELAY_MS = 1000;
const BUILDER_EBUSY_PREFIX = "EBUSY: resource busy or locked";
const BUILDER_EBUSY_FRAGMENT =
  `${BUILDER_EBUSY_PREFIX}, rename `;

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

function normalizedKey(value, pathFlavor) {
  return pathFlavor.sep === "\\" ? value.toLowerCase() : value;
}

export function isWithin(parent, candidate, pathFlavor = path) {
  const relative = pathFlavor.relative(
    normalizedKey(parent, pathFlavor),
    normalizedKey(candidate, pathFlavor),
  );
  return (
    relative === "" ||
    (!pathFlavor.isAbsolute(relative) &&
      relative !== ".." &&
      !relative.startsWith(`..${pathFlavor.sep}`))
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

async function captureBoundedStdout(stream, limit) {
  const chunks = [];
  let capturedBytes = 0;
  let stdoutOverflow = false;
  for await (const value of stream) {
    const chunk = Buffer.isBuffer(value) ? value : Buffer.from(value);
    const remaining = limit - capturedBytes;
    if (remaining > 0) {
      const accepted = chunk.subarray(0, remaining);
      chunks.push(Buffer.from(accepted));
      capturedBytes += accepted.length;
      process.stdout.write(accepted);
    }
    if (chunk.length > remaining) {
      stdoutOverflow = true;
    }
  }
  return {
    stdout: Buffer.concat(chunks, capturedBytes),
    stdoutOverflow,
  };
}

async function defaultRunner(executable, args, options) {
  const { captureStdoutBytes, ...spawnOptions } = options;
  const captureStdout =
    Number.isSafeInteger(captureStdoutBytes) && captureStdoutBytes > 0;
  const child = spawn(executable, args, {
    ...spawnOptions,
    shell: false,
    stdio: captureStdout ? ["inherit", "pipe", "inherit"] : "inherit",
  });
  const exited = new Promise((resolve, reject) => {
    child.once("error", () => reject(new PackageShellError("package_tool_unavailable")));
    child.once("close", (status) => resolve(status));
  });
  if (!captureStdout) {
    return exited;
  }
  const [status, captured] = await Promise.all([
    exited,
    captureBoundedStdout(child.stdout, captureStdoutBytes),
  ]);
  return { status, ...captured };
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

function runnerStatus(result) {
  if (typeof result === "number" || result === null) {
    return result;
  }
  return result?.status;
}

function completeOutputLines(output) {
  const lines = [];
  let start = 0;
  for (const match of output.matchAll(/\r\n|\n/g)) {
    lines.push(output.slice(start, match.index));
    start = match.index + match[0].length;
  }
  return { lines, trailing: output.slice(start) };
}

function isRetryableWindowsBuilderFailure(result, boundPath) {
  if (
    !result ||
    typeof result !== "object" ||
    result.stdoutOverflow !== false ||
    !Buffer.isBuffer(result.stdout) ||
    result.stdout.length > MAX_BUILDER_STDOUT_BYTES
  ) {
    return false;
  }
  let output;
  try {
    output = new TextDecoder("utf-8", { fatal: true }).decode(
      result.stdout,
    );
  } catch {
    return false;
  }
  if (output.includes("\u001b")) {
    return false;
  }
  const temporaryPath = path.join(boundPath, "win-unpacked.tmp");
  const finalPath = path.join(boundPath, "win-unpacked");
  const expected =
    `  ⨯ ${BUILDER_EBUSY_FRAGMENT}'${temporaryPath}' -> ` +
    `'${finalPath}'  failedTask=build stackTrace=Error: ` +
    `${BUILDER_EBUSY_FRAGMENT}'${temporaryPath}' -> '${finalPath}'`;
  const { lines, trailing } = completeOutputLines(output);
  const candidates = lines.filter((line) =>
    line.includes(BUILDER_EBUSY_PREFIX),
  );
  return (
    !trailing.includes(BUILDER_EBUSY_PREFIX) &&
    candidates.length === 1 &&
    candidates[0] === expected
  );
}

function defaultDelay(milliseconds) {
  return new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });
}

export async function runShellPackage({
  argv,
  builderCli = path.join(STUDIO_ROOT, "node_modules/electron-builder/cli.js"),
  delay = defaultDelay,
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
  const platformFlag = targetId === "linux-x64" ? "--linux" : "--win";
  let activeOutput = reboundOutput;
  let reservation;
  for (let attempt = 1; attempt <= MAX_BUILDER_ATTEMPTS; attempt += 1) {
    reservation = await reservationFactory(activeOutput, {
      pythonExecutable,
      repositoryRoot,
    });
    let builderResult;
    try {
      const packageEnvironment = {
        ...process.env,
        RWF_STUDIO_PACKAGE_OUTPUT: reservation.boundPath,
      };
      builderResult = await runner(
        nodeExecutable,
        [
          packageTool,
          "--dir",
          platformFlag,
          "--x64",
          "--publish=never",
          `--config.directories.output=${reservation.boundPath}`,
        ],
        {
          captureStdoutBytes: MAX_BUILDER_STDOUT_BYTES,
          cwd: STUDIO_ROOT,
          env: packageEnvironment,
        },
      );
    } catch (error) {
      await reservation.close();
      throw error;
    }
    if (runnerStatus(builderResult) === 0) {
      break;
    }
    const retryable =
      targetId === "win32-x64" &&
      attempt < MAX_BUILDER_ATTEMPTS &&
      isRetryableWindowsBuilderFailure(
        builderResult,
        reservation.boundPath,
      );
    await reservation.close();
    reservation = undefined;
    if (!retryable) {
      fail("shell_package_failed");
    }
    await delay(BUILDER_RETRY_DELAY_MS);
    activeOutput = await validatePackageOutput(
      `${canonicalOutput}.retry-${attempt + 1}`,
      { repositoryRoot },
    );
  }
  if (!reservation) {
    fail("shell_package_failed");
  }
  try {
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
    activeOutput,
    targetId === "linux-x64" ? "linux-unpacked" : "win-unpacked",
  );
  return Object.freeze({
    output_path: activeOutput,
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
