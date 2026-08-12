import { describe, expect, it } from "vitest";

import {
  MAX_STRICT_JSON_DEPTH,
  MAX_STRICT_JSON_NODES,
  decodeStrictJsonObject,
  snapshotStrictJsonObject,
} from "../../scripts/strict-json.mjs";
import { toPortableFixtureKey } from "../../scripts/generator-paths.mjs";

describe("generator fixture keys", () => {
  it("normalizes host-native Windows separators without changing portable keys", () => {
    expect(
      toPortableFixtureKey(
        String.raw`branching-narrative\source\narrative\branching.json`,
        "\\",
      ),
    ).toBe("branching-narrative/source/narrative/branching.json");
    expect(
      toPortableFixtureKey(
        "branching-narrative/source/narrative/branching.json",
        "/",
      ),
    ).toBe("branching-narrative/source/narrative/branching.json");
  });
});

describe("strict JSON decoder", () => {
  it("accepts bounded integer-only object documents", () => {
    expect(
      decodeStrictJsonObject(
        Buffer.from('{"array":[true,null,"text"],"integer":9007199254740991}', "utf8"),
        { context: "valid fixture" },
      ),
    ).toEqual({
      array: [true, null, "text"],
      integer: 9_007_199_254_740_991,
    });
  });

  it.each([
    ['{"key":1,"key":2}', "duplicate object key"],
    ['{"key":1,"\\u006bey":2}', "duplicate object key"],
    ['{"value":1.0}', "decimal or exponent"],
    ['{"value":1e0}', "decimal or exponent"],
    ['{"value":9007199254740992}', "safe integer"],
    ['{"value":-9007199254740992}', "safe integer"],
    ['{"value":"\\ud800"}', "Unicode scalar"],
    ["[]", "object root"],
  ])("rejects %s", (source, expected) => {
    expect(() =>
      decodeStrictJsonObject(Buffer.from(source, "utf8"), {
        context: "invalid fixture",
      }),
    ).toThrow(expected);
  });

  it("rejects invalid UTF-8, raw lone surrogates, and excessive depth", () => {
    expect(() =>
      decodeStrictJsonObject(Uint8Array.from([0xff]), {
        context: "invalid UTF-8",
      }),
    ).toThrow("UTF-8");
    expect(() =>
      decodeStrictJsonObject(
        Uint8Array.from([0xef, 0xbb, 0xbf, 0x7b, 0x7d]),
        { context: "UTF-8 BOM" },
      ),
    ).toThrow("unexpected JSON token");
    expect(() =>
      decodeStrictJsonObject('{"value":"\ud800"}', {
        context: "raw lone surrogate",
      }),
    ).toThrow("Unicode scalar");

    const deep = `${'{"child":'.repeat(MAX_STRICT_JSON_DEPTH + 1)}null${"}".repeat(
      MAX_STRICT_JSON_DEPTH + 1,
    )}`;
    expect(() =>
      decodeStrictJsonObject(deep, {
        context: "deep fixture",
      }),
    ).toThrow("depth");
  });
});

describe("strict JSON descriptor snapshot", () => {
  it("rejects lone surrogate code units recursively in values and keys", () => {
    const hostileKey = { safe: true } as Record<string, unknown>;
    Object.defineProperty(hostileKey, `key-\ud800`, {
      enumerable: true,
      value: true,
    });
    for (const hostile of [
      { value: "\ud800" },
      { nested: [{ value: "\udfff" }] },
      hostileKey,
    ]) {
      expect(() =>
        snapshotStrictJsonObject(hostile, {
          context: "Unicode scalar snapshot",
        }),
      ).toThrow("Unicode scalar");
    }
    expect(
      snapshotStrictJsonObject(
        { key: "valid \ud83d\ude80 pair" },
        { context: "Unicode scalar snapshot" },
      ),
    ).toEqual({ key: "valid 🚀 pair" });
  });

  it("creates an owned plain snapshot without reading accessors", () => {
    const source = {
      array: [true, null, "text"],
      nested: { integer: 9_007_199_254_740_991 },
    };
    const snapshot = snapshotStrictJsonObject(source, {
      context: "valid snapshot",
    });
    expect(snapshot).toEqual(source);
    expect(snapshot).not.toBe(source);
    expect(snapshot.array).not.toBe(source.array);
    expect(snapshot.nested).not.toBe(source.nested);
    const snapshotArray = snapshot.array as object;
    const snapshotNested = snapshot.nested as object;
    expect(Reflect.getPrototypeOf(snapshot)).toBeNull();
    expect(Reflect.getPrototypeOf(snapshotNested)).toBeNull();
    expect(Reflect.getPrototypeOf(snapshotArray)).toBeNull();

    let getterCalls = 0;
    Object.defineProperty(source.nested, "getter", {
      enumerable: true,
      get() {
        getterCalls += 1;
        return "unsafe";
      },
    });
    expect(() =>
      snapshotStrictJsonObject(source, { context: "accessor snapshot" }),
    ).toThrow("data properties");
    expect(getterCalls).toBe(0);

    let setterCalls = 0;
    Object.defineProperty(source.nested, "setter", {
      enumerable: true,
      set(_value: unknown) {
        void _value;
        setterCalls += 1;
      },
    });
    expect(() =>
      snapshotStrictJsonObject(source, { context: "setter snapshot" }),
    ).toThrow("data properties");
    expect(setterCalls).toBe(0);
  });

  it("rejects proxies, exotic graphs, symbols, pollution keys, and sparse arrays", () => {
    const nestedProxy = {
      nested: new Proxy(
        { value: 1 },
        {
          ownKeys() {
            throw new Error("proxy trap must not run");
          },
        },
      ),
    };
    expect(() =>
      snapshotStrictJsonObject(nestedProxy, { context: "proxy snapshot" }),
    ).toThrow("proxy");

    const nullPrototype = Object.create(null) as Record<string, unknown>;
    nullPrototype.value = 1;
    expect(() =>
      snapshotStrictJsonObject(nullPrototype, { context: "prototype snapshot" }),
    ).toThrow("plain");

    const symbol = { value: 1 };
    Object.defineProperty(symbol, Symbol("hidden"), {
      enumerable: true,
      value: 2,
    });
    expect(() =>
      snapshotStrictJsonObject(symbol, { context: "symbol snapshot" }),
    ).toThrow("symbol");

    const pollution = { value: 1 } as Record<string, unknown>;
    Object.defineProperty(pollution, "__proto__", {
      enumerable: true,
      value: {},
    });
    expect(() =>
      snapshotStrictJsonObject(pollution, { context: "pollution snapshot" }),
    ).toThrow("prototype pollution");

    const cycle = {} as Record<string, unknown>;
    cycle.self = cycle;
    expect(() =>
      snapshotStrictJsonObject(cycle, { context: "cycle snapshot" }),
    ).toThrow("cycle or shared alias");

    const shared = { value: 1 };
    expect(() =>
      snapshotStrictJsonObject(
        { left: shared, right: shared },
        { context: "alias snapshot" },
      ),
    ).toThrow("cycle or shared alias");

    const sparse = new Array(2);
    sparse[1] = true;
    expect(() =>
      snapshotStrictJsonObject({ sparse }, { context: "sparse snapshot" }),
    ).toThrow("dense");

    const customArray: unknown[] = [];
    Object.defineProperty(customArray, "extra", {
      enumerable: true,
      value: true,
    });
    expect(() =>
      snapshotStrictJsonObject(
        { customArray },
        { context: "custom array snapshot" },
      ),
    ).toThrow("dense");

    const exoticArray: unknown[] = [];
    Object.setPrototypeOf(exoticArray, {});
    expect(() =>
      snapshotStrictJsonObject(
        { exoticArray },
        { context: "exotic array snapshot" },
      ),
    ).toThrow("plain arrays");
  });

  it("rejects unsupported scalars and depth, node, key, and string overruns", () => {
    for (const unsupported of [
      1.5,
      Number.NaN,
      1n,
      undefined,
      Symbol("x"),
      () => true,
    ]) {
      expect(() =>
        snapshotStrictJsonObject(
          { unsupported },
          { context: "scalar snapshot" },
        ),
      ).toThrow();
    }

    const deep: Record<string, unknown> = {};
    let cursor = deep;
    for (let index = 0; index < MAX_STRICT_JSON_DEPTH; index += 1) {
      const child: Record<string, unknown> = {};
      cursor.child = child;
      cursor = child;
    }
    expect(() =>
      snapshotStrictJsonObject(deep, { context: "deep snapshot" }),
    ).toThrow("depth");

    expect(() =>
      snapshotStrictJsonObject(
        { nodes: Array.from({ length: MAX_STRICT_JSON_NODES }, () => null) },
        { context: "node snapshot" },
      ),
    ).toThrow("node");
    expect(() =>
      snapshotStrictJsonObject(
        { first: 1, second: 2 },
        { context: "key snapshot", maxKeys: 1 },
      ),
    ).toThrow("key");
    expect(() =>
      snapshotStrictJsonObject(
        { string: "x".repeat(4 * 1024 * 1024 + 1) },
        { context: "string snapshot" },
      ),
    ).toThrow("string");
  });
});
