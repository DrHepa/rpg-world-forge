import { useEffect, useMemo, useRef, useState } from "react";

import {
  fitMapView,
  normalizedDevicePixelRatio,
  parseNeutralMap,
  TILE_HEIGHT,
  TILE_WIDTH,
  visibleProjectedCells,
  type MapView,
  type NeutralTileStyle,
  type ProjectedCell,
} from "./neutral-map";

interface NeutralMapCanvasProps {
  text: string;
  tileStyles?: Readonly<Record<string, NeutralTileStyle>>;
}

interface KeyedView {
  key: string;
  view: MapView;
}

interface KeyedSelection {
  key: string;
  row: number;
  column: number;
}

export function NeutralMapCanvas({ text, tileStyles = {} }: NeutralMapCanvasProps) {
  const parsed = useMemo(() => parseNeutralMap(text), [text]);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 640, height: 360 });
  const [storedView, setStoredView] = useState<KeyedView | null>(null);
  const [storedSelection, setStoredSelection] = useState<KeyedSelection | null>(null);

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return undefined;
    const update = (width: number, height: number) => {
      setSize({
        width: Math.max(240, Math.round(width)),
        height: Math.max(220, Math.round(height)),
      });
    };
    const rect = frame.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) update(rect.width, rect.height);
    if (typeof ResizeObserver === "undefined") {
      const onResize = () => {
        const next = frame.getBoundingClientRect();
        if (next.width > 0 && next.height > 0) update(next.width, next.height);
      };
      window.addEventListener("resize", onResize);
      return () => window.removeEventListener("resize", onResize);
    }
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) update(entry.contentRect.width, entry.contentRect.height);
    });
    observer.observe(frame);
    return () => observer.disconnect();
  }, []);

  const mapKey = parsed.ok ? `${text.length}:${text}` : "invalid";
  const viewKey = `${mapKey}:${String(size.width)}x${String(size.height)}`;
  const defaultView = parsed.ok
    ? fitMapView(parsed.map, size.width, size.height, tileStyles)
    : { zoom: 1, panX: 0, panY: 0 };
  const view = storedView?.key === viewKey ? storedView.view : defaultView;
  const selected =
    storedSelection?.key === mapKey
      ? { row: storedSelection.row, column: storedSelection.column }
      : { row: 0, column: 0 };

  useEffect(() => {
    if (!parsed.ok) return;
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;
    const dpr = normalizedDevicePixelRatio(window.devicePixelRatio);
    canvas.width = Math.max(1, Math.round(size.width * dpr));
    canvas.height = Math.max(1, Math.round(size.height * dpr));
    canvas.style.width = `${String(size.width)}px`;
    canvas.style.height = `${String(size.height)}px`;
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, size.width, size.height);
    context.fillStyle = "#0b1118";
    context.fillRect(0, 0, size.width, size.height);
    for (const cell of visibleProjectedCells(parsed.map, tileStyles, view, size)) {
      drawCell(context, cell, tileStyles[cell.tileId], view.zoom);
      if (cell.row === selected.row && cell.column === selected.column) {
        drawSelection(context, cell, view.zoom);
      }
    }
  }, [parsed, selected.column, selected.row, size, tileStyles, view]);

  if (!parsed.ok) {
    return (
      <section className="map-empty" aria-label="Map preview unavailable">
        <p>No neutral map preview is available.</p>
        <small>{parsed.message}</small>
      </section>
    );
  }

  const map = parsed.map;
  const selectedSymbol = Array.from(map.rows[selected.row] ?? "")[selected.column] ?? "?";
  const selectedTile = map.legend[selectedSymbol] ?? "unknown";

  function updateView(update: (current: MapView) => MapView): void {
    setStoredView({ key: viewKey, view: update(view) });
  }

  function resetView(): void {
    setStoredView({ key: viewKey, view: fitMapView(map, size.width, size.height, tileStyles) });
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLDivElement>): void {
    const distance = event.shiftKey ? 64 : 24;
    const movement: Record<string, [number, number]> = {
      ArrowLeft: [distance, 0],
      ArrowRight: [-distance, 0],
      ArrowUp: [0, distance],
      ArrowDown: [0, -distance],
    };
    if (event.key === "Home") {
      event.preventDefault();
      resetView();
      return;
    }
    const delta = movement[event.key];
    if (!delta) return;
    event.preventDefault();
    updateView((current) => ({
      ...current,
      panX: current.panX + delta[0],
      panY: current.panY + delta[1],
    }));
  }

  function selectFromPointer(event: React.PointerEvent<HTMLDivElement>): void {
    const rect = event.currentTarget.getBoundingClientRect();
    const worldX = (event.clientX - rect.left - view.panX) / view.zoom;
    const worldY = (event.clientY - rect.top - view.panY) / view.zoom;
    const column = Math.round(worldX / TILE_WIDTH + worldY / TILE_HEIGHT);
    const row = Math.round(worldY / TILE_HEIGHT - worldX / TILE_WIDTH);
    if (row >= 0 && row < map.height && column >= 0 && column < map.width) {
      setStoredSelection({ key: mapKey, row, column });
    }
  }

  return (
    <section className="map-preview" aria-labelledby="map-preview-heading">
      <div className="preview-toolbar">
        <div>
          <h3 id="map-preview-heading">Neutral 2.5D preview</h3>
          <small>Draft preview — non-authoritative</small>
        </div>
        <label className="zoom-control">
          Preview zoom
          <input
            aria-label="Preview zoom"
            type="range"
            min="0.4"
            max="2.5"
            step="0.1"
            value={view.zoom}
            onChange={(event) =>
              updateView((current) => ({ ...current, zoom: Number(event.target.value) }))
            }
          />
        </label>
        <button type="button" className="secondary compact" onClick={resetView}>
          Fit / reset
        </button>
      </div>
      <div
        ref={frameRef}
        className="canvas-frame"
        role="group"
        aria-label="Interactive neutral map viewport. Use arrow keys to pan and Home to fit."
        tabIndex={0}
        data-pan-x={Math.round(view.panX)}
        data-pan-y={Math.round(view.panY)}
        onKeyDown={handleKeyDown}
        onPointerDown={selectFromPointer}
      >
        <canvas ref={canvasRef} aria-hidden="true" />
      </div>
      <p className="map-summary" aria-live="polite">
        {map.displayName}: {map.width} × {map.height} cells, {Object.keys(map.legend).length} tile
        types. Selected cell: row {selected.row + 1}, column {selected.column + 1}, symbol “
        {selectedSymbol}”, tile {tileStyles[selectedTile]?.label ?? selectedTile}.
      </p>
    </section>
  );
}

function drawCell(
  context: CanvasRenderingContext2D,
  cell: ProjectedCell,
  style: NeutralTileStyle | undefined,
  zoom: number,
): void {
  const halfWidth = (TILE_WIDTH / 2) * zoom;
  const halfHeight = (TILE_HEIGHT / 2) * zoom;
  context.beginPath();
  context.moveTo(cell.x, cell.y - halfHeight);
  context.lineTo(cell.x + halfWidth, cell.y);
  context.lineTo(cell.x, cell.y + halfHeight);
  context.lineTo(cell.x - halfWidth, cell.y);
  context.closePath();
  context.fillStyle = style?.color ? rgba(style.color) : fallbackColor(cell.tileId);
  context.fill();
  context.strokeStyle = "rgba(7, 14, 20, 0.62)";
  context.lineWidth = Math.max(0.6, zoom);
  context.stroke();
}

function drawSelection(context: CanvasRenderingContext2D, cell: ProjectedCell, zoom: number): void {
  const halfWidth = (TILE_WIDTH / 2) * zoom;
  const halfHeight = (TILE_HEIGHT / 2) * zoom;
  context.beginPath();
  context.moveTo(cell.x, cell.y - halfHeight);
  context.lineTo(cell.x + halfWidth, cell.y);
  context.lineTo(cell.x, cell.y + halfHeight);
  context.lineTo(cell.x - halfWidth, cell.y);
  context.closePath();
  context.strokeStyle = "#ffffff";
  context.lineWidth = Math.max(2, zoom * 2);
  context.stroke();
}

function fallbackColor(tileId: string): string {
  let hash = 0;
  for (const character of tileId) hash = (hash * 31 + character.codePointAt(0)!) >>> 0;
  return `hsl(${String(hash % 360)} 34% ${String(38 + (hash % 13))}%)`;
}

function rgba(color: readonly [number, number, number, number?]): string {
  const [red, green, blue, alpha = 255] = color.map((component) =>
    Math.max(0, Math.min(255, Number(component))),
  ) as [number, number, number, number];
  return `rgba(${String(red)}, ${String(green)}, ${String(blue)}, ${String(alpha / 255)})`;
}
