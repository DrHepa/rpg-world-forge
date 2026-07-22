import path from "node:path";

import {
  FuseState,
  FuseV1Options,
  getCurrentFuseWire,
} from "@electron/fuses";

const executable = process.argv[2];
if (!executable || !path.isAbsolute(executable)) {
  throw new Error("Usage: npm run verify:fuses -- /absolute/path/to/electron-executable");
}

const expected = new Map([
  [FuseV1Options.RunAsNode, FuseState.DISABLE],
  [FuseV1Options.EnableCookieEncryption, FuseState.ENABLE],
  [FuseV1Options.EnableNodeOptionsEnvironmentVariable, FuseState.DISABLE],
  [FuseV1Options.EnableNodeCliInspectArguments, FuseState.DISABLE],
  [FuseV1Options.EnableEmbeddedAsarIntegrityValidation, FuseState.ENABLE],
  [FuseV1Options.OnlyLoadAppFromAsar, FuseState.ENABLE],
  [FuseV1Options.GrantFileProtocolExtraPrivileges, FuseState.DISABLE],
]);

const wire = await getCurrentFuseWire(executable);
const failures = [];
for (const [option, state] of expected) {
  if (wire[option] !== state) {
    failures.push(`${FuseV1Options[option]}=${String(wire[option])}, expected ${String(state)}`);
  }
}
if (failures.length > 0) {
  throw new Error(`Packaged Electron fuse verification failed: ${failures.join("; ")}`);
}

console.log(`Verified ${String(expected.size)} hardened Electron fuses in ${executable}`);
