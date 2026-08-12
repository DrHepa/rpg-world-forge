import { contextBridge, ipcRenderer } from "electron";

const PAYLOAD_CHANNEL = "studio:authority-modal-payload";
const REPLY_CHANNEL = "studio:authority-modal-reply";

contextBridge.exposeInMainWorld("worldForgeAuthorityModal", {
    onPayload(callback: (payload: unknown) => void): void {
        ipcRenderer.once(PAYLOAD_CHANNEL, (_event, payload) => callback(payload));
    },
    reply(payload: unknown): void {
        ipcRenderer.send(REPLY_CHANNEL, payload);
    },
});
