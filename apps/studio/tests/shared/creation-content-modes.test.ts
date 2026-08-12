import { describe, expect, it } from "vitest";

import {
  CREATION_CONTENT_MODES,
  DEFAULT_CREATION_CONTENT_MODE,
  isCreationContentMode,
} from "../../src/generated/creation-content-modes";

describe("generated creation content mode constants", () => {
  it("exports the canonical closed production vocabulary and default", () => {
    expect(CREATION_CONTENT_MODES).toEqual([
      "authored",
      "modular",
      "deterministic_procedural",
      "generated_at_authoring_time",
      "player_generated",
      "hybrid",
      "not_applicable",
    ]);
    expect(DEFAULT_CREATION_CONTENT_MODE).toBe("authored");
    expect(isCreationContentMode("not_applicable")).toBe(true);
    expect(isCreationContentMode("unknown")).toBe(false);
  });
});
