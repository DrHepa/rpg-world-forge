import { createHash } from "node:crypto";

import { describe, expect, it, vi } from "vitest";

import {
  registerStudioIpc,
  validateAssetCatalogInspectArgument,
  validateAssetCatalogListArgument,
  validateAssetPreviewCloseArgument,
  validateAssetPreviewOpenArgument,
  validateAssetPreviewReadArgument,
  validateAssetpackArgument,
  validateAssetReceiptArgument,
  validateCancelJobArgument,
  validateChangesetActionArgument,
  validateChangesetIdArgument,
  validateChangesetsListParams,
  validateEventsListParams,
  validateHeadlessArgument,
  validateJobsListParams,
  validateInterruptTurnArgument,
  validateLoginArgument,
  validateReplayArgument,
  validateStageSourceDocumentArgument,
  validateSourceReadArgument,
  validateStartTurnArgument,
  validateUserInputArgument,
  validateWorkspaceArgument,
} from "../../src/main/ipc";
import { IPC_CHANNELS } from "../../src/shared/studio-api";

describe("Studio named authoring and job IPC contracts", () => {
  it("accepts only exact workspace, source, and fixed-operation inputs", () => {
    expect(
      validateSourceReadArgument({
        workspaceId: "workspace_01",
        path: "source/lore/entry.md",
      }),
    ).toEqual({ workspaceId: "workspace_01", path: "source/lore/entry.md" });
    expect(
      validateAssetReceiptArgument({
        workspaceId: "workspace_01",
        input: { receipt: "receipts/item.json" },
      }),
    ).toEqual({ workspaceId: "workspace_01", input: { receipt: "receipts/item.json" } });
    expect(
      validateAssetpackArgument({
        workspaceId: "workspace_01",
        input: { assetpack: "build/assets.json", worldpack: "build/world.json" },
      }),
    ).toEqual({
      workspaceId: "workspace_01",
      input: { assetpack: "build/assets.json", worldpack: "build/world.json" },
    });
    expect(
      validateHeadlessArgument({
        workspaceId: "workspace_01",
        input: { worldpack: "build/world.json", ticks: 0 },
      }),
    ).toEqual({
      workspaceId: "workspace_01",
      input: { worldpack: "build/world.json", ticks: 0 },
    });
    expect(
      validateReplayArgument({
        workspaceId: "workspace_01",
        input: { worldpack: "build/world.json", replay: "replays/slot.json" },
      }),
    ).toEqual({
      workspaceId: "workspace_01",
      input: { worldpack: "build/world.json", replay: "replays/slot.json" },
    });
    expect(validateCancelJobArgument({ jobId: "job_01" })).toEqual({ jobId: "job_01" });
  });

  it.each([
    [validateSourceReadArgument, { workspaceId: "workspace_01", path: "../world.json" }],
    [validateSourceReadArgument, { workspaceId: "workspace_01", path: "source/../world.json" }],
    [validateSourceReadArgument, { workspaceId: "workspace_01", path: "source/CON.json" }],
    [
      validateAssetReceiptArgument,
      {
        workspaceId: "workspace_01",
        input: { receipt: "receipt.json", operation: "shell.execute" },
      },
    ],
    [
      validateAssetpackArgument,
      {
        workspaceId: "workspace_01",
        input: { assetpack: "pack.json", worldpack: "world.json", cwd: "/tmp" },
      },
    ],
    [
      validateHeadlessArgument,
      { workspaceId: "workspace_01", input: { worldpack: "world.json", ticks: true } },
    ],
    [
      validateHeadlessArgument,
      { workspaceId: "workspace_01", input: { worldpack: "world.json", ticks: -1 } },
    ],
    [
      validateHeadlessArgument,
      { workspaceId: "workspace_01", input: { worldpack: "world.json", ticks: 1_000_001 } },
    ],
    [
      validateReplayArgument,
      {
        workspaceId: "workspace_01",
        input: { worldpack: "world.json", replay: "slot.json", env: { PATH: "/tmp" } },
      },
    ],
    [validateCancelJobArgument, { jobId: "../job" }],
  ])("rejects malformed or capability-shaped authoring/job input %#", (validate, value) => {
    expect(() => validate(value)).toThrow();
  });
});

describe("Studio named asset catalog IPC contracts", () => {
  const revision = "a".repeat(64);
  const entryId = `asset_${"b".repeat(64)}`;

  it("accepts only initial, revision-bound page, and exact inspection inputs", () => {
    expect(validateAssetCatalogListArgument({ workspaceId: "workspace_01" })).toEqual({
      workspaceId: "workspace_01",
    });
    expect(
      validateAssetCatalogListArgument({
        workspaceId: "workspace_01",
        offset: 0,
        expectedManifestRevision: revision,
      }),
    ).toEqual({
      workspaceId: "workspace_01",
      offset: 0,
      expectedManifestRevision: revision,
    });
    expect(
      validateAssetCatalogListArgument({
        workspaceId: "workspace_01",
        offset: 64,
        expectedManifestRevision: revision,
      }),
    ).toEqual({
      workspaceId: "workspace_01",
      offset: 64,
      expectedManifestRevision: revision,
    });
    expect(
      validateAssetCatalogInspectArgument({
        workspaceId: "workspace_01",
        manifestRevision: revision,
        entryId,
      }),
    ).toEqual({
      workspaceId: "workspace_01",
      manifestRevision: revision,
      entryId,
    });
  });

  it.each([
    { workspaceId: "workspace_01", offset: 64 },
    { workspaceId: "workspace_01", expectedManifestRevision: "a".repeat(64) },
    {
      workspaceId: "workspace_01",
      offset: -1,
      expectedManifestRevision: "a".repeat(64),
    },
    {
      workspaceId: "workspace_01",
      offset: 1.5,
      expectedManifestRevision: "a".repeat(64),
    },
    {
      workspaceId: "workspace_01",
      offset: Number.MAX_SAFE_INTEGER + 1,
      expectedManifestRevision: "a".repeat(64),
    },
    {
      workspaceId: "workspace_01",
      offset: 64,
      expectedManifestRevision: "A".repeat(64),
    },
    { workspaceId: "workspace_01", limit: 64 },
    { workspaceId: "workspace_01", cursor: "opaque" },
    { workspaceId: "workspace_01", path: "assets/manifest.json" },
    { workspaceId: "workspace_01", category: "manifest" },
    { workspaceId: "workspace_01", mediaType: "application/json" },
    { workspaceId: "workspace_01", method: "asset.catalog.list" },
  ])("rejects renderer-shaped or malformed list input %#", (value) => {
    expect(() => validateAssetCatalogListArgument(value)).toThrow();
  });

  it.each([
    {
      workspaceId: "workspace_01",
      manifestRevision: "A".repeat(64),
      entryId: `asset_${"b".repeat(64)}`,
    },
    {
      workspaceId: "workspace_01",
      manifestRevision: "a".repeat(64),
      entryId: "asset_bad",
    },
    {
      workspaceId: "workspace_01",
      manifestRevision: "a".repeat(64),
      entryId: `asset_${"b".repeat(64)}`,
      path: "assets/manifest.json",
    },
    {
      workspaceId: "workspace_01",
      manifestRevision: "a".repeat(64),
      entryId: `asset_${"b".repeat(64)}`,
      binary: true,
    },
  ])("rejects malformed or authority-shaped inspection input %#", (value) => {
    expect(() => validateAssetCatalogInspectArgument(value)).toThrow();
  });

  it("maps initial, revision-bound, and inspect calls with main-owned bounds", async () => {
    const harness = createIpcHarness();
    expect(
      await harness.invoke(IPC_CHANNELS.listAssetCatalog, {
        workspaceId: "workspace_01",
      }),
    ).toMatchObject({ ok: true });
    expect(
      await harness.invoke(IPC_CHANNELS.listAssetCatalog, {
        workspaceId: "workspace_01",
        offset: 64,
        expectedManifestRevision: revision,
      }),
    ).toMatchObject({ ok: true });
    expect(
      await harness.invoke(IPC_CHANNELS.inspectAssetCatalogEntry, {
        workspaceId: "workspace_01",
        manifestRevision: revision,
        entryId,
      }),
    ).toMatchObject({ ok: true });

    expect(harness.request.mock.calls.map((call) => [call[1], call[2], call[3]])).toEqual([
      [
        "asset.catalog.list",
        { workspace_id: "workspace_01", offset: 0, limit: 64 },
        60_000,
      ],
      [
        "asset.catalog.list",
        {
          workspace_id: "workspace_01",
          offset: 64,
          limit: 64,
          expected_manifest_revision: revision,
        },
        60_000,
      ],
      [
        "asset.catalog.inspect",
        {
          workspace_id: "workspace_01",
          expected_manifest_revision: revision,
          entry_id: entryId,
        },
        60_000,
      ],
    ]);
  });

  it("accepts exact correlated list and inspection replies", async () => {
    const harness = createIpcHarness();
    harness.request.mockImplementationOnce((requestId: string) =>
      Promise.resolve(createAssetCatalogListResponse(requestId, {
        manifestRevision: "d".repeat(64),
        offset: 0,
        entries: createAssetEntries(1),
        nextOffset: null,
      })),
    );
    expect(
      await harness.invoke(IPC_CHANNELS.listAssetCatalog, {
        workspaceId: "workspace_01",
      }),
    ).toMatchObject({
      ok: true,
      value: {
        method: "asset.catalog.list",
        result: { manifest_revision: "d".repeat(64), offset: 0, next_offset: null },
      },
    });

    harness.request.mockImplementationOnce((requestId: string) =>
      Promise.resolve(createAssetCatalogListResponse(requestId, {
        manifestRevision: revision,
        offset: 64,
        entries: createAssetEntries(64),
        nextOffset: 128,
      })),
    );
    expect(
      await harness.invoke(IPC_CHANNELS.listAssetCatalog, {
        workspaceId: "workspace_01",
        offset: 64,
        expectedManifestRevision: revision,
      }),
    ).toMatchObject({
      ok: true,
      value: {
        method: "asset.catalog.list",
        result: { manifest_revision: revision, offset: 64, next_offset: 128 },
      },
    });

    harness.request.mockImplementationOnce((requestId: string) =>
      Promise.resolve(createAssetCatalogInspectResponse(requestId, revision, entryId)),
    );
    expect(
      await harness.invoke(IPC_CHANNELS.inspectAssetCatalogEntry, {
        workspaceId: "workspace_01",
        manifestRevision: revision,
        entryId,
      }),
    ).toMatchObject({
      ok: true,
      value: {
        method: "asset.catalog.inspect",
        result: {
          manifest_revision: revision,
          entry: { entry_id: entryId },
          inspection: { kind: "json" },
        },
      },
    });
  });

  it("rejects mismatched, duplicate, oversized, nonmonotonic, and forged list replies", async () => {
    const harness = createIpcHarness();
    const exactArgument = {
      workspaceId: "workspace_01",
      offset: 64,
      expectedManifestRevision: revision,
    };
    const base = createAssetCatalogListResponse("placeholder", {
      manifestRevision: revision,
      offset: 64,
      entries: createAssetEntries(1),
      nextOffset: null,
    });
    const cases = [
      (requestId: string) => ({ ...base, request_id: `${requestId}-wrong` }),
      (requestId: string) => ({ ...base, request_id: requestId, method: "source.list" }),
      (requestId: string) => ({
        ...base,
        request_id: requestId,
        result: { ...base.result, manifest_revision: "c".repeat(64) },
      }),
      (requestId: string) => ({
        ...base,
        request_id: requestId,
        result: { ...base.result, offset: 0 },
      }),
      (requestId: string) => ({
        ...base,
        request_id: requestId,
        result: { ...base.result, limit: 63 },
      }),
      (requestId: string) =>
        createAssetCatalogListResponse(requestId, {
          manifestRevision: revision,
          offset: 64,
          entries: [createAssetEntry(0), createAssetEntry(0)],
          nextOffset: null,
        }),
      (requestId: string) =>
        createAssetCatalogListResponse(requestId, {
          manifestRevision: revision,
          offset: 64,
          entries: createAssetEntries(65),
          nextOffset: null,
        }),
      (requestId: string) =>
        createAssetCatalogListResponse(requestId, {
          manifestRevision: revision,
          offset: 64,
          entries: createAssetEntries(1),
          nextOffset: 64,
        }),
      (requestId: string) =>
        createAssetCatalogListResponse(requestId, {
          manifestRevision: revision,
          offset: 64,
          entries: createAssetEntries(64),
          nextOffset: 129,
        }),
      (requestId: string) => ({
        ...createAssetCatalogListResponse(requestId, {
          manifestRevision: revision,
          offset: 64,
          entries: createAssetEntries(1),
          nextOffset: null,
        }),
        result: {
          ...base.result,
          workspace_path: "/private/world",
        },
      }),
      (requestId: string) =>
        createAssetCatalogListResponse(requestId, {
          manifestRevision: revision,
          offset: 64,
          entries: [{ ...createAssetEntry(0), path: "/private/world/asset.png" }],
          nextOffset: null,
        }),
      (requestId: string) =>
        createAssetCatalogListResponse(requestId, {
          manifestRevision: revision,
          offset: 64,
          entries: [{ ...createAssetEntry(0), binary: "AA==" }],
          nextOffset: null,
        }),
    ];

    for (const reply of cases) {
      harness.request.mockImplementationOnce((requestId: string) =>
        Promise.resolve(reply(requestId)),
      );
      expect(await harness.invoke(IPC_CHANNELS.listAssetCatalog, exactArgument)).toMatchObject({
        ok: false,
        error: { code: "service_unavailable" },
      });
    }

    const largestPageOffset = Number.MAX_SAFE_INTEGER - 63;
    harness.request.mockImplementationOnce((requestId: string) =>
      Promise.resolve(createAssetCatalogListResponse(requestId, {
        manifestRevision: revision,
        offset: largestPageOffset,
        entries: createAssetEntries(64),
        nextOffset: Number.MAX_SAFE_INTEGER + 1,
      })),
    );
    expect(
      await harness.invoke(IPC_CHANNELS.listAssetCatalog, {
        workspaceId: "workspace_01",
        offset: largestPageOffset,
        expectedManifestRevision: revision,
      }),
    ).toMatchObject({
      ok: false,
      error: { code: "service_unavailable" },
    });
  });

  it("rejects inspection replies with wrong authority or extra binary data", async () => {
    const harness = createIpcHarness();
    const argument = {
      workspaceId: "workspace_01",
      manifestRevision: revision,
      entryId,
    };
    const cases = [
      (requestId: string) =>
        createAssetCatalogInspectResponse(requestId, "c".repeat(64), entryId),
      (requestId: string) =>
        createAssetCatalogInspectResponse(
          requestId,
          revision,
          `asset_${"c".repeat(64)}`,
        ),
      (requestId: string) => ({
        ...createAssetCatalogInspectResponse(requestId, revision, entryId),
        result: {
          ...createAssetCatalogInspectResponse(requestId, revision, entryId).result,
          inspection: {
            ...createAssetCatalogInspectResponse(requestId, revision, entryId).result
              .inspection,
            binary: "AA==",
          },
        },
      }),
    ];

    for (const reply of cases) {
      harness.request.mockImplementationOnce((requestId: string) =>
        Promise.resolve(reply(requestId)),
      );
      expect(
        await harness.invoke(IPC_CHANNELS.inspectAssetCatalogEntry, argument),
      ).toMatchObject({
        ok: false,
        error: { code: "service_unavailable" },
      });
    }
  });

  it("rejects untrusted and extra-argument catalog requests before the service", async () => {
    const harness = createIpcHarness();
    expect(
      await harness.invoke(
        IPC_CHANNELS.listAssetCatalog,
        { workspaceId: "workspace_01" },
        { trusted: false },
      ),
    ).toMatchObject({ ok: false, error: { code: "invalid_request" } });
    expect(
      await harness.invoke(
        IPC_CHANNELS.inspectAssetCatalogEntry,
        {
          workspaceId: "workspace_01",
          manifestRevision: revision,
          entryId,
        },
        { extraArgument: true },
      ),
    ).toMatchObject({ ok: false, error: { code: "invalid_request" } });
    expect(harness.request).not.toHaveBeenCalled();
  });

  it("removes both catalog handlers during teardown", () => {
    const harness = createIpcHarness();
    harness.dispose();
    expect(harness.removeHandler).toHaveBeenCalledWith(IPC_CHANNELS.listAssetCatalog);
    expect(harness.removeHandler).toHaveBeenCalledWith(
      IPC_CHANNELS.inspectAssetCatalogEntry,
    );
  });
});

describe("Studio named asset preview IPC contracts", () => {
  const revision = "a".repeat(64);
  const entryId = `asset_${"b".repeat(64)}`;
  const handle = "C".repeat(43);

  it("accepts only closed authority, handle, and bounded sequence inputs", () => {
    expect(
      validateAssetPreviewOpenArgument({
        workspaceId: "workspace_01",
        manifestRevision: revision,
        entryId,
      }),
    ).toEqual({
      workspaceId: "workspace_01",
      manifestRevision: revision,
      entryId,
    });
    expect(validateAssetPreviewReadArgument({ handle, sequence: 8191 })).toEqual({
      handle,
      sequence: 8191,
    });
    expect(validateAssetPreviewCloseArgument({ handle })).toEqual({ handle });

    for (const value of [
      {
        workspaceId: "workspace_01",
        manifestRevision: revision,
        entryId,
        path: "/private/preview.png",
      },
      { handle, sequence: 0, offset: 0 },
      { handle, sequence: 0, size: 65_536 },
      { handle, sequence: 0, encoding: "base64" },
      { handle, sequence: 0, data_base64: "YQ==" },
      { handle, sequence: -1 },
      { handle, sequence: 8192 },
      { handle, sequence: true },
      { handle: "bad" },
    ]) {
      expect(() =>
        "workspaceId" in value
          ? validateAssetPreviewOpenArgument(value)
          : "sequence" in value
            ? validateAssetPreviewReadArgument(value)
            : validateAssetPreviewCloseArgument(value),
      ).toThrow();
    }
  });

  it.each([
    ["image/png", Buffer.from([0x89, 0x50, 0x4e, 0x47])],
    ["audio/wav", Buffer.from("RIFF....WAVE", "ascii")],
  ] as const)("round-trips %s only as a fresh Uint8Array", async (mediaType, payload) => {
    const harness = createIpcHarness();
    harness.request
      .mockImplementationOnce((requestId: string) =>
        Promise.resolve(
          createAssetPreviewOpenResponse(
            requestId,
            handle,
            revision,
            entryId,
            mediaType,
            payload,
          ),
        ),
      )
      .mockImplementationOnce((requestId: string) =>
        Promise.resolve(
          createAssetPreviewReadResponse(
            requestId,
            handle,
            0,
            payload,
            payload,
            true,
          ),
        ),
      )
      .mockImplementationOnce((requestId: string) =>
        Promise.resolve(createAssetPreviewCloseResponse(requestId, handle)),
      );

    expect(
      await harness.invoke(IPC_CHANNELS.openAssetPreview, {
        workspaceId: "workspace_01",
        manifestRevision: revision,
        entryId,
      }),
    ).toMatchObject({ ok: true, value: { result: { handle, media_type: mediaType } } });
    const read = await harness.invoke(IPC_CHANNELS.readAssetPreviewChunk, {
      handle,
      sequence: 0,
    });
    expect(read).toMatchObject({
      ok: true,
      value: {
        method: "asset.preview.read",
        result: { handle, sequence: 0, byte_length: payload.byteLength, eof: true },
      },
    });
    const result = (read as { value: { result: Record<string, unknown> } }).value.result;
    expect(result.bytes).toBeInstanceOf(Uint8Array);
    expect(Buffer.from(result.bytes as Uint8Array)).toEqual(payload);
    expect(result).not.toHaveProperty("data_base64");
    expect(result).not.toHaveProperty("path");
    expect(
      await harness.invoke(IPC_CHANNELS.closeAssetPreview, { handle }),
    ).toMatchObject({ ok: true, value: { result: { handle, closed: true } } });

    expect(harness.request.mock.calls.map((call) => [call[1], call[2]])).toEqual([
      [
        "asset.preview.open",
        {
          workspace_id: "workspace_01",
          manifest_revision: revision,
          entry_id: entryId,
        },
      ],
      ["asset.preview.read", { handle, sequence: 0 }],
      ["asset.preview.close", { handle }],
    ]);
  });

  it("enforces fixed chunks, sequence, cumulative identity, EOF, and replay copies", async () => {
    const harness = createIpcHarness();
    const first = Buffer.alloc(65_536, 0x61);
    const final = Buffer.from("tail");
    const whole = Buffer.concat([first, final]);
    harness.request
      .mockImplementationOnce((requestId: string) =>
        Promise.resolve(
          createAssetPreviewOpenResponse(
            requestId,
            handle,
            revision,
            entryId,
            "image/png",
            whole,
          ),
        ),
      )
      .mockImplementationOnce((requestId: string) =>
        Promise.resolve(
          createAssetPreviewReadResponse(
            requestId,
            handle,
            0,
            first,
            first,
            false,
          ),
        ),
      )
      .mockImplementationOnce((requestId: string) =>
        Promise.resolve(
          createAssetPreviewReadResponse(
            requestId,
            handle,
            1,
            final,
            whole,
            true,
          ),
        ),
      )
      .mockImplementationOnce((requestId: string) =>
        Promise.resolve(
          createAssetPreviewReadResponse(
            requestId,
            handle,
            1,
            final,
            whole,
            true,
          ),
        ),
      );

    await harness.invoke(IPC_CHANNELS.openAssetPreview, {
      workspaceId: "workspace_01",
      manifestRevision: revision,
      entryId,
    });
    const chunk0 = await harness.invoke(IPC_CHANNELS.readAssetPreviewChunk, {
      handle,
      sequence: 0,
    });
    const chunk1 = await harness.invoke(IPC_CHANNELS.readAssetPreviewChunk, {
      handle,
      sequence: 1,
    });
    const replay = await harness.invoke(IPC_CHANNELS.readAssetPreviewChunk, {
      handle,
      sequence: 1,
    });
    expect(chunk0).toMatchObject({
      ok: true,
      value: { result: { byte_length: 65_536, cumulative_bytes: 65_536, eof: false } },
    });
    expect(chunk1).toMatchObject({
      ok: true,
      value: {
        result: {
          byte_length: final.byteLength,
          cumulative_bytes: whole.byteLength,
          eof: true,
        },
      },
    });
    const firstBytes = (chunk1 as { value: { result: { bytes: Uint8Array } } }).value.result
      .bytes;
    const replayBytes = (replay as { value: { result: { bytes: Uint8Array } } }).value.result
      .bytes;
    expect(replayBytes).not.toBe(firstBytes);
    expect(replayBytes).toEqual(firstBytes);
  });

  it("rejects forged open/read/close correlations and malformed base64", async () => {
    const harness = createIpcHarness();
    const payload = Buffer.from("abc");
    harness.request.mockImplementationOnce((requestId: string) =>
      Promise.resolve(
        createAssetPreviewOpenResponse(
          requestId,
          handle,
          revision,
          entryId,
          "image/png",
          payload,
        ),
      ),
    );
    expect(
      await harness.invoke(IPC_CHANNELS.openAssetPreview, {
        workspaceId: "workspace_01",
        manifestRevision: revision,
        entryId,
      }),
    ).toMatchObject({ ok: true });

    const valid = createAssetPreviewReadResponse(
      "placeholder",
      handle,
      0,
      payload,
      payload,
      true,
    );
    const forged = [
      (requestId: string) => ({ ...valid, request_id: `${requestId}-wrong` }),
      (requestId: string) => ({ ...valid, request_id: requestId, method: "source.read" }),
      (requestId: string) => ({
        ...valid,
        request_id: requestId,
        result: { ...valid.result, handle: "D".repeat(43) },
      }),
      (requestId: string) => ({
        ...valid,
        request_id: requestId,
        result: {
          ...valid.result,
          sequence: 1,
          cumulative_bytes: 65_539,
        },
      }),
      (requestId: string) => ({
        ...valid,
        request_id: requestId,
        result: { ...valid.result, data_base64: "YR==" },
      }),
      (requestId: string) => ({
        ...valid,
        request_id: requestId,
        result: { ...valid.result, byte_length: 2 },
      }),
      (requestId: string) => ({
        ...valid,
        request_id: requestId,
        result: { ...valid.result, cumulative_bytes: 4 },
      }),
      (requestId: string) => ({
        ...valid,
        request_id: requestId,
        result: { ...valid.result, cumulative_sha256: "0".repeat(64) },
      }),
      (requestId: string) => ({
        ...valid,
        request_id: requestId,
        result: { ...valid.result, eof: false },
      }),
      (requestId: string) => ({
        ...valid,
        request_id: requestId,
        result: { ...valid.result, path: "/private/preview.png" },
      }),
    ];
    for (const reply of forged) {
      harness.request.mockImplementationOnce((requestId: string) =>
        Promise.resolve(reply(requestId)),
      );
      expect(
        await harness.invoke(IPC_CHANNELS.readAssetPreviewChunk, {
          handle,
          sequence: 0,
        }),
      ).toMatchObject({
        ok: false,
        error: { code: "service_unavailable" },
      });
    }

    harness.request.mockImplementationOnce((requestId: string) =>
      Promise.resolve(createAssetPreviewCloseResponse(requestId, "D".repeat(43))),
    );
    expect(
      await harness.invoke(IPC_CHANNELS.closeAssetPreview, { handle }),
    ).toMatchObject({ ok: false, error: { code: "service_unavailable" } });
  });

  it("rejects mismatched preview authority, forbidden media, and duplicate handles", async () => {
    const payload = Buffer.from("preview");
    const mutations: Array<(response: ReturnType<typeof createAssetPreviewOpenResponse>) => unknown> = [
      (response) => ({
        ...response,
        result: { ...response.result, manifest_revision: "d".repeat(64) },
      }),
      (response) => ({
        ...response,
        result: {
          ...response.result,
          entry_id: `asset_${"d".repeat(64)}`,
        },
      }),
      (response) => ({
        ...response,
        result: { ...response.result, media_type: "font/ttf" },
      }),
      (response) => ({
        ...response,
        result: { ...response.result, media_type: "model/gltf-binary" },
      }),
      (response) => ({
        ...response,
        result: { ...response.result, path: "/private/preview.png" },
      }),
    ];
    for (const mutate of mutations) {
      const harness = createIpcHarness();
      harness.request.mockImplementationOnce((requestId: string) =>
        Promise.resolve(
          mutate(
            createAssetPreviewOpenResponse(
              requestId,
              handle,
              revision,
              entryId,
              "image/png",
              payload,
            ),
          ),
        ),
      );
      expect(
        await harness.invoke(IPC_CHANNELS.openAssetPreview, {
          workspaceId: "workspace_01",
          manifestRevision: revision,
          entryId,
        }),
      ).toMatchObject({
        ok: false,
        error: { code: "service_unavailable" },
      });
    }

    const duplicate = createIpcHarness();
    duplicate.request
      .mockImplementationOnce((requestId: string) =>
        Promise.resolve(
          createAssetPreviewOpenResponse(
            requestId,
            handle,
            revision,
            entryId,
            "image/png",
            payload,
          ),
        ),
      )
      .mockImplementationOnce((requestId: string) =>
        Promise.resolve(
          createAssetPreviewOpenResponse(
            requestId,
            handle,
            revision,
            entryId,
            "image/png",
            payload,
          ),
        ),
      );
    const argument = {
      workspaceId: "workspace_01",
      manifestRevision: revision,
      entryId,
    };
    expect(await duplicate.invoke(IPC_CHANNELS.openAssetPreview, argument)).toMatchObject({
      ok: true,
    });
    expect(await duplicate.invoke(IPC_CHANNELS.openAssetPreview, argument)).toMatchObject({
      ok: false,
      error: { code: "service_unavailable" },
    });
  });

  it("rejects untrusted, extra-argument, and unopened-handle calls before service", async () => {
    const harness = createIpcHarness();
    expect(
      await harness.invoke(
        IPC_CHANNELS.openAssetPreview,
        {
          workspaceId: "workspace_01",
          manifestRevision: revision,
          entryId,
        },
        { trusted: false },
      ),
    ).toMatchObject({ ok: false, error: { code: "invalid_request" } });
    expect(
      await harness.invoke(
        IPC_CHANNELS.openAssetPreview,
        {
          workspaceId: "workspace_01",
          manifestRevision: revision,
          entryId,
        },
        { extraArgument: true },
      ),
    ).toMatchObject({ ok: false, error: { code: "invalid_request" } });
    expect(
      await harness.invoke(IPC_CHANNELS.readAssetPreviewChunk, {
        handle,
        sequence: 0,
      }),
    ).toMatchObject({ ok: false, error: { code: "invalid_request" } });
    expect(harness.request).not.toHaveBeenCalled();
  });

  it("removes all preview handlers during teardown", () => {
    const harness = createIpcHarness();
    harness.dispose();
    expect(harness.removeHandler).toHaveBeenCalledWith(IPC_CHANNELS.openAssetPreview);
    expect(harness.removeHandler).toHaveBeenCalledWith(
      IPC_CHANNELS.readAssetPreviewChunk,
    );
    expect(harness.removeHandler).toHaveBeenCalledWith(IPC_CHANNELS.closeAssetPreview);
  });
});

describe("Studio named changeset IPC contracts", () => {
  it("accepts only closed portable stage, identity, and action inputs", () => {
    const baseSha256 = "a".repeat(64);
    expect(
      validateStageSourceDocumentArgument({
        workspaceId: "workspace_01",
        path: "source/lore/entry.md",
        baseSha256,
        content: "new\n",
      }),
    ).toEqual({
      workspaceId: "workspace_01",
      path: "source/lore/entry.md",
      baseSha256,
      content: "new\n",
    });
    expect(validateChangesetIdArgument({ changesetId: "changeset_01" })).toEqual({
      changesetId: "changeset_01",
    });
    expect(
      validateChangesetActionArgument({
        changesetId: "changeset_01",
        expectedReviewSha256: "b".repeat(64),
      }),
    ).toEqual({
      changesetId: "changeset_01",
      expectedReviewSha256: "b".repeat(64),
    });
    expect(validateChangesetActionArgument({ changesetId: "legacy_01" })).toEqual({
      changesetId: "legacy_01",
    });
  });

  it.each([
    {
      workspaceId: "workspace_01",
      path: "source/../entry.md",
      baseSha256: "a".repeat(64),
      content: "new\n",
    },
    {
      workspaceId: "workspace_01",
      path: "source/lore/entry.md",
      baseSha256: "A".repeat(64),
      content: "new\n",
    },
    {
      workspaceId: "workspace_01",
      path: "source/lore/entry.md",
      baseSha256: "a".repeat(64),
      content: "bad\ud800",
    },
    {
      workspaceId: "workspace_01",
      path: "source/lore/entry.md",
      baseSha256: "a".repeat(64),
      content: "x",
      operation: "delete",
    },
  ])("rejects malformed or capability-shaped stage input %#", (value) => {
    expect(() => validateStageSourceDocumentArgument(value)).toThrow();
  });

  it("enforces the exact UTF-8 byte ceiling for staged text", () => {
    expect(() =>
      validateStageSourceDocumentArgument({
        workspaceId: "workspace_01",
        path: "source/lore/entry.md",
        baseSha256: "a".repeat(64),
        content: "é".repeat(128 * 1024 + 1),
      }),
    ).toThrow();
  });

  it.each([
    [validateChangesetIdArgument, { changesetId: "../bad" }],
    [validateChangesetIdArgument, { changesetId: "changeset_01", operation: "apply" }],
    [
      validateChangesetActionArgument,
      { changesetId: "changeset_01", expectedReviewSha256: null },
    ],
    [
      validateChangesetActionArgument,
      { changesetId: "changeset_01", expectedReviewSha256: "A".repeat(64) },
    ],
  ])("rejects malformed changeset identity input %#", (validate, value) => {
    expect(() => validate(value)).toThrow();
  });

  it("maps six review controls to fixed methods and one replace operation", async () => {
    const harness = createIpcHarness();
    const baseSha256 = "a".repeat(64);
    const calls = [
      [
        IPC_CHANNELS.stageSourceDocument,
        {
          workspaceId: "workspace_01",
          path: "source/lore/entry.md",
          baseSha256,
          content: "new\n",
        },
        "changeset.create",
        {
          workspace_id: "workspace_01",
          operations: [
            {
              path: "source/lore/entry.md",
              operation: "replace",
              expected_base_sha256: baseSha256,
              content: "new\n",
            },
          ],
        },
      ],
      [
        IPC_CHANNELS.getChangeset,
        { changesetId: "changeset_01" },
        "changeset.get",
        { changeset_id: "changeset_01" },
      ],
      [
        IPC_CHANNELS.readChangesetDiff,
        { changesetId: "changeset_01" },
        "changeset.diff",
        { changeset_id: "changeset_01" },
      ],
      [
        IPC_CHANNELS.approveChangeset,
        { changesetId: "changeset_01", expectedReviewSha256: "b".repeat(64) },
        "changeset.approve",
        {
          changeset_id: "changeset_01",
          expected_review_sha256: "b".repeat(64),
        },
      ],
      [
        IPC_CHANNELS.rejectChangeset,
        { changesetId: "legacy_01" },
        "changeset.reject",
        { changeset_id: "legacy_01" },
      ],
      [
        IPC_CHANNELS.applyChangeset,
        { changesetId: "changeset_01", expectedReviewSha256: "b".repeat(64) },
        "changeset.apply",
        {
          changeset_id: "changeset_01",
          expected_review_sha256: "b".repeat(64),
        },
      ],
    ] as const;
    for (const [channel, argument] of calls) {
      expect(await harness.invoke(channel, argument)).toMatchObject({ ok: true });
    }
    expect(harness.request.mock.calls.map((call) => [call[1], call[2]])).toEqual(
      calls.map(([, , method, params]) => [method, params]),
    );
  });

  it("rejects untrusted callers and extra arguments before changeset requests", async () => {
    const harness = createIpcHarness();
    expect(
      await harness.invoke(
        IPC_CHANNELS.stageSourceDocument,
        {
          workspaceId: "workspace_01",
          path: "source/lore/entry.md",
          baseSha256: "a".repeat(64),
          content: "new\n",
        },
        { trusted: false },
      ),
    ).toMatchObject({ ok: false, error: { code: "invalid_request" } });
    expect(
      await harness.invoke(
        IPC_CHANNELS.getChangeset,
        { changesetId: "changeset_01" },
        { extraArgument: true },
      ),
    ).toMatchObject({ ok: false, error: { code: "invalid_request" } });
    expect(harness.request).not.toHaveBeenCalled();
  });

  it("correlates one staged replacement through its full v2 review identity", async () => {
    const harness = createIpcHarness();
    const baseSha256 = "a".repeat(64);
    const content = "new\n";
    harness.request.mockImplementationOnce((requestId: string) =>
      Promise.resolve(
        createChangesetResponse(
          requestId,
          "changeset.create",
          createV2Changeset({ baseSha256, content }),
        ),
      ),
    );
    expect(
      await harness.invoke(IPC_CHANNELS.stageSourceDocument, {
        workspaceId: "workspace_01",
        path: "source/lore/entry.md",
        baseSha256,
        content,
      }),
    ).toMatchObject({ ok: true, value: { result: { changeset: { format_version: 2 } } } });

    for (const mutation of [
      { workspace_id: "workspace_02" },
      { status: "approved" },
      { format_version: 1, review_sha256: undefined },
      { review_sha256: "0".repeat(64) },
      {
        operations: [
          {
            ...createV2Changeset({ baseSha256, content }).operations[0],
            operation: "delete",
          },
        ],
      },
    ]) {
      const record = { ...createV2Changeset({ baseSha256, content }), ...mutation };
      if (mutation.review_sha256 === undefined) delete record.review_sha256;
      harness.request.mockImplementationOnce((requestId: string) =>
        Promise.resolve(createChangesetResponse(requestId, "changeset.create", record)),
      );
      expect(
        await harness.invoke(IPC_CHANNELS.stageSourceDocument, {
          workspaceId: "workspace_01",
          path: "source/lore/entry.md",
          baseSha256,
          content,
        }),
      ).toMatchObject({ ok: false, error: { code: "service_unavailable" } });
    }
  });

  it("correlates get, diff, and exact action status for v1 and v2 replies", async () => {
    const harness = createIpcHarness();
    const v2 = createV2Changeset({ baseSha256: "a".repeat(64), content: "new\n" });
    harness.request.mockImplementationOnce((requestId: string) =>
      Promise.resolve(createChangesetResponse(requestId, "changeset.get", v2)),
    );
    expect(
      await harness.invoke(IPC_CHANNELS.getChangeset, { changesetId: "changeset_01" }),
    ).toMatchObject({ ok: true });

    harness.request.mockImplementationOnce((requestId: string) =>
      Promise.resolve(createDiffResponse(requestId, v2)),
    );
    expect(
      await harness.invoke(IPC_CHANNELS.readChangesetDiff, { changesetId: "changeset_01" }),
    ).toMatchObject({ ok: true });

    for (const [channel, method, status] of [
      [IPC_CHANNELS.approveChangeset, "changeset.approve", "approved"],
      [IPC_CHANNELS.rejectChangeset, "changeset.reject", "rejected"],
      [IPC_CHANNELS.applyChangeset, "changeset.apply", "applied"],
    ] as const) {
      harness.request.mockImplementationOnce((requestId: string) =>
        Promise.resolve(
          createChangesetResponse(requestId, method, { ...v2, status }),
        ),
      );
      expect(
        await harness.invoke(channel, {
          changesetId: "changeset_01",
          expectedReviewSha256: v2.review_sha256,
        }),
      ).toMatchObject({ ok: true });
    }

    const legacy = createV1Changeset();
    harness.request.mockImplementationOnce((requestId: string) =>
      Promise.resolve(
        createChangesetResponse(requestId, "changeset.reject", {
          ...legacy,
          status: "rejected",
        }),
      ),
    );
    expect(
      await harness.invoke(IPC_CHANNELS.rejectChangeset, { changesetId: "legacy_01" }),
    ).toMatchObject({ ok: true });
  });

  it("rejects mismatched IDs, review hashes, statuses, methods, and diff operations", async () => {
    const harness = createIpcHarness();
    const v2 = createV2Changeset({ baseSha256: "a".repeat(64), content: "new\n" });
    const cases = [
      {
        channel: IPC_CHANNELS.getChangeset,
        argument: { changesetId: "changeset_01" },
        reply: (requestId: string) =>
          createChangesetResponse(requestId, "changeset.get", {
            ...v2,
            changeset_id: "changeset_02",
          }),
      },
      {
        channel: IPC_CHANNELS.readChangesetDiff,
        argument: { changesetId: "changeset_01" },
        reply: (requestId: string) => ({
          ...createDiffResponse(requestId, v2),
          result: {
            diff: {
              ...createDiffResponse(requestId, v2).result.diff,
              operations: [
                {
                  ...createDiffResponse(requestId, v2).result.diff.operations[0],
                  operation: "execute",
                },
              ],
            },
          },
        }),
      },
      {
        channel: IPC_CHANNELS.approveChangeset,
        argument: {
          changesetId: "changeset_01",
          expectedReviewSha256: v2.review_sha256,
        },
        reply: (requestId: string) =>
          createChangesetResponse(requestId, "changeset.approve", {
            ...v2,
            status: "rejected",
          }),
      },
      {
        channel: IPC_CHANNELS.applyChangeset,
        argument: {
          changesetId: "changeset_01",
          expectedReviewSha256: "0".repeat(64),
        },
        reply: (requestId: string) =>
          createChangesetResponse(requestId, "changeset.apply", {
            ...v2,
            status: "applied",
          }),
      },
      {
        channel: IPC_CHANNELS.approveChangeset,
        argument: { changesetId: "changeset_01" },
        reply: (requestId: string) =>
          createChangesetResponse(requestId, "changeset.approve", {
            ...v2,
            status: "approved",
          }),
      },
      {
        channel: IPC_CHANNELS.rejectChangeset,
        argument: {
          changesetId: "legacy_01",
          expectedReviewSha256: v2.review_sha256,
        },
        reply: (requestId: string) =>
          createChangesetResponse(requestId, "changeset.reject", {
            ...createV1Changeset(),
            status: "rejected",
          }),
      },
      {
        channel: IPC_CHANNELS.rejectChangeset,
        argument: {
          changesetId: "changeset_01",
          expectedReviewSha256: v2.review_sha256,
        },
        reply: (requestId: string) =>
          createChangesetResponse(requestId, "changeset.approve", {
            ...v2,
            status: "rejected",
          }),
      },
    ];
    for (const testCase of cases) {
      harness.request.mockImplementationOnce((requestId: string) =>
        Promise.resolve(testCase.reply(requestId)),
      );
      expect(await harness.invoke(testCase.channel, testCase.argument)).toMatchObject({
        ok: false,
        error: { code: "service_unavailable" },
      });
    }
  });
});

describe("Studio named authoring and job IPC routing", () => {
  it("owns request IDs and maps every capability to a fixed method and operation", async () => {
    const harness = createIpcHarness();
    const cases: Array<[string, unknown, string, Record<string, unknown>]> = [
      [
        IPC_CHANNELS.getWorkspaceOverview,
        { workspaceId: "workspace_01" },
        "workspace.overview",
        { workspace_id: "workspace_01" },
      ],
      [
        IPC_CHANNELS.listSourceDocuments,
        { workspaceId: "workspace_01" },
        "source.list",
        { workspace_id: "workspace_01" },
      ],
      [
        IPC_CHANNELS.readSourceDocument,
        { workspaceId: "workspace_01", path: "source/world.json" },
        "source.read",
        { workspace_id: "workspace_01", path: "source/world.json" },
      ],
      [
        IPC_CHANNELS.validateWorld,
        { workspaceId: "workspace_01" },
        "world.validate",
        { workspace_id: "workspace_01" },
      ],
      [
        IPC_CHANNELS.analyzeWorld,
        { workspaceId: "workspace_01" },
        "world.analyze",
        { workspace_id: "workspace_01" },
      ],
      [
        IPC_CHANNELS.validateAssetReceipt,
        { workspaceId: "workspace_01", input: { receipt: "receipts/item.json" } },
        "job.create",
        {
          workspace_id: "workspace_01",
          operation: "asset.receipt.validate",
          input: { receipt: "receipts/item.json" },
        },
      ],
      [
        IPC_CHANNELS.verifyAssetpack,
        {
          workspaceId: "workspace_01",
          input: { assetpack: "build/assets.json", worldpack: "build/world.json" },
        },
        "job.create",
        {
          workspace_id: "workspace_01",
          operation: "assetpack.verify",
          input: { assetpack: "build/assets.json", worldpack: "build/world.json" },
        },
      ],
      [
        IPC_CHANNELS.runHeadless,
        { workspaceId: "workspace_01", input: { worldpack: "build/world.json", ticks: 0 } },
        "job.create",
        {
          workspace_id: "workspace_01",
          operation: "runtime.headless",
          input: { worldpack: "build/world.json", ticks: 0 },
        },
      ],
      [
        IPC_CHANNELS.runReplay,
        {
          workspaceId: "workspace_01",
          input: { worldpack: "build/world.json", replay: "replays/slot.json" },
        },
        "job.create",
        {
          workspace_id: "workspace_01",
          operation: "runtime.replay",
          input: { worldpack: "build/world.json", replay: "replays/slot.json" },
        },
      ],
      [IPC_CHANNELS.cancelJob, { jobId: "job_01" }, "job.cancel", { job_id: "job_01" }],
    ];

    for (const [channel, argument] of cases) {
      const result = await harness.invoke(channel, argument);
      expect(result).toMatchObject({ ok: true });
    }
    const calls = harness.request.mock.calls;
    expect(calls).toHaveLength(cases.length);
    expect(calls.map((call) => [call[1], call[2]])).toEqual(
      cases.map(([, , method, params]) => [method, params]),
    );
    const requestIds = calls.map((call) => call[0]);
    expect(new Set(requestIds).size).toBe(requestIds.length);
    expect(requestIds.every((value) => /^[0-9a-f-]{36}$/u.test(value))).toBe(true);
  });

  it("accepts exact operation-specific v2 job.create replies for all four capabilities", async () => {
    const harness = createIpcHarness();
    for (const testCase of jobCapabilityCases()) {
      harness.request.mockImplementationOnce((requestId: string) =>
        Promise.resolve(
          createManagedJobResponse(requestId, testCase.operation, testCase.input),
        ),
      );
      expect(await harness.invoke(testCase.channel, testCase.argument)).toMatchObject({
        ok: true,
        value: {
          kind: "response",
          method: "job.create",
          result: { job: { format_version: 2, operation: testCase.operation } },
        },
      });
    }
  });

  it("rejects cross-operation job.create replies for all four capabilities", async () => {
    const harness = createIpcHarness();
    const cases = jobCapabilityCases();
    for (const [index, testCase] of cases.entries()) {
      const other = cases[(index + 1) % cases.length];
      harness.request.mockImplementationOnce((requestId: string) =>
        Promise.resolve(createManagedJobResponse(requestId, other.operation, other.input)),
      );
      expect(await harness.invoke(testCase.channel, testCase.argument)).toMatchObject({
        ok: false,
        error: { code: "service_unavailable" },
      });
    }
  });

  it("rejects cross-workspace same-operation job.create replies without exposing a job", async () => {
    const harness = createIpcHarness();
    for (const testCase of jobCapabilityCases()) {
      harness.request.mockImplementationOnce((requestId: string) =>
        Promise.resolve(
          createManagedJobResponse(
            requestId,
            testCase.operation,
            testCase.input,
            "workspace_02",
          ),
        ),
      );
      const result = await harness.invoke(testCase.channel, testCase.argument);
      expect(result).toMatchObject({
        ok: false,
        error: { code: "service_unavailable" },
      });
      expect(result).not.toHaveProperty("value");
      expect(JSON.stringify(result)).not.toContain('"job"');
    }
  });

  it("rejects same-operation replies whose inputs do not match the requested job", async () => {
    const harness = createIpcHarness();
    const mismatchedInputs = [
      { receipt: "receipts/other.json" },
      { assetpack: "build/other-assets.json", worldpack: "build/world.json" },
      { worldpack: "build/other-world.json", ticks: 0 },
      { worldpack: "build/world.json", replay: "replays/other.json" },
    ];
    for (const [index, testCase] of jobCapabilityCases().entries()) {
      harness.request.mockImplementationOnce((requestId: string) =>
        Promise.resolve(
          createManagedJobResponse(requestId, testCase.operation, mismatchedInputs[index]),
        ),
      );
      expect(await harness.invoke(testCase.channel, testCase.argument)).toMatchObject({
        ok: false,
        error: { code: "service_unavailable" },
      });
    }
  });

  it("rejects untrusted, extra-argument, mismatched, and malformed replies", async () => {
    const harness = createIpcHarness();
    expect(
      await harness.invoke(
        IPC_CHANNELS.runHeadless,
        { workspaceId: "workspace_01", input: { worldpack: "world.json", ticks: 0 } },
        { trusted: false },
      ),
    ).toMatchObject({ ok: false, error: { code: "invalid_request" } });
    expect(harness.request).not.toHaveBeenCalled();

    expect(
      await harness.invoke(
        IPC_CHANNELS.validateWorld,
        { workspaceId: "workspace_01" },
        { extraArgument: true },
      ),
    ).toMatchObject({ ok: false, error: { code: "invalid_request" } });
    expect(harness.request).not.toHaveBeenCalled();

    harness.request.mockImplementationOnce((requestId: string) =>
      Promise.resolve({
        protocol: "rpg-world-forge.studio_protocol",
        protocol_version: 1,
        kind: "response",
        request_id: requestId,
        method: "world.validate",
        result: {
          validation: {
            valid: true,
            profile: "release",
            world_id: "world_01",
            object_count: 0,
            diagnostics: [],
            diagnostics_truncated: false,
          },
        },
      }),
    );
    expect(
      await harness.invoke(IPC_CHANNELS.analyzeWorld, { workspaceId: "workspace_01" }),
    ).toMatchObject({ ok: false, error: { code: "service_unavailable" } });

    harness.request.mockImplementationOnce(() =>
      Promise.resolve({
        protocol: "rpg-world-forge.studio_protocol",
        protocol_version: 1,
        kind: "error",
        request_id: "wrong-request-id",
        error: { code: "not_found", message: "fixture", details: {} },
      }),
    );
    expect(
      await harness.invoke(IPC_CHANNELS.validateWorld, { workspaceId: "workspace_01" }),
    ).toMatchObject({ ok: false, error: { code: "service_unavailable" } });

    harness.request.mockImplementationOnce((requestId: string) =>
      Promise.resolve({
        protocol: "rpg-world-forge.studio_protocol",
        protocol_version: 1,
        kind: "response",
        request_id: requestId,
        method: "world.validate",
        result: {},
      }),
    );
    expect(
      await harness.invoke(IPC_CHANNELS.validateWorld, { workspaceId: "workspace_01" }),
    ).toMatchObject({ ok: false, error: { code: "service_unavailable" } });
  });
});

describe("Studio named read-only IPC filters", () => {
  it("accepts closed, bounded filters for each list operation", () => {
    expect(
      validateEventsListParams({
        workspace_id: "workspace_01",
        after_id: 0,
        limit: 1_000,
      }),
    ).toEqual({ workspace_id: "workspace_01", after_id: 0, limit: 1_000 });
    expect(
      validateChangesetsListParams({
        workspace_id: "workspace_01",
        status: "approved",
        limit: 100,
      }),
    ).toEqual({ workspace_id: "workspace_01", status: "approved", limit: 100 });
    expect(
      validateJobsListParams({
        workspace_id: "workspace_01",
        state: "awaiting_approval",
        limit: 1,
      }),
    ).toEqual({ workspace_id: "workspace_01", state: "awaiting_approval", limit: 1 });
  });

  it.each([
    [validateEventsListParams, { method: "workspace.register" }],
    [validateEventsListParams, { workspace_id: "../bad" }],
    [validateEventsListParams, { after_id: -1 }],
    [validateEventsListParams, { after_id: true }],
    [validateEventsListParams, { limit: 0 }],
    [validateChangesetsListParams, { status: "created" }],
    [validateChangesetsListParams, { limit: 1_001 }],
    [validateJobsListParams, { state: "approved" }],
    [validateJobsListParams, []],
    [validateJobsListParams, { state: "queued", command: "shell.exec" }],
  ])("rejects malformed, unknown, or mutation-shaped filters %#", (validate, value) => {
    expect(() => validate(value)).toThrow();
  });
});

describe("Codex named IPC contracts", () => {
  it("accepts only closed bounded values", () => {
    expect(validateWorkspaceArgument({ workspaceId: "workspace_01" })).toEqual({ workspaceId: "workspace_01" });
    expect(validateLoginArgument({ mode: "device-code" })).toEqual({ mode: "device-code" });
    expect(validateStartTurnArgument({ threadId: "thread-1", text: "hello" })).toEqual({ threadId: "thread-1", text: "hello" });
    expect(validateInterruptTurnArgument({ threadId: "thread-1", turnId: "turn-1" })).toEqual({ threadId: "thread-1", turnId: "turn-1" });
    expect(validateUserInputArgument({
      token: "00000000-0000-4000-8000-000000000000",
      answers: { choice: ["North"] },
    })).toEqual({
      token: "00000000-0000-4000-8000-000000000000",
      answers: { choice: ["North"] },
    });
  });

  it.each([
    [validateWorkspaceArgument, { workspaceId: "../bad" }],
    [validateLoginArgument, { mode: "api-key" }],
    [validateStartTurnArgument, { threadId: "thread-1", text: "ok", command: "shell" }],
    [validateStartTurnArgument, { threadId: "../bad", text: "ok" }],
    [validateInterruptTurnArgument, { threadId: "thread-1" }],
    [validateUserInputArgument, { token: "bad", answers: { choice: ["x"] } }],
  ])("rejects malformed or capability-shaped input %#", (validate, value) => {
    expect(() => validate(value)).toThrow();
  });
});

function createAssetPreviewOpenResponse(
  requestId: string,
  handle: string,
  manifestRevision: string,
  entryId: string,
  mediaType: "image/png" | "audio/wav",
  payload: Uint8Array,
) {
  return {
    protocol: "rpg-world-forge.studio_protocol",
    protocol_version: 1,
    kind: "response",
    request_id: requestId,
    method: "asset.preview.open",
    result: {
      handle,
      manifest_revision: manifestRevision,
      entry_id: entryId,
      media_type: mediaType,
      byte_length: payload.byteLength,
      sha256: createHash("sha256").update(payload).digest("hex"),
      chunk_bytes: 65_536,
    },
  };
}

function createAssetPreviewReadResponse(
  requestId: string,
  handle: string,
  sequence: number,
  payload: Uint8Array,
  cumulativePayload: Uint8Array,
  eof: boolean,
) {
  return {
    protocol: "rpg-world-forge.studio_protocol",
    protocol_version: 1,
    kind: "response",
    request_id: requestId,
    method: "asset.preview.read",
    result: {
      handle,
      sequence,
      data_base64: Buffer.from(payload).toString("base64"),
      byte_length: payload.byteLength,
      cumulative_bytes: cumulativePayload.byteLength,
      cumulative_sha256: createHash("sha256")
        .update(cumulativePayload)
        .digest("hex"),
      eof,
    },
  };
}

function createAssetPreviewCloseResponse(requestId: string, handle: string) {
  return {
    protocol: "rpg-world-forge.studio_protocol",
    protocol_version: 1,
    kind: "response",
    request_id: requestId,
    method: "asset.preview.close",
    result: { handle, closed: true },
  };
}

function createIpcHarness() {
  const handlers = new Map<string, (event: unknown, ...args: unknown[]) => unknown>();
  const ipcMain = {
    handle: vi.fn((channel: string, handler: (event: unknown, ...args: unknown[]) => unknown) => {
      handlers.set(channel, handler);
    }),
    removeHandler: vi.fn((channel: string) => handlers.delete(channel)),
  };
  const mainFrame = { url: "rwf-studio://app/index.html" };
  const webContents = {
    mainFrame,
    isDestroyed: () => false,
    send: vi.fn(),
  };
  const window = { webContents, isDestroyed: () => false };
  const request = vi.fn(
    (
      requestId: string,
      method: string,
      params: Record<string, unknown>,
      timeoutMs: number,
    ): Promise<unknown> => {
      void method;
      void params;
      void timeoutMs;
      return Promise.resolve({
        protocol: "rpg-world-forge.studio_protocol",
        protocol_version: 1,
        kind: "error",
        request_id: requestId,
        error: { code: "not_found", message: "fixture", details: {} },
      });
    },
  );
  const service = {
    status: { state: "ready", message: "ready", pid: 1 },
    subscribe: () => () => undefined,
    initialize: vi.fn(),
    request,
    getWorkspace: vi.fn(),
    stop: vi.fn(),
  };
  const codex = {
    status: { state: "unbound", message: "unbound", pid: null, workspaceId: null },
    subscribe: () => () => undefined,
  };
  const dispose = registerStudioIpc(
    ipcMain as never,
    window as never,
    service as never,
    codex as never,
  );

  return {
    dispose,
    removeHandler: ipcMain.removeHandler,
    request,
    async invoke(
      channel: string,
      argument: unknown,
      options: { trusted?: boolean; extraArgument?: boolean } = {},
    ): Promise<unknown> {
      const handler = handlers.get(channel);
      if (!handler) throw new Error(`Missing fixture handler for ${channel}`);
      const trusted = options.trusted ?? true;
      const event = trusted
        ? { sender: webContents, senderFrame: mainFrame }
        : { sender: {}, senderFrame: mainFrame };
      const args = options.extraArgument ? [argument, { forbidden: true }] : [argument];
      return await handler(event, ...args);
    },
  };
}

function createAssetEntry(index: number) {
  const suffix = index.toString(16).padStart(64, "0");
  return {
    entry_id: `asset_${suffix}`,
    asset_id: `asset-${String(index)}`,
    category: "manifest",
    role: null,
    path: `assets/catalog-${String(index)}.json`,
    sha256: suffix,
    media_type: "application/json",
    selected: false,
    inspectable: true,
  };
}

function createAssetEntries(count: number) {
  return Array.from({ length: count }, (_, index) => createAssetEntry(index));
}

function createAssetCatalogListResponse(
  requestId: string,
  {
    manifestRevision,
    offset,
    entries,
    nextOffset,
  }: {
    manifestRevision: string;
    offset: number;
    entries: readonly Record<string, unknown>[];
    nextOffset: number | null;
  },
) {
  return {
    protocol: "rpg-world-forge.studio_protocol",
    protocol_version: 1,
    kind: "response",
    request_id: requestId,
    method: "asset.catalog.list",
    result: {
      manifest_revision: manifestRevision,
      offset,
      limit: 64,
      entries,
      next_offset: nextOffset,
    },
  };
}

function createAssetCatalogInspectResponse(
  requestId: string,
  manifestRevision: string,
  entryId: string,
) {
  return {
    protocol: "rpg-world-forge.studio_protocol",
    protocol_version: 1,
    kind: "response",
    request_id: requestId,
    method: "asset.catalog.inspect",
    result: {
      manifest_revision: manifestRevision,
      entry: { ...createAssetEntry(0), entry_id: entryId },
      inspection: {
        kind: "json",
        encoding: "utf-8",
        content: "{}",
        value: {},
      },
    },
  };
}

function jobCapabilityCases() {
  return [
    {
      channel: IPC_CHANNELS.validateAssetReceipt,
      argument: {
        workspaceId: "workspace_01",
        input: { receipt: "receipts/item.json" },
      },
      operation: "asset.receipt.validate",
      input: { receipt: "receipts/item.json" },
    },
    {
      channel: IPC_CHANNELS.verifyAssetpack,
      argument: {
        workspaceId: "workspace_01",
        input: { assetpack: "build/assets.json", worldpack: "build/world.json" },
      },
      operation: "assetpack.verify",
      input: { assetpack: "build/assets.json", worldpack: "build/world.json" },
    },
    {
      channel: IPC_CHANNELS.runHeadless,
      argument: {
        workspaceId: "workspace_01",
        input: { worldpack: "build/world.json", ticks: 0 },
      },
      operation: "runtime.headless",
      input: { worldpack: "build/world.json", ticks: 0 },
    },
    {
      channel: IPC_CHANNELS.runReplay,
      argument: {
        workspaceId: "workspace_01",
        input: { worldpack: "build/world.json", replay: "replays/slot.json" },
      },
      operation: "runtime.replay",
      input: { worldpack: "build/world.json", replay: "replays/slot.json" },
    },
  ] as const;
}

function createManagedJobResponse(
  requestId: string,
  operation: string,
  input: Readonly<Record<string, unknown>>,
  workspaceId = "workspace_01",
) {
  return {
    protocol: "rpg-world-forge.studio_protocol",
    protocol_version: 1,
    kind: "response",
    request_id: requestId,
    method: "job.create",
    result: {
      job: {
        format: "rpg-world-forge.studio_job",
        format_version: 2,
        job_id: "job_01",
        workspace_id: workspaceId,
        operation,
        state: "queued",
        input,
        result: null,
        error: null,
        created_at: "2026-07-23T00:00:00Z",
        updated_at: "2026-07-23T00:00:00Z",
      },
    },
  };
}

function createV2Changeset({ baseSha256, content }: { baseSha256: string; content: string }) {
  const operation = {
    path: "source/lore/entry.md",
    operation: "replace" as const,
    base_sha256: baseSha256,
    base_size: 4,
    proposed_sha256: createHash("sha256").update(content, "utf8").digest("hex"),
    size: Buffer.byteLength(content, "utf8"),
  };
  return {
    format: "rpg-world-forge.studio_changeset" as const,
    format_version: 2 as const,
    changeset_id: "changeset_01",
    workspace_id: "workspace_01",
    status: "staged" as "staged" | "approved" | "rejected" | "applied",
    operations: [operation],
    review_sha256: reviewSha256([operation]),
    created_at: "2026-07-23T00:00:00Z",
    updated_at: "2026-07-23T00:00:00Z",
  };
}

function createV1Changeset() {
  return {
    format: "rpg-world-forge.studio_changeset" as const,
    format_version: 1 as const,
    changeset_id: "legacy_01",
    workspace_id: "workspace_01",
    status: "staged" as "staged" | "approved" | "rejected" | "applied",
    operations: [
      {
        path: "source/lore/legacy.md",
        operation: "replace" as const,
        base_sha256: "a".repeat(64),
        proposed_sha256: "b".repeat(64),
        size: 4,
      },
    ],
    created_at: "2026-07-22T00:00:00Z",
    updated_at: "2026-07-22T00:00:00Z",
  };
}

function reviewSha256(operations: readonly Record<string, unknown>[]): string {
  const projected = operations.map((operation) => ({
    base_sha256: operation.base_sha256,
    base_size: operation.base_size,
    operation: operation.operation,
    path: operation.path,
    proposed_sha256: operation.proposed_sha256,
    size: operation.size,
  }));
  return createHash("sha256")
    .update(
      JSON.stringify({
        format: "rpg-world-forge.studio_changeset_review",
        format_version: 1,
        operations: projected,
      }),
      "utf8",
    )
    .digest("hex");
}

function createChangesetResponse(
  requestId: string,
  method: string,
  changeset: Record<string, unknown>,
) {
  return {
    protocol: "rpg-world-forge.studio_protocol",
    protocol_version: 1,
    kind: "response",
    request_id: requestId,
    method,
    result: { changeset },
  };
}

function createDiffResponse(requestId: string, changeset: ReturnType<typeof createV2Changeset>) {
  const operation = changeset.operations[0];
  return {
    protocol: "rpg-world-forge.studio_protocol",
    protocol_version: 1,
    kind: "response",
    request_id: requestId,
    method: "changeset.diff",
    result: {
      diff: {
        changeset_id: changeset.changeset_id,
        changeset_format_version: 2,
        available: true,
        unavailable_reason: null,
        review_sha256: changeset.review_sha256,
        operations: [
          {
            ...operation,
            text_hunks: [
              {
                base_start: 1,
                base_count: 1,
                proposed_start: 1,
                proposed_count: 1,
                lines: [
                  { kind: "remove", text: "old\n" },
                  { kind: "add", text: "new\n" },
                ],
              },
            ],
            json_pointer_changes: null,
          },
        ],
      },
    },
  };
}
