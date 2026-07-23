// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AssetInspection } from "../../src/renderer/AssetInspection";
import type {
  StudioAssetCatalogEntry,
  StudioAssetInspection,
} from "../../src/shared/studio-api";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AssetInspection", () => {
  it("renders bounded JSON and escaped GLSL as inert text", () => {
    const { rerender } = render(
      <AssetInspection
        entry={entry()}
        inspection={{
          kind: "json",
          encoding: "utf-8",
          content: '{"markup":"<img src=x onerror=alert(1)>"}',
          value: {
            markup: "<img src=x onerror=alert(1)>",
            nested: { enabled: true, count: 2, empty: null },
          },
        }}
        pending={false}
      />,
    );
    expect(screen.getByRole("heading", { name: "Semantic JSON tree" })).toBeInTheDocument();
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(document.querySelector("img")).not.toBeInTheDocument();
    expect(screen.getByText(/2,000 semantic nodes and depth 12/u)).toBeInTheDocument();

    rerender(
      <AssetInspection
        entry={entry()}
        inspection={{
          kind: "glsl",
          encoding: "utf-8",
          content: "</code><script>unsafe()</script>",
        }}
        pending={false}
      />,
    );
    expect(screen.getByText("</code><script>unsafe()</script>")).toBeInTheDocument();
    expect(document.querySelector("script")).not.toBeInTheDocument();
    expect(screen.getByText(/Shader compilation and rendering are unavailable/u)).toBeInTheDocument();
  });

  it.each([
    [
      "PNG structure",
      {
        kind: "png",
        width: 64,
        height: 32,
        bit_depth: 8,
        color_type: 6,
        interlaced: false,
      },
      /Image preview is unavailable/u,
    ],
    [
      "WAV structure",
      {
        kind: "wav",
        channels: 2,
        sample_rate: 48_000,
        sample_width_bits: 16,
        frame_count: 96_000,
        duration_ms: 2_000,
      },
      /Audio playback is unavailable/u,
    ],
    [
      "Font structure",
      { kind: "font", flavor: "opentype", table_count: 14 },
      /Font loading and glyph preview are unavailable/u,
    ],
    [
      "GLB structure",
      {
        kind: "glb",
        byte_length: 4_096,
        json_chunk_bytes: 1_024,
        bin_chunk_bytes: 3_072,
        extensions_used: ["KHR_materials_unlit"],
        extensions_required: [],
        external_uris: ["textures/albedo.png"],
        embedded_uris: 0,
        max_texture_dimension: 1_024,
        metrics: {
          nodes: 2,
          meshes: 1,
          materials: 1,
          textures: 1,
          skins: 0,
          bones: 0,
          influences: 0,
          animations: 0,
          vertices: 24,
          triangles: 12,
          external_uris: 1,
        },
      },
      /3D scene rendering is unavailable/u,
    ],
  ] as const)("renders verified %s metadata without constructing preview APIs", (heading, inspection, copy) => {
    const BlobProbe = vi.fn();
    const ImageProbe = vi.fn();
    const AudioProbe = vi.fn();
    const FontProbe = vi.fn();
    const WebGlProbe = vi.fn();
    vi.stubGlobal("Blob", BlobProbe);
    vi.stubGlobal("Image", ImageProbe);
    vi.stubGlobal("Audio", AudioProbe);
    vi.stubGlobal("FontFace", FontProbe);
    vi.stubGlobal("WebGLRenderingContext", WebGlProbe);

    render(
      <AssetInspection
        entry={entry()}
        inspection={inspection as StudioAssetInspection}
        pending={false}
      />,
    );
    expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
    expect(screen.getByText(copy)).toBeInTheDocument();
    for (const probe of [BlobProbe, ImageProbe, AudioProbe, FontProbe, WebGlProbe]) {
      expect(probe).not.toHaveBeenCalled();
    }
    expect(document.querySelector("canvas, img, audio, video")).not.toBeInTheDocument();
  });

  it("renders both honest unavailable reasons and the no-selection state", () => {
    const { rerender } = render(
      <AssetInspection entry={null} inspection={null} pending={false} />,
    );
    expect(screen.getByText(/Select an inspectable entry/u)).toBeInTheDocument();

    rerender(
      <AssetInspection
        entry={entry()}
        inspection={{ kind: "unavailable", reason: "identity_only" }}
        pending={false}
      />,
    );
    expect(screen.getByText(/identity-only/u)).toBeInTheDocument();

    rerender(
      <AssetInspection
        entry={entry()}
        inspection={{ kind: "unavailable", reason: "unsupported_media_type" }}
        pending={false}
      />,
    );
    expect(screen.getByText(/does not have a metadata inspector/u)).toBeInTheDocument();
  });
});

function entry(): StudioAssetCatalogEntry {
  return {
    entry_id: `asset_${"1".repeat(64)}`,
    asset_id: "asset_01",
    category: "visual_bible",
    role: "concept",
    path: "assets/concept.png",
    sha256: "a".repeat(64),
    media_type: "image/png",
    selected: false,
    inspectable: true,
  };
}
