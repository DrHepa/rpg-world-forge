import { createHash } from "node:crypto";
import { crc32, inflateSync } from "node:zlib";

import {
  canonicalGenericAssetContentHash,
  hasCanonicalGenericAssetContentHash,
  hasPortableGenericAssetPathTree,
  isCanonicalGenericAssetObjectArray,
  isPortableGenericAssetRuntimePath,
} from "./generic-asset-validation.mjs";
import { decodeStrictJsonObject } from "./strict-json.mjs";

const sha256Pattern = /^[0-9a-f]{64}$/u;
const assetpackIdPattern = /^assetpack_[0-9a-f]{48}$/u;
const noticePathPattern = /^notices\/([0-9a-f]{64})\.txt$/u;
const utf8Decoder = new TextDecoder("utf-8", { fatal: true });
const arraySortIntrinsic = Array.prototype.sort;
const objectKeysIntrinsic = Object.keys;

function digest(payload) {
  return createHash("sha256").update(payload).digest("hex");
}

function equalJson(left, right) {
  if (left === right) {
    return true;
  }
  if (
    left === null ||
    right === null ||
    typeof left !== "object" ||
    typeof right !== "object"
  ) {
    return false;
  }
  if (Array.isArray(left)) {
    if (!Array.isArray(right) || left.length !== right.length) {
      return false;
    }
    for (let index = 0; index < left.length; index += 1) {
      if (!equalJson(left[index], right[index])) {
        return false;
      }
    }
    return true;
  }
  if (Array.isArray(right)) {
    return false;
  }
  const leftKeys = Reflect.apply(objectKeysIntrinsic, Object, [left]);
  const rightKeys = Reflect.apply(objectKeysIntrinsic, Object, [right]);
  Reflect.apply(arraySortIntrinsic, leftKeys, []);
  Reflect.apply(arraySortIntrinsic, rightKeys, []);
  if (leftKeys.length !== rightKeys.length) {
    return false;
  }
  for (let index = 0; index < leftKeys.length; index += 1) {
    const key = leftKeys[index];
    if (
      key !== rightKeys[index] ||
      !equalJson(left[key], right[key])
    ) {
      return false;
    }
  }
  return true;
}

function assetpackIdSeed(value) {
  return {
    gamepack: value.gamepack,
    asset_subject: value.asset_subject,
    target: value.target,
    style: value.style,
    asset_inventory: value.asset_inventory,
    release_ready_manifest: value.release_ready_manifest,
    assets: value.assets,
    inventory: value.inventory,
  };
}

export function canonicalGenericAssetpackId(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const seedHash = canonicalGenericAssetContentHash(assetpackIdSeed(value));
  return seedHash === null ? null : `assetpack_${seedHash.slice(0, 48)}`;
}

export function hasCoherentGenericAssetpack(value) {
  if (
    value === null ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    value.format !== "world-forge.assetpack" ||
    value.format_version !== 1 ||
    value.state !== "sealed" ||
    !assetpackIdPattern.test(value.assetpack_id) ||
    !hasCanonicalGenericAssetContentHash(value) ||
    value.inventory === null ||
    typeof value.inventory !== "object" ||
    Array.isArray(value.inventory) ||
    !hasCanonicalGenericAssetContentHash(value.inventory) ||
    !Array.isArray(value.inventory.files) ||
    !Array.isArray(value.assets)
  ) {
    return false;
  }
  if (
    value.assetpack_id !== canonicalGenericAssetpackId(value) ||
    value.inventory.file_count !== value.inventory.files.length ||
    !isCanonicalGenericAssetObjectArray(value.inventory.files, {
      orderBy: ["path"],
      uniqueBy: [["path"]],
    }) ||
    !hasPortableGenericAssetPathTree(value.inventory.files, "path")
  ) {
    return false;
  }
  let totalBytes = 0;
  const inventory = new Map();
  for (
    let fileIndex = 0;
    fileIndex < value.inventory.files.length;
    fileIndex += 1
  ) {
    const entry = value.inventory.files[fileIndex];
    if (
      entry === null ||
      typeof entry !== "object" ||
      !isPortableGenericAssetRuntimePath(entry.path) ||
      !sha256Pattern.test(entry.sha256) ||
      !Number.isSafeInteger(entry.size_bytes) ||
      entry.size_bytes < 0
    ) {
      return false;
    }
    totalBytes += entry.size_bytes;
    inventory.set(entry.path, [entry.sha256, entry.size_bytes]);
  }
  if (totalBytes !== value.inventory.total_bytes) {
    return false;
  }
  const expected = new Map();
  for (
    let assetIndex = 0;
    assetIndex < value.assets.length;
    assetIndex += 1
  ) {
    const asset = value.assets[assetIndex];
    if (
      asset === null ||
      typeof asset !== "object" ||
      !Array.isArray(asset.outputs) ||
      !Array.isArray(asset.licenses)
    ) {
      return false;
    }
    const licensesById = new Map();
    for (
      let licenseIndex = 0;
      licenseIndex < asset.licenses.length;
      licenseIndex += 1
    ) {
      const license = asset.licenses[licenseIndex];
      if (
        license === null ||
        typeof license !== "object" ||
        licensesById.has(license.id)
      ) {
        return false;
      }
      licensesById.set(license.id, license);
    }
    const boundLicenseIds = new Set();
    for (
      let outputIndex = 0;
      outputIndex < asset.outputs.length;
      outputIndex += 1
    ) {
      const output = asset.outputs[outputIndex];
      const noticeMatch = noticePathPattern.exec(output?.runtime_notice?.path);
      if (
        output === null ||
        typeof output !== "object" ||
        !isPortableGenericAssetRuntimePath(output.runtime_path) ||
        output.runtime_path.toLowerCase() === "assetpack.json" ||
        output.runtime_path.toLowerCase().startsWith("notices/") ||
        !sha256Pattern.test(output.sha256) ||
        !Number.isSafeInteger(output.size_bytes) ||
        output.size_bytes < 1 ||
        output.constraints === null ||
        typeof output.constraints !== "object" ||
        output.constraints.max_bytes !== output.size_bytes ||
        noticeMatch === null ||
        noticeMatch[1] !== output.runtime_notice.sha256 ||
        !Number.isSafeInteger(output.runtime_notice.size_bytes) ||
        output.runtime_notice.size_bytes < 0 ||
        output.runtime_notice.size_bytes > 4096 ||
        !licensesById.has(output.license_record?.id) ||
        !equalJson(
          licensesById.get(output.license_record.id),
          output.license_record,
        )
      ) {
        return false;
      }
      boundLicenseIds.add(output.license_record.id);
      const bindExpected = (path, identity) => {
        const prior = expected.get(path);
        if (prior !== undefined && !equalJson(prior, identity)) {
          return false;
        }
        expected.set(path, identity);
        return true;
      };
      if (
        !bindExpected(output.runtime_path, [
          output.sha256,
          output.size_bytes,
        ]) ||
        !bindExpected(output.runtime_notice.path, [
          output.runtime_notice.sha256,
          output.runtime_notice.size_bytes,
        ])
      ) {
        return false;
      }
    }
    if (boundLicenseIds.size !== licensesById.size) {
      return false;
    }
    for (const identifier of licensesById.keys()) {
      if (!boundLicenseIds.has(identifier)) {
        return false;
      }
    }
  }
  if (expected.size !== inventory.size) {
    return false;
  }
  for (const [path, identity] of expected) {
    if (!equalJson(inventory.get(path), identity)) {
      return false;
    }
  }
  return true;
}

function inspectPng(payload) {
  const signature = Buffer.from("89504e470d0a1a0a", "hex");
  if (payload.length < 33 || !payload.subarray(0, 8).equals(signature)) {
    return null;
  }
  let offset = 8;
  let width;
  let height;
  let mode;
  const compressed = [];
  let ended = false;
  while (offset < payload.length) {
    if (offset + 12 > payload.length) {
      return null;
    }
    const length = payload.readUInt32BE(offset);
    const end = offset + 12 + length;
    if (end > payload.length) {
      return null;
    }
    const type = payload.toString("ascii", offset + 4, offset + 8);
    const data = payload.subarray(offset + 8, offset + 8 + length);
    const expectedCrc = payload.readUInt32BE(offset + 8 + length);
    if (
      (crc32(payload.subarray(offset + 4, offset + 8 + length)) >>> 0) !==
      expectedCrc
    ) {
      return null;
    }
    if (type === "IHDR") {
      if (offset !== 8 || length !== 13) {
        return null;
      }
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      const modes = new Map([
        ["8:6", ["rgba8", 4]],
        ["8:2", ["rgb8", 3]],
        ["8:0", ["grayscale8", 1]],
      ]);
      const selected = modes.get(`${String(data[8])}:${String(data[9])}`);
      if (
        selected === undefined ||
        width < 1 ||
        height < 1 ||
        data[10] !== 0 ||
        data[11] !== 0 ||
        data[12] !== 0
      ) {
        return null;
      }
      mode = selected;
    } else if (type === "IDAT") {
      compressed.push(data);
    } else if (type === "IEND") {
      if (length !== 0 || end !== payload.length) {
        return null;
      }
      ended = true;
    }
    offset = end;
  }
  if (!ended || width === undefined || height === undefined || compressed.length === 0) {
    return null;
  }
  const [modeName, channels] = mode;
  const expectedBytes = height * (1 + width * channels);
  if (expectedBytes > 64 * 1024 * 1024) {
    return null;
  }
  let decoded;
  try {
    decoded = inflateSync(Buffer.concat(compressed), {
      maxOutputLength: expectedBytes + 1,
    });
  } catch {
    return null;
  }
  if (decoded.length !== expectedBytes) {
    return null;
  }
  const rowBytes = 1 + width * channels;
  for (let row = 0; row < height; row += 1) {
    if (decoded[row * rowBytes] > 4) {
      return null;
    }
  }
  return { height, kind: "png", mode: modeName, width };
}

function inspectWav(payload) {
  if (
    payload.length < 44 ||
    payload.toString("ascii", 0, 4) !== "RIFF" ||
    payload.toString("ascii", 8, 12) !== "WAVE" ||
    payload.readUInt32LE(4) + 8 !== payload.length
  ) {
    return null;
  }
  let offset = 12;
  let format;
  let dataBytes;
  while (offset + 8 <= payload.length) {
    const kind = payload.toString("ascii", offset, offset + 4);
    const length = payload.readUInt32LE(offset + 4);
    const dataOffset = offset + 8;
    if (dataOffset + length > payload.length) {
      return null;
    }
    if (kind === "fmt ") {
      if (length < 16) {
        return null;
      }
      format = {
        audioFormat: payload.readUInt16LE(dataOffset),
        channels: payload.readUInt16LE(dataOffset + 2),
        sampleRate: payload.readUInt32LE(dataOffset + 4),
        blockAlign: payload.readUInt16LE(dataOffset + 12),
        bitsPerSample: payload.readUInt16LE(dataOffset + 14),
      };
    } else if (kind === "data") {
      dataBytes = length;
    }
    offset = dataOffset + length + (length % 2);
  }
  if (
    format === undefined ||
    dataBytes === undefined ||
    format.audioFormat !== 1 ||
    format.bitsPerSample !== 16 ||
    ![1, 2].includes(format.channels) ||
    format.blockAlign !== format.channels * 2 ||
    dataBytes % format.blockAlign !== 0
  ) {
    return null;
  }
  return {
    channels: format.channels,
    frames: dataBytes / format.blockAlign,
    kind: "wav_pcm16",
    sample_rate: format.sampleRate,
    sample_width: 2,
  };
}

function fontRanges(payload, cmapOffset, cmapLength, glyphLimit) {
  if (cmapLength < 4 || payload.readUInt16BE(cmapOffset) !== 0) {
    return null;
  }
  const count = payload.readUInt16BE(cmapOffset + 2);
  if (count < 1 || count > 256 || 4 + count * 8 > cmapLength) {
    return null;
  }
  const mapped = new Set();
  for (let index = 0; index < count; index += 1) {
    const record = cmapOffset + 4 + index * 8;
    const platform = payload.readUInt16BE(record);
    const encoding = payload.readUInt16BE(record + 2);
    const relative = payload.readUInt32BE(record + 4);
    if (!((platform === 0 && encoding <= 6) || (platform === 3 && [1, 10].includes(encoding)))) {
      continue;
    }
    const subtable = cmapOffset + relative;
    if (subtable + 2 > cmapOffset + cmapLength) {
      return null;
    }
    const format = payload.readUInt16BE(subtable);
    if (format === 12) {
      if (subtable + 16 > cmapOffset + cmapLength) {
        return null;
      }
      const length = payload.readUInt32BE(subtable + 4);
      const groups = payload.readUInt32BE(subtable + 12);
      if (
        groups < 1 ||
        groups > 65536 ||
        length !== 16 + groups * 12 ||
        subtable + length > cmapOffset + cmapLength
      ) {
        return null;
      }
      for (let group = 0; group < groups; group += 1) {
        const groupOffset = subtable + 16 + group * 12;
        const start = payload.readUInt32BE(groupOffset);
        const end = payload.readUInt32BE(groupOffset + 4);
        const startGlyph = payload.readUInt32BE(groupOffset + 8);
        if (
          start > end ||
          end > 0x10ffff ||
          startGlyph + (end - start) >= glyphLimit ||
          end - start > 1_000_000
        ) {
          return null;
        }
        for (let codepoint = start + (startGlyph === 0 ? 1 : 0); codepoint <= end; codepoint += 1) {
          mapped.add(codepoint);
        }
      }
    } else if (format === 4) {
      if (subtable + 16 > cmapOffset + cmapLength) {
        return null;
      }
      const length = payload.readUInt16BE(subtable + 2);
      const segmentCountX2 = payload.readUInt16BE(subtable + 6);
      if (
        segmentCountX2 % 2 !== 0 ||
        segmentCountX2 < 2 ||
        segmentCountX2 > 8192 ||
        subtable + length > cmapOffset + cmapLength
      ) {
        return null;
      }
      const segmentCount = segmentCountX2 / 2;
      const ends = subtable + 14;
      const starts = ends + segmentCount * 2 + 2;
      const deltas = starts + segmentCount * 2;
      const rangeOffsets = deltas + segmentCount * 2;
      for (let segment = 0; segment < segmentCount; segment += 1) {
        const start = payload.readUInt16BE(starts + segment * 2);
        const end = payload.readUInt16BE(ends + segment * 2);
        const delta = payload.readUInt16BE(deltas + segment * 2);
        const rangeOffset = payload.readUInt16BE(rangeOffsets + segment * 2);
        if (start > end || (start === 0xffff && end === 0xffff)) {
          continue;
        }
        for (let codepoint = start; codepoint <= end; codepoint += 1) {
          let glyph;
          if (rangeOffset === 0) {
            glyph = (codepoint + delta) & 0xffff;
          } else {
            const glyphOffset =
              rangeOffsets +
              segment * 2 +
              rangeOffset +
              (codepoint - start) * 2;
            if (glyphOffset + 2 > subtable + length) {
              return null;
            }
            glyph = payload.readUInt16BE(glyphOffset);
            if (glyph !== 0) {
              glyph = (glyph + delta) & 0xffff;
            }
          }
          if (glyph >= glyphLimit) {
            return null;
          }
          if (glyph !== 0) {
            mapped.add(codepoint);
          }
        }
      }
    }
  }
  const codepoints = [...mapped].sort((left, right) => left - right);
  if (codepoints.length === 0) {
    return null;
  }
  const ranges = [];
  let start = codepoints[0];
  let end = start;
  for (let index = 1; index < codepoints.length; index += 1) {
    if (codepoints[index] === end + 1) {
      end = codepoints[index];
    } else {
      ranges.push([start, end]);
      start = codepoints[index];
      end = start;
    }
  }
  ranges.push([start, end]);
  return ranges.map(
    ([rangeStart, rangeEnd]) =>
      `U+${rangeStart.toString(16).toUpperCase().padStart(4, "0")}-${rangeEnd
        .toString(16)
        .toUpperCase()
        .padStart(4, "0")}`,
  );
}

function inspectFont(payload, mediaType) {
  const container = mediaType === "font/ttf" ? "ttf" : "otf";
  const expectedSignature =
    container === "ttf" ? Buffer.from([0, 1, 0, 0]) : Buffer.from("OTTO", "ascii");
  if (payload.length < 12 || !payload.subarray(0, 4).equals(expectedSignature)) {
    return null;
  }
  const tableCount = payload.readUInt16BE(4);
  if (tableCount < 1 || tableCount > 4096 || 12 + tableCount * 16 > payload.length) {
    return null;
  }
  const tables = new Map();
  for (let index = 0; index < tableCount; index += 1) {
    const entry = 12 + index * 16;
    const tag = payload.toString("ascii", entry, entry + 4);
    const offset = payload.readUInt32BE(entry + 8);
    const length = payload.readUInt32BE(entry + 12);
    if (tables.has(tag) || offset + length > payload.length) {
      return null;
    }
    tables.set(tag, [offset, length]);
  }
  const cmap = tables.get("cmap");
  const maxp = tables.get("maxp");
  if (cmap === undefined || maxp === undefined || maxp[1] < 6) {
    return null;
  }
  const glyphLimit = payload.readUInt16BE(maxp[0] + 4);
  const glyphRanges = fontRanges(payload, cmap[0], cmap[1], glyphLimit);
  if (glyphRanges === null) {
    return null;
  }
  let glyphCount = 0;
  for (const range of glyphRanges) {
    const [start, end] = range
      .slice(2)
      .split("-", 2)
      .map((value) => Number.parseInt(value, 16));
    glyphCount += end - start + 1;
  }
  return { container, glyph_count: glyphCount, glyph_ranges: glyphRanges, kind: "font" };
}

function inspectGlsl(payload, role) {
  if (payload.length < 1 || payload.length > 1024 * 1024) {
    return null;
  }
  let text;
  try {
    text = utf8Decoder.decode(payload);
  } catch {
    return null;
  }
  if (
    text.startsWith("\ufeff") ||
    text.includes("\0") ||
    /^\s*#\s*include\b/mu.test(text) ||
    /[A-Za-z][A-Za-z0-9+.-]*:\/\//u.test(text)
  ) {
    return null;
  }
  const lines = text.split(/\r\n|\r|\n/u);
  const lineCount =
    lines.length > 1 && lines[lines.length - 1] === "" ? lines.length - 1 : lines.length;
  return {
    kind: "glsl",
    line_count: lineCount,
    stage: role === "vertex_shader" ? "vertex" : "fragment",
  };
}

function inspectJson(payload) {
  let document;
  try {
    document = decodeStrictJsonObject(payload, {
      context: "sealed generic assetpack JSON",
      maxBytes: 16 * 1024 * 1024,
    });
  } catch {
    return null;
  }
  if (
    Object.keys(document).sort().join(",") !== "records,schema_id,schema_version" ||
    typeof document.schema_id !== "string" ||
    !Number.isSafeInteger(document.schema_version) ||
    !Array.isArray(document.records)
  ) {
    return null;
  }
  return {
    kind: "schema_json",
    record_count: document.records.length,
    schema_id: document.schema_id,
    schema_version: document.schema_version,
  };
}

class FiniteJsonParser {
  constructor(text) {
    this.text = text;
    this.index = 0;
    this.nodes = 0;
  }

  skipWhitespace() {
    while (
      this.index < this.text.length &&
      [" ", "\n", "\r", "\t"].includes(this.text[this.index])
    ) {
      this.index += 1;
    }
  }

  value(depth) {
    if (depth > 64 || this.nodes >= 100_000) {
      throw new Error("GLB JSON bounds exceeded");
    }
    this.nodes += 1;
    const token = this.text[this.index];
    if (token === "{") {
      return this.object(depth + 1);
    }
    if (token === "[") {
      return this.array(depth + 1);
    }
    if (token === '"') {
      return this.string();
    }
    for (const [literal, value] of [
      ["true", true],
      ["false", false],
      ["null", null],
    ]) {
      if (this.text.startsWith(literal, this.index)) {
        this.index += literal.length;
        return value;
      }
    }
    const match =
      /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/u.exec(
        this.text.slice(this.index),
      );
    if (match === null) {
      throw new Error("invalid GLB JSON token");
    }
    this.index += match[0].length;
    const value = Number(match[0]);
    if (!Number.isFinite(value)) {
      throw new Error("non-finite GLB JSON number");
    }
    return value;
  }

  string() {
    const start = this.index;
    this.index += 1;
    while (this.index < this.text.length) {
      const codeUnit = this.text.charCodeAt(this.index);
      if (codeUnit < 0x20) {
        throw new Error("control character in GLB JSON string");
      }
      if (this.text[this.index] === '"') {
        this.index += 1;
        const value = JSON.parse(this.text.slice(start, this.index));
        for (let offset = 0; offset < value.length; offset += 1) {
          const unit = value.charCodeAt(offset);
          if (unit >= 0xd800 && unit <= 0xdbff) {
            const low = value.charCodeAt(offset + 1);
            if (!(low >= 0xdc00 && low <= 0xdfff)) {
              throw new Error("unpaired surrogate in GLB JSON string");
            }
            offset += 1;
          } else if (unit >= 0xdc00 && unit <= 0xdfff) {
            throw new Error("unpaired surrogate in GLB JSON string");
          }
        }
        return value;
      }
      if (this.text[this.index] === "\\") {
        this.index += 1;
        const escape = this.text[this.index];
        if (!'"\\/bfnrtu'.includes(escape)) {
          throw new Error("invalid GLB JSON escape");
        }
        if (escape === "u") {
          const digits = this.text.slice(this.index + 1, this.index + 5);
          if (!/^[0-9a-fA-F]{4}$/u.test(digits)) {
            throw new Error("invalid GLB JSON Unicode escape");
          }
          this.index += 4;
        }
      }
      this.index += 1;
    }
    throw new Error("unterminated GLB JSON string");
  }

  object(depth) {
    const result = {};
    const keys = new Set();
    this.index += 1;
    this.skipWhitespace();
    if (this.text[this.index] === "}") {
      this.index += 1;
      return result;
    }
    while (this.index < this.text.length) {
      if (this.text[this.index] !== '"') {
        throw new Error("invalid GLB JSON object key");
      }
      const key = this.string();
      if (keys.has(key)) {
        throw new Error("duplicate GLB JSON object key");
      }
      keys.add(key);
      this.skipWhitespace();
      if (this.text[this.index] !== ":") {
        throw new Error("invalid GLB JSON object separator");
      }
      this.index += 1;
      this.skipWhitespace();
      Object.defineProperty(result, key, {
        configurable: true,
        enumerable: true,
        value: this.value(depth),
        writable: true,
      });
      this.skipWhitespace();
      if (this.text[this.index] === "}") {
        this.index += 1;
        return result;
      }
      if (this.text[this.index] !== ",") {
        throw new Error("invalid GLB JSON object terminator");
      }
      this.index += 1;
      this.skipWhitespace();
    }
    throw new Error("unterminated GLB JSON object");
  }

  array(depth) {
    const result = [];
    this.index += 1;
    this.skipWhitespace();
    if (this.text[this.index] === "]") {
      this.index += 1;
      return result;
    }
    while (this.index < this.text.length) {
      result.push(this.value(depth));
      this.skipWhitespace();
      if (this.text[this.index] === "]") {
        this.index += 1;
        return result;
      }
      if (this.text[this.index] !== ",") {
        throw new Error("invalid GLB JSON array terminator");
      }
      this.index += 1;
      this.skipWhitespace();
    }
    throw new Error("unterminated GLB JSON array");
  }

  parse() {
    this.skipWhitespace();
    const result = this.value(0);
    this.skipWhitespace();
    if (
      this.index !== this.text.length ||
      result === null ||
      Array.isArray(result) ||
      typeof result !== "object"
    ) {
      throw new Error("GLB JSON root must be one object");
    }
    return result;
  }
}

function finiteGlbJson(payload) {
  let text;
  try {
    text = utf8Decoder.decode(payload);
  } catch {
    return null;
  }
  try {
    return new FiniteJsonParser(text).parse();
  } catch {
    return null;
  }
}

function glbObjectArray(document, name) {
  const values = document[name] ?? [];
  if (
    !Array.isArray(values) ||
    values.length > 100_000 ||
    values.some(
      (value) =>
        value === null ||
        Array.isArray(value) ||
        typeof value !== "object",
    )
  ) {
    return null;
  }
  return values;
}

function glbIndex(value, length) {
  return Number.isSafeInteger(value) && value >= 0 && value < length
    ? value
    : null;
}

function safeGlbMetadata(document) {
  const allowedExtensions = new Set([
    "KHR_materials_unlit",
    "KHR_mesh_quantization",
  ]);
  const used = document.extensionsUsed ?? [];
  const required = document.extensionsRequired ?? [];
  if (
    !Array.isArray(used) ||
    !Array.isArray(required) ||
    new Set(used).size !== used.length ||
    new Set(required).size !== required.length ||
    used.some(
      (name) =>
        typeof name !== "string" || !allowedExtensions.has(name),
    ) ||
    required.some((name) => !used.includes(name))
  ) {
    return false;
  }
  const declaredExtensions = new Set(used);
  const forbiddenKeyParts =
    /(?:api.?key|authorization|authoring|blender|cookie|credential|mcp|modly|openai|password|private.?key|prompt|provider|receipt|secret|signed.?url|token|workflow)/iu;
  const secretText =
    /(?:\bbearer\s+[a-z0-9._~+/-]{8,}|\bsk-[a-z0-9_-]{12,}|\bAKIA[0-9A-Z]{16}\b|-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----)/iu;
  const pending = [document];
  while (pending.length > 0) {
    const current = pending.pop();
    if (Array.isArray(current)) {
      for (let index = 0; index < current.length; index += 1) {
        pending.push(current[index]);
      }
      continue;
    }
    if (current !== null && typeof current === "object") {
      for (const [key, value] of Object.entries(current)) {
        if (
          key === "extras" ||
          forbiddenKeyParts.test(key.replace(/[^a-z0-9]+/giu, "_"))
        ) {
          return false;
        }
        if (
          key !== "uri" &&
          typeof value === "string" &&
          (secretText.test(value) ||
            /(?:blender|mcp:\/\/|modly|openai|provider_response|raw transcript)/iu.test(
              value,
            ))
        ) {
          return false;
        }
        if (key === "uri") {
          return false;
        }
        if (key === "extensions") {
          if (
            value === null ||
            Array.isArray(value) ||
            typeof value !== "object"
          ) {
            return false;
          }
          for (const [extensionName, extensionValue] of Object.entries(
            value,
          )) {
            if (
              !declaredExtensions.has(extensionName) ||
              !allowedExtensions.has(extensionName) ||
              extensionValue === null ||
              Array.isArray(extensionValue) ||
              typeof extensionValue !== "object"
            ) {
              return false;
            }
          }
        }
        pending.push(value);
      }
    }
  }
  return true;
}

const glbComponentBytes = new Map([
  [5120, 1],
  [5121, 1],
  [5122, 2],
  [5123, 2],
  [5125, 4],
  [5126, 4],
]);
const glbTypeComponents = new Map([
  ["SCALAR", 1],
  ["VEC2", 2],
  ["VEC3", 3],
  ["VEC4", 4],
  ["MAT2", 4],
  ["MAT3", 9],
  ["MAT4", 16],
]);

function glbElementSize(type, componentBytes) {
  if (!type.startsWith("MAT")) {
    return glbTypeComponents.get(type) * componentBytes;
  }
  const width = Number.parseInt(type.at(-1), 10);
  const columnBytes = width * componentBytes;
  const alignedColumnBytes = Math.ceil(columnBytes / 4) * 4;
  return width * alignedColumnBytes;
}

function glbAccessors(document, views) {
  const accessors = glbObjectArray(document, "accessors");
  if (accessors === null) {
    return null;
  }
  for (const accessor of accessors) {
    const componentBytes = glbComponentBytes.get(accessor.componentType);
    const componentCount = glbTypeComponents.get(accessor.type);
    if (
      componentBytes === undefined ||
      componentCount === undefined ||
      !Number.isSafeInteger(accessor.count) ||
      accessor.count < 1 ||
      accessor.count > 100_000_000 ||
      accessor.sparse !== undefined
    ) {
      return null;
    }
    const normalized = accessor.normalized ?? false;
    if (
      typeof normalized !== "boolean" ||
      (normalized &&
        ![5120, 5121, 5122, 5123].includes(accessor.componentType))
    ) {
      return null;
    }
    const viewIndex = glbIndex(accessor.bufferView, views.length);
    const byteOffset = accessor.byteOffset ?? 0;
    if (
      viewIndex === null ||
      !Number.isSafeInteger(byteOffset) ||
      byteOffset < 0
    ) {
      return null;
    }
    const view = views[viewIndex];
    const elementSize = glbElementSize(accessor.type, componentBytes);
    const alignment = accessor.type.startsWith("MAT") ? 4 : componentBytes;
    const stride = view.byteStride ?? elementSize;
    if (
      ((view.byteOffset ?? 0) + byteOffset) % alignment !== 0 ||
      !Number.isSafeInteger(stride) ||
      stride < elementSize ||
      stride > 252 ||
      stride % alignment !== 0 ||
      byteOffset + (accessor.count - 1) * stride + elementSize >
        view.byteLength
    ) {
      return null;
    }
    for (const field of ["min", "max"]) {
      const values = accessor[field];
      if (
        values !== undefined &&
        (!Array.isArray(values) ||
          values.length !== componentCount ||
          values.some(
            (value) =>
              typeof value !== "number" || !Number.isFinite(value),
          ))
      ) {
        return null;
      }
    }
  }
  return accessors;
}

function glbFiniteNumberArray(value, length) {
  return (
    Array.isArray(value) &&
    value.length === length &&
    value.every(
      (item) => typeof item === "number" && Number.isFinite(item),
    )
  );
}

function glbNodeReferencesValid(document, nodes, meshes, skins) {
  const scenes = glbObjectArray(document, "scenes");
  const cameras = glbObjectArray(document, "cameras");
  if (scenes === null || cameras === null || cameras.length !== 0) {
    return false;
  }
  const childEdges = [];
  const parentCounts = new Uint32Array(nodes.length);
  for (let nodeIndex = 0; nodeIndex < nodes.length; nodeIndex += 1) {
    const node = nodes[nodeIndex];
    if (
      (node.name !== undefined &&
        (typeof node.name !== "string" || node.name.length === 0)) ||
      (node.mesh !== undefined &&
        glbIndex(node.mesh, meshes.length) === null) ||
      (node.skin !== undefined &&
        glbIndex(node.skin, skins.length) === null) ||
      node.camera !== undefined
    ) {
      return false;
    }
    const children = node.children ?? [];
    if (
      !Array.isArray(children) ||
      new Set(children).size !== children.length ||
      children.some(
        (child) =>
          glbIndex(child, nodes.length) === null || child === nodeIndex,
      )
    ) {
      return false;
    }
    for (const child of children) {
      parentCounts[child] += 1;
      if (parentCounts[child] > 1) {
        return false;
      }
    }
    childEdges.push(children);
    if (
      (node.matrix !== undefined &&
        (!glbFiniteNumberArray(node.matrix, 16) ||
          node.translation !== undefined ||
          node.rotation !== undefined ||
          node.scale !== undefined)) ||
      (node.translation !== undefined &&
        !glbFiniteNumberArray(node.translation, 3)) ||
      (node.rotation !== undefined &&
        !glbFiniteNumberArray(node.rotation, 4)) ||
      (node.scale !== undefined && !glbFiniteNumberArray(node.scale, 3)) ||
      (node.weights !== undefined &&
        (!Array.isArray(node.weights) ||
          node.weights.length > 65_536 ||
          node.weights.some(
            (weight) =>
              typeof weight !== "number" || !Number.isFinite(weight),
          )))
    ) {
      return false;
    }
  }
  const colors = new Uint8Array(nodes.length);
  for (let nodeIndex = 0; nodeIndex < nodes.length; nodeIndex += 1) {
    if (colors[nodeIndex] !== 0) {
      continue;
    }
    const stack = [{ childIndex: 0, nodeIndex }];
    colors[nodeIndex] = 1;
    while (stack.length > 0) {
      const frame = stack[stack.length - 1];
      const children = childEdges[frame.nodeIndex];
      if (frame.childIndex >= children.length) {
        colors[frame.nodeIndex] = 2;
        stack.pop();
        continue;
      }
      const child = children[frame.childIndex];
      frame.childIndex += 1;
      if (colors[child] === 1) {
        return false;
      }
      if (colors[child] === 0) {
        colors[child] = 1;
        stack.push({ childIndex: 0, nodeIndex: child });
      }
    }
  }
  for (const scene of scenes) {
    const roots = scene.nodes ?? [];
    if (
      !Array.isArray(roots) ||
      new Set(roots).size !== roots.length ||
      roots.some(
        (root) =>
          glbIndex(root, nodes.length) === null ||
          parentCounts[root] !== 0,
      )
    ) {
      return false;
    }
  }
  return (
    document.scene === undefined ||
    glbIndex(document.scene, scenes.length) !== null
  );
}

function glbAnimationReferencesValid(animations, accessors, nodes) {
  for (const animation of animations) {
    const samplers = animation.samplers;
    const channels = animation.channels;
    if (
      (animation.name !== undefined &&
        (typeof animation.name !== "string" ||
          animation.name.length === 0)) ||
      !Array.isArray(samplers) ||
      samplers.length < 1 ||
      !Array.isArray(channels) ||
      channels.length < 1
    ) {
      return false;
    }
    const samplerRecords = [];
    for (const sampler of samplers) {
      if (
        sampler === null ||
        Array.isArray(sampler) ||
        typeof sampler !== "object"
      ) {
        return false;
      }
      const inputIndex = glbIndex(sampler.input, accessors.length);
      const outputIndex = glbIndex(sampler.output, accessors.length);
      const interpolation = sampler.interpolation ?? "LINEAR";
      if (
        inputIndex === null ||
        outputIndex === null ||
        !["LINEAR", "STEP", "CUBICSPLINE"].includes(interpolation)
      ) {
        return false;
      }
      const input = accessors[inputIndex];
      const output = accessors[outputIndex];
      if (input.componentType !== 5126 || input.type !== "SCALAR") {
        return false;
      }
      samplerRecords.push({ input, interpolation, output });
    }
    const targets = new Set();
    for (const channel of channels) {
      if (
        channel === null ||
        Array.isArray(channel) ||
        typeof channel !== "object" ||
        channel.target === null ||
        Array.isArray(channel.target) ||
        typeof channel.target !== "object"
      ) {
        return false;
      }
      const samplerIndex = glbIndex(channel.sampler, samplers.length);
      const nodeIndex = glbIndex(channel.target.node, nodes.length);
      const path = channel.target.path;
      if (
        samplerIndex === null ||
        nodeIndex === null ||
        !["translation", "rotation", "scale"].includes(path)
      ) {
        return false;
      }
      const sampler = samplerRecords[samplerIndex];
      const expectedType = {
        rotation: "VEC4",
        scale: "VEC3",
        translation: "VEC3",
      }[path];
      const multiplier = sampler.interpolation === "CUBICSPLINE" ? 3 : 1;
      if (
        sampler.output.componentType !== 5126 ||
        sampler.output.type !== expectedType ||
        sampler.output.count !== sampler.input.count * multiplier
      ) {
        return false;
      }
      const targetKey = `${nodeIndex}:${path}`;
      if (targets.has(targetKey)) {
        return false;
      }
      targets.add(targetKey);
    }
  }
  return true;
}

function glbMaterialsSubsetValid(materials) {
  for (const material of materials) {
    if (
      (material.name !== undefined && typeof material.name !== "string") ||
      (material.doubleSided !== undefined &&
        typeof material.doubleSided !== "boolean") ||
      (material.alphaMode !== undefined &&
        !["OPAQUE", "MASK", "BLEND"].includes(material.alphaMode)) ||
      (material.alphaCutoff !== undefined &&
        (typeof material.alphaCutoff !== "number" ||
          !Number.isFinite(material.alphaCutoff) ||
          material.alphaCutoff < 0)) ||
      (material.emissiveFactor !== undefined &&
        (!glbFiniteNumberArray(material.emissiveFactor, 3) ||
          material.emissiveFactor.some(
            (value) => value < 0 || value > 1,
          ))) ||
      material.normalTexture !== undefined ||
      material.occlusionTexture !== undefined ||
      material.emissiveTexture !== undefined
    ) {
      return false;
    }
    const pbr = material.pbrMetallicRoughness;
    if (pbr !== undefined) {
      if (
        pbr === null ||
        Array.isArray(pbr) ||
        typeof pbr !== "object" ||
        pbr.baseColorTexture !== undefined ||
        pbr.metallicRoughnessTexture !== undefined ||
        (pbr.baseColorFactor !== undefined &&
          (!glbFiniteNumberArray(pbr.baseColorFactor, 4) ||
            pbr.baseColorFactor.some(
              (value) => value < 0 || value > 1,
            ))) ||
        (pbr.metallicFactor !== undefined &&
          (typeof pbr.metallicFactor !== "number" ||
            !Number.isFinite(pbr.metallicFactor) ||
            pbr.metallicFactor < 0 ||
            pbr.metallicFactor > 1)) ||
        (pbr.roughnessFactor !== undefined &&
          (typeof pbr.roughnessFactor !== "number" ||
            !Number.isFinite(pbr.roughnessFactor) ||
            pbr.roughnessFactor < 0 ||
            pbr.roughnessFactor > 1))
      ) {
        return false;
      }
    }
    const extensions = material.extensions ?? {};
    if (
      extensions === null ||
      Array.isArray(extensions) ||
      typeof extensions !== "object" ||
      Object.keys(extensions).some(
        (name) => name !== "KHR_materials_unlit",
      ) ||
      (extensions.KHR_materials_unlit !== undefined &&
        Object.keys(extensions.KHR_materials_unlit).length !== 0)
    ) {
      return false;
    }
  }
  return true;
}

function glbProductionMetrics(document, accessors) {
  const nodes = glbObjectArray(document, "nodes");
  const meshes = glbObjectArray(document, "meshes");
  const materials = glbObjectArray(document, "materials");
  const skins = glbObjectArray(document, "skins");
  const animations = glbObjectArray(document, "animations");
  if (
    nodes === null ||
    meshes === null ||
    materials === null ||
    skins === null ||
    animations === null
  ) {
    return null;
  }
  if (
    !glbNodeReferencesValid(document, nodes, meshes, skins) ||
    !glbAnimationReferencesValid(animations, accessors, nodes) ||
    !glbMaterialsSubsetValid(materials)
  ) {
    return null;
  }
  const accessorCounts = [];
  for (const accessor of accessors) {
    accessorCounts.push(accessor.count);
  }
  let primitiveCount = 0;
  let triangleCount = 0;
  for (const mesh of meshes) {
    if (!Array.isArray(mesh.primitives) || mesh.primitives.length < 1) {
      return null;
    }
    primitiveCount += mesh.primitives.length;
    for (const primitive of mesh.primitives) {
      if (
        primitive === null ||
        Array.isArray(primitive) ||
        typeof primitive !== "object" ||
        primitive.attributes === null ||
        Array.isArray(primitive.attributes) ||
        typeof primitive.attributes !== "object" ||
        Object.keys(primitive.attributes).length < 1
      ) {
        return null;
      }
      const attributeCounts = new Set();
      const jointSets = new Set();
      const weightSets = new Set();
      for (const [semantic, accessorIndex] of Object.entries(
        primitive.attributes,
      )) {
        const index = glbIndex(accessorIndex, accessors.length);
        if (semantic.length === 0 || index === null) {
          return null;
        }
        const accessor = accessors[index];
        const expectedTypes = semantic === "POSITION" || semantic === "NORMAL"
          ? ["VEC3"]
          : semantic === "TANGENT" ||
              semantic.startsWith("JOINTS_") ||
              semantic.startsWith("WEIGHTS_")
            ? ["VEC4"]
            : semantic.startsWith("TEXCOORD_")
              ? ["VEC2"]
              : semantic.startsWith("COLOR_")
                ? ["VEC3", "VEC4"]
                : null;
        if (
          (expectedTypes !== null &&
            !expectedTypes.includes(accessor.type)) ||
          (semantic.startsWith("JOINTS_") &&
            (![5121, 5123].includes(accessor.componentType) ||
              accessor.normalized === true)) ||
          (semantic.startsWith("WEIGHTS_") &&
            !(
              accessor.componentType === 5126 ||
              ([5121, 5123].includes(accessor.componentType) &&
                accessor.normalized === true)
            ))
        ) {
          return null;
        }
        attributeCounts.add(accessor.count);
        const setMatch = /^(JOINTS|WEIGHTS)_([0-9]+)$/u.exec(semantic);
        if (setMatch !== null) {
          const setIndex = Number.parseInt(setMatch[2], 10);
          (setMatch[1] === "JOINTS" ? jointSets : weightSets).add(
            setIndex,
          );
        }
      }
      if (
        attributeCounts.size !== 1 ||
        jointSets.size !== weightSets.size ||
        [...jointSets].some((setIndex) => !weightSets.has(setIndex)) ||
        [...jointSets]
          .sort((left, right) => left - right)
          .some((setIndex, index) => setIndex !== index)
      ) {
        return null;
      }
      const position = glbIndex(
        primitive.attributes.POSITION,
        accessors.length,
      );
      if (
        position === null ||
        accessors[position].type !== "VEC3"
      ) {
        return null;
      }
      let elementCount = accessorCounts[position];
      if (Object.hasOwn(primitive, "indices")) {
        const indices = glbIndex(primitive.indices, accessors.length);
        if (
          indices === null ||
          accessors[indices].type !== "SCALAR" ||
          ![5121, 5123, 5125].includes(accessors[indices].componentType) ||
          accessors[indices].normalized === true
        ) {
          return null;
        }
        elementCount = accessorCounts[indices];
      }
      if (
        primitive.material !== undefined &&
        glbIndex(primitive.material, materials.length) === null
      ) {
        return null;
      }
      const targets = primitive.targets ?? [];
      if (!Array.isArray(targets)) {
        return null;
      }
      for (const target of targets) {
        if (
          target === null ||
          Array.isArray(target) ||
          typeof target !== "object" ||
          Object.keys(target).length < 1
        ) {
          return null;
        }
        for (const [semantic, accessorIndex] of Object.entries(target)) {
          const index = glbIndex(accessorIndex, accessors.length);
          if (
            !["POSITION", "NORMAL", "TANGENT"].includes(semantic) ||
            index === null ||
            accessors[index].count !== accessorCounts[position] ||
            accessors[index].type !== "VEC3"
          ) {
            return null;
          }
        }
      }
      const mode = primitive.mode ?? 4;
      if (!Number.isSafeInteger(mode) || mode < 0 || mode > 6) {
        return null;
      }
      if (mode === 4) {
        if (elementCount % 3 !== 0) {
          return null;
        }
        triangleCount += elementCount / 3;
      } else if (mode === 5 || mode === 6) {
        triangleCount += Math.max(0, elementCount - 2);
      }
    }
  }
  let joints = 0;
  for (const skin of skins) {
    if (
      !Array.isArray(skin.joints) ||
      skin.joints.length < 1 ||
      new Set(skin.joints).size !== skin.joints.length ||
      skin.joints.some((joint) => glbIndex(joint, nodes.length) === null) ||
      (skin.skeleton !== undefined &&
        glbIndex(skin.skeleton, nodes.length) === null)
    ) {
      return null;
    }
    if (skin.inverseBindMatrices !== undefined) {
      const inverseIndex = glbIndex(
        skin.inverseBindMatrices,
        accessors.length,
      );
      if (
        inverseIndex === null ||
        accessors[inverseIndex].componentType !== 5126 ||
        accessors[inverseIndex].type !== "MAT4" ||
        accessors[inverseIndex].count < skin.joints.length
      ) {
        return null;
      }
    }
    joints += skin.joints.length;
  }
  return {
    animations: animations.length,
    joints,
    materials: materials.length,
    meshes: meshes.length,
    nodes: nodes.length,
    primitives: primitiveCount,
    triangles: triangleCount,
  };
}

function glbBinaryReachabilityValid(
  document,
  accessors,
  views,
  binary,
  declaredLength,
) {
  const usedAccessors = new Set();
  for (const mesh of document.meshes ?? []) {
    for (const primitive of mesh.primitives) {
      for (const accessorIndex of Object.values(primitive.attributes)) {
        usedAccessors.add(accessorIndex);
      }
      if (primitive.indices !== undefined) {
        usedAccessors.add(primitive.indices);
      }
      for (const target of primitive.targets ?? []) {
        for (const accessorIndex of Object.values(target)) {
          usedAccessors.add(accessorIndex);
        }
      }
    }
  }
  for (const skin of document.skins ?? []) {
    if (skin.inverseBindMatrices !== undefined) {
      usedAccessors.add(skin.inverseBindMatrices);
    }
  }
  for (const animation of document.animations ?? []) {
    for (const sampler of animation.samplers) {
      usedAccessors.add(sampler.input);
      usedAccessors.add(sampler.output);
    }
  }
  if (usedAccessors.size !== accessors.length) {
    return false;
  }
  const usedViews = new Set();
  for (const accessorIndex of usedAccessors) {
    usedViews.add(accessors[accessorIndex].bufferView);
  }
  if (usedViews.size !== views.length) {
    return false;
  }
  if (binary === null) {
    return declaredLength === 0 && views.length === 0;
  }
  const intervals = [...usedViews]
    .map((viewIndex) => {
      const view = views[viewIndex];
      const offset = view.byteOffset ?? 0;
      return [offset, offset + view.byteLength];
    })
    .sort((left, right) => left[0] - right[0] || left[1] - right[1]);
  if (intervals.length === 0) {
    return false;
  }
  let coveredUntil = 0;
  for (const [start, end] of intervals) {
    if (
      start > coveredUntil &&
      binary.subarray(coveredUntil, start).some((byte) => byte !== 0)
    ) {
      return false;
    }
    coveredUntil = Math.max(coveredUntil, end);
  }
  return (
    !binary
      .subarray(coveredUntil, declaredLength)
      .some((byte) => byte !== 0) &&
    !binary
      .subarray(declaredLength)
      .some((byte) => byte !== 0)
  );
}

function inspectGlb(payload) {
  if (
    payload.length < 20 ||
    payload.toString("ascii", 0, 4) !== "glTF" ||
    payload.readUInt32LE(4) !== 2 ||
    payload.readUInt32LE(8) !== payload.length
  ) {
    return null;
  }
  let offset = 12;
  const chunks = [];
  while (offset < payload.length) {
    if (offset + 8 > payload.length) {
      return null;
    }
    const length = payload.readUInt32LE(offset);
    const type = payload.readUInt32LE(offset + 4);
    offset += 8;
    if (
      length % 4 !== 0 ||
      offset + length > payload.length ||
      chunks.length >= 2
    ) {
      return null;
    }
    chunks.push({ length, offset, type });
    offset += length;
  }
  if (
    chunks.length < 1 ||
    chunks[0].type !== 0x4e4f534a ||
    (chunks.length === 2 && chunks[1].type !== 0x004e4942) ||
    chunks[0].length > 16 * 1024 * 1024
  ) {
    return null;
  }
  const document = finiteGlbJson(
    payload.subarray(
      chunks[0].offset,
      chunks[0].offset + chunks[0].length,
    ),
  );
  if (
    document === null ||
    document.asset?.version !== "2.0" ||
    !safeGlbMetadata(document)
  ) {
    return null;
  }
  const buffers = glbObjectArray(document, "buffers");
  const bufferViews = glbObjectArray(document, "bufferViews");
  const images = glbObjectArray(document, "images");
  const textures = glbObjectArray(document, "textures");
  const textureSamplers = glbObjectArray(document, "samplers");
  if (
    buffers === null ||
    bufferViews === null ||
    images === null ||
    textures === null ||
    textureSamplers === null ||
    images.length !== 0 ||
    textures.length !== 0 ||
    textureSamplers.length !== 0 ||
    buffers.length !== (chunks.length === 2 ? 1 : 0)
  ) {
    return null;
  }
  if (buffers.length === 1) {
    const binaryLength = chunks[1].length;
    if (
      !Number.isSafeInteger(buffers[0].byteLength) ||
      buffers[0].byteLength < 1 ||
      buffers[0].byteLength > binaryLength ||
      binaryLength - buffers[0].byteLength > 3
    ) {
      return null;
    }
  }
  for (const view of bufferViews) {
    const buffer = glbIndex(view.buffer, buffers.length);
    const byteOffset = view.byteOffset ?? 0;
    const byteStride = view.byteStride;
    const target = view.target;
    if (
      buffer === null ||
      !Number.isSafeInteger(byteOffset) ||
      byteOffset < 0 ||
      !Number.isSafeInteger(view.byteLength) ||
      view.byteLength < 1 ||
      byteOffset + view.byteLength > buffers[buffer].byteLength ||
      (byteStride !== undefined &&
        (!Number.isSafeInteger(byteStride) ||
          byteStride < 4 ||
          byteStride > 252 ||
          byteStride % 4 !== 0)) ||
      (target !== undefined && target !== 34962 && target !== 34963)
    ) {
      return null;
    }
  }
  const accessors = glbAccessors(document, bufferViews);
  if (accessors === null) {
    return null;
  }
  const metrics = glbProductionMetrics(document, accessors);
  const binary =
    chunks.length === 2
      ? payload.subarray(
          chunks[1].offset,
          chunks[1].offset + chunks[1].length,
        )
      : null;
  const declaredBinaryLength =
    buffers.length === 1 ? buffers[0].byteLength : 0;
  if (
    metrics === null ||
    !glbBinaryReachabilityValid(
      document,
      accessors,
      bufferViews,
      binary,
      declaredBinaryLength,
    )
  ) {
    return null;
  }
  return {
    kind: "glb",
    max_texture_dimension: 0,
    metrics,
  };
}

function rangeBounds(value) {
  if (typeof value !== "string") {
    return null;
  }
  const match = /^U\+([0-9A-F]{4,6})-([0-9A-F]{4,6})$/u.exec(value);
  if (match === null) {
    return null;
  }
  return [Number.parseInt(match[1], 16), Number.parseInt(match[2], 16)];
}

function fontCoversRequiredRanges(actual, required) {
  if (!Array.isArray(actual) || !Array.isArray(required)) {
    return false;
  }
  for (
    let requiredIndex = 0;
    requiredIndex < required.length;
    requiredIndex += 1
  ) {
    const expected = rangeBounds(required[requiredIndex]);
    if (expected === null) {
      return false;
    }
    let covered = false;
    for (
      let actualIndex = 0;
      actualIndex < actual.length;
      actualIndex += 1
    ) {
      const candidate = rangeBounds(actual[actualIndex]);
      if (
        candidate !== null &&
        candidate[0] <= expected[0] &&
        candidate[1] >= expected[1]
      ) {
        covered = true;
        break;
      }
    }
    if (!covered) {
      return false;
    }
  }
  return true;
}

function metadataMatchesConstraints(metadata, output, payloadLength) {
  const constraints = output.constraints;
  if (
    metadata === null ||
    constraints === null ||
    typeof constraints !== "object" ||
    !Number.isSafeInteger(constraints.max_bytes) ||
    payloadLength !== constraints.max_bytes
  ) {
    return false;
  }
  if (output.media_type === "image/png") {
    return (
      constraints.kind === "png" &&
      metadata.width === constraints.width &&
      metadata.height === constraints.height &&
      metadata.mode === constraints.color_type
    );
  }
  if (output.media_type === "audio/wav") {
    return (
      constraints.kind === "wav_pcm16" &&
      metadata.channels === constraints.channels &&
      metadata.sample_rate === constraints.sample_rate &&
      metadata.frames === constraints.frames
    );
  }
  if (output.media_type === "font/ttf" || output.media_type === "font/otf") {
    return (
      constraints.kind === "font" &&
      metadata.container === constraints.container &&
      metadata.glyph_count <= constraints.max_glyphs &&
      fontCoversRequiredRanges(
        metadata.glyph_ranges,
        constraints.glyph_ranges,
      )
    );
  }
  if (output.media_type === "text/x-glsl") {
    return (
      constraints.kind === "glsl" &&
      metadata.stage === constraints.stage &&
      metadata.line_count <= constraints.max_lines
    );
  }
  if (output.media_type === "application/json") {
    return (
      constraints.kind === "schema_json" &&
      metadata.schema_id === constraints.schema_id &&
      metadata.schema_version === constraints.schema_version &&
      metadata.record_count <= constraints.max_records
    );
  }
  if (output.media_type === "model/gltf-binary") {
    if (constraints.kind !== "glb") {
      return false;
    }
    for (const field of [
      "animations",
      "joints",
      "materials",
      "meshes",
      "nodes",
      "primitives",
      "triangles",
    ]) {
      if (metadata.metrics[field] > constraints[`max_${field}`]) {
        return false;
      }
    }
    return true;
  }
  return false;
}

export function inspectGenericAssetpackMedia(payload, output) {
  if (
    !Buffer.isBuffer(payload) ||
    output === null ||
    typeof output !== "object" ||
    payload.length !== output.size_bytes ||
    digest(payload) !== output.sha256
  ) {
    return null;
  }
  let metadata;
  try {
    metadata = {
      "application/json": () => inspectJson(payload),
      "audio/wav": () => inspectWav(payload),
      "font/otf": () => inspectFont(payload, "font/otf"),
      "font/ttf": () => inspectFont(payload, "font/ttf"),
      "image/png": () => inspectPng(payload),
      "model/gltf-binary": () => inspectGlb(payload),
      "text/x-glsl": () => inspectGlsl(payload, output.role),
    }[output.media_type]?.();
  } catch {
    return null;
  }
  return (
    metadata !== null &&
    metadataMatchesConstraints(metadata, output, payload.length) &&
    equalJson(metadata, output.metadata)
  )
    ? metadata
    : null;
}
