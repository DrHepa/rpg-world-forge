import {
  parseCreationObjectJson,
  snapshotCreationJson,
} from "./creation-state";

export const CREATION_MODULE_GROUPS = [
  { collection: "world_modules", label: "World", format: "world-forge.world_module" },
  { collection: "activity_modules", label: "Activities", format: "world-forge.activity_module" },
  { collection: "narrative_modules", label: "Narrative", format: "world-forge.narrative_module" },
  { collection: "system_modules", label: "Systems", format: "world-forge.system_module" },
  { collection: "logic_modules", label: "Logic", format: "world-forge.logic_module" },
] as const;

export type CreationModuleCollection = (typeof CREATION_MODULE_GROUPS)[number]["collection"];
export type CreationModuleFormat = (typeof CREATION_MODULE_GROUPS)[number]["format"];

export interface CreationModuleReference {
  collection: CreationModuleCollection;
  label: string;
  format: CreationModuleFormat;
  formatVersion: 1;
  id: string;
  manifestPath: string;
  projectPath: string;
  contentHash: string;
}

export type CreationModuleDocument = Record<string, unknown> & {
  format: CreationModuleFormat;
  format_version: 1;
  module_id: string;
  project_id: string;
  content_hash: string;
};

const SHA256 = /^[0-9a-f]{64}$/u;
const ENTITY_ID = /^[a-z0-9][a-z0-9_-]{0,127}$/u;
const RESERVED_SEGMENT = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/iu;

export function parseCreationModuleJson(
  source: string,
  context: string,
): Record<string, unknown> {
  return parseCreationObjectJson(source, context);
}

export function resolveCreationModuleReferences(
  manifestPath: string,
  value: unknown,
): CreationModuleReference[] {
  if (!isPortablePath(manifestPath) || !isRecord(value)) {
    throw new Error("Creation source manifest identity is invalid");
  }
  if (
    value.format !== "world-forge.creation_source_manifest" ||
    value.format_version !== 1 ||
    typeof value.project_id !== "string" ||
    !isRecord(value.modules)
  ) {
    throw new Error("Creation source manifest format is unsupported");
  }
  const directory = manifestPath.includes("/")
    ? manifestPath.slice(0, manifestPath.lastIndexOf("/"))
    : "";
  const references: CreationModuleReference[] = [];
  const ids = new Set<string>();
  const paths = new Set<string>();
  for (const group of CREATION_MODULE_GROUPS) {
    const raw = value.modules[group.collection];
    if (!Array.isArray(raw)) {
      throw new Error(`Creation source manifest ${group.collection} must be an array`);
    }
    let previousId: string | null = null;
    for (const candidate of raw) {
      if (!isRecord(candidate)) {
        throw new Error(`Creation source manifest ${group.collection} reference is invalid`);
      }
      const id = requireEntityId(candidate.id, "creation module reference");
      const manifestReferencePath = requirePortablePath(
        candidate.path,
        "creation module reference",
      );
      const contentHash = requireSha256(
        candidate.content_hash,
        "creation module reference",
      );
      if (candidate.format !== group.format || candidate.format_version !== 1) {
        throw new Error(`Creation source manifest ${group.collection} format differs`);
      }
      if (previousId !== null && compareUtf8(previousId, id) > 0) {
        throw new Error(`Creation source manifest ${group.collection} is not canonically sorted`);
      }
      previousId = id;
      const projectPath = directory
        ? `${directory}/${manifestReferencePath}`
        : manifestReferencePath;
      const idKey = unicodeKey(id);
      const pathKey = unicodeKey(projectPath);
      if (ids.has(idKey) || paths.has(pathKey)) {
        throw new Error("Creation source manifest module references collide");
      }
      ids.add(idKey);
      paths.add(pathKey);
      references.push({
        collection: group.collection,
        label: group.label,
        format: group.format,
        formatVersion: 1,
        id,
        manifestPath: manifestReferencePath,
        projectPath,
        contentHash,
      });
    }
  }
  return references;
}

export function validateCreationModuleDocument(
  value: unknown,
  reference: CreationModuleReference,
  projectId: string,
): CreationModuleDocument {
  const snapshot = snapshotCreationJson(value);
  if (!isRecord(snapshot)) throw new Error("Creation module must have an object root");
  if (
    snapshot.format !== reference.format ||
    snapshot.format_version !== reference.formatVersion ||
    snapshot.module_id !== reference.id ||
    snapshot.project_id !== projectId ||
    snapshot.content_hash !== reference.contentHash
  ) {
    throw new Error("Creation module identity differs from its manifest reference");
  }
  requireSha256(snapshot.content_hash, "creation module");
  return snapshot as CreationModuleDocument;
}

export function creationModulePreview(value: unknown): string {
  const snapshot = snapshotCreationJson(value);
  if (!isRecord(snapshot)) throw new Error("Creation module must have an object root");
  return `${JSON.stringify(sortJson(snapshot), null, 2)}\n`;
}

export function creationModuleGroupLabel(collection: CreationModuleCollection): string {
  return CREATION_MODULE_GROUPS.find((group) => group.collection === collection)?.label ?? collection;
}

export function validateCreationModuleId(value: unknown): string {
  if (typeof value !== "string" || !ENTITY_ID.test(value)) {
    throw new Error("Creation module ID is invalid");
  }
  return value;
}

function compareUtf8(left: string, right: string): number {
  const leftBytes = new TextEncoder().encode(left);
  const rightBytes = new TextEncoder().encode(right);
  const length = Math.min(leftBytes.length, rightBytes.length);
  for (let index = 0; index < length; index += 1) {
    const difference = leftBytes[index] - rightBytes[index];
    if (difference !== 0) return difference;
  }
  return leftBytes.length - rightBytes.length;
}

function requirePortablePath(value: unknown, context: string): string {
  if (typeof value !== "string" || !isPortablePath(value)) {
    throw new Error(`${context} path is not portable`);
  }
  return value;
}

function isPortablePath(value: string): boolean {
  if (
    value.length < 1 ||
    value !== value.normalize("NFC") ||
    value.startsWith("/") ||
    value.endsWith("/") ||
    value.includes("\\") ||
    hasPortableControl(value)
  ) return false;
  const segments = value.split("/");
  return segments.every(
    (segment) =>
      segment.length > 0 &&
      segment !== "." &&
      segment !== ".." &&
      !segment.endsWith(".") &&
      !segment.endsWith(" ") &&
      !RESERVED_SEGMENT.test(segment),
  );
}

function hasPortableControl(value: string): boolean {
  return [...value].some((character) => {
    const code = character.codePointAt(0) ?? 0;
    return code <= 0x1f || code === 0x7f;
  });
}

function requireEntityId(value: unknown, context: string): string {
  try {
    return validateCreationModuleId(value);
  } catch {
    throw new Error(`${context} ID is invalid`);
  }
}

function requireSha256(value: unknown, context: string): string {
  if (typeof value !== "string" || !SHA256.test(value)) {
    throw new Error(`${context} hash is invalid`);
  }
  return value;
}

function unicodeKey(value: string): string {
  return value.normalize("NFC").toLowerCase();
}

function sortJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortJson);
  if (!isRecord(value)) return value;
  const result: Record<string, unknown> = {};
  for (const key of Object.keys(value).sort()) result[key] = sortJson(value[key]);
  return result;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
