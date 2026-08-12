import path from "node:path";

import type {
    BrowserWindow,
    IpcMain,
    IpcMainEvent,
} from "electron";
import { BrowserWindow as ElectronBrowserWindow } from "electron";

import {
    buildAuthorityReviewModalOptions,
    validateAuthorityReviewReply,
    type AuthorityReviewReply,
} from "./creation-authority-actions";
import type { StudioAuthorityModalClient } from "./ipc";
import { installWebContentsDenials } from "./security";

const AUTHORITY_MODAL_PAYLOAD_CHANNEL = "studio:authority-modal-payload";
const AUTHORITY_MODAL_REPLY_CHANNEL = "studio:authority-modal-reply";

export function createStudioAuthorityModalClient(
    ipcMain: IpcMain,
): StudioAuthorityModalClient {
    return {
        requestReview: (parent, payload) =>
            openAuthorityModal(ipcMain, parent, payload),
    };
}

async function openAuthorityModal(
    ipcMain: IpcMain,
    parent: BrowserWindow,
    payload: {
        nonce: string;
        title: string;
        preview: {
            artifactId: string;
            subject: {
                format: string;
                formatVersion: number;
                id: string;
                contentHash: string;
            };
            mediaType: "image/png" | "audio/wav" | "text/plain";
            data: Uint8Array;
            sha256: string;
            byteLength: number;
        };
        criteria: readonly string[];
    },
): Promise<AuthorityReviewReply> {
    const modal = new ElectronBrowserWindow(
        buildAuthorityReviewModalOptions({
            parent,
            preloadPath: path.join(
                __dirname,
                "../authority-modal/preload.cjs",
            ),
        }),
    );
    installWebContentsDenials(modal.webContents);
    let settled = false;
    let resolveReply!: (reply: AuthorityReviewReply) => void;
    let rejectReply!: (error: Error) => void;
    const replyPromise = new Promise<AuthorityReviewReply>((resolve, reject) => {
        resolveReply = resolve;
        rejectReply = reject;
    });

    const fail = (message: string): void => {
        if (settled) return;
        settled = true;
        cleanup();
        rejectReply(new Error(message));
        if (!modal.isDestroyed()) modal.close();
    };
    const succeed = (reply: AuthorityReviewReply): void => {
        if (settled) {
            fail("Authority modal reply was duplicated");
            return;
        }
        settled = true;
        cleanup();
        resolveReply(reply);
        if (!modal.isDestroyed()) modal.close();
    };
    const onReply = (event: IpcMainEvent, value: unknown): void => {
        if (event.sender !== modal.webContents) {
            fail("Authority modal reply sender is invalid");
            return;
        }
        try {
            succeed(
                validateAuthorityReviewReply(value, {
                    expectedNonce: payload.nonce,
                    expectedDecisionCount: payload.criteria.length,
                }),
            );
        } catch (error) {
            fail(error instanceof Error ? error.message : "Authority modal reply is invalid");
        }
    };
    const cleanup = (): void => {
        ipcMain.off(AUTHORITY_MODAL_REPLY_CHANNEL, onReply);
        modal.removeAllListeners("closed");
        modal.webContents.removeAllListeners("will-navigate");
        modal.webContents.removeAllListeners("render-process-gone");
        modal.webContents.removeAllListeners("destroyed");
    };
    ipcMain.on(AUTHORITY_MODAL_REPLY_CHANNEL, onReply);
    modal.once("closed", () => fail("Authority modal was closed"));
    modal.webContents.on("will-navigate", (event) => {
        event.preventDefault();
        fail("Authority modal navigation is denied");
    });
    modal.webContents.on("render-process-gone", () =>
        fail("Authority modal crashed"),
    );
    modal.webContents.on("destroyed", () => fail("Authority modal was destroyed"));
    await modal.loadFile(path.join(__dirname, "../authority-modal/index.html"));
    modal.webContents.send(AUTHORITY_MODAL_PAYLOAD_CHANNEL, {
        nonce: payload.nonce,
        title: payload.title,
        preview: {
            ...payload.preview,
            data: new Uint8Array(payload.preview.data),
        },
        criteria: [...payload.criteria],
    });
    modal.show();
    return await replyPromise;
}
