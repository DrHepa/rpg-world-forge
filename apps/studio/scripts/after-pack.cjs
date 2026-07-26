"use strict";

const path = require("node:path");
const {
  flipFuses,
  FuseVersion,
  FuseV1Options,
} = require("@electron/fuses");

module.exports = async function applyStudioFuses(context) {
  const platform = context.electronPlatformName;
  const productName = context.packager.appInfo.productFilename;
  let executablePath;

  if (platform === "darwin") {
    executablePath = path.join(
      context.appOutDir,
      `${productName}.app`,
      "Contents",
      "MacOS",
      productName,
    );
  } else if (platform === "win32") {
    executablePath = path.join(context.appOutDir, `${productName}.exe`);
  } else {
    const executableName = context.packager.executableName || productName;
    executablePath = path.join(context.appOutDir, executableName);
  }

  await flipFuses(executablePath, {
    version: FuseVersion.V1,
    [FuseV1Options.RunAsNode]: false,
    [FuseV1Options.EnableCookieEncryption]: true,
    [FuseV1Options.EnableNodeOptionsEnvironmentVariable]: false,
    [FuseV1Options.EnableNodeCliInspectArguments]: false,
    [FuseV1Options.EnableEmbeddedAsarIntegrityValidation]: true,
    [FuseV1Options.OnlyLoadAppFromAsar]: true,
    [FuseV1Options.GrantFileProtocolExtraPrivileges]: false,
  });
};
