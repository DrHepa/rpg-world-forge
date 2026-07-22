import type { ForgeStudioApi } from "../shared/studio-api";

declare global {
  interface Window {
    forgeStudio: ForgeStudioApi;
  }
}

export {};
