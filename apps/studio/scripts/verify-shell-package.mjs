import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  ShellPackageError,
  verifyPackagedShell,
} from "./shell-package-verifier.mjs";

export function parseShellPackageArguments(argv) {
  if (
    argv.length !== 4 ||
    argv[0] !== "--path" ||
    argv[2] !== "--target" ||
    !path.isAbsolute(argv[1]) ||
    path.normalize(argv[1]) !== argv[1] ||
    !["linux-x64", "win32-x64"].includes(argv[3])
  ) {
    throw new ShellPackageError(
      "invalid_arguments",
      "Usage: node scripts/verify-shell-package.mjs --path /absolute/package --target linux-x64|win32-x64",
    );
  }
  return { outputPath: argv[1], targetId: argv[3] };
}

export async function run(argv = process.argv.slice(2)) {
  const options = parseShellPackageArguments(argv);
  const result = await verifyPackagedShell(options);
  process.stdout.write(
    `${JSON.stringify({
      package_kind: result.package_kind,
      redistribution_status: result.redistribution_status,
      release_ready: result.release_ready,
      status: "verified",
      target_id: result.target_id,
      verified_files: result.verified_files,
    })}\n`,
  );
}

const invokedPath = process.argv[1]
  ? path.resolve(process.argv[1])
  : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  try {
    await run();
  } catch (error) {
    const code =
      error instanceof ShellPackageError
        ? error.code
        : "shell_package_verification_failed";
    process.stderr.write(`Studio shell package verification failed: ${code}\n`);
    process.exitCode = 1;
  }
}
