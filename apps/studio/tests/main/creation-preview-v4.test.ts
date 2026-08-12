import { describe, expect, it } from "vitest";

import {
    validateCreationPreviewCloseArgument,
    validateCreationPreviewOpenArgument,
    validateCreationPreviewReadArgument,
} from "../../src/main/ipc";
import { STUDIO_V4_METHODS } from "../../src/shared/studio-api";

const hash = "a".repeat(64);
const handle = "B".repeat(43);
const authority = {
    workspaceId: "workspace_01",
    expectedRootGeneration: 3,
    expectedSourceRevision: hash,
    expectedWorkflowStatusHash: null,
    expectedArtifactSnapshotHash: hash,
    assetpackArtifactId: "artifact_assetpack",
    outputGrantId: "grant_assetpack",
    expectedOutputGrantGeneration: 2,
    assetId: "board_ui",
};

describe("Studio v4 creation preview boundary", () => {
    it("exposes only fixed pathless open, sequential read, and close arguments", () => {
        expect(validateCreationPreviewOpenArgument(authority)).toEqual(
            authority,
        );
        expect(
            validateCreationPreviewReadArgument({ handle, sequence: 1023 }),
        ).toEqual({
            handle,
            sequence: 1023,
        });
        expect(validateCreationPreviewCloseArgument({ handle })).toEqual({
            handle,
        });
        expect(() =>
            validateCreationPreviewOpenArgument({
                ...authority,
                path: "/private/assets/board.png",
            }),
        ).toThrow();
        expect(() =>
            validateCreationPreviewOpenArgument({ ...authority, offset: 0 }),
        ).toThrow();
        expect(() =>
            validateCreationPreviewReadArgument({ handle, sequence: 1024 }),
        ).toThrow();
        expect(() =>
            validateCreationPreviewReadArgument({
                handle,
                sequence: 0,
                bytes: 1,
            }),
        ).toThrow();
        expect(() =>
            validateCreationPreviewCloseArgument({ handle, force: true }),
        ).toThrow();
        for (const method of [
            "creation_preview.open",
            "creation_preview.read",
            "creation_preview.close",
        ] as const) {
            expect(STUDIO_V4_METHODS.has(method)).toBe(true);
        }
    });
});
