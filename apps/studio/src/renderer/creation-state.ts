import type { WorldForgeCreationProfileV1 } from "../generated/world-forge-contracts";

export const CREATION_PROFILE_FACETS = [
  "experience",
  "gameplay",
  "world",
  "narrative",
  "fiction",
  "presentation",
  "production",
  "runtime_target",
] as const;

export type CreationProfileFacet = (typeof CREATION_PROFILE_FACETS)[number];
export type CreationNavigationKind =
  | "clean"
  | "facet_buffer"
  | "draft"
  | "request_pending"
  | "output_grant"
  | "staged"
  | "approved"
  | "recovery_required";

export interface CreationNavigationState {
  blocksNavigation: boolean;
  kind: CreationNavigationKind;
}
export type CreationProfileDocument = WorldForgeCreationProfileV1;

const MAX_FACET_JSON_BYTES = 256 * 1024;
const MAX_JSON_DEPTH = 64;
const FORBIDDEN_KEYS = new Set(["__proto__", "constructor", "prototype"]);

export function parseCreationFacetJson(
  source: string,
  facet: CreationProfileFacet,
): Record<string, unknown> {
  return parseCreationObjectJson(source, facet);
}

export function parseCreationObjectJson(
  source: string,
  context: string,
  maximumBytes = MAX_FACET_JSON_BYTES,
): Record<string, unknown> {
  const value = parseCreationJson(source, context, maximumBytes);
  if (!isRecord(value)) {
    throw new Error(`${context} JSON must have an object root`);
  }
  return value;
}

export function parseCreationJson(
  source: string,
  context: string,
  maximumBytes = MAX_FACET_JSON_BYTES,
): unknown {
  if (!Number.isSafeInteger(maximumBytes) || maximumBytes < 1) {
    throw new Error(`${context} JSON byte limit is invalid`);
  }
  if (new TextEncoder().encode(source).byteLength > maximumBytes) {
    throw new Error(`${context} JSON exceeds the ${String(maximumBytes)}-byte limit`);
  }
  const value = new StrictJsonParser(source, context).parse();
  return value;
}

export function snapshotCreationJson(value: unknown): unknown {
  return strictSnapshot(value);
}

export function validateCreationProfileDocument(value: unknown): CreationProfileDocument {
  if (!isRecord(value)) {
    throw new Error("Creation profile must be an object");
  }
  if (value.format !== "world-forge.creation_profile" || value.format_version !== 1) {
    throw new Error("Creation profile format is unsupported");
  }
  for (const facet of CREATION_PROFILE_FACETS) {
    if (!isRecord(value[facet])) {
      throw new Error(`Creation profile ${facet} facet must be an object`);
    }
  }
  if (
    typeof value.content_hash !== "string" ||
    !/^[0-9a-f]{64}$/u.test(value.content_hash) ||
    typeof value.profile_id !== "string" ||
    typeof value.project_id !== "string" ||
    typeof value.title !== "string"
  ) {
    throw new Error("Creation profile identity is invalid");
  }
  if (!Array.isArray(value.extensions)) {
    throw new Error("Creation profile extensions must be an array");
  }
  return strictSnapshot(value) as CreationProfileDocument;
}

export function replaceCreationFacet(
  profileValue: unknown,
  facet: CreationProfileFacet,
  value: Record<string, unknown>,
): CreationProfileDocument {
  const profile = validateCreationProfileDocument(profileValue);
  const replacement = strictSnapshot(value);
  return validateCreationProfileDocument({ ...profile, [facet]: replacement });
}

export function creationProfilePreview(profileValue: unknown): string {
  return `${JSON.stringify(sortJson(validateCreationProfileDocument(profileValue)), null, 2)}\n`;
}

export function isCreationProfileDirty(baseValue: unknown, draftValue: unknown): boolean {
  return creationProfilePreview(baseValue) !== creationProfilePreview(draftValue);
}

export function summarizeCreationFacet(
  facet: CreationProfileFacet,
  rawValue: unknown,
): string {
  if (!isRecord(rawValue)) return "Invalid facet";
  if (facet === "experience") {
    return boundedSummary(stringValue(rawValue.player_promise) || "Player promise not set");
  }
  if (facet === "gameplay") {
    const family = stringValue(rawValue.primary_family) || "unspecified";
    const verbs = Array.isArray(rawValue.core_verbs) ? rawValue.core_verbs.length : 0;
    return `${titleToken(family)} · ${String(verbs)} core ${verbs === 1 ? "verb" : "verbs"}`;
  }
  if (facet === "world") {
    const presence = stringValue(rawValue.presence);
    return presence === "none" ? "No world" : `${titleToken(presence || "unspecified")} world`;
  }
  if (facet === "narrative") {
    const requirement = stringValue(rawValue.requirement);
    if (requirement === "none") return "No narrative";
    const topology = stringValue(rawValue.topology) || "unspecified";
    return `${titleToken(requirement || "unspecified")} · ${titleToken(topology)}`;
  }
  if (facet === "fiction") {
    const genres = stringList(rawValue.genres);
    const tones = stringList(rawValue.tones);
    return boundedSummary(
      `${genres.length > 0 ? genres.join(", ") : "No fiction genre"} · ${tones.length > 0 ? tones.join(", ") : "No tone"}`,
    );
  }
  if (facet === "presentation") {
    const mode = stringValue(rawValue.mode);
    const label =
      mode === "2d" ? "2D" : mode === "2_5d" ? "2.5D" : mode === "3d" ? "3D" : titleToken(mode || "unspecified");
    return `${label} · ${boundedSummary(stringValue(rawValue.perspective) || "perspective not set")}`;
  }
  if (facet === "production") {
    return rawValue.human_review === true
      ? "Human review required"
      : "Human review not required by profile";
  }
  return boundedSummary(
    `${stringValue(rawValue.requested_adapter) || "No adapter requested"} · ${titleToken(stringValue(rawValue.presentation_mode) || "unspecified")}`,
  );
}

function boundedSummary(value: string): string {
  const normalized = value.replaceAll(/\s+/gu, " ").trim();
  return normalized.length <= 160 ? normalized : `${normalized.slice(0, 157)}…`;
}

function titleToken(value: string): string {
  return value
    .replaceAll("_", " ")
    .replace(/\b\p{L}/gu, (character) => character.toUpperCase());
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string").slice(0, 4)
    : [];
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function sortJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortJson);
  if (!isRecord(value)) return value;
  const result: Record<string, unknown> = {};
  for (const key of Object.keys(value).sort()) result[key] = sortJson(value[key]);
  return result;
}

function strictSnapshot(value: unknown, depth = 0): unknown {
  if (depth > MAX_JSON_DEPTH) throw new Error("Creation JSON exceeds the depth limit");
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("Creation JSON numbers must be finite");
    return value;
  }
  if (Array.isArray(value)) return value.map((item) => strictSnapshot(item, depth + 1));
  if (!isRecord(value)) throw new Error("Creation JSON contains an unsupported value");
  const result: Record<string, unknown> = {};
  for (const key of Object.keys(value)) {
    if (FORBIDDEN_KEYS.has(key)) throw new Error(`Creation JSON contains unsupported object key ${key}`);
    result[key] = strictSnapshot(value[key], depth + 1);
  }
  return result;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

class StrictJsonParser {
  readonly #text: string;
  readonly #context: string;
  #index = 0;

  public constructor(text: string, context: string) {
    this.#text = text;
    this.#context = context;
  }

  public parse(): unknown {
    this.#skipWhitespace();
    const value = this.#parseValue(0);
    this.#skipWhitespace();
    if (this.#index !== this.#text.length) this.#fail("unexpected trailing JSON content");
    return value;
  }

  #parseValue(depth: number): unknown {
    if (depth > MAX_JSON_DEPTH) this.#fail("JSON depth exceeds the supported limit");
    const token = this.#text[this.#index];
    if (token === "{") return this.#parseObject(depth + 1);
    if (token === "[") return this.#parseArray(depth + 1);
    if (token === '"') return this.#parseString();
    if (token === "t") return this.#parseLiteral("true", true);
    if (token === "f") return this.#parseLiteral("false", false);
    if (token === "n") return this.#parseLiteral("null", null);
    if (token === "-" || (token !== undefined && token >= "0" && token <= "9")) {
      return this.#parseNumber();
    }
    this.#fail("unexpected JSON token");
  }

  #parseObject(depth: number): Record<string, unknown> {
    this.#index += 1;
    this.#skipWhitespace();
    const result: Record<string, unknown> = {};
    const keys = new Set<string>();
    if (this.#text[this.#index] === "}") {
      this.#index += 1;
      return result;
    }
    while (this.#index < this.#text.length) {
      if (this.#text[this.#index] !== '"') this.#fail("object key must be a JSON string");
      const key = this.#parseString();
      if (keys.has(key)) this.#fail(`duplicate object key ${JSON.stringify(key)}`);
      if (FORBIDDEN_KEYS.has(key)) this.#fail(`unsupported object key ${JSON.stringify(key)}`);
      keys.add(key);
      this.#skipWhitespace();
      if (this.#text[this.#index] !== ":") this.#fail("object key must be followed by ':'");
      this.#index += 1;
      this.#skipWhitespace();
      result[key] = this.#parseValue(depth);
      this.#skipWhitespace();
      const separator = this.#text[this.#index];
      if (separator === "}") {
        this.#index += 1;
        return result;
      }
      if (separator !== ",") this.#fail("object member must be followed by ',' or '}'");
      this.#index += 1;
      this.#skipWhitespace();
    }
    this.#fail("unterminated JSON object");
  }

  #parseArray(depth: number): unknown[] {
    this.#index += 1;
    this.#skipWhitespace();
    const result: unknown[] = [];
    if (this.#text[this.#index] === "]") {
      this.#index += 1;
      return result;
    }
    while (this.#index < this.#text.length) {
      result.push(this.#parseValue(depth));
      this.#skipWhitespace();
      const separator = this.#text[this.#index];
      if (separator === "]") {
        this.#index += 1;
        return result;
      }
      if (separator !== ",") this.#fail("array item must be followed by ',' or ']'");
      this.#index += 1;
      this.#skipWhitespace();
    }
    this.#fail("unterminated JSON array");
  }

  #parseString(): string {
    const start = this.#index;
    this.#index += 1;
    while (this.#index < this.#text.length) {
      const character = this.#text[this.#index];
      const code = this.#text.charCodeAt(this.#index);
      if (character === '"') {
        this.#index += 1;
        try {
          const value = JSON.parse(this.#text.slice(start, this.#index)) as unknown;
          if (typeof value !== "string") this.#fail("invalid JSON string");
          return value;
        } catch {
          this.#fail("invalid JSON string");
        }
      }
      if (character === "\\") {
        this.#index += 2;
        continue;
      }
      if (code < 0x20) this.#fail("JSON strings contain an unescaped control character");
      this.#index += 1;
    }
    this.#fail("unterminated JSON string");
  }

  #parseNumber(): number {
    const remaining = this.#text.slice(this.#index);
    const match = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/u.exec(remaining);
    if (!match) this.#fail("invalid JSON number");
    const lexeme = match[0];
    this.#index += lexeme.length;
    const value = Number(lexeme);
    if (!Number.isFinite(value)) this.#fail("JSON numbers must be finite");
    return value;
  }

  #parseLiteral<T>(literal: string, value: T): T {
    if (this.#text.slice(this.#index, this.#index + literal.length) !== literal) {
      this.#fail("invalid JSON literal");
    }
    this.#index += literal.length;
    return value;
  }

  #skipWhitespace(): void {
    while (isJsonWhitespace(this.#text[this.#index])) this.#index += 1;
  }

  #fail(message: string): never {
    throw new Error(`${this.#context} JSON: ${message} at character ${String(this.#index)}`);
  }
}

function isJsonWhitespace(value: string | undefined): boolean {
  return value === " " || value === "\t" || value === "\r" || value === "\n";
}
