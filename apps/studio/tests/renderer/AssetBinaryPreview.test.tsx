// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssetBinaryPreview } from "../../src/renderer/AssetBinaryPreview";
import { AssetInspection } from "../../src/renderer/AssetInspection";
import {
  ASSET_PREVIEW_CHUNK_BYTES,
  ASSET_PREVIEW_MAX_BYTES,
  type AssetBinaryPreviewContext,
} from "../../src/renderer/asset-binary-preview-state";
import type {
  ForgeStudioApi,
  StudioAssetCatalogEntry,
  StudioAssetInspection,
} from "../../src/shared/studio-api";

const ENTRY_ID = `asset_${"1".repeat(64)}`;
const SECOND_ENTRY_ID = `asset_${"2".repeat(64)}`;
const HANDLE = "H".repeat(43);
const REVISION = "a".repeat(64);
const SECOND_REVISION = "c".repeat(64);
const SHA256 = "b".repeat(64);
const BYTES = new Uint8Array([1, 2, 3, 4]);

const originalCreateObjectUrl = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
const originalRevokeObjectUrl = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");

let createObjectURL: ReturnType<typeof vi.fn>;
let revokeObjectURL: ReturnType<typeof vi.fn>;

beforeEach(() => {
  createObjectURL = vi.fn().mockReturnValue("blob:verified-preview");
  revokeObjectURL = vi.fn();
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: createObjectURL,
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: revokeObjectURL,
  });
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => undefined);
  vi
    .spyOn(HTMLMediaElement.prototype, "play")
    .mockImplementation(() => Promise.resolve());
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  Reflect.deleteProperty(window, "forgeStudio");
  restoreProperty(URL, "createObjectURL", originalCreateObjectUrl);
  restoreProperty(URL, "revokeObjectURL", originalRevokeObjectUrl);
});

describe("AssetBinaryPreview", () => {
  it("streams PNG bytes sequentially, closes before publication, and replaces URLs by identity", async () => {
    const order: string[] = [];
    createObjectURL
      .mockImplementationOnce((blob: Blob) => {
        order.push(`url:${blob.type}:${String(blob.size)}`);
        return "blob:preview-1";
      })
      .mockImplementationOnce((blob: Blob) => {
        order.push(`url:${blob.type}:${String(blob.size)}`);
        return "blob:preview-2";
      });
    revokeObjectURL.mockImplementation((url: string) => order.push(`revoke:${url}`));
    const api = installPreviewApi({
      open: vi.fn(() => {
        order.push("open");
        return Promise.resolve(openReply());
      }),
      read: vi.fn((_handle: string, sequence: number) => {
        order.push(`read:${String(sequence)}`);
        return Promise.resolve(readReply());
      }),
      close: vi.fn(() => {
        order.push("close");
        return Promise.resolve(closeReply());
      }),
    });
    const view = render(
      <AssetBinaryPreview
        context={context()}
        entry={entry()}
        inspection={pngInspection()}
      />,
    );

    const image = await screen.findByRole("img", {
      name: "Verified PNG preview for asset_01, 64 by 32 pixels",
    });
    expect(image).toHaveAttribute("src", "blob:preview-1");
    expect(image).toHaveAttribute("width", "64");
    expect(image).toHaveAttribute("height", "32");
    expect(api.open).toHaveBeenCalledWith("workspace_01", REVISION, ENTRY_ID);
    expect(api.read).toHaveBeenCalledWith(HANDLE, 0);
    expect(api.close).toHaveBeenCalledWith(HANDLE);
    expect(order.slice(0, 4)).toEqual([
      "open",
      "read:0",
      "close",
      "url:image/png:4",
    ]);

    view.rerender(
      <AssetBinaryPreview
        context={context()}
        entry={entry({ category: "processing_output" })}
        inspection={pngInspection()}
      />,
    );
    await waitFor(() =>
      expect(
        screen.getByRole("img", { name: /Verified PNG preview/u }),
      ).toHaveAttribute("src", "blob:preview-2"),
    );
    expect(order.indexOf("revoke:blob:preview-1")).toBeLessThan(
      order.lastIndexOf("url:image/png:4"),
    );

    view.unmount();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:preview-2");
  });

  it.each([
    ["PNG tab inactive", "png", context({ active: false }), entry(), pngInspection()],
    [
      "PNG catalog noncurrent",
      "png",
      context({ catalogCurrent: false }),
      entry(),
      pngInspection(),
    ],
    [
      "PNG workspace",
      "png",
      context({ workspaceId: "workspace_02" }),
      entry(),
      pngInspection(),
    ],
    ["PNG generation", "png", context({ generation: 8 }), entry(), pngInspection()],
    [
      "PNG revision",
      "png",
      context({ manifestRevision: SECOND_REVISION }),
      entry(),
      pngInspection(),
    ],
    [
      "PNG same-kind entry",
      "png",
      context(),
      entry({ entry_id: SECOND_ENTRY_ID }),
      pngInspection(),
    ],
    [
      "PNG same-kind category",
      "png",
      context(),
      entry({ category: "processing_output" }),
      pngInspection(),
    ],
    [
      "PNG kind",
      "png",
      context(),
      entry({ media_type: "audio/wav", path: "assets/sound.wav" }),
      wavInspection(),
    ],
    [
      "PNG MIME disagreement",
      "png",
      context(),
      entry({ media_type: "audio/wav" }),
      pngInspection(),
    ],
    ["WAV tab inactive", "wav", context({ active: false }), audioEntry(), wavInspection()],
    [
      "WAV same-kind entry",
      "wav",
      context(),
      audioEntry({ entry_id: SECOND_ENTRY_ID }),
      wavInspection(),
    ],
    [
      "WAV same-kind revision",
      "wav",
      context({ manifestRevision: SECOND_REVISION }),
      audioEntry(),
      wavInspection(),
    ],
    [
      "WAV same-kind category",
      "wav",
      context(),
      audioEntry({ category: "runtime_output" }),
      wavInspection(),
    ],
  ] as const)(
    "synchronously removes stale %s media before transition cleanup",
    async (_label, initialKind, nextContext, nextEntry, nextInspection) => {
      const mediaType = initialKind === "png" ? "image/png" : "audio/wav";
      const neverOpen = deferred<ReturnType<typeof openReply>>();
      const open = vi
        .fn()
        .mockResolvedValueOnce(openReply({ mediaType }))
        .mockImplementationOnce(() => neverOpen.promise);
      installPreviewApi({ mediaType, open });
      const initialEntry = initialKind === "png" ? entry() : audioEntry();
      const initialInspection =
        initialKind === "png" ? pngInspection() : wavInspection();
      const view = render(
        <AssetBinaryPreview
          context={context()}
          entry={initialEntry}
          inspection={initialInspection}
        />,
      );
      const media =
        initialKind === "png"
          ? await screen.findByRole("img")
          : await screen.findByLabelText("Verified WAV preview for ambience_01");
      expect(media).toHaveAttribute("src", "blob:verified-preview");

      view.rerender(
        <AssetBinaryPreview
          context={nextContext}
          entry={nextEntry}
          inspection={nextInspection}
        />,
      );
      expect(media).not.toHaveAttribute("src");
      expect(screen.queryByRole("img")).not.toBeInTheDocument();
      expect(document.querySelector("audio")).not.toBeInTheDocument();
      await waitFor(() =>
        expect(revokeObjectURL).toHaveBeenCalledWith("blob:verified-preview"),
      );
      view.unmount();
    },
  );

  it("renders native non-autoplay WAV controls and disposes media before revoking", async () => {
    const disposalOrder: string[] = [];
    const pauseMedia = vi.spyOn(HTMLMediaElement.prototype, "pause");
    const loadMedia = vi.spyOn(HTMLMediaElement.prototype, "load");
    const playMedia = vi.spyOn(HTMLMediaElement.prototype, "play");
    pauseMedia.mockImplementation(() => disposalOrder.push("pause"));
    loadMedia.mockImplementation(function (this: HTMLMediaElement) {
      disposalOrder.push(this.hasAttribute("src") ? "load-with-src" : "load-without-src");
    });
    revokeObjectURL.mockImplementation(() => disposalOrder.push("revoke"));
    installPreviewApi({ mediaType: "audio/wav" });
    const view = render(
      <AssetBinaryPreview
        context={context()}
        entry={entry({
          asset_id: "ambience_01",
          media_type: "audio/wav",
          path: "assets/ambience.wav",
        })}
        inspection={wavInspection()}
      />,
    );

    const audio = await screen.findByLabelText("Verified WAV preview for ambience_01");
    expect(audio).toHaveAttribute("controls");
    expect(audio).toHaveAttribute("preload", "metadata");
    expect(audio).not.toHaveAttribute("autoplay");
    expect(audio).toHaveAttribute("src", "blob:verified-preview");
    expect(playMedia).not.toHaveBeenCalled();

    view.rerender(
      <AssetBinaryPreview
        context={context({ active: false })}
        entry={entry({
          asset_id: "ambience_01",
          media_type: "audio/wav",
          path: "assets/ambience.wav",
        })}
        inspection={wavInspection()}
      />,
    );
    await waitFor(() => expect(revokeObjectURL).toHaveBeenCalled());
    expect(disposalOrder).toEqual(["pause", "load-without-src", "revoke"]);
    expect(playMedia).not.toHaveBeenCalled();
  });

  it("keeps exactly one sequential read in flight and exposes polite busy progress", async () => {
    const firstRead = deferred<ReturnType<typeof readReply>>();
    const read = vi
      .fn()
      .mockImplementationOnce(() => firstRead.promise)
      .mockImplementationOnce(() =>
        Promise.resolve(
          readReply({
            sequence: 1,
            bytes: new Uint8Array([5, 6, 7]),
            cumulativeBytes: ASSET_PREVIEW_CHUNK_BYTES + 3,
          }),
        ),
      );
    installPreviewApi({
      byteLength: ASSET_PREVIEW_CHUNK_BYTES + 3,
      read,
    });
    render(
      <AssetBinaryPreview
        context={context()}
        entry={entry()}
        inspection={pngInspection()}
      />,
    );

    await waitFor(() => expect(read).toHaveBeenCalledWith(HANDLE, 0));
    expect(read).toHaveBeenCalledTimes(1);
    const region = screen.getByRole("region", { name: "Verified PNG preview" });
    expect(region).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("status")).toHaveTextContent(/Reading verified preview/u);

    act(() =>
      firstRead.resolve(
        readReply({
          bytes: new Uint8Array(ASSET_PREVIEW_CHUNK_BYTES),
          cumulativeBytes: ASSET_PREVIEW_CHUNK_BYTES,
          cumulativeSha256: "d".repeat(64),
          eof: false,
        }),
      ),
    );
    await waitFor(() => expect(read).toHaveBeenCalledWith(HANDLE, 1));
    expect(read).toHaveBeenCalledTimes(2);
    expect(await screen.findByRole("img")).toBeInTheDocument();
    expect(region).toHaveAttribute("aria-busy", "false");
  });

  it("fails closed on malformed chunks and keeps Retry focus through a successful retry", async () => {
    const read = vi
      .fn()
      .mockResolvedValueOnce(readReply({ sequence: 1 }))
      .mockResolvedValueOnce(readReply());
    const api = installPreviewApi({ read });
    render(
      <>
        <button type="button">Outside focus</button>
        <AssetBinaryPreview
          context={context()}
          entry={entry()}
          inspection={pngInspection()}
        />
      </>,
    );

    const outside = screen.getByRole("button", { name: "Outside focus" });
    outside.focus();
    expect(await screen.findByRole("alert")).toHaveTextContent(/did not match/u);
    expect(outside).toHaveFocus();
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(api.close).toHaveBeenCalledTimes(1);
    const retry = screen.getByRole("button", { name: "Retry preview" });
    retry.focus();
    fireEvent.click(retry);
    expect(retry).toHaveFocus();

    expect(
      await screen.findByRole("img", { name: /Verified PNG preview/u }),
    ).toHaveAttribute("src", "blob:verified-preview");
    expect(retry).toHaveFocus();
    expect(api.open).toHaveBeenCalledTimes(2);
    expect(api.close).toHaveBeenCalledTimes(2);
  });

  it("does not publish a URL when EOF close fails", async () => {
    installPreviewApi({
      close: vi.fn().mockResolvedValue({
        ok: false,
        error: { code: "service_unavailable", message: "unavailable" },
      }),
    });
    render(
      <AssetBinaryPreview
        context={context()}
        entry={entry()}
        inspection={pngInspection()}
      />,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be closed/u);
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("closes an oversized open immediately without reading or publishing", async () => {
    const api = installPreviewApi({
      byteLength: ASSET_PREVIEW_MAX_BYTES + 1,
    });
    render(
      <AssetBinaryPreview
        context={context()}
        entry={entry()}
        inspection={pngInspection()}
      />,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(/64 MiB/u);
    expect(api.close).toHaveBeenCalledWith(HANDLE);
    expect(api.read).not.toHaveBeenCalled();
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  it("closes a late open after cancellation and never starts reading", async () => {
    const pendingOpen = deferred<ReturnType<typeof openReply>>();
    const api = installPreviewApi({ open: vi.fn(() => pendingOpen.promise) });
    const view = render(
      <AssetBinaryPreview
        context={context()}
        entry={entry()}
        inspection={pngInspection()}
      />,
    );
    await waitFor(() => expect(api.open).toHaveBeenCalledTimes(1));

    view.rerender(
      <AssetBinaryPreview
        context={context({ active: false })}
        entry={entry()}
        inspection={pngInspection()}
      />,
    );
    act(() => pendingOpen.resolve(openReply()));
    await waitFor(() => expect(api.close).toHaveBeenCalledWith(HANDLE));
    expect(api.read).not.toHaveBeenCalled();
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  it("discards a late read after cancellation and never continues the stream", async () => {
    const pendingRead = deferred<ReturnType<typeof readReply>>();
    const api = installPreviewApi({ read: vi.fn(() => pendingRead.promise) });
    const view = render(
      <AssetBinaryPreview
        context={context()}
        entry={entry()}
        inspection={pngInspection()}
      />,
    );
    await waitFor(() => expect(api.read).toHaveBeenCalledWith(HANDLE, 0));

    view.rerender(
      <AssetBinaryPreview
        context={context({ catalogCurrent: false })}
        entry={entry()}
        inspection={pngInspection()}
      />,
    );
    await waitFor(() => expect(api.close).toHaveBeenCalledWith(HANDLE));
    act(() => pendingRead.resolve(readReply()));
    expect(api.read).toHaveBeenCalledTimes(1);
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  it.each([
    ["workspace", context({ workspaceId: "workspace_02" }), entry(), pngInspection()],
    ["generation", context({ generation: 8 }), entry(), pngInspection()],
    [
      "revision",
      context({ manifestRevision: SECOND_REVISION }),
      entry(),
      pngInspection(),
    ],
    [
      "entry",
      context(),
      entry({ entry_id: SECOND_ENTRY_ID }),
      pngInspection(),
    ],
    [
      "kind",
      context(),
      entry({ media_type: "audio/wav", path: "assets/sound.wav" }),
      wavInspection(),
    ],
    [
      "category",
      context(),
      entry({ category: "runtime_output" }),
      pngInspection(),
    ],
  ] as const)(
    "closes the active lease before opening a changed %s identity",
    async (_name, nextContext, nextEntry, nextInspection) => {
      const pendingRead = deferred<ReturnType<typeof readReply>>();
      const neverOpen = deferred<ReturnType<typeof openReply>>();
      const open = vi
        .fn()
        .mockResolvedValueOnce(openReply())
        .mockImplementationOnce(() => neverOpen.promise);
      const api = installPreviewApi({
        open,
        read: vi.fn(() => pendingRead.promise),
      });
      const view = render(
        <AssetBinaryPreview
          context={context()}
          entry={entry()}
          inspection={pngInspection()}
        />,
      );
      await waitFor(() => expect(api.read).toHaveBeenCalledTimes(1));

      view.rerender(
        <AssetBinaryPreview
          context={nextContext}
          entry={nextEntry}
          inspection={nextInspection}
        />,
      );
      await waitFor(() => expect(api.close).toHaveBeenCalledWith(HANDLE));
      act(() => pendingRead.resolve(readReply()));
      await waitFor(() => expect(open).toHaveBeenCalledTimes(2));
      expect(createObjectURL).not.toHaveBeenCalled();
      view.unmount();
    },
  );

  it("never requests bytes for forbidden categories, MIME disagreement, or other kinds", async () => {
    const api = installPreviewApi();
    const previewContext = context();
    const inspections: StudioAssetInspection[] = [
      {
        kind: "json",
        encoding: "utf-8",
        content: "{}",
        value: {},
      },
      {
        kind: "glsl",
        encoding: "utf-8",
        content: "void main() {}",
      },
      { kind: "font", flavor: "truetype", table_count: 1 },
      {
        kind: "glb",
        byte_length: 32,
        json_chunk_bytes: 20,
        bin_chunk_bytes: 12,
        extensions_used: [],
        extensions_required: [],
        external_uris: [],
        embedded_uris: 0,
        max_texture_dimension: 0,
        metrics: {
          nodes: 0,
          meshes: 0,
          materials: 0,
          textures: 0,
          skins: 0,
          bones: 0,
          influences: 0,
          animations: 0,
          vertices: 0,
          triangles: 0,
          external_uris: 0,
        },
      },
      { kind: "unavailable", reason: "unsupported_media_type" },
    ];
    const view = render(
      <AssetInspection
        entry={entry({ category: "qa" })}
        inspection={pngInspection()}
        pending={false}
        previewContext={previewContext}
      />,
    );
    for (const inspection of inspections) {
      view.rerender(
        <AssetInspection
          entry={entry()}
          inspection={inspection}
          pending={false}
          previewContext={previewContext}
        />,
      );
    }
    view.rerender(
      <AssetInspection
        entry={entry({ media_type: "audio/wav" })}
        inspection={pngInspection()}
        pending={false}
        previewContext={previewContext}
      />,
    );
    await act(async () => Promise.resolve());
    expect(api.open).not.toHaveBeenCalled();
    expect(api.read).not.toHaveBeenCalled();
    expect(api.close).not.toHaveBeenCalled();
  });
});

function context(
  overrides: Partial<AssetBinaryPreviewContext> = {},
): AssetBinaryPreviewContext {
  return {
    active: true,
    catalogCurrent: true,
    workspaceId: "workspace_01",
    generation: 7,
    manifestRevision: REVISION,
    ...overrides,
  };
}

function entry(
  overrides: Partial<StudioAssetCatalogEntry> = {},
): StudioAssetCatalogEntry {
  return {
    entry_id: ENTRY_ID,
    asset_id: "asset_01",
    category: "production_output",
    role: "sprite",
    path: "assets/sprite.png",
    sha256: SHA256,
    media_type: "image/png",
    selected: true,
    inspectable: true,
    ...overrides,
  };
}

function audioEntry(
  overrides: Partial<StudioAssetCatalogEntry> = {},
): StudioAssetCatalogEntry {
  return entry({
    asset_id: "ambience_01",
    media_type: "audio/wav",
    path: "assets/ambience.wav",
    ...overrides,
  });
}

function pngInspection(): Extract<StudioAssetInspection, { kind: "png" }> {
  return {
    kind: "png",
    width: 64,
    height: 32,
    bit_depth: 8,
    color_type: 6,
    interlaced: false,
  };
}

function wavInspection(): Extract<StudioAssetInspection, { kind: "wav" }> {
  return {
    kind: "wav",
    channels: 2,
    sample_rate: 48_000,
    sample_width_bits: 16,
    frame_count: 96_000,
    duration_ms: 2_000,
  };
}

function installPreviewApi({
  mediaType = "image/png",
  byteLength = BYTES.byteLength,
  open = vi.fn(() => Promise.resolve(openReply({ mediaType, byteLength }))),
  read = vi.fn(() => Promise.resolve(readReply())),
  close = vi.fn(() => Promise.resolve(closeReply())),
}: {
  mediaType?: "image/png" | "audio/wav";
  byteLength?: number;
  open?: ReturnType<typeof vi.fn>;
  read?: ReturnType<typeof vi.fn>;
  close?: ReturnType<typeof vi.fn>;
} = {}) {
  const forgeStudio = {
    openAssetPreview: open,
    readAssetPreviewChunk: read,
    closeAssetPreview: close,
  } as unknown as ForgeStudioApi;
  Object.defineProperty(window, "forgeStudio", {
    configurable: true,
    value: forgeStudio,
  });
  return { open, read, close };
}

function openReply({
  mediaType = "image/png",
  byteLength = BYTES.byteLength,
  manifestRevision = REVISION,
  entryId = ENTRY_ID,
}: {
  mediaType?: "image/png" | "audio/wav";
  byteLength?: number;
  manifestRevision?: string;
  entryId?: string;
} = {}) {
  return response("asset.preview.open", {
    handle: HANDLE,
    manifest_revision: manifestRevision,
    entry_id: entryId,
    media_type: mediaType,
    byte_length: byteLength,
    sha256: SHA256,
    chunk_bytes: ASSET_PREVIEW_CHUNK_BYTES,
  });
}

function readReply({
  sequence = 0,
  bytes = new Uint8Array(BYTES),
  cumulativeBytes = bytes.byteLength,
  cumulativeSha256 = SHA256,
  eof = true,
}: {
  sequence?: number;
  bytes?: Uint8Array;
  cumulativeBytes?: number;
  cumulativeSha256?: string;
  eof?: boolean;
} = {}) {
  return response("asset.preview.read", {
    handle: HANDLE,
    sequence,
    bytes,
    byte_length: bytes.byteLength,
    cumulative_bytes: cumulativeBytes,
    cumulative_sha256: cumulativeSha256,
    eof,
  });
}

function closeReply() {
  return response("asset.preview.close", {
    handle: HANDLE,
    closed: true,
  });
}

function response(method: string, result: Record<string, unknown>) {
  return {
    ok: true as const,
    value: {
      protocol: "rpg-world-forge.studio_protocol" as const,
      protocol_version: 1 as const,
      kind: "response" as const,
      request_id: "00000000-0000-4000-8000-000000000001",
      method,
      result,
    },
  };
}

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
} {
  let resolvePromise: ((value: T) => void) | null = null;
  const promise = new Promise<T>((resolve) => {
    resolvePromise = resolve;
  });
  return {
    promise,
    resolve(value: T): void {
      resolvePromise?.(value);
    },
  };
}

function restoreProperty(
  target: object,
  property: PropertyKey,
  descriptor: PropertyDescriptor | undefined,
): void {
  if (descriptor) Object.defineProperty(target, property, descriptor);
  else Reflect.deleteProperty(target, property);
}
