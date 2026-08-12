/* AUTO-GENERATED from schemas/creation-profile.schema.json. Do not edit by hand. */
export const CREATION_CONTENT_MODES = [
  "authored",
  "modular",
  "deterministic_procedural",
  "generated_at_authoring_time",
  "player_generated",
  "hybrid",
  "not_applicable"
] as const;
export type CreationContentMode = (typeof CREATION_CONTENT_MODES)[number];
export const DEFAULT_CREATION_CONTENT_MODE: CreationContentMode = "authored";
const CREATION_CONTENT_MODE_SET: ReadonlySet<string> = new Set(CREATION_CONTENT_MODES);
export function isCreationContentMode(value: unknown): value is CreationContentMode {
  return typeof value === "string" && CREATION_CONTENT_MODE_SET.has(value);
}
