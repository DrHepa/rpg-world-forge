// @vitest-environment jsdom

import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NeutralMapCanvas } from "../../src/renderer/NeutralMapCanvas";
import {
  fitMapView,
  normalizedDevicePixelRatio,
  parseNeutralMap,
  projectIsometric,
  visibleProjectedCells,
} from "../../src/renderer/neutral-map";

const MAP_TEXT = JSON.stringify({
  id: "garden",
  display_name: "Neutral garden",
  width: 3,
  height: 2,
  legend: { ".": "ground", "#": "rock" },
  rows: ["...", ".#."],
});

beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(canvasContext());
  Object.defineProperty(window, "devicePixelRatio", { configurable: true, value: 2 });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("neutral map projection", () => {
  it("requires exact bounded rows and legend entries", () => {
    expect(parseNeutralMap(MAP_TEXT)).toMatchObject({ ok: true, map: { width: 3, height: 2 } });
    expect(
      parseNeutralMap(JSON.stringify({ width: 2, height: 1, legend: { ".": "ground" }, rows: ["."] })),
    ).toEqual({ ok: false, message: "Every map row must exactly match the declared width." });
    expect(
      parseNeutralMap(JSON.stringify({ width: 2, height: 1, legend: { ".": "ground" }, rows: [".#"] })),
    ).toEqual({ ok: false, message: "Every row symbol must have an exact legend entry." });
    expect(
      parseNeutralMap(JSON.stringify({ width: 128, height: 128, legend: { ".": "ground" }, rows: ["."] })),
    ).toMatchObject({ ok: false });
  });

  it("projects elevation deterministically and culls outside the viewport", () => {
    expect(projectIsometric(1, 2, 0)).toEqual({ x: 32, y: 48 });
    expect(projectIsometric(1, 2, 2)).toEqual({ x: 32, y: 32 });
    const parsed = parseNeutralMap(MAP_TEXT);
    if (!parsed.ok) throw new Error(parsed.message);
    const fit = fitMapView(parsed.map, 320, 220);
    expect(fit.zoom).toBeGreaterThanOrEqual(0.4);
    expect(
      visibleProjectedCells(parsed.map, {}, { zoom: 1, panX: 10_000, panY: 10_000 }, { width: 320, height: 220 }),
    ).toHaveLength(0);
    expect(visibleProjectedCells(parsed.map, {}, fit, { width: 320, height: 220 }).length).toBeGreaterThan(0);
  });

  it("fits and keeps highly elevated cells visible", () => {
    const parsed = parseNeutralMap(
      JSON.stringify({
        width: 1,
        height: 1,
        legend: { ".": "ground" },
        rows: ["."],
        elevations: [[32]],
      }),
    );
    if (!parsed.ok) throw new Error(parsed.message);
    const viewport = { width: 320, height: 220 };
    const fit = fitMapView(parsed.map, viewport.width, viewport.height);
    expect(visibleProjectedCells(parsed.map, {}, fit, viewport)).toHaveLength(1);
  });

  it("bounds device pixel ratio", () => {
    expect(normalizedDevicePixelRatio(Number.NaN)).toBe(1);
    expect(normalizedDevicePixelRatio(0.5)).toBe(1);
    expect(normalizedDevicePixelRatio(2)).toBe(2);
    expect(normalizedDevicePixelRatio(8)).toBe(3);
  });
});

describe("NeutralMapCanvas", () => {
  it("keeps canvas decorative, exposes a textual fallback, and supports keyboard pan", () => {
    const { container } = render(<NeutralMapCanvas text={MAP_TEXT} />);
    const viewport = screen.getByRole("group", { name: /Interactive neutral map viewport/u });
    const initialPan = viewport.getAttribute("data-pan-x");
    fireEvent.keyDown(viewport, { key: "ArrowRight" });
    expect(viewport.getAttribute("data-pan-x")).not.toBe(initialPan);
    expect(screen.getByText(/Neutral garden: 3 × 2 cells/u)).toHaveTextContent(/Selected cell/u);
    expect(container.querySelector("canvas")).toHaveAttribute("aria-hidden", "true");
    expect(container.querySelector("canvas")?.width).toBe(1_280);
  });
});

function canvasContext(): CanvasRenderingContext2D {
  return {
    beginPath: vi.fn(),
    clearRect: vi.fn(),
    closePath: vi.fn(),
    fill: vi.fn(),
    fillRect: vi.fn(),
    lineTo: vi.fn(),
    moveTo: vi.fn(),
    setTransform: vi.fn(),
    stroke: vi.fn(),
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 1,
  } as unknown as CanvasRenderingContext2D;
}
