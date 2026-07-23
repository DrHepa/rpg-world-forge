import { readFileSync } from "node:fs";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import Ajv2020 from "ajv/dist/2020.js";
import { describe, expect, it } from "vitest";

type JsonScalar = boolean | null | number | string;
type JsonValue = JsonObject | JsonScalar | JsonValue[];
type JsonObject = { [key: string]: JsonValue };
type JsonPath = Array<number | string>;

const testRoot = fileURLToPath(new URL(".", import.meta.url));
const studioRoot = path.resolve(testRoot, "../..");
const repoRoot = path.resolve(studioRoot, "../..");
const schema = JSON.parse(
  readFileSync(path.join(studioRoot, "packaging/runtime-sources.schema.json"), "utf8"),
) as JsonObject;
const source = JSON.parse(
  readFileSync(path.join(studioRoot, "packaging/runtime-sources.json"), "utf8"),
) as JsonObject;

function getAtPath(document: JsonObject, targetPath: JsonPath): JsonValue {
  let cursor: JsonValue = document;
  for (const component of targetPath) {
    if (Array.isArray(cursor) && typeof component === "number") {
      cursor = cursor[component];
    } else if (
      !Array.isArray(cursor) &&
      typeof cursor === "object" &&
      cursor !== null &&
      typeof component === "string"
    ) {
      cursor = cursor[component];
    } else {
      throw new Error("invalid JSON test path");
    }
  }
  return cursor;
}

function setAtPath(document: JsonObject, targetPath: JsonPath, value: JsonValue): void {
  const parentPath = targetPath.slice(0, -1);
  const key = targetPath.at(-1);
  const parent = getAtPath(document, parentPath);
  if (Array.isArray(parent) && typeof key === "number") {
    parent[key] = value;
    return;
  }
  if (
    !Array.isArray(parent) &&
    typeof parent === "object" &&
    parent !== null &&
    typeof key === "string"
  ) {
    parent[key] = value;
    return;
  }
  throw new Error("invalid JSON test mutation path");
}

function collectNumericPaths(value: JsonValue, prefix: JsonPath = []): JsonPath[] {
  if (Array.isArray(value)) {
    return value.flatMap((child, index) => collectNumericPaths(child, [...prefix, index]));
  }
  if (typeof value === "object" && value !== null) {
    return Object.entries(value).flatMap(([key, child]) =>
      collectNumericPaths(child, [...prefix, key]),
    );
  }
  return typeof value === "number" && Number.isInteger(value) ? [prefix] : [];
}

function jsonWithNumericToken(targetPath: JsonPath, token: string): string {
  const document = structuredClone(source);
  const sentinel = "__RWF_NUMERIC_TOKEN__";
  setAtPath(document, targetPath, sentinel);
  const encoded = JSON.stringify(document);
  const needle = JSON.stringify(sentinel);
  if (encoded.split(needle).length !== 2) {
    throw new Error("numeric token sentinel was not unique");
  }
  return encoded.replace(needle, token);
}

function findPython(): { command: string; prefix: string[] } {
  const candidates = [
    process.env.PYTHON ? { command: process.env.PYTHON, prefix: [] } : null,
    { command: "python3", prefix: [] },
    { command: "python", prefix: [] },
    { command: "py", prefix: ["-3"] },
  ].filter((candidate): candidate is { command: string; prefix: string[] } =>
    Boolean(candidate),
  );
  for (const candidate of candidates) {
    const probe = spawnSync(candidate.command, [...candidate.prefix, "--version"], {
      cwd: repoRoot,
      stdio: "ignore",
    });
    if (probe.status === 0) {
      return candidate;
    }
  }
  throw new Error("supported Python interpreter was not found");
}

async function validateWithPython(documents: string[]): Promise<boolean[]> {
  const python = findPython();
  const program = [
    "import base64,json,sys",
    "from scripts.studio_runtime_sources import RuntimeSourcesError,load_strict_json_bytes,validate_document",
    "results=[]",
    "for encoded in json.load(sys.stdin):",
    "    try:",
    "        validate_document(load_strict_json_bytes(base64.b64decode(encoded)))",
    "    except RuntimeSourcesError:",
    "        results.append(False)",
    "    else:",
    "        results.append(True)",
    "json.dump(results,sys.stdout,separators=(',',':'))",
  ].join("\n");
  const encoded = documents.map((document) =>
    Buffer.from(document, "utf8").toString("base64"),
  );
  const stdout = await new Promise<string>((resolve, reject) => {
    const child = spawn(python.command, [...python.prefix, "-c", program], {
      cwd: repoRoot,
      stdio: ["pipe", "pipe", "pipe"],
    });
    let output = "";
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      output += chunk;
    });
    child.once("error", () => {
      reject(new Error("Python provenance parity validator could not start"));
    });
    child.stdin.once("error", () => {
      reject(new Error("Python provenance parity corpus could not be written"));
    });
    child.once("close", (status) => {
      if (status !== 0) {
        reject(new Error("Python provenance parity validator failed"));
        return;
      }
      resolve(output);
    });
    child.stdin.end(JSON.stringify(encoded), "utf8");
  });
  return JSON.parse(stdout) as boolean[];
}

describe("Studio runtime source schema parity", () => {
  it("matches Python for every fixed numeric field and pinned structural mutation", async () => {
    const ajv = new Ajv2020({ allErrors: true, strict: true });
    const validate = ajv.compile(schema);
    const documents: string[] = [JSON.stringify(source)];
    const expected: boolean[] = [true];
    const labels: string[] = ["checked source"];
    const numericPaths = collectNumericPaths(source);
    expect(numericPaths.length).toBeGreaterThan(20);

    for (const numericPath of numericPaths) {
      const value = getAtPath(source, numericPath);
      if (typeof value !== "number") {
        throw new Error("numeric test path did not resolve to a number");
      }
      for (const [label, token, accepted] of [
        ["decimal equivalent", `${value}.0`, true],
        ["decimal zero equivalent", `${value}.000`, true],
        ["exponent equivalent", `${value}e0`, true],
        ["boolean true", "true", false],
        ["boolean false", "false", false],
        ["fraction", `${value}.5`, false],
        ["wrong exponent", `${value}e1`, false],
      ] as const) {
        documents.push(jsonWithNumericToken(numericPath, token));
        expected.push(accepted);
        labels.push(`${label} at ${numericPath.join(".")}`);
      }
    }

    const structuralMutations: Array<[string, (document: JsonObject) => void]> = [
      [
        "wrong Codex size",
        (document) =>
          setAtPath(document, ["codex", "targets", 0, "archive", "size"], 131212688),
      ],
      [
        "duplicate inventory path",
        (document) =>
          setAtPath(
            document,
            ["codex", "targets", 0, "inventory", 1, "path"],
            getAtPath(document, ["codex", "targets", 0, "inventory", 0, "path"]),
          ),
      ],
      [
        "duplicate Codex target",
        (document) =>
          setAtPath(document, ["codex", "targets", 1, "target_id"], "linux-x64"),
      ],
      [
        "duplicate Python target",
        (document) =>
          setAtPath(document, ["python", "targets", 1, "target_id"], "linux-x64"),
      ],
      [
        "resealed Codex source commit",
        (document) => {
          const replacement = "6d1fbf26c43abc65a203928b2e31561cb039e06d";
          setAtPath(document, ["codex", "commit"], replacement);
          setAtPath(
            document,
            ["codex", "targets", 0, "npm_provenance", "source_commit"],
            replacement,
          );
          setAtPath(
            document,
            ["codex", "targets", 1, "npm_provenance", "source_commit"],
            replacement,
          );
        },
      ],
      [
        "resealed Python source commit",
        (document) => {
          const replacement = "1e4d9c24b72d28573e622518f09b16aef4a33be8";
          setAtPath(document, ["python", "commit"], replacement);
          setAtPath(
            document,
            ["python", "release_attestation", "subject_commit_sha1"],
            replacement,
          );
        },
      ],
      [
        "wrong Python runtime size",
        (document) =>
          setAtPath(
            document,
            ["python", "targets", 0, "runtime_archive", "size"],
            34199824,
          ),
      ],
      [
        "wrong Python metadata size",
        (document) =>
          setAtPath(
            document,
            ["python", "targets", 0, "metadata_archive", "size"],
            112247884,
          ),
      ],
    ];
    for (const [label, mutate] of structuralMutations) {
      const document = structuredClone(source);
      mutate(document);
      documents.push(JSON.stringify(document));
      expected.push(false);
      labels.push(label);
    }

    const ajvResults = documents.map((document, index) => {
      const accepted = validate(JSON.parse(document));
      if (accepted !== expected[index]) {
        throw new Error(
          `Ajv mismatch for ${labels[index]}: ${JSON.stringify(validate.errors)}`,
        );
      }
      return accepted;
    });
    const pythonResults = await validateWithPython(documents);
    expect(pythonResults).toEqual(expected);
    expect(ajvResults).toEqual(expected);
  });
});
