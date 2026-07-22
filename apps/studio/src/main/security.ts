import path from "node:path";

import type { Session, WebContents } from "electron";

export const STUDIO_SCHEME = "rwf-studio";
export const STUDIO_HOST = "app";
export const STUDIO_ORIGIN = `${STUDIO_SCHEME}://${STUDIO_HOST}`;
export const STUDIO_ENTRY_URL = `${STUDIO_ORIGIN}/index.html`;
export const CONTENT_SECURITY_POLICY =
  "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; " +
  "font-src 'self'; media-src 'self'; connect-src 'none'; object-src 'none'; " +
  "frame-src 'none'; base-uri 'none'; form-action 'none'";

interface SenderFrameLike {
  url: string;
}

interface IpcSenderLike {
  sender: unknown;
  senderFrame: SenderFrameLike | null;
}

interface ExpectedContentsLike {
  mainFrame: unknown;
}

export function isTrustedStudioSender(
  event: IpcSenderLike,
  expectedContents: ExpectedContentsLike,
): boolean {
  const senderFrame = event.senderFrame;
  if (
    !senderFrame ||
    event.sender !== expectedContents ||
    senderFrame !== expectedContents.mainFrame
  ) {
    return false;
  }
  return isStudioDocumentUrl(senderFrame.url);
}

export function isStudioDocumentUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (
      url.protocol === `${STUDIO_SCHEME}:` &&
      url.hostname === STUDIO_HOST &&
      url.username === "" &&
      url.password === "" &&
      url.port === ""
    );
  } catch {
    return false;
  }
}

export function resolveStudioResource(rendererRoot: string, requestUrl: string): string | null {
  let url: URL;
  try {
    url = new URL(requestUrl);
  } catch {
    return null;
  }
  if (
    url.protocol !== `${STUDIO_SCHEME}:` ||
    url.hostname !== STUDIO_HOST ||
    url.username !== "" ||
    url.password !== "" ||
    url.port !== ""
  ) {
    return null;
  }
  let decoded: string;
  try {
    decoded = decodeURIComponent(url.pathname);
  } catch {
    return null;
  }
  if (decoded.includes("\\") || containsControl(decoded)) {
    return null;
  }
  const relative = decoded === "/" ? "index.html" : decoded.replace(/^\/+/, "");
  const components = relative.split("/");
  if (
    components.some(
      (component) => component.length === 0 || component === "." || component === "..",
    )
  ) {
    return null;
  }
  const root = path.resolve(rendererRoot);
  const candidate = path.resolve(root, ...components);
  const relativeToRoot = path.relative(root, candidate);
  if (
    relativeToRoot === "" ||
    relativeToRoot === ".." ||
    relativeToRoot.startsWith(`..${path.sep}`) ||
    path.isAbsolute(relativeToRoot)
  ) {
    return null;
  }
  return candidate;
}

function containsControl(value: string): boolean {
  return [...value].some((character) => {
    const code = character.codePointAt(0);
    return code !== undefined && (code <= 0x1f || code === 0x7f);
  });
}

export function installSessionDenials(targetSession: Session): void {
  targetSession.setPermissionCheckHandler(() => false);
  targetSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false));
}

export function installWebContentsDenials(webContents: WebContents): void {
  webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  webContents.on("will-navigate", (event) => event.preventDefault());
  webContents.on("will-redirect", (event) => event.preventDefault());
  webContents.on("will-attach-webview", (event) => event.preventDefault());
}
