import path from "node:path";

export const STUDIO_PRODUCT_NAME = "World Forge Studio";
export const STUDIO_EXECUTABLE_NAME = "world-forge-studio";
export const LEGACY_STUDIO_APP_ID = "org.rpgworldforge.studio";
export const LEGACY_STUDIO_PROTOCOL = "rwf-studio";
export const LEGACY_STUDIO_USER_DATA_DIRECTORY = "RPG World Forge Studio";

interface ElectronApplicationPaths {
  getPath(name: "appData"): string;
  setPath(name: "userData", value: string): void;
}

export function legacyStudioUserDataPath(appDataPath: string): string {
  return path.join(appDataPath, LEGACY_STUDIO_USER_DATA_DIRECTORY);
}

export function pinLegacyStudioUserDataRoot(application: ElectronApplicationPaths): void {
  application.setPath(
    "userData",
    legacyStudioUserDataPath(application.getPath("appData")),
  );
}
