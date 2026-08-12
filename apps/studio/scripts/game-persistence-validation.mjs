import { createHash } from "node:crypto";
import { types as utilTypes } from "node:util";

const MAX_CANONICAL_PERSISTENCE_BYTES = 4 * 1024 * 1024 + 64 * 1024;
const MAX_CANONICAL_PERSISTENCE_DEPTH = 64;
const MAX_CANONICAL_PERSISTENCE_NODES = 100_000;

function utf8Compare(left, right) {
  return Buffer.compare(Buffer.from(left, "utf8"), Buffer.from(right, "utf8"));
}

function isUnicodeScalarString(value) {
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index);
    if (codeUnit >= 0xd800 && codeUnit <= 0xdbff) {
      const low = value.charCodeAt(index + 1);
      if (!(low >= 0xdc00 && low <= 0xdfff)) {
        return false;
      }
      index += 1;
    } else if (codeUnit >= 0xdc00 && codeUnit <= 0xdfff) {
      return false;
    }
  }
  return true;
}

function canonicalBytes(value, { omitContentHash = false } = {}) {
  const seen = new WeakSet();
  let nodes = 0;
  let stringCodeUnits = 0;

  function accountString(candidate) {
    if (
      !isUnicodeScalarString(candidate) ||
      candidate.normalize("NFC") !== candidate
    ) {
      throw new TypeError("canonical persistence strings must be NFC scalars");
    }
    stringCodeUnits += candidate.length;
    if (stringCodeUnits > MAX_CANONICAL_PERSISTENCE_BYTES) {
      throw new TypeError("canonical persistence string budget exceeded");
    }
  }

  function dataValue(source, key) {
    const descriptor = Object.getOwnPropertyDescriptor(source, key);
    if (
      descriptor === undefined ||
      !("value" in descriptor) ||
      !descriptor.enumerable
    ) {
      throw new TypeError(
        "canonical persistence values require enumerable data properties",
      );
    }
    return descriptor.value;
  }

  function allocate(candidate, depth) {
    nodes += 1;
    if (nodes > MAX_CANONICAL_PERSISTENCE_NODES) {
      throw new TypeError("canonical persistence node budget exceeded");
    }
    if (candidate === null || typeof candidate === "boolean") {
      return { frame: null, snapshot: candidate };
    }
    if (typeof candidate === "number") {
      if (!Number.isSafeInteger(candidate)) {
        throw new TypeError(
          "canonical persistence numbers must be safe integers",
        );
      }
      return { frame: null, snapshot: candidate };
    }
    if (typeof candidate === "string") {
      accountString(candidate);
      return { frame: null, snapshot: candidate };
    }
    if (typeof candidate !== "object" || utilTypes.isProxy(candidate)) {
      throw new TypeError("canonical persistence value is not plain JSON");
    }
    if (depth > MAX_CANONICAL_PERSISTENCE_DEPTH || seen.has(candidate)) {
      throw new TypeError(
        "canonical persistence depth, cycle, or alias is invalid",
      );
    }
    seen.add(candidate);
    if (Array.isArray(candidate)) {
      const prototype = Object.getPrototypeOf(candidate);
      if (prototype !== Array.prototype && prototype !== null) {
        throw new TypeError("canonical persistence arrays must be plain");
      }
      const length = candidate.length;
      const ownKeys = Reflect.ownKeys(candidate);
      if (
        ownKeys.some((key) => typeof key !== "string") ||
        ownKeys.length !== length + 1
      ) {
        throw new TypeError(
          "canonical persistence arrays must be dense without custom keys",
        );
      }
      const snapshot = new Array(length);
      return {
        frame: {
          depth,
          kind: "array",
          length,
          snapshot,
          source: candidate,
        },
        snapshot,
      };
    }
    const prototype = Object.getPrototypeOf(candidate);
    if (prototype !== Object.prototype && prototype !== null) {
      throw new TypeError("canonical persistence objects must be plain");
    }
    const keys = Reflect.ownKeys(candidate);
    if (keys.some((key) => typeof key !== "string")) {
      throw new TypeError("canonical persistence symbol keys are invalid");
    }
    const snapshot = Object.create(null);
    return {
      frame: {
        depth,
        keys,
        kind: "object",
        snapshot,
        source: candidate,
      },
      snapshot,
    };
  }

  let root;
  try {
    root = allocate(value, 1);
    if (root.frame === null || root.frame.kind !== "object") {
      return null;
    }
    const pending = [root.frame];
    while (pending.length > 0) {
      const frame = pending.pop();
      if (frame.kind === "array") {
        for (let index = frame.length - 1; index >= 0; index -= 1) {
          const item = allocate(
            dataValue(frame.source, String(index)),
            frame.depth + 1,
          );
          frame.snapshot[index] = item.snapshot;
          if (item.frame !== null) {
            pending.push(item.frame);
          }
        }
        continue;
      }
      for (let index = frame.keys.length - 1; index >= 0; index -= 1) {
        const key = frame.keys[index];
        if (typeof key !== "string" || key.length === 0) {
          throw new TypeError(
            "canonical persistence object keys must be non-empty strings",
          );
        }
        accountString(key);
        const item = allocate(
          dataValue(frame.source, key),
          frame.depth + 1,
        );
        Object.defineProperty(frame.snapshot, key, {
          configurable: true,
          enumerable: true,
          value: item.snapshot,
          writable: true,
        });
        if (item.frame !== null) {
          pending.push(item.frame);
        }
      }
    }
  } catch {
    return null;
  }

  const chunks = [];
  let bytes = 0;
  function append(text) {
    bytes += Buffer.byteLength(text, "utf8");
    if (bytes > MAX_CANONICAL_PERSISTENCE_BYTES) {
      throw new TypeError("canonical persistence byte budget exceeded");
    }
    chunks.push(text);
  }

  try {
    const pending = [
      {
        kind: "value",
        root: true,
        value: root.snapshot,
      },
    ];
    while (pending.length > 0) {
      const token = pending.pop();
      if (token.kind === "literal") {
        append(token.value);
        continue;
      }
      const candidate = token.value;
      if (Array.isArray(candidate)) {
        append("[");
        pending.push({ kind: "literal", value: "]" });
        for (let index = candidate.length - 1; index >= 0; index -= 1) {
          if (index < candidate.length - 1) {
            pending.push({ kind: "literal", value: "," });
          }
          pending.push({
            kind: "value",
            root: false,
            value: candidate[index],
          });
        }
        continue;
      }
      if (candidate !== null && typeof candidate === "object") {
        const keys = Object.keys(candidate)
          .filter(
            (key) =>
              !(token.root && omitContentHash && key === "content_hash"),
          )
          .sort(utf8Compare);
        append("{");
        pending.push({ kind: "literal", value: "}" });
        for (let index = keys.length - 1; index >= 0; index -= 1) {
          const key = keys[index];
          if (index < keys.length - 1) {
            pending.push({ kind: "literal", value: "," });
          }
          pending.push({
            kind: "value",
            root: false,
            value: candidate[key],
          });
          pending.push({ kind: "literal", value: ":" });
          pending.push({
            kind: "literal",
            value: JSON.stringify(key),
          });
        }
        continue;
      }
      const encoded = JSON.stringify(candidate);
      if (typeof encoded !== "string") {
        return null;
      }
      append(encoded);
    }
  } catch {
    return null;
  }
  return Buffer.from(chunks.join(""), "utf8");
}

function digest(value, options) {
  const payload = canonicalBytes(value, options);
  return payload === null
    ? null
    : createHash("sha256").update(payload).digest("hex");
}

export function canonicalGamePersistenceContentHash(value) {
  return digest(value, { omitContentHash: true });
}

export function canonicalGamePersistenceByteLength(value) {
  const payload = canonicalBytes(value);
  return payload === null ? null : payload.byteLength;
}

function canonicalGamePersistenceValueHash(value) {
  return digest(value);
}

export function canonicalGamePersistenceId(value) {
  if (
    value === null ||
    typeof value !== "object" ||
    Array.isArray(value)
  ) {
    return null;
  }
  const replay = value.format === "world-forge.game_replay";
  const save = value.format === "world-forge.game_save";
  if (!replay && !save) {
    return null;
  }
  const identifier = replay ? "replay_id" : "save_id";
  const seed = Object.fromEntries(
    Object.entries(value).filter(
      ([key]) => key !== identifier && key !== "content_hash",
    ),
  );
  const hash = canonicalGamePersistenceValueHash(seed);
  return hash === null
    ? null
    : `${replay ? "game_replay_" : "game_save_"}${hash.slice(0, 48)}`;
}

function isRecord(value) {
  const prototype =
    value !== null && typeof value === "object"
      ? Object.getPrototypeOf(value)
      : undefined;
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    (prototype === Object.prototype || prototype === null)
  );
}

function isSha(value) {
  return typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
}

function exactKeys(value, expected) {
  return (
    isRecord(value) &&
    Object.keys(value).sort(utf8Compare).join("\0") ===
      [...expected].sort(utf8Compare).join("\0")
  );
}

function coherentBindings(value) {
  if (
    !exactKeys(value, [
      "execution_semantics",
      "gamepack",
      "runtime_api",
      "runtime_bundle",
      "runtime_composition",
    ])
  ) {
    return false;
  }
  for (const field of [
    "gamepack",
    "runtime_bundle",
    "runtime_composition",
  ]) {
    if (
      !exactKeys(value[field], [
        "content_hash",
        "format",
        "format_version",
        "id",
      ]) ||
      !isSha(value[field].content_hash)
    ) {
      return false;
    }
  }
  return (
    exactKeys(value.runtime_api, ["id", "version"]) &&
    value.runtime_api.id === "gamepack_runtime" &&
    value.runtime_api.version === "1.0.0" &&
    exactKeys(value.execution_semantics, ["content_hash", "version"]) &&
    value.execution_semantics.version === 1 &&
    isSha(value.execution_semantics.content_hash)
  );
}

function coherentClassification(value) {
  return exactKeys(value, [
    "ending_ids",
    "ending_kind",
    "failure_ids",
    "goal_ids",
    "recovery_action_ids",
    "terminal",
  ]);
}

function coherentSave(value) {
  return (
    exactKeys(value, [
      "bindings",
      "content_hash",
      "format",
      "format_version",
      "save_id",
      "state",
    ]) &&
    coherentBindings(value.bindings) &&
    exactKeys(value.state, [
      "classification",
      "restored_state_hash",
      "saved",
      "saved_hash",
    ]) &&
    isRecord(value.state.saved) &&
    coherentClassification(value.state.classification) &&
    value.state.saved_hash ===
      canonicalGamePersistenceValueHash(value.state.saved) &&
    isSha(value.state.restored_state_hash)
  );
}

function coherentReplay(value) {
  if (
    !exactKeys(value, [
      "bindings",
      "classification",
      "content_hash",
      "final_state_hash",
      "format",
      "format_version",
      "initial_state_hash",
      "replay_id",
      "steps",
      "trace_hash",
    ]) ||
    !coherentBindings(value.bindings) ||
    !coherentClassification(value.classification) ||
    !Array.isArray(value.steps) ||
    value.steps.length > 128 ||
    !isSha(value.initial_state_hash) ||
    !isSha(value.final_state_hash)
  ) {
    return false;
  }
  for (let index = 0; index < value.steps.length; index += 1) {
    const step = value.steps[index];
    if (
      !exactKeys(step, [
        "action_id",
        "events",
        "index",
        "parameters",
        "post_state_hash",
        "pre_state_hash",
      ]) ||
      step.index !== index ||
      !isRecord(step.parameters) ||
      !Array.isArray(step.events) ||
      !isSha(step.pre_state_hash) ||
      !isSha(step.post_state_hash)
    ) {
      return false;
    }
  }
  return (
    value.trace_hash ===
    canonicalGamePersistenceValueHash({ steps: value.steps })
  );
}

function coherentGenerationParents(value) {
  if (!Array.isArray(value) || value.length > 128) {
    return false;
  }
  const parents = [];
  for (let index = 0; index < value.length; index += 1) {
    if (!isSha(value[index])) {
      return false;
    }
    parents.push(value[index]);
  }
  return (
    parents.join("\0") ===
      [...parents].sort(utf8Compare).join("\0") &&
    new Set(parents).size === parents.length
  );
}

function coherentGeneration(value) {
  if (
    !exactKeys(value, [
      "content_hash",
      "format",
      "format_version",
      "kind",
      "operation",
      "parent_hashes",
      "payload",
      "payload_hash",
      "sequence",
      "slot",
    ]) ||
    !["save", "replay"].includes(value.kind) ||
    typeof value.slot !== "string" ||
    !/^[a-z][a-z0-9_-]{0,31}$/u.test(value.slot) ||
    /^(?:aux|con|nul|prn|com[1-9]|lpt[1-9])$/u.test(value.slot) ||
    !Number.isSafeInteger(value.sequence) ||
    value.sequence < 0 ||
    !coherentGenerationParents(value.parent_hashes) ||
    ![
      "conflict_resolution",
      "legacy_migration",
      "rollback",
      "write",
    ].includes(value.operation) ||
    !hasCoherentGamePersistence(value.payload) ||
    value.payload_hash !== value.payload.content_hash ||
    (value.kind === "save" &&
      value.payload.format !== "world-forge.game_save") ||
    (value.kind === "replay" &&
      value.payload.format !== "world-forge.game_replay")
  ) {
    return false;
  }
  if (value.sequence === 0) {
    return (
      value.parent_hashes.length === 0 &&
      ["legacy_migration", "write"].includes(value.operation)
    );
  }
  if (
    value.parent_hashes.length === 0 ||
    value.operation === "legacy_migration"
  ) {
    return false;
  }
  if (["rollback", "write"].includes(value.operation)) {
    return value.parent_hashes.length === 1;
  }
  return (
    value.operation === "conflict_resolution" &&
    value.parent_hashes.length >= 2
  );
}

export function hasCoherentPersistenceGeneration(value) {
  return (
    isRecord(value) &&
    value.format === "world-forge.persistence_generation" &&
    value.format_version === 1 &&
    value.content_hash ===
      canonicalGamePersistenceContentHash(value) &&
    coherentGeneration(value)
  );
}

export function hasCoherentGamePersistence(value) {
  if (
    !isRecord(value) ||
    value.format_version !== 1 ||
    value.content_hash !==
      canonicalGamePersistenceContentHash(value)
  ) {
    return false;
  }
  if (value.format === "world-forge.persistence_generation") {
    return coherentGeneration(value);
  }
  if (
    value[
      value.format === "world-forge.game_save"
        ? "save_id"
        : "replay_id"
    ] !== canonicalGamePersistenceId(value)
  ) {
    return false;
  }
  if (value.format === "world-forge.game_save") {
    return coherentSave(value);
  }
  if (value.format === "world-forge.game_replay") {
    return coherentReplay(value);
  }
  return false;
}
