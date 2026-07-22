import { contextBridge, ipcRenderer } from "electron";

import { createStudioApi } from "./api";

contextBridge.exposeInMainWorld("forgeStudio", createStudioApi(ipcRenderer));
