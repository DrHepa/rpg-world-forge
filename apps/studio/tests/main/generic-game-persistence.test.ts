import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  canonicalGamePersistenceContentHash,
  canonicalGamePersistenceId,
} from "../../scripts/game-persistence-validation.mjs";
import {
  GENERIC_GAME_PERSISTENCE_INSPECTOR_RUNTIME,
  buildGenericGamePersistencePythonInvocation,
  inspectGenericGamePersistence,
  validateGenericGamePersistence,
} from "../../src/main/generic-game-persistence";

const repositoryRoot = path.resolve(import.meta.dirname, "../../../..");

async function fixture(relative: string): Promise<unknown> {
  return JSON.parse(
    await readFile(path.join(repositoryRoot, relative), "utf8"),
  ) as unknown;
}

function record(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("fixture is not an object");
  }
  return value as Record<string, unknown>;
}

function unsafeCanonicalHash(
  value: unknown,
  { omitContentHash = false }: { omitContentHash?: boolean } = {},
): string {
  function copy(candidate: unknown, root = false): unknown {
    if (Array.isArray(candidate)) {
      return candidate.map((item) => copy(item));
    }
    if (candidate !== null && typeof candidate === "object") {
      return Object.fromEntries(
        Object.keys(candidate)
          .filter((key) => !(root && omitContentHash && key === "content_hash"))
          .sort((left, right) =>
            Buffer.compare(Buffer.from(left, "utf8"), Buffer.from(right, "utf8")),
          )
          .map((key) => [
            key,
            copy(Reflect.get(candidate, key)),
          ]),
      );
    }
    return candidate;
  }
  return createHash("sha256")
    .update(Buffer.from(JSON.stringify(copy(value, true)), "utf8"))
    .digest("hex");
}

function unsafeResealSave(save: Record<string, unknown>): void {
  const seed = Object.fromEntries(
    Object.entries(save).filter(
      ([key]) => key !== "save_id" && key !== "content_hash",
    ),
  );
  save.save_id = `game_save_${unsafeCanonicalHash(seed).slice(0, 48)}`;
  save.content_hash = unsafeCanonicalHash(save, {
    omitContentHash: true,
  });
}

describe("generic game persistence inspection", () => {
  it("canonicalizes numeric-looking keys in lexical UTF-8 order", () => {
    expect(
      canonicalGamePersistenceContentHash({
        "10": "a",
        "2": "b",
        content_hash: "",
      }),
    ).toBe(
      createHash("sha256")
        .update(Buffer.from('{"10":"a","2":"b"}', "utf8"))
        .digest("hex"),
    );
  });

  it("structurally inspects save and replay without claiming semantic execution", async () => {
    const save = await fixture(
      "examples/multigenre-contracts/abstract-puzzle/runtime/persistence/" +
        "saves/solved.json",
    );
    const replay = await fixture(
      "examples/multigenre-contracts/branching-narrative/runtime/persistence/" +
        "replays/right.json",
    );
    const generation = await fixture(
      "examples/multigenre-contracts/abstract-puzzle/runtime/persistence/" +
        "generations/saves/initial.json",
    );

    const validatedSave = validateGenericGamePersistence(save);
    const validatedReplay = validateGenericGamePersistence(replay);
    const validatedGeneration =
      validateGenericGamePersistence(generation);
    expect(validatedSave).not.toBeNull();
    expect(validatedReplay).not.toBeNull();
    expect(validatedGeneration).not.toBeNull();
    expect(inspectGenericGamePersistence(validatedSave)).toEqual({
      content_hash: record(save).content_hash,
      format: "world-forge.game_save",
      id: record(save).save_id,
      semantic_verification: "required_python",
      status: "structurally_valid",
    });
    expect(inspectGenericGamePersistence(validatedReplay)).toEqual({
      content_hash: record(replay).content_hash,
      format: "world-forge.game_replay",
      id: record(replay).replay_id,
      semantic_verification: "required_python",
      status: "structurally_valid",
    });
    expect(
      inspectGenericGamePersistence(validatedGeneration),
    ).toEqual({
      content_hash: record(generation).content_hash,
      format: "world-forge.persistence_generation",
      id: record(generation).content_hash,
      semantic_verification: "required_python",
      status: "structurally_valid",
    });
    expect(
      GENERIC_GAME_PERSISTENCE_INSPECTOR_RUNTIME.semantic_boundary,
    ).toBe("packaged_python_required");
    expect(
      GENERIC_GAME_PERSISTENCE_INSPECTOR_RUNTIME.interprets_gameplay,
    ).toBe(false);
  });

  it("retains an immutable validated graph before structural inspection", async () => {
    const validated = validateGenericGamePersistence(
      await fixture(
        "examples/multigenre-contracts/abstract-puzzle/runtime/persistence/" +
          "saves/solved.json",
      ),
    );
    expect(validated).not.toBeNull();
    if (validated === null) {
      throw new Error("fixture save did not validate");
    }
    const state = record(validated.state);
    const saved = record(state.saved);
    const board = saved.board;
    if (!Array.isArray(board)) {
      throw new Error("fixture save board is missing");
    }
    expect(Object.isFrozen(validated)).toBe(true);
    expect(Object.isFrozen(state)).toBe(true);
    expect(Object.isFrozen(saved)).toBe(true);
    expect(Object.isFrozen(board)).toBe(true);
    expect(() => {
      board[0] = 999;
    }).toThrow(TypeError);
    expect(inspectGenericGamePersistence(validated)?.status).toBe(
      "structurally_valid",
    );
  });

  it("rejects self-resealed saved projection and replay trace incoherence", async () => {
    const save = structuredClone(
      record(await fixture(
        "examples/multigenre-contracts/abstract-puzzle/runtime/persistence/" +
          "saves/solved.json",
      )),
    );
    record(save.state).saved_hash = "f".repeat(64);
    save.save_id = canonicalGamePersistenceId(save) ?? "";
    save.content_hash =
      canonicalGamePersistenceContentHash(save) ?? "";
    expect(validateGenericGamePersistence(save)).toBeNull();

    const replay = structuredClone(
      record(await fixture(
        "examples/multigenre-contracts/abstract-puzzle/runtime/persistence/" +
          "replays/solve.json",
      )),
    );
    const steps = replay.steps;
    if (!Array.isArray(steps)) {
      throw new Error("fixture replay steps are missing");
    }
    record(steps[0]).index = 7;
    replay.trace_hash =
      canonicalGamePersistenceContentHash({ steps: replay.steps }) ?? "";
    replay.replay_id = canonicalGamePersistenceId(replay) ?? "";
    replay.content_hash =
      canonicalGamePersistenceContentHash(replay) ?? "";
    expect(validateGenericGamePersistence(replay)).toBeNull();
  });

  it("rejects a self-resealed save containing a lone surrogate", async () => {
    const save = structuredClone(
      record(await fixture(
        "examples/multigenre-contracts/abstract-puzzle/runtime/persistence/" +
          "saves/solved.json",
      )),
    );
    const saved = record(record(save.state).saved);
    const board = saved.board;
    if (!Array.isArray(board)) {
      throw new Error("fixture save board is missing");
    }
    board[0] = "\ud800";
    record(save.state).saved_hash = unsafeCanonicalHash(saved);
    unsafeResealSave(save);

    expect(validateGenericGamePersistence(save)).toBeNull();
    expect(canonicalGamePersistenceId(save)).toBeNull();
    expect(canonicalGamePersistenceContentHash(save)).toBeNull();
  });

  it("rejects a self-resealed save containing a lone-surrogate key", async () => {
    const save = structuredClone(
      record(await fixture(
        "examples/multigenre-contracts/abstract-puzzle/runtime/persistence/" +
          "saves/solved.json",
      )),
    );
    const saved = record(record(save.state).saved);
    Reflect.set(saved, "\ud800", 0);
    record(save.state).saved_hash = unsafeCanonicalHash(saved);
    unsafeResealSave(save);

    expect(validateGenericGamePersistence(save)).toBeNull();
    expect(canonicalGamePersistenceId(save)).toBeNull();
    expect(canonicalGamePersistenceContentHash(save)).toBeNull();
  });

  it("rejects a structurally valid save beyond the Python byte budget", async () => {
    const save = structuredClone(
      record(await fixture(
        "examples/multigenre-contracts/abstract-puzzle/runtime/persistence/" +
          "saves/solved.json",
      )),
    );
    const saved = record(record(save.state).saved);
    for (let index = 0; index < 100; index += 1) {
      saved[`padding_${index.toString().padStart(3, "0")}`] =
        "x".repeat(3_000);
    }
    record(save.state).saved_hash =
      canonicalGamePersistenceContentHash(saved) ?? "";
    save.save_id = canonicalGamePersistenceId(save) ?? "";
    save.content_hash =
      canonicalGamePersistenceContentHash(save) ?? "";

    expect(validateGenericGamePersistence(save)).toBeNull();
  });

  it("routes semantic verification only through isolated packaged Python", () => {
    const invocation = buildGenericGamePersistencePythonInvocation({
      bundleRoot: "/tmp/world-forge/bundle",
      kind: "replay",
      pythonExecutable: "/tmp/world-forge/python",
      source: "/tmp/world-forge/replay.json",
    });
    expect(invocation).toEqual({
      args: [
        "-I",
        "-B",
        "-m",
        "worldforge",
        "verify-game-replay",
        "/tmp/world-forge/replay.json",
        "--bundle",
        "/tmp/world-forge/bundle",
      ],
      executable: "/tmp/world-forge/python",
    });

    const generationInvocation =
      buildGenericGamePersistencePythonInvocation({
        bundleRoot: "/tmp/world-forge/bundle",
        kind: "generation",
        pythonExecutable: "/tmp/world-forge/python",
        source: "/tmp/world-forge/generation.json",
      });
    expect(generationInvocation.args).toContain(
      "verify-persistence-generation",
    );
  });

  it("rejects self-resealed generation kind and payload-hash incoherence", async () => {
    const generation = structuredClone(
      record(await fixture(
        "examples/multigenre-contracts/abstract-puzzle/runtime/persistence/" +
          "generations/saves/initial.json",
      )),
    );
    generation.kind = "replay";
    generation.content_hash =
      canonicalGamePersistenceContentHash(generation) ?? "";
    expect(validateGenericGamePersistence(generation)).toBeNull();

    generation.kind = "save";
    generation.payload_hash = "f".repeat(64);
    generation.content_hash =
      canonicalGamePersistenceContentHash(generation) ?? "";
    expect(validateGenericGamePersistence(generation)).toBeNull();
  });
});
