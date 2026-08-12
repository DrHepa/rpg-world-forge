import { createHash } from "node:crypto";
import {
  mkdir,
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  canonicalGameRuntimeBundleId,
} from "./game-runtime-bundle-validation.mjs";
import {
  canonicalGenericAssetContentHash,
} from "./generic-asset-validation.mjs";
import {
  GENERIC_RUNTIME_TRUSTED_SNAPSHOT,
} from "./generic-runtime-trusted-files.mjs";
import {
  buildAssetpackFixture,
} from "./verify-generic-asset-runtime.mjs";

const scriptRoot = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(scriptRoot, "../../..");
const require = createRequire(import.meta.url);
const asar = require("@electron/asar");

export const GAME_RUNTIME_BUNDLE_ENTRY =
  "dist-electron/main/generic-game-runtime-bundle.cjs";

function fail(code) {
  throw new Error(`game_runtime_bundle_smoke:${code}`);
}

function canonicalPretty(value) {
  const ordered = (candidate) => {
    if (Array.isArray(candidate)) {
      return candidate.map(ordered);
    }
    if (candidate !== null && typeof candidate === "object") {
      return Object.fromEntries(
        Object.keys(candidate)
          .sort()
          .map((key) => [key, ordered(candidate[key])]),
      );
    }
    return candidate;
  };
  return Buffer.from(`${JSON.stringify(ordered(value), null, 2)}\n`, "utf8");
}

function digest(payload) {
  return createHash("sha256").update(payload).digest("hex");
}

function fileRecords(files) {
  return [...files.entries()]
    .map(([relative, payload]) => ({
      path: relative,
      sha256: digest(payload),
      size_bytes: payload.length,
    }))
    .sort((left, right) =>
      Buffer.compare(
        Buffer.from(left.path, "utf8"),
        Buffer.from(right.path, "utf8"),
      ),
    );
}

async function readDocument(relative) {
  return JSON.parse(
    await readFile(
      path.join(repositoryRoot, ...relative.split("/")),
      "utf8",
    ),
  );
}

async function readBytes(relative) {
  return readFile(path.join(repositoryRoot, ...relative.split("/")));
}

function identity(document, id, bundlePath) {
  return {
    path: bundlePath,
    format: document.format,
    format_version: document.format_version,
    id,
    content_hash: document.content_hash,
  };
}

export async function buildGameRuntimeBundleFixture(fixtureName) {
  if (
    !["abstract-puzzle", "branching-narrative"].includes(fixtureName)
  ) {
    throw new Error("unsupported game runtime bundle fixture");
  }
  const fixtureRoot = `examples/multigenre-contracts/${fixtureName}`;
  const artifactName =
    fixtureName === "abstract-puzzle"
      ? "abstract-puzzle.gamepack.json"
      : "branching-narrative.gamepack.json";
  const documentPaths = {
    gamepack: `${fixtureRoot}/artifacts/${artifactName}`,
    snapshot: "examples/multigenre-contracts/runtime/snapshot.json",
    registry: "examples/multigenre-contracts/runtime/registry.json",
    composition: `${fixtureRoot}/runtime/composition.json`,
    support: `${fixtureRoot}/runtime/support-report.json`,
  };
  const [gamepack, snapshot, registry, composition, support] =
    await Promise.all(
      Object.values(documentPaths).map((relative) => readDocument(relative)),
    );
  const contractBytes = await Promise.all(
    Object.values(documentPaths).map((relative) => readBytes(relative)),
  );
  const assetpack = await buildAssetpackFixture(fixtureName);
  const assetpackFiles = new Map([
    ["assetpack.json", canonicalPretty(assetpack.document)],
    ...assetpack.files,
  ]);
  const assetpackRecords = fileRecords(assetpackFiles);
  const assetpackRootHash = canonicalGenericAssetContentHash({
    files: assetpackRecords,
  });
  if (
    assetpack.document.content_hash !== composition.assetpack.content_hash ||
    assetpack.document.assetpack_id !== composition.assetpack.id ||
    assetpack.document.inventory.content_hash !==
      composition.assetpack.inventory_hash ||
    assetpackRootHash !== composition.assetpack.root_hash
  ) {
    throw new Error("generic assetpack fixture differs from runtime composition");
  }

  const runtimeFiles = new Map();
  const trustedRecords = [];
  for (const entry of GENERIC_RUNTIME_TRUSTED_SNAPSHOT.files) {
    const payload = Buffer.from(entry.base64, "base64");
    if (
      payload.toString("base64") !== entry.base64 ||
      payload.length !== entry.size_bytes ||
      digest(payload) !== entry.sha256
    ) {
      throw new Error(`trusted runtime map changed: ${entry.path}`);
    }
    runtimeFiles.set(entry.path, payload);
    trustedRecords.push({
      path: entry.path,
      sha256: entry.sha256,
      size_bytes: entry.size_bytes,
    });
  }
  if (
    JSON.stringify(snapshot.files) !== JSON.stringify(trustedRecords) ||
    canonicalGenericAssetContentHash({ files: trustedRecords }) !==
      GENERIC_RUNTIME_TRUSTED_SNAPSHOT.tree_hash ||
    snapshot.tree_hash !== GENERIC_RUNTIME_TRUSTED_SNAPSHOT.tree_hash
  ) {
    throw new Error("runtime snapshot fixture differs from trusted runtime map");
  }
  const adapter = registry.adapters.find(
    (candidate) => candidate.adapter_id === composition.adapter.id,
  );
  if (adapter === undefined) {
    throw new Error("runtime bundle fixture adapter is absent");
  }
  const adapterPath =
    `runtime/snapshot-tree/descriptors/${adapter.adapter_id}` +
    `@${adapter.adapter_version}.json`;
  const files = new Map([
    ["contracts/gamepack.json", contractBytes[0]],
    ["contracts/runtime-snapshot.json", contractBytes[1]],
    ["contracts/runtime-adapter-registry.json", contractBytes[2]],
    ["contracts/runtime-composition.json", contractBytes[3]],
    ["status/runtime-support-report.json", contractBytes[4]],
    [
      "licenses/world-forge-mit.txt",
      await readBytes("src/worldforge/templates/pyray_game/LICENSE.tmpl"),
    ],
  ]);
  for (const [relative, payload] of assetpackFiles) {
    files.set(`assetpack/${relative}`, payload);
  }
  for (const [relative, payload] of runtimeFiles) {
    files.set(`runtime/snapshot-tree/${relative}`, payload);
  }
  const inventory = fileRecords(files);
  const runtimeRecords = fileRecords(runtimeFiles);
  const bindings = composition.bindings.map((binding) => ({
    ...binding,
    bundle_path: `assetpack/${binding.runtime_path}`,
  }));
  const notices = assetpack.document.assets
    .flatMap((asset) =>
      asset.outputs.map((output) => ({
        path: `assetpack/${output.runtime_notice.path}`,
        sha256: output.runtime_notice.sha256,
        size_bytes: output.runtime_notice.size_bytes,
      })),
    )
    .sort((left, right) =>
      Buffer.compare(
        Buffer.from(left.path, "utf8"),
        Buffer.from(right.path, "utf8"),
      ),
    );
  const document = {
    format: "world-forge.game_runtime_bundle",
    format_version: 1,
    bundle_id: "",
    state: "pre_execution",
    contracts: {
      gamepack: identity(
        gamepack,
        gamepack.game.id,
        "contracts/gamepack.json",
      ),
      runtime_snapshot: identity(
        snapshot,
        snapshot.snapshot_id,
        "contracts/runtime-snapshot.json",
      ),
      runtime_adapter: {
        ...identity(
          adapter,
          adapter.adapter_id,
          adapterPath,
        ),
        adapter_version: adapter.adapter_version,
      },
      runtime_adapter_registry: identity(
        registry,
        registry.registry_id,
        "contracts/runtime-adapter-registry.json",
      ),
      runtime_composition: identity(
        composition,
        composition.composition_id,
        "contracts/runtime-composition.json",
      ),
      runtime_support_report: identity(
        support,
        support.report_id,
        "status/runtime-support-report.json",
      ),
    },
    assetpack: {
      root: "assetpack",
      manifest: identity(
        assetpack.document,
        assetpack.document.assetpack_id,
        "assetpack/assetpack.json",
      ),
      root_hash: assetpackRootHash,
      inventory_hash: assetpack.document.inventory.content_hash,
    },
    runtime_snapshot_tree: {
      root: "runtime/snapshot-tree",
      runtime_api: snapshot.runtime_api,
      tree_hash: canonicalGenericAssetContentHash({
        files: runtimeRecords,
      }),
      file_count: runtimeRecords.length,
      total_bytes: runtimeRecords.reduce(
        (total, entry) => total + entry.size_bytes,
        0,
      ),
    },
    bindings,
    legal: {
      bundle_license: {
        path: "licenses/world-forge-mit.txt",
        sha256:
          "2e55c53ff294650e049d844f2544fec947c3516440aeffca4b2334cf94b13eeb",
        size_bytes: 1063,
      },
      asset_notices: notices,
    },
    files: inventory,
    tree_hash: canonicalGenericAssetContentHash({ files: inventory }),
    content_hash: "",
  };
  document.bundle_id = canonicalGameRuntimeBundleId(document);
  document.content_hash = canonicalGenericAssetContentHash(document);
  return { document, files };
}

export async function writeGameRuntimeBundleFixture(root, fixture) {
  await mkdir(root, { recursive: false });
  await writeFile(
    path.join(root, "game-runtime-bundle.json"),
    canonicalPretty(fixture.document),
    { flag: "wx" },
  );
  for (const [relative, payload] of fixture.files) {
    const target = path.join(root, ...relative.split("/"));
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, payload, { flag: "wx" });
  }
}

function resealSnapshot(document) {
  document.tree_hash = canonicalGenericAssetContentHash({
    files: document.files,
  });
  document.snapshot_id =
    `runtime_snapshot_${canonicalGenericAssetContentHash({
      runtime_api: document.runtime_api,
      adapter_descriptors: document.adapter_descriptors,
      files: document.files,
      tree_hash: document.tree_hash,
    }).slice(0, 40)}`;
  document.content_hash = canonicalGenericAssetContentHash(document);
}

function resealRegistry(document) {
  document.registry_id =
    `runtime_registry_${canonicalGenericAssetContentHash({
      runtime_snapshot: document.runtime_snapshot,
      adapters: document.adapters,
    }).slice(0, 40)}`;
  document.content_hash = canonicalGenericAssetContentHash(document);
}

function resealComposition(document) {
  document.composition_id =
    `runtime_composition_${canonicalGenericAssetContentHash({
      gamepack: document.gamepack,
      asset_inventory: document.asset_inventory,
      assetpack: document.assetpack,
      adapter: document.adapter,
      registry: document.registry,
      runtime_snapshot: document.runtime_snapshot,
      platforms: document.platforms,
      bindings: document.bindings,
    }).slice(0, 40)}`;
  document.content_hash = canonicalGenericAssetContentHash(document);
}

function resealSupport(document) {
  document.report_id =
    `runtime_support_${canonicalGenericAssetContentHash({
      gamepack: document.gamepack,
      composition: document.composition,
      adapter: document.adapter,
      evidence: document.evidence,
      dimensions: document.dimensions,
      compatibility_status: document.compatibility_status,
      mechanics: document.mechanics,
      features: document.features,
      missing_capabilities: document.missing_capabilities,
      reason_codes: document.reason_codes,
      supported: document.supported,
    }).slice(0, 40)}`;
  document.content_hash = canonicalGenericAssetContentHash(document);
}

function resealAssetpack(document) {
  document.assetpack_id =
    `assetpack_${canonicalGenericAssetContentHash({
      gamepack: document.gamepack,
      asset_subject: document.asset_subject,
      target: document.target,
      style: document.style,
      asset_inventory: document.asset_inventory,
      release_ready_manifest: document.release_ready_manifest,
      assets: document.assets,
      inventory: document.inventory,
    }).slice(0, 48)}`;
  document.content_hash = canonicalGenericAssetContentHash(document);
}

function replaceManifestFile(document, relative, payload) {
  const record = {
    path: relative,
    sha256: digest(payload),
    size_bytes: payload.length,
  };
  const index = document.files.findIndex(
    (candidate) => candidate.path === relative,
  );
  if (index === -1) {
    document.files.push(record);
  } else {
    document.files[index] = record;
  }
  document.files.sort((left, right) =>
    Buffer.compare(
      Buffer.from(left.path, "utf8"),
      Buffer.from(right.path, "utf8"),
    ),
  );
}

function resealBundleManifest(document) {
  const runtimeRecords = document.files
    .filter((entry) =>
      entry.path.startsWith("runtime/snapshot-tree/"),
    )
    .map((entry) => ({
      path: entry.path.slice("runtime/snapshot-tree/".length),
      sha256: entry.sha256,
      size_bytes: entry.size_bytes,
    }));
  document.runtime_snapshot_tree.tree_hash =
    canonicalGenericAssetContentHash({ files: runtimeRecords });
  document.runtime_snapshot_tree.file_count = runtimeRecords.length;
  document.runtime_snapshot_tree.total_bytes = runtimeRecords.reduce(
    (total, entry) => total + entry.size_bytes,
    0,
  );
  document.tree_hash = canonicalGenericAssetContentHash({
    files: document.files,
  });
  document.bundle_id = canonicalGameRuntimeBundleId(document);
  document.content_hash = canonicalGenericAssetContentHash(document);
}

async function readBundleDocument(root, relative) {
  return JSON.parse(
    await readFile(path.join(root, ...relative.split("/")), "utf8"),
  );
}

async function writeBundleDocument(root, relative, document) {
  const payload = canonicalPretty(document);
  await writeFile(path.join(root, ...relative.split("/")), payload);
  return payload;
}

export async function applySelfResealedGameRuntimeBundleMutation(
  root,
  mutation,
) {
  if (
    ![
      "extra_authoring_file",
      "runtime_source",
      "assetpack_gamepack_id",
      "composition_assetpack_id",
      "composition_asset_inventory_id",
    ].includes(mutation)
  ) {
    throw new Error("unsupported self-resealed runtime bundle mutation");
  }
  const manifest = await readBundleDocument(
    root,
    "game-runtime-bundle.json",
  );
  if (mutation === "extra_authoring_file") {
    const relative = "authoring/provider.json";
    const payload = canonicalPretty({ classification: "authoring_only" });
    const target = path.join(root, ...relative.split("/"));
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, payload, { flag: "wx" });
    replaceManifestFile(manifest, relative, payload);
  } else {
    const composition = await readBundleDocument(
      root,
      "contracts/runtime-composition.json",
    );
    const support = await readBundleDocument(
      root,
      "status/runtime-support-report.json",
    );
    if (mutation === "runtime_source") {
      const runtimeRelative =
        "runtime/snapshot-tree/gamepack_runtime/session.py";
      const runtimePath = path.join(
        root,
        ...runtimeRelative.split("/"),
      );
      const runtimePayload = await readFile(runtimePath);
      const tampered = Buffer.from(runtimePayload);
      tampered[Math.floor(tampered.length / 2)] ^= 1;
      await writeFile(runtimePath, tampered);

      const snapshot = await readBundleDocument(
        root,
        "contracts/runtime-snapshot.json",
      );
      const snapshotRecord = snapshot.files.find(
        (candidate) =>
          candidate.path === "gamepack_runtime/session.py",
      );
      if (snapshotRecord === undefined) {
        throw new Error("runtime source mutation target is absent");
      }
      snapshotRecord.sha256 = digest(tampered);
      snapshotRecord.size_bytes = tampered.length;
      resealSnapshot(snapshot);
      const snapshotPayload = await writeBundleDocument(
        root,
        "contracts/runtime-snapshot.json",
        snapshot,
      );

      const registry = await readBundleDocument(
        root,
        "contracts/runtime-adapter-registry.json",
      );
      registry.runtime_snapshot = {
        content_hash: snapshot.content_hash,
        format: snapshot.format,
        format_version: snapshot.format_version,
        id: snapshot.snapshot_id,
      };
      resealRegistry(registry);
      const registryPayload = await writeBundleDocument(
        root,
        "contracts/runtime-adapter-registry.json",
        registry,
      );

      composition.runtime_snapshot = {
        content_hash: snapshot.content_hash,
        format: snapshot.format,
        format_version: snapshot.format_version,
        id: snapshot.snapshot_id,
      };
      resealComposition(composition);
      support.composition = {
        content_hash: composition.content_hash,
        format: composition.format,
        format_version: composition.format_version,
        id: composition.composition_id,
      };
      resealSupport(support);
      replaceManifestFile(manifest, runtimeRelative, tampered);
      replaceManifestFile(
        manifest,
        "contracts/runtime-snapshot.json",
        snapshotPayload,
      );
      replaceManifestFile(
        manifest,
        "contracts/runtime-adapter-registry.json",
        registryPayload,
      );
      manifest.contracts.runtime_snapshot.id = snapshot.snapshot_id;
      manifest.contracts.runtime_snapshot.content_hash =
        snapshot.content_hash;
      manifest.contracts.runtime_adapter_registry.id =
        registry.registry_id;
      manifest.contracts.runtime_adapter_registry.content_hash =
        registry.content_hash;
    } else if (mutation === "assetpack_gamepack_id") {
      const assetpack = await readBundleDocument(
        root,
        "assetpack/assetpack.json",
      );
      assetpack.gamepack.id = "gamepack_crossed";
      resealAssetpack(assetpack);
      const assetpackPayload = await writeBundleDocument(
        root,
        "assetpack/assetpack.json",
        assetpack,
      );
      const assetpackRecords = [
        {
          path: "assetpack.json",
          sha256: digest(assetpackPayload),
          size_bytes: assetpackPayload.length,
        },
        ...assetpack.inventory.files,
      ].sort((left, right) =>
        Buffer.compare(
          Buffer.from(left.path, "utf8"),
          Buffer.from(right.path, "utf8"),
        ),
      );
      const assetpackRootHash = canonicalGenericAssetContentHash({
        files: assetpackRecords,
      });
      composition.assetpack = {
        content_hash: assetpack.content_hash,
        format: assetpack.format,
        format_version: assetpack.format_version,
        id: assetpack.assetpack_id,
        inventory_hash: assetpack.inventory.content_hash,
        root_hash: assetpackRootHash,
      };
      resealComposition(composition);
      support.composition = {
        content_hash: composition.content_hash,
        format: composition.format,
        format_version: composition.format_version,
        id: composition.composition_id,
      };
      resealSupport(support);
      replaceManifestFile(
        manifest,
        "assetpack/assetpack.json",
        assetpackPayload,
      );
      manifest.assetpack.manifest.id = assetpack.assetpack_id;
      manifest.assetpack.manifest.content_hash =
        assetpack.content_hash;
      manifest.assetpack.root_hash = assetpackRootHash;
    } else {
      const field =
        mutation === "composition_assetpack_id"
          ? "assetpack"
          : "asset_inventory";
      composition[field].id = `${field}_crossed`;
      resealComposition(composition);
      support.composition = {
        content_hash: composition.content_hash,
        format: composition.format,
        format_version: composition.format_version,
        id: composition.composition_id,
      };
      resealSupport(support);
    }
    const compositionPayload = await writeBundleDocument(
      root,
      "contracts/runtime-composition.json",
      composition,
    );
    const supportPayload = await writeBundleDocument(
      root,
      "status/runtime-support-report.json",
      support,
    );
    replaceManifestFile(
      manifest,
      "contracts/runtime-composition.json",
      compositionPayload,
    );
    replaceManifestFile(
      manifest,
      "status/runtime-support-report.json",
      supportPayload,
    );
    manifest.contracts.runtime_composition.id =
      composition.composition_id;
    manifest.contracts.runtime_composition.content_hash =
      composition.content_hash;
    manifest.contracts.runtime_support_report.id = support.report_id;
    manifest.contracts.runtime_support_report.content_hash =
      support.content_hash;
  }
  resealBundleManifest(manifest);
  await writeBundleDocument(
    root,
    "game-runtime-bundle.json",
    manifest,
  );
}

function loadRuntimeModule(modulePath) {
  let resolved;
  try {
    resolved = require.resolve(modulePath);
  } catch {
    fail("module_missing");
  }
  try {
    return require(resolved);
  } catch {
    fail("module_load_failed");
  }
}

async function loadRuntimeFromAsarBytes(archiveBytes) {
  const temporaryRoot = await mkdtemp(
    path.join(os.tmpdir(), "world-forge-game-runtime-bundle-"),
  );
  const archivePath = path.join(temporaryRoot, "app.asar");
  const modulePath = path.join(
    temporaryRoot,
    "generic-game-runtime-bundle.cjs",
  );
  try {
    await writeFile(archivePath, archiveBytes, {
      flag: "wx",
      mode: 0o600,
    });
    const payload = Buffer.from(
      asar.extractFile(
        archivePath,
        GAME_RUNTIME_BUNDLE_ENTRY.split("/").join(path.sep),
        false,
      ),
    );
    await writeFile(modulePath, payload, {
      flag: "wx",
      mode: 0o600,
    });
    return {
      close: async () => {
        try {
          delete require.cache[require.resolve(modulePath)];
        } catch {
          // The retained smoke is already complete if the module disappeared.
        }
        asar.uncache(archivePath);
        await rm(temporaryRoot, { force: true, recursive: true });
      },
      runtime: loadRuntimeModule(modulePath),
    };
  } catch (error) {
    asar.uncache(archivePath);
    await rm(temporaryRoot, { force: true, recursive: true });
    throw error;
  }
}

async function smokeRuntime(runtimeSurface, artifactKind) {
  const descriptor =
    runtimeSurface.GENERIC_GAME_RUNTIME_BUNDLE_VALIDATOR_RUNTIME;
  if (
    descriptor?.format !==
      "world-forge.studio_internal_game_runtime_bundle_validator" ||
    descriptor?.format_version !== 1 ||
    descriptor?.verification_scope !== "transfer_integrity_only" ||
    JSON.stringify(descriptor?.contract_formats) !==
      JSON.stringify(["world-forge.game_runtime_bundle"]) ||
    typeof runtimeSurface.validateGenericGameRuntimeBundle !== "function" ||
    typeof runtimeSurface.verifyGenericGameRuntimeBundleDirectory !==
      "function"
  ) {
    fail("surface_invalid");
  }
  const temporaryRoot = await mkdtemp(
    path.join(os.tmpdir(), "world-forge-game-runtime-bundle-smoke-"),
  );
  let verified = 0;
  let tamperRejected = 0;
  let resealedTamperRejected = 0;
  try {
    for (const fixtureName of [
      "abstract-puzzle",
      "branching-narrative",
    ]) {
      const fixture = await buildGameRuntimeBundleFixture(fixtureName);
      const structural =
        runtimeSurface.validateGenericGameRuntimeBundle(fixture.document);
      if (structural === null) {
        fail("structural_document_rejected");
      }
      if (
        !Object.isFrozen(structural) ||
        Object.hasOwn(structural, "integrity")
      ) {
        fail("structural_validation_overclaimed");
      }
      const root = path.join(temporaryRoot, fixtureName);
      await writeGameRuntimeBundleFixture(root, fixture);
      const evidence =
        await runtimeSurface.verifyGenericGameRuntimeBundleDirectory(root);
      if (
        evidence === null ||
        evidence.integrity !== "valid" ||
        evidence.state !== "pre_execution" ||
        evidence.release !== "blocked" ||
        evidence.supported !== false ||
        evidence.bundle_id !== fixture.document.bundle_id ||
        evidence.content_hash !== fixture.document.content_hash ||
        !Object.isFrozen(evidence)
      ) {
        fail("integral_validation_failed");
      }
      verified += 1;
      const target = path.join(root, "contracts", "gamepack.json");
      const original = await readFile(target);
      const tampered = Buffer.from(original);
      tampered[tampered.length - 2] ^= 1;
      await writeFile(target, tampered);
      if (
        (await runtimeSurface.verifyGenericGameRuntimeBundleDirectory(root)) !==
        null
      ) {
        fail("tamper_accepted");
      }
      tamperRejected += 1;
      for (const mutation of [
        "extra_authoring_file",
        "runtime_source",
        "assetpack_gamepack_id",
        "composition_assetpack_id",
        "composition_asset_inventory_id",
      ]) {
        const mutationRoot = path.join(
          temporaryRoot,
          `${fixtureName}-${mutation}`,
        );
        await writeGameRuntimeBundleFixture(mutationRoot, fixture);
        await applySelfResealedGameRuntimeBundleMutation(
          mutationRoot,
          mutation,
        );
        const mutationManifest = JSON.parse(
          await readFile(
            path.join(mutationRoot, "game-runtime-bundle.json"),
            "utf8",
          ),
        );
        if (
          runtimeSurface.validateGenericGameRuntimeBundle(
            mutationManifest,
          ) === null
        ) {
          fail("resealed_mutation_is_not_structurally_valid");
        }
        if (
          (await runtimeSurface.verifyGenericGameRuntimeBundleDirectory(
            mutationRoot,
          )) !== null
        ) {
          fail("resealed_mutation_accepted");
        }
        resealedTamperRejected += 1;
      }
    }
  } finally {
    await rm(temporaryRoot, { force: true, recursive: true });
  }
  return Object.freeze({
    artifact_kind: artifactKind,
    bundles_verified: verified,
    contract_formats: descriptor.contract_formats,
    format: "world-forge.studio_game_runtime_bundle_smoke",
    format_version: 1,
    release: "blocked",
    resealed_tamper_rejections: resealedTamperRejected,
    status: "verified",
    supported: false,
    tamper_rejections: tamperRejected,
    verification_scope: "transfer_integrity_only",
  });
}

export async function verifyGameRuntimeBundleArtifact({
  artifactKind,
  artifactPath,
}) {
  if (
    !["asar", "module"].includes(artifactKind) ||
    typeof artifactPath !== "string" ||
    !path.isAbsolute(artifactPath)
  ) {
    fail("artifact_invalid");
  }
  if (artifactKind === "module") {
    return smokeRuntime(loadRuntimeModule(artifactPath), artifactKind);
  }
  let archiveBytes;
  try {
    archiveBytes = await readFile(artifactPath);
  } catch {
    fail("asar_entry_missing");
  }
  const loaded = await loadRuntimeFromAsarBytes(archiveBytes);
  try {
    return await smokeRuntime(loaded.runtime, artifactKind);
  } finally {
    await loaded.close();
  }
}

export async function verifyGameRuntimeBundleSnapshot({
  artifactBytes,
  expectedSha256,
  expectedSize,
}) {
  if (
    !Buffer.isBuffer(artifactBytes) ||
    !Number.isSafeInteger(expectedSize) ||
    expectedSize < 1 ||
    artifactBytes.length !== expectedSize ||
    typeof expectedSha256 !== "string" ||
    !/^[0-9a-f]{64}$/u.test(expectedSha256) ||
    digest(artifactBytes) !== expectedSha256
  ) {
    fail("snapshot_identity_mismatch");
  }
  const retainedBytes = Buffer.from(artifactBytes);
  const loaded = await loadRuntimeFromAsarBytes(retainedBytes);
  try {
    const report = await smokeRuntime(loaded.runtime, "asar");
    if (
      retainedBytes.length !== expectedSize ||
      digest(retainedBytes) !== expectedSha256
    ) {
      fail("snapshot_identity_mismatch");
    }
    return Object.freeze({
      ...report,
      artifact_sha256: expectedSha256,
      artifact_size_bytes: expectedSize,
    });
  } finally {
    await loaded.close();
  }
}

function parseArguments(argv) {
  if (
    argv.length !== 2 ||
    !["--asar", "--module"].includes(argv[0])
  ) {
    fail("invalid_arguments");
  }
  return {
    artifactKind: argv[0] === "--asar" ? "asar" : "module",
    artifactPath: path.resolve(argv[1]),
  };
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  try {
    const report = await verifyGameRuntimeBundleArtifact(
      parseArguments(process.argv.slice(2)),
    );
    process.stdout.write(`${JSON.stringify(report)}\n`);
  } catch (error) {
    process.stderr.write(
      `${
        error instanceof Error
          ? error.message
          : "game_runtime_bundle_smoke:failed"
      }\n`,
    );
    process.exitCode = 1;
  }
}
