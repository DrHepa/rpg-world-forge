import { types as utilTypes } from "node:util";

export const MAX_STRICT_JSON_BYTES = 4 * 1024 * 1024;
export const MAX_STRICT_JSON_DEPTH = 64;
export const MAX_STRICT_JSON_NODES = 100_000;
export const MAX_STRICT_JSON_KEYS = 100_000;
export const MAX_STRICT_JSON_STRING_CODE_UNITS = MAX_STRICT_JSON_BYTES;

const MAX_SAFE_INTEGER_DECIMAL = "9007199254740991";
const PROTOTYPE_POLLUTION_KEYS = new Set([
  "__proto__",
  "constructor",
  "prototype",
]);

function defineDataProperty(
  target,
  key,
  value,
  configurable = true,
  enumerable = true,
  writable = true,
) {
  const descriptor = Object.create(null);
  descriptor.configurable = configurable;
  descriptor.enumerable = enumerable;
  descriptor.value = value;
  descriptor.writable = writable;
  if (!Reflect.defineProperty(target, key, descriptor)) {
    throw new TypeError(`cannot define isolated property ${String(key)}`);
  }
}

function containsSymbolKey(keys) {
  for (let index = 0; index < keys.length; index += 1) {
    if (typeof keys[index] === "symbol") {
      return true;
    }
  }
  return false;
}

function strictJsonError(context, message) {
  return new Error(`${context}: ${message}`);
}

function decodeSource(source, context, maxBytes) {
  if (typeof source === "string") {
    if (new TextEncoder().encode(source).byteLength > maxBytes) {
      throw strictJsonError(context, `JSON exceeds the ${maxBytes}-byte limit`);
    }
    return source;
  }
  if (!(source instanceof Uint8Array)) {
    throw strictJsonError(context, "JSON source must be a string or Uint8Array");
  }
  if (source.byteLength > maxBytes) {
    throw strictJsonError(context, `JSON exceeds the ${maxBytes}-byte limit`);
  }
  try {
    return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(source);
  } catch (error) {
    throw strictJsonError(context, `invalid UTF-8: ${error.message}`);
  }
}

class StrictJsonParser {
  constructor(text, { context, maxDepth }) {
    this.text = text;
    this.context = context;
    this.maxDepth = maxDepth;
    this.index = 0;
  }

  fail(message) {
    throw strictJsonError(this.context, `${message} at character ${this.index}`);
  }

  skipWhitespace() {
    while (
      this.index < this.text.length &&
      (this.text[this.index] === " " ||
        this.text[this.index] === "\n" ||
        this.text[this.index] === "\r" ||
        this.text[this.index] === "\t")
    ) {
      this.index += 1;
    }
  }

  parse() {
    this.skipWhitespace();
    const value = this.parseValue(0);
    this.skipWhitespace();
    if (this.index !== this.text.length) {
      this.fail("unexpected trailing JSON content");
    }
    if (value === null || Array.isArray(value) || typeof value !== "object") {
      this.fail("JSON document must have an object root");
    }
    return value;
  }

  parseValue(depth) {
    const token = this.text[this.index];
    if (token === "{") {
      return this.parseObject(depth + 1);
    }
    if (token === "[") {
      return this.parseArray(depth + 1);
    }
    if (token === '"') {
      return this.parseString();
    }
    if (token === "t") {
      return this.parseLiteral("true", true);
    }
    if (token === "f") {
      return this.parseLiteral("false", false);
    }
    if (token === "n") {
      return this.parseLiteral("null", null);
    }
    if (token === "-" || (token >= "0" && token <= "9")) {
      return this.parseInteger();
    }
    this.fail("unexpected JSON token");
  }

  checkDepth(depth) {
    if (depth > this.maxDepth) {
      this.fail(`JSON depth exceeds the ${this.maxDepth}-level limit`);
    }
  }

  parseObject(depth) {
    this.checkDepth(depth);
    this.index += 1;
    this.skipWhitespace();
    const result = {};
    const keys = new Set();
    if (this.text[this.index] === "}") {
      this.index += 1;
      return result;
    }
    while (this.index < this.text.length) {
      if (this.text[this.index] !== '"') {
        this.fail("object key must be a JSON string");
      }
      const key = this.parseString();
      if (keys.has(key)) {
        this.fail(`duplicate object key ${JSON.stringify(key)}`);
      }
      keys.add(key);
      this.skipWhitespace();
      if (this.text[this.index] !== ":") {
        this.fail("object key must be followed by ':'");
      }
      this.index += 1;
      this.skipWhitespace();
      const value = this.parseValue(depth);
      defineDataProperty(result, key, value);
      this.skipWhitespace();
      const separator = this.text[this.index];
      if (separator === "}") {
        this.index += 1;
        return result;
      }
      if (separator !== ",") {
        this.fail("object member must be followed by ',' or '}'");
      }
      this.index += 1;
      this.skipWhitespace();
    }
    this.fail("unterminated JSON object");
  }

  parseArray(depth) {
    this.checkDepth(depth);
    this.index += 1;
    this.skipWhitespace();
    const result = [];
    if (this.text[this.index] === "]") {
      this.index += 1;
      return result;
    }
    while (this.index < this.text.length) {
      result.push(this.parseValue(depth));
      this.skipWhitespace();
      const separator = this.text[this.index];
      if (separator === "]") {
        this.index += 1;
        return result;
      }
      if (separator !== ",") {
        this.fail("array item must be followed by ',' or ']'");
      }
      this.index += 1;
      this.skipWhitespace();
    }
    this.fail("unterminated JSON array");
  }

  parseLiteral(literal, value) {
    if (this.text.slice(this.index, this.index + literal.length) !== literal) {
      this.fail(`invalid JSON literal beginning with ${JSON.stringify(this.text[this.index])}`);
    }
    this.index += literal.length;
    return value;
  }

  parseInteger() {
    const start = this.index;
    if (this.text[this.index] === "-") {
      this.index += 1;
    }
    if (this.text[this.index] === "0") {
      this.index += 1;
      if (this.text[this.index] >= "0" && this.text[this.index] <= "9") {
        this.fail("JSON integers must not contain leading zeroes");
      }
    } else {
      if (!(this.text[this.index] >= "1" && this.text[this.index] <= "9")) {
        this.fail("invalid JSON integer");
      }
      while (this.text[this.index] >= "0" && this.text[this.index] <= "9") {
        this.index += 1;
      }
    }
    if (
      this.text[this.index] === "." ||
      this.text[this.index] === "e" ||
      this.text[this.index] === "E"
    ) {
      this.fail("decimal or exponent JSON numbers are unsupported");
    }
    const lexeme = this.text.slice(start, this.index);
    const magnitude = lexeme.startsWith("-") ? lexeme.slice(1) : lexeme;
    if (
      magnitude.length > MAX_SAFE_INTEGER_DECIMAL.length ||
      (magnitude.length === MAX_SAFE_INTEGER_DECIMAL.length &&
        magnitude > MAX_SAFE_INTEGER_DECIMAL)
    ) {
      this.fail("JSON integer is outside the JavaScript safe integer range");
    }
    return Number(lexeme);
  }

  parseHexCodeUnit() {
    const digits = this.text.slice(this.index, this.index + 4);
    if (!/^[0-9a-fA-F]{4}$/u.test(digits)) {
      this.fail("invalid JSON Unicode escape");
    }
    this.index += 4;
    return Number.parseInt(digits, 16);
  }

  parseString() {
    this.index += 1;
    let result = "";
    while (this.index < this.text.length) {
      const codeUnit = this.text.charCodeAt(this.index);
      const character = this.text[this.index];
      if (character === '"') {
        this.index += 1;
        return result;
      }
      if (character === "\\") {
        this.index += 1;
        const escape = this.text[this.index];
        this.index += 1;
        const simple = {
          '"': '"',
          "\\": "\\",
          "/": "/",
          b: "\b",
          f: "\f",
          n: "\n",
          r: "\r",
          t: "\t",
        };
        if (Object.hasOwn(simple, escape)) {
          result += simple[escape];
          continue;
        }
        if (escape !== "u") {
          this.fail("invalid JSON string escape");
        }
        const escaped = this.parseHexCodeUnit();
        if (escaped >= 0xd800 && escaped <= 0xdbff) {
          if (this.text.slice(this.index, this.index + 2) !== "\\u") {
            this.fail("JSON string must contain only Unicode scalar values");
          }
          this.index += 2;
          const low = this.parseHexCodeUnit();
          if (low < 0xdc00 || low > 0xdfff) {
            this.fail("JSON string must contain only Unicode scalar values");
          }
          result += String.fromCodePoint(
            0x10000 + ((escaped - 0xd800) << 10) + (low - 0xdc00),
          );
          continue;
        }
        if (escaped >= 0xdc00 && escaped <= 0xdfff) {
          this.fail("JSON string must contain only Unicode scalar values");
        }
        result += String.fromCharCode(escaped);
        continue;
      }
      if (codeUnit < 0x20) {
        this.fail("JSON strings must not contain unescaped control characters");
      }
      if (codeUnit >= 0xd800 && codeUnit <= 0xdbff) {
        const low = this.text.charCodeAt(this.index + 1);
        if (low < 0xdc00 || low > 0xdfff) {
          this.fail("JSON string must contain only Unicode scalar values");
        }
        result += this.text.slice(this.index, this.index + 2);
        this.index += 2;
        continue;
      }
      if (codeUnit >= 0xdc00 && codeUnit <= 0xdfff) {
        this.fail("JSON string must contain only Unicode scalar values");
      }
      result += character;
      this.index += 1;
    }
    this.fail("unterminated JSON string");
  }
}

export function decodeStrictJsonObject(
  source,
  {
    context = "strict JSON document",
    maxBytes = MAX_STRICT_JSON_BYTES,
    maxDepth = MAX_STRICT_JSON_DEPTH,
  } = {},
) {
  if (
    !Number.isSafeInteger(maxBytes) ||
    maxBytes < 1 ||
    !Number.isSafeInteger(maxDepth) ||
    maxDepth < 1
  ) {
    throw strictJsonError(context, "strict JSON bounds must be positive safe integers");
  }
  const text = decodeSource(source, context, maxBytes);
  return new StrictJsonParser(text, { context, maxDepth }).parse();
}

export function snapshotStrictJsonObject(
  value,
  {
    context = "strict JSON snapshot",
    maxDepth = MAX_STRICT_JSON_DEPTH,
    maxNodes = MAX_STRICT_JSON_NODES,
    maxKeys = MAX_STRICT_JSON_KEYS,
    maxStringCodeUnits = MAX_STRICT_JSON_STRING_CODE_UNITS,
  } = {},
) {
  function validateBound(name, bound) {
    if (!Number.isSafeInteger(bound) || bound < 1) {
      throw strictJsonError(
        context,
        `${name} must be a positive safe integer`,
      );
    }
  }
  validateBound("maxDepth", maxDepth);
  validateBound("maxNodes", maxNodes);
  validateBound("maxKeys", maxKeys);
  validateBound("maxStringCodeUnits", maxStringCodeUnits);

  const budget = {
    keys: 0,
    nodes: 0,
    stringCodeUnits: 0,
  };
  const seen = new WeakSet();

  function accountString(candidate) {
    for (let index = 0; index < candidate.length; index += 1) {
      const codeUnit = candidate.charCodeAt(index);
      if (codeUnit >= 0xd800 && codeUnit <= 0xdbff) {
        const low = candidate.charCodeAt(index + 1);
        if (!(low >= 0xdc00 && low <= 0xdfff)) {
          throw strictJsonError(
            context,
            "JSON strings must contain only Unicode scalar values",
          );
        }
        index += 1;
      } else if (codeUnit >= 0xdc00 && codeUnit <= 0xdfff) {
        throw strictJsonError(
          context,
          "JSON strings must contain only Unicode scalar values",
        );
      }
    }
    budget.stringCodeUnits += candidate.length;
    if (
      candidate.length > maxStringCodeUnits ||
      budget.stringCodeUnits > maxStringCodeUnits
    ) {
      throw strictJsonError(
        context,
        `JSON string content exceeds the ${maxStringCodeUnits}-code-unit limit`,
      );
    }
  }

  function allocate(candidate, depth) {
    budget.nodes += 1;
    if (budget.nodes > maxNodes) {
      throw strictJsonError(
        context,
        `JSON node count exceeds the ${maxNodes}-node limit`,
      );
    }
    if (candidate === null || typeof candidate === "boolean") {
      return { frame: null, snapshot: candidate };
    }
    if (typeof candidate === "string") {
      accountString(candidate);
      return { frame: null, snapshot: candidate };
    }
    if (typeof candidate === "number") {
      if (!Number.isSafeInteger(candidate)) {
        throw strictJsonError(
          context,
          "JSON numbers must be safe integers",
        );
      }
      return { frame: null, snapshot: candidate };
    }
    if (typeof candidate !== "object") {
      throw strictJsonError(
        context,
        `unsupported JSON scalar type ${typeof candidate}`,
      );
    }
    if (depth > maxDepth) {
      throw strictJsonError(
        context,
        `JSON depth exceeds the ${maxDepth}-level limit`,
      );
    }
    if (utilTypes.isProxy(candidate)) {
      throw strictJsonError(context, "JSON proxies are unsupported");
    }
    if (seen.has(candidate)) {
      throw strictJsonError(
        context,
        "JSON cycle or shared alias is unsupported",
      );
    }
    seen.add(candidate);

    if (Array.isArray(candidate)) {
      if (Object.getPrototypeOf(candidate) !== Array.prototype) {
        throw strictJsonError(context, "JSON arrays must be plain arrays");
      }
      const lengthDescriptor = Object.getOwnPropertyDescriptor(
        candidate,
        "length",
      );
      if (
        lengthDescriptor === undefined ||
        !("value" in lengthDescriptor) ||
        !Number.isSafeInteger(lengthDescriptor.value) ||
        lengthDescriptor.value < 0
      ) {
        throw strictJsonError(context, "JSON array length is invalid");
      }
      const length = lengthDescriptor.value;
      if (budget.nodes + length > maxNodes) {
        throw strictJsonError(
          context,
          `JSON node count exceeds the ${maxNodes}-node limit`,
        );
      }
      const ownKeys = Reflect.ownKeys(candidate);
      if (containsSymbolKey(ownKeys)) {
        throw strictJsonError(context, "JSON symbol keys are unsupported");
      }
      if (ownKeys.length !== length + 1) {
        throw strictJsonError(
          context,
          "JSON arrays must be dense and contain no custom properties",
        );
      }
      budget.keys += length;
      if (budget.keys > maxKeys) {
        throw strictJsonError(
          context,
          `JSON key count exceeds the ${maxKeys}-key limit`,
        );
      }
      const snapshot = new Array(length);
      Reflect.setPrototypeOf(snapshot, null);
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

    if (Object.getPrototypeOf(candidate) !== Object.prototype) {
      throw strictJsonError(context, "JSON objects must be plain objects");
    }
    const ownKeys = Reflect.ownKeys(candidate);
    if (containsSymbolKey(ownKeys)) {
      throw strictJsonError(context, "JSON symbol keys are unsupported");
    }
    budget.keys += ownKeys.length;
    if (budget.keys > maxKeys) {
      throw strictJsonError(
        context,
        `JSON key count exceeds the ${maxKeys}-key limit`,
      );
    }
    const snapshot = Object.create(null);
    return {
      frame: {
        depth,
        keys: ownKeys,
        kind: "object",
        snapshot,
        source: candidate,
      },
      snapshot,
    };
  }

  function dataDescriptor(source, key) {
    const descriptor = Object.getOwnPropertyDescriptor(source, key);
    if (
      descriptor === undefined ||
      !("value" in descriptor) ||
      !descriptor.enumerable
    ) {
      throw strictJsonError(
        context,
        "JSON values must use enumerable data properties",
      );
    }
    return descriptor;
  }

  const root = allocate(value, 1);
  if (root.frame === null || root.frame.kind !== "object") {
    throw strictJsonError(context, "JSON snapshot must have an object root");
  }
  const stack = [root.frame];
  Reflect.setPrototypeOf(stack, null);
  while (stack.length > 0) {
    const frameIndex = stack.length - 1;
    const frame = stack[frameIndex];
    stack.length = frameIndex;
    if (frame.kind === "array") {
      for (let index = frame.length - 1; index >= 0; index -= 1) {
        const key = String(index);
        const descriptor = dataDescriptor(frame.source, key);
        const child = allocate(descriptor.value, frame.depth + 1);
        defineDataProperty(frame.snapshot, key, child.snapshot);
        if (child.frame !== null) {
          stack[stack.length] = child.frame;
        }
      }
      continue;
    }
    for (let index = frame.keys.length - 1; index >= 0; index -= 1) {
      const key = frame.keys[index];
      if (typeof key !== "string") {
        throw strictJsonError(context, "JSON symbol keys are unsupported");
      }
      accountString(key);
      if (PROTOTYPE_POLLUTION_KEYS.has(key)) {
        throw strictJsonError(
          context,
          "JSON prototype pollution keys are unsupported",
        );
      }
      const descriptor = dataDescriptor(frame.source, key);
      const child = allocate(descriptor.value, frame.depth + 1);
      defineDataProperty(frame.snapshot, key, child.snapshot);
      if (child.frame !== null) {
        stack[stack.length] = child.frame;
      }
    }
  }
  return root.snapshot;
}
