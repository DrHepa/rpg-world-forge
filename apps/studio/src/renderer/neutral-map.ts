export const TILE_WIDTH = 64;
export const TILE_HEIGHT = 32;
const MAX_MAP_DIMENSION = 128;
export const MAX_MAP_CELLS = 4_096;

export interface NeutralTileStyle {
  color?: readonly [number, number, number, number?];
  height?: number;
  label?: string;
}

export interface NeutralMap {
  id: string;
  displayName: string;
  width: number;
  height: number;
  legend: Record<string, string>;
  rows: string[];
  elevations: number[][] | null;
}

export interface MapView {
  zoom: number;
  panX: number;
  panY: number;
}

export interface ProjectedCell {
  row: number;
  column: number;
  symbol: string;
  tileId: string;
  elevation: number;
  x: number;
  y: number;
}

export type ParsedNeutralMap =
  | { ok: true; map: NeutralMap }
  | { ok: false; message: string };

export function parseNeutralMap(text: string): ParsedNeutralMap {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return { ok: false, message: "The draft is not valid JSON." };
  }
  if (!isRecord(value)) return { ok: false, message: "The draft root is not an object." };
  const widthValue = value.width;
  const heightValue = value.height;
  if (!Number.isSafeInteger(widthValue) || !Number.isSafeInteger(heightValue)) {
    return { ok: false, message: "Map dimensions must be integers." };
  }
  const width = Number(widthValue);
  const height = Number(heightValue);
  if (
    width < 1 ||
    height < 1 ||
    width > MAX_MAP_DIMENSION ||
    height > MAX_MAP_DIMENSION ||
    width * height > MAX_MAP_CELLS
  ) {
    return {
      ok: false,
      message: `Map dimensions exceed the ${String(MAX_MAP_CELLS)}-cell preview limit.`,
    };
  }
  const rowsValue = value.rows;
  if (!Array.isArray(rowsValue) || rowsValue.length !== height) {
    return { ok: false, message: "Map rows do not exactly match the declared height." };
  }
  const rows: string[] = [];
  for (const row of rowsValue) {
    if (typeof row !== "string" || Array.from(row).length !== width) {
      return { ok: false, message: "Every map row must exactly match the declared width." };
    }
    rows.push(row);
  }
  if (!isRecord(value.legend)) {
    return { ok: false, message: "Map legend must be an object." };
  }
  const legend: Record<string, string> = {};
  for (const [symbol, tileId] of Object.entries(value.legend)) {
    if (Array.from(symbol).length !== 1 || typeof tileId !== "string" || tileId.length === 0) {
      return { ok: false, message: "Map legend entries must map one symbol to one tile ID." };
    }
    legend[symbol] = tileId;
  }
  for (const row of rows) {
    if (Array.from(row).some((symbol) => legend[symbol] === undefined)) {
      return { ok: false, message: "Every row symbol must have an exact legend entry." };
    }
  }
  return {
    ok: true,
    map: {
      id: typeof value.id === "string" ? value.id : "draft_map",
      displayName: typeof value.display_name === "string" ? value.display_name : "Draft map",
      width,
      height,
      legend,
      rows,
      elevations: parseElevations(value.elevations, width, height),
    },
  };
}

export function projectIsometric(
  row: number,
  column: number,
  elevation = 0,
): { x: number; y: number } {
  return {
    x: (column - row) * (TILE_WIDTH / 2),
    y: (column + row) * (TILE_HEIGHT / 2) - elevation * (TILE_HEIGHT / 4),
  };
}

export function normalizedDevicePixelRatio(value: number): number {
  return Number.isFinite(value) ? Math.min(3, Math.max(1, value)) : 1;
}

export function fitMapView(
  map: NeutralMap,
  width: number,
  height: number,
  tileStyles: Readonly<Record<string, NeutralTileStyle>> = {},
): MapView {
  const projected = [];
  for (let row = 0; row < map.height; row += 1) {
    const symbols = Array.from(map.rows[row]);
    for (let column = 0; column < map.width; column += 1) {
      const tileId = map.legend[symbols[column]];
      const elevation = boundedElevation(
        map.elevations?.[row]?.[column] ?? tileStyles[tileId]?.height ?? 0,
      );
      projected.push(projectIsometric(row, column, elevation));
    }
  }
  const minX = Math.min(...projected.map((point) => point.x)) - TILE_WIDTH / 2;
  const maxX = Math.max(...projected.map((point) => point.x)) + TILE_WIDTH / 2;
  const minY = Math.min(...projected.map((point) => point.y)) - TILE_HEIGHT;
  const maxY = Math.max(...projected.map((point) => point.y)) + TILE_HEIGHT;
  const zoom = Math.min(
    2.5,
    Math.max(0.4, Math.min((width - 32) / (maxX - minX), (height - 32) / (maxY - minY))),
  );
  return {
    zoom,
    panX: width / 2 - ((minX + maxX) / 2) * zoom,
    panY: height / 2 - ((minY + maxY) / 2) * zoom,
  };
}

export function visibleProjectedCells(
  map: NeutralMap,
  tileStyles: Readonly<Record<string, NeutralTileStyle>>,
  view: MapView,
  viewport: { width: number; height: number },
): ProjectedCell[] {
  const visible: ProjectedCell[] = [];
  for (let row = 0; row < map.height; row += 1) {
    const symbols = Array.from(map.rows[row]);
    for (let column = 0; column < map.width; column += 1) {
      const symbol = symbols[column];
      const tileId = map.legend[symbol];
      const elevation = boundedElevation(
        map.elevations?.[row]?.[column] ?? tileStyles[tileId]?.height ?? 0,
      );
      const projected = projectIsometric(row, column, elevation);
      const x = projected.x * view.zoom + view.panX;
      const y = projected.y * view.zoom + view.panY;
      const marginX = TILE_WIDTH * view.zoom;
      const marginY = TILE_HEIGHT * view.zoom * 2;
      if (
        x < -marginX ||
        x > viewport.width + marginX ||
        y < -marginY ||
        y > viewport.height + marginY
      ) {
        continue;
      }
      visible.push({ row, column, symbol, tileId, elevation, x, y });
    }
  }
  return visible;
}

function parseElevations(value: unknown, width: number, height: number): number[][] | null {
  if (!Array.isArray(value) || value.length !== height) return null;
  const rows: number[][] = [];
  for (const row of value) {
    if (
      !Array.isArray(row) ||
      row.length !== width ||
      !row.every((entry) => typeof entry === "number" && Number.isFinite(entry))
    ) {
      return null;
    }
    rows.push(row.map((entry) => boundedElevation(Number(entry))));
  }
  return rows;
}

function boundedElevation(value: number): number {
  return Number.isFinite(value) ? Math.max(-32, Math.min(32, value)) : 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
