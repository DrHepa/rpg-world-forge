import { describe, expect, it, vi } from "vitest";

import { createStudioApi, type PreloadTransport } from "../../src/preload/api";
import { IPC_CHANNELS } from "../../src/shared/studio-api";

describe("creation preview preload API", () => {
    it("exposes fixed pathless calls without arbitrary transport access", async () => {
        const invoke = vi.fn().mockResolvedValue({ ok: true, value: {} });
        const transport: PreloadTransport = {
            invoke,
            on: vi.fn(),
            removeListener: vi.fn(),
        };
        const api = createStudioApi(transport);
        const authority = {
            workspaceId: "workspace_01",
            expectedRootGeneration: 3,
            expectedSourceRevision: "a".repeat(64),
            expectedWorkflowStatusHash: null,
            expectedArtifactSnapshotHash: "b".repeat(64),
            assetpackArtifactId: "artifact_assetpack",
            outputGrantId: "grant_assetpack",
            expectedOutputGrantGeneration: 2,
            assetId: "board_ui",
        };

        await api.openCreationPreview(authority);
        await api.readCreationPreviewChunk("C".repeat(43), 0);
        await api.closeCreationPreview("C".repeat(43));

        expect(invoke.mock.calls).toEqual([
            [IPC_CHANNELS.openCreationPreview, authority],
            [
                IPC_CHANNELS.readCreationPreviewChunk,
                { handle: "C".repeat(43), sequence: 0 },
            ],
            [IPC_CHANNELS.closeCreationPreview, { handle: "C".repeat(43) }],
        ]);
        expect(api).not.toHaveProperty("readCreationPreviewRange");
        expect(api).not.toHaveProperty("openCreationPreviewPath");
        expect(api).not.toHaveProperty("request");
    });
});
