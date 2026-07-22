import { mkdir } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

import {
  app,
  BrowserWindow,
  ipcMain,
  net,
  protocol,
  session,
} from "electron";

import { ForgeServiceSupervisor, UnavailableForgeService, type ForgeServiceClient } from "./forge-service";
import { registerStudioIpc } from "./ipc";
import { resolveForgeServiceLaunch } from "./runtime-manifest";
import {
  CONTENT_SECURITY_POLICY,
  installSessionDenials,
  installWebContentsDenials,
  resolveStudioResource,
  STUDIO_ENTRY_URL,
  STUDIO_SCHEME,
} from "./security";

protocol.registerSchemesAsPrivileged([
  {
    scheme: STUDIO_SCHEME,
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      corsEnabled: false,
      stream: true,
    },
  },
]);

app.enableSandbox();

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  void startApplication();
}

async function startApplication(): Promise<void> {
  await app.whenReady();
  installSessionDenials(session.defaultSession);
  registerRendererProtocol();

  const dataDir = path.join(app.getPath("userData"), "service");
  await mkdir(dataDir, { recursive: true });
  const service = await createForgeService(dataDir);
  const window = createMainWindow();
  const unregisterIpc = registerStudioIpc(ipcMain, window, service);
  await window.loadURL(STUDIO_ENTRY_URL);

  let shuttingDown = false;
  app.on("before-quit", (event) => {
    if (shuttingDown) {
      return;
    }
    event.preventDefault();
    shuttingDown = true;
    unregisterIpc();
    void service.stop().finally(() => app.quit());
  });

  app.on("window-all-closed", () => app.quit());
}

async function createForgeService(dataDir: string): Promise<ForgeServiceClient> {
  try {
    const spec = await resolveForgeServiceLaunch({
      packaged: app.isPackaged,
      resourcesPath: process.resourcesPath,
      dataDir,
    });
    return new ForgeServiceSupervisor(spec);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Forge Studio runtime is unavailable";
    return new UnavailableForgeService(message);
  }
}

function createMainWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 860,
    minHeight: 620,
    show: false,
    title: "RPG World Forge Studio",
    backgroundColor: "#11151c",
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "../preload/index.cjs"),
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: true,
      allowRunningInsecureContent: false,
      webviewTag: false,
      spellcheck: false,
      safeDialogs: true,
    },
  });
  installWebContentsDenials(window.webContents);
  window.once("ready-to-show", () => window.show());
  return window;
}

function registerRendererProtocol(): void {
  const rendererRoot = path.join(app.getAppPath(), "dist-renderer");
  protocol.handle(STUDIO_SCHEME, async (request) => {
    const resource = resolveStudioResource(rendererRoot, request.url);
    if (!resource) {
      return new Response("Not found", { status: 404 });
    }
    try {
      const response = await net.fetch(pathToFileURL(resource).toString());
      if (!response.ok) {
        return new Response("Not found", { status: 404 });
      }
      const headers = new Headers(response.headers);
      headers.set("Content-Security-Policy", CONTENT_SECURITY_POLICY);
      headers.set("Cross-Origin-Opener-Policy", "same-origin");
      headers.set("Cross-Origin-Resource-Policy", "same-origin");
      headers.set("X-Content-Type-Options", "nosniff");
      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers,
      });
    } catch {
      return new Response("Not found", { status: 404 });
    }
  });
}
