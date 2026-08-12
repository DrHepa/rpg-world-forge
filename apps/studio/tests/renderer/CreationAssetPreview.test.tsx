// @vitest-environment jsdom
/* eslint-disable @typescript-eslint/unbound-method */

import { createHash, webcrypto } from "node:crypto";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CreationAssetPreview } from "../../src/renderer/CreationAssetPreview";
import type { CreationPreviewCandidate } from "../../src/renderer/creation-preview-state";
import type { ForgeStudioApi } from "../../src/shared/studio-api";

const SOURCE = "a".repeat(64);
const SNAPSHOT = "b".repeat(64);
const HANDLE_A = "A".repeat(43);
const HANDLE_B = "B".repeat(43);

beforeEach(() => {
  Object.defineProperty(globalThis, "crypto", {
    configurable: true,
    value: webcrypto,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("CreationAssetPreview", () => {
  it("streams a PNG, verifies Web Crypto, closes before Blob publication, and renders exact metadata", async () => {
    const payload = new Uint8Array([1, 2, 3, 4]);
    const selected = candidate("board_ui", "image/png", "texture");
    const order: string[] = [];
    const createObjectURL = vi.fn((blob: Blob) => {
      order.push("blob");
      expect(blob.type).toBe("image/png");
      expect(blob.size).toBe(payload.byteLength);
      return "blob:verified-png";
    });
    const revokeObjectURL = vi.fn();
    stubObjectUrls(createObjectURL, revokeObjectURL);
    const api = previewApi({
      openCreationPreview: vi.fn().mockResolvedValue(
        openReply(selected, HANDLE_A, payload, {
          kind: "png",
          width: 16,
          height: 9,
          mode: "rgba8",
        }),
      ),
      readCreationPreviewChunk: vi.fn().mockResolvedValue(
        readReply(HANDLE_A, 0, payload, true),
      ),
      closeCreationPreview: vi.fn().mockImplementation((handle: string) => {
        order.push("close");
        return Promise.resolve(closeReply(handle));
      }),
    });

    render(<CreationAssetPreview api={api} authorityKey="authority-a" items={[selected]} />);
    fireEvent.click(screen.getByRole("button", { name: "Open verified preview" }));

    const image = await screen.findByRole("img", {
      name: "Verified PNG preview for board_ui, 16 by 9 pixels",
    });
    expect(image).toHaveAttribute("src", "blob:verified-png");
    expect(image).toHaveAttribute("width", "16");
    expect(image).toHaveAttribute("height", "9");
    expect(order).toEqual(["close", "blob"]);
    expect(api.openCreationPreview).toHaveBeenCalledWith({
      workspaceId: "creation_workspace",
      expectedRootGeneration: 4,
      expectedSourceRevision: SOURCE,
      expectedWorkflowStatusHash: null,
      expectedArtifactSnapshotHash: SNAPSHOT,
      assetpackArtifactId: "artifact_assetpack",
      outputGrantId: "grant_assetpack",
      expectedOutputGrantGeneration: 2,
      assetId: "board_ui",
    });
    expect(screen.getByRole("status")).toHaveTextContent("Verified preview ready");
    expect(revokeObjectURL).not.toHaveBeenCalled();
  });

  it("streams WAV with an accessible native player and reports exact byte progress", async () => {
    const payload = new Uint8Array([5, 6, 7, 8, 9, 10]);
    const selected = candidate("choice_audio", "audio/wav", "audio");
    let resolveRead: ((value: ReturnType<typeof readReply>) => void) | undefined;
    const read = new Promise<ReturnType<typeof readReply>>((resolve) => {
      resolveRead = resolve;
    });
    stubObjectUrls(vi.fn().mockReturnValue("blob:verified-wav"), vi.fn());
    const api = previewApi({
      openCreationPreview: vi.fn().mockResolvedValue(
        openReply(selected, HANDLE_A, payload, {
          kind: "wav_pcm16",
          channels: 2,
          sample_rate: 48_000,
          frames: 96_000,
          sample_width: 2,
        }),
      ),
      readCreationPreviewChunk: vi.fn().mockReturnValue(read),
      closeCreationPreview: vi.fn().mockResolvedValue(closeReply(HANDLE_A)),
    });

    render(<CreationAssetPreview api={api} authorityKey="authority-a" items={[selected]} />);
    fireEvent.click(screen.getByRole("button", { name: "Open verified preview" }));
    expect(await screen.findByText("Reading verified preview… 0 of 6 bytes")).toBeInTheDocument();
    expect(screen.getByLabelText("Verified preview byte progress")).toHaveAttribute("max", "6");
    expect(screen.getByLabelText("Verified preview byte progress")).toHaveAttribute("value", "0");
    resolveRead?.(readReply(HANDLE_A, 0, payload, true));

    const audio = await screen.findByLabelText("Verified WAV preview for choice_audio");
    expect(audio.tagName).toBe("AUDIO");
    expect(audio).toHaveAttribute("controls");
    expect(audio).toHaveAttribute("src", "blob:verified-wav");
  });

  it("never publishes media when the final digest or close result is invalid", async () => {
    const payload = new Uint8Array([1, 2, 3, 4]);
    const selected = candidate("board_ui", "image/png", "texture");
    const createObjectURL = vi.fn().mockReturnValue("blob:must-not-publish");
    stubObjectUrls(createObjectURL, vi.fn());
    const wrongDigestOpen = openReply(selected, HANDLE_A, payload, pngMetadata());
    if (wrongDigestOpen.ok) {
      (wrongDigestOpen.value as unknown as { result: { preview: { sha256: string } } }).result.preview.sha256 =
        "f".repeat(64);
    }
    const hashApi = previewApi({
      openCreationPreview: vi.fn().mockResolvedValue(wrongDigestOpen),
      readCreationPreviewChunk: vi.fn().mockResolvedValue(
        readReply(HANDLE_A, 0, payload, true, "f".repeat(64)),
      ),
      closeCreationPreview: vi.fn().mockResolvedValue(closeReply(HANDLE_A)),
    });
    const first = render(
      <CreationAssetPreview api={hashApi} authorityKey="authority-a" items={[selected]} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Open verified preview" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/SHA-256/iu);
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(hashApi.closeCreationPreview).toHaveBeenCalledWith(HANDLE_A);
    first.unmount();

    const closeApi = previewApi({
      openCreationPreview: vi.fn().mockResolvedValue(
        openReply(selected, HANDLE_B, payload, pngMetadata()),
      ),
      readCreationPreviewChunk: vi.fn().mockResolvedValue(
        readReply(HANDLE_B, 0, payload, true),
      ),
      closeCreationPreview: vi.fn().mockResolvedValue(clientError("conflict", "close failed")),
    });
    render(<CreationAssetPreview api={closeApi} authorityKey="authority-a" items={[selected]} />);
    fireEvent.click(screen.getByRole("button", { name: "Open verified preview" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/lease could not be closed/iu);
    expect(
      screen.queryByRole("button", { name: "Retry verified preview" }),
    ).not.toBeInTheDocument();
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  it("closes stale authority and unmounted opens without resurrecting media", async () => {
    const firstCandidate = candidate("board_ui", "image/png", "texture");
    const nextCandidate = candidate("choice_audio", "audio/wav", "audio", {
      artifactSnapshotHash: "f".repeat(64),
      outputGrantGeneration: 3,
    });
    let resolveOpen: ((value: ReturnType<typeof openReply>) => void) | undefined;
    const pendingOpen = new Promise<ReturnType<typeof openReply>>((resolve) => {
      resolveOpen = resolve;
    });
    const createObjectURL = vi.fn().mockReturnValue("blob:stale");
    stubObjectUrls(createObjectURL, vi.fn());
    const api = previewApi({
      openCreationPreview: vi.fn().mockReturnValue(pendingOpen),
      closeCreationPreview: vi.fn().mockResolvedValue(closeReply(HANDLE_A)),
    });
    const view = render(
      <CreationAssetPreview api={api} authorityKey="authority-a" items={[firstCandidate]} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Open verified preview" }));
    await waitFor(() => expect(api.openCreationPreview).toHaveBeenCalledTimes(1));
    view.rerender(
      <CreationAssetPreview api={api} authorityKey="authority-b" items={[nextCandidate]} />,
    );
    resolveOpen?.(openReply(firstCandidate, HANDLE_A, new Uint8Array([1]), pngMetadata()));
    await waitFor(() => expect(api.closeCreationPreview).toHaveBeenCalledWith(HANDLE_A));
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Verified sealed asset")).toHaveValue(nextCandidate.key);
    view.unmount();

    let resolveUnmounted: ((value: ReturnType<typeof openReply>) => void) | undefined;
    const unmountedOpen = new Promise<ReturnType<typeof openReply>>((resolve) => {
      resolveUnmounted = resolve;
    });
    const unmountApi = previewApi({
      openCreationPreview: vi.fn().mockReturnValue(unmountedOpen),
      closeCreationPreview: vi.fn().mockResolvedValue(closeReply(HANDLE_B)),
    });
    const unmounted = render(
      <CreationAssetPreview api={unmountApi} authorityKey="authority-c" items={[firstCandidate]} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Open verified preview" }));
    await waitFor(() => expect(unmountApi.openCreationPreview).toHaveBeenCalledTimes(1));
    unmounted.unmount();
    resolveUnmounted?.(openReply(firstCandidate, HANDLE_B, new Uint8Array([1]), pngMetadata()));
    await waitFor(() => expect(unmountApi.closeCreationPreview).toHaveBeenCalledWith(HANDLE_B));
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  it("rejects a reused opaque handle on retry and never runs concurrent reads", async () => {
    const payload = new Uint8Array([1, 2, 3, 4]);
    const selected = candidate("board_ui", "image/png", "texture");
    stubObjectUrls(vi.fn(), vi.fn());
    const wrongHash = "f".repeat(64);
    const open = openReply(selected, HANDLE_A, payload, pngMetadata());
    if (open.ok) {
      (open.value as unknown as { result: { preview: { sha256: string } } }).result.preview.sha256 = wrongHash;
    }
    const api = previewApi({
      openCreationPreview: vi.fn().mockResolvedValue(open),
      readCreationPreviewChunk: vi.fn().mockResolvedValue(
        readReply(HANDLE_A, 0, payload, true, wrongHash),
      ),
      closeCreationPreview: vi.fn().mockResolvedValue(closeReply(HANDLE_A)),
    });
    render(<CreationAssetPreview api={api} authorityKey="authority-a" items={[selected]} />);
    fireEvent.click(screen.getByRole("button", { name: "Open verified preview" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Retry verified preview" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/reused/iu));
    expect(api.readCreationPreviewChunk).toHaveBeenCalledTimes(1);
    expect(api.openCreationPreview).toHaveBeenCalledTimes(2);
    expect(api.closeCreationPreview).toHaveBeenCalledTimes(2);
  });

  it("revokes each published Blob URL exactly once on replacement and unmount", async () => {
    const payload = new Uint8Array([1, 2, 3, 4]);
    const firstCandidate = candidate("board_ui", "image/png", "texture");
    const nextCandidate = candidate("other_ui", "image/png", "texture", {
      selectedCandidateId: "other_png",
    });
    const revokeObjectURL = vi.fn();
    stubObjectUrls(vi.fn().mockReturnValue("blob:verified-once"), revokeObjectURL);
    const api = previewApi({
      openCreationPreview: vi.fn().mockResolvedValue(
        openReply(firstCandidate, HANDLE_A, payload, pngMetadata()),
      ),
      readCreationPreviewChunk: vi.fn().mockResolvedValue(
        readReply(HANDLE_A, 0, payload, true),
      ),
      closeCreationPreview: vi.fn().mockResolvedValue(closeReply(HANDLE_A)),
    });
    const view = render(
      <CreationAssetPreview api={api} authorityKey="authority-a" items={[firstCandidate]} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Open verified preview" }));
    await screen.findByRole("img");
    view.rerender(
      <CreationAssetPreview api={api} authorityKey="authority-a" items={[nextCandidate]} />,
    );
    await waitFor(() => expect(revokeObjectURL).toHaveBeenCalledTimes(1));
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:verified-once");
    view.unmount();
    expect(revokeObjectURL).toHaveBeenCalledTimes(1);
  });

  it("hides ready media as soon as its authority becomes stale and revokes its URL once", async () => {
    const payload = new Uint8Array([1, 2, 3, 4]);
    const selected = candidate("board_ui", "image/png", "texture");
    const revokeObjectURL = vi.fn();
    stubObjectUrls(vi.fn().mockReturnValue("blob:stale-ready"), revokeObjectURL);
    const api = previewApi({
      openCreationPreview: vi.fn().mockResolvedValue(
        openReply(selected, HANDLE_A, payload, pngMetadata()),
      ),
      readCreationPreviewChunk: vi.fn().mockResolvedValue(
        readReply(HANDLE_A, 0, payload, true),
      ),
      closeCreationPreview: vi.fn().mockResolvedValue(closeReply(HANDLE_A)),
    });
    const view = render(
      <CreationAssetPreview api={api} authorityKey="authority-a" items={[selected]} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Open verified preview" }));
    await screen.findByRole("img");

    view.rerender(
      <CreationAssetPreview api={api} authorityKey="authority-b" items={[]} />,
    );

    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Verified sealed asset")).not.toBeInTheDocument();
    await waitFor(() => expect(revokeObjectURL).toHaveBeenCalledTimes(1));
    view.unmount();
    expect(revokeObjectURL).toHaveBeenCalledTimes(1);
  });

  it("lists unsupported formats without opening them and exposes accessible controls", () => {
    const model = candidate("board_model", "model/gltf-binary", "model", {
      eligible: false,
      unsupportedReason: "model/gltf-binary preview is not supported; only PNG and WAV can be opened.",
    });
    const api = previewApi({ openCreationPreview: vi.fn() });
    render(<CreationAssetPreview api={api} authorityKey="authority-a" items={[model]} />);

    expect(screen.getByRole("region", { name: "Sealed asset previews" })).toHaveAttribute(
      "aria-busy",
      "false",
    );
    expect(screen.getByText("board_model")).toBeInTheDocument();
    expect(screen.getByText(/model\/gltf-binary preview is not supported/iu)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /open verified preview/iu })).not.toBeInTheDocument();
    expect(api.openCreationPreview).not.toHaveBeenCalled();
  });
});

function previewApi(overrides: Partial<ForgeStudioApi>): ForgeStudioApi {
  return {
    openCreationPreview: vi.fn(),
    readCreationPreviewChunk: vi.fn(),
    closeCreationPreview: vi.fn(),
    ...overrides,
  } as unknown as ForgeStudioApi;
}

function candidate(
  assetId: string,
  mediaType: string,
  role: string,
  overrides: {
    artifactSnapshotHash?: string;
    outputGrantGeneration?: number;
    selectedCandidateId?: string;
    eligible?: boolean;
    unsupportedReason?: string | null;
  } = {},
): CreationPreviewCandidate {
  const value: CreationPreviewCandidate = {
    key: "",
    workspaceId: "creation_workspace",
    rootGeneration: 4,
    sourceRevision: SOURCE,
    workflowStatusHash: null,
    artifactSnapshotHash: overrides.artifactSnapshotHash ?? SNAPSHOT,
    assetpackArtifactId: "artifact_assetpack",
    assetpackId: "puzzle_assetpack",
    assetpackContentHash: "c".repeat(64),
    outputGrantId: "grant_assetpack",
    outputGrantGeneration: overrides.outputGrantGeneration ?? 2,
    sealJobId: "job_seal",
    assetId,
    mediaType,
    selectedOutput: {
      candidateArtifactId: overrides.selectedCandidateId ?? `${assetId}_candidate`,
      role,
      mediaType,
    },
    eligible: overrides.eligible ?? (mediaType === "image/png" || mediaType === "audio/wav"),
    unsupportedReason: overrides.unsupportedReason ?? null,
  };
  value.key = JSON.stringify([
    value.artifactSnapshotHash,
    value.outputGrantGeneration,
    value.assetId,
    value.selectedOutput.candidateArtifactId,
  ]);
  return value;
}

function openReply(
  selected: CreationPreviewCandidate,
  handle: string,
  payload: Uint8Array,
  metadata: Record<string, unknown>,
) {
  return v4("creation_preview.open", {
    preview: {
      format: "world-forge.studio_creation_preview",
      format_version: 1,
      handle,
      workspace_id: selected.workspaceId,
      assetpack_artifact_id: selected.assetpackArtifactId,
      output_grant_id: selected.outputGrantId,
      output_grant_generation: selected.outputGrantGeneration,
      asset_id: selected.assetId,
      media_type: selected.mediaType,
      byte_length: payload.byteLength,
      sha256: digest(payload),
      chunk_bytes: 65_536,
      metadata,
    },
  });
}

function readReply(
  handle: string,
  sequence: number,
  payload: Uint8Array,
  eof: boolean,
  cumulativeSha256 = digest(payload),
) {
  return v4("creation_preview.read", {
    handle,
    sequence,
    data_base64: Buffer.from(payload).toString("base64"),
    byte_length: payload.byteLength,
    cumulative_bytes: payload.byteLength,
    cumulative_sha256: cumulativeSha256,
    eof,
  });
}

function closeReply(handle: string) {
  return v4("creation_preview.close", { handle, closed: true });
}

function clientError(code: string, message: string) {
  return { ok: false as const, error: { code, message } };
}

function v4(method: string, result: Record<string, unknown>) {
  return {
    ok: true as const,
    value: {
      protocol: "rpg-world-forge.studio_protocol" as const,
      protocol_version: 4 as const,
      kind: "response" as const,
      request_id: "preview_request",
      method,
      result,
    },
  };
}

function digest(payload: Uint8Array): string {
  return createHash("sha256").update(payload).digest("hex");
}

function pngMetadata() {
  return { kind: "png", width: 4, height: 4, mode: "rgba8" };
}

function stubObjectUrls(
  createObjectURL: ReturnType<typeof vi.fn>,
  revokeObjectURL: ReturnType<typeof vi.fn>,
): void {
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: createObjectURL,
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: revokeObjectURL,
  });
}
