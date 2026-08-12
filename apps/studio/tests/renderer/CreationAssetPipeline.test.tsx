// @vitest-environment jsdom

import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CreationAssetPipeline } from "../../src/renderer/CreationAssetPipeline";
import type { CreationExecutionCensus } from "../../src/renderer/creation-execution-state";
import type {
  ForgeStudioApi,
  StudioCreationArtifact,
  StudioCreationArtifactInspectResult,
  StudioCreationOutputGrant,
  StudioCreationWorkspace,
} from "../../src/shared/studio-api";

const SOURCE = "a".repeat(64);
const SNAPSHOT = "b".repeat(64);
const RECORD = "c".repeat(64);
const CRITERION = "1".repeat(64);
const EVIDENCE = "2".repeat(64);

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("CreationAssetPipeline", () => {
  it("admits bounded strict JSON with dependencies selected only from the current census", async () => {
    const active = artifact("artifact_gamepack", "world-forge.gamepack", "active", [], {
      id: "puzzle_gamepack",
    });
    const candidate = artifact("artifact_style", "world-forge.asset_style", "candidate");
    const census = censusWith([active], [candidate]);
    const admitCreationArtifact = vi.fn().mockResolvedValue(
      v4("creation_job.create", { job: job("artifact.admit") }),
    );
    const onSubmittedJob = vi.fn().mockResolvedValue(undefined);
    const onNavigationStateChange = vi.fn();
    const api = pipelineApi({ admitCreationArtifact });

    renderPipeline({ api, census, onSubmittedJob, onNavigationStateChange });
    const admission = screen.getByRole("group", { name: "Admit canonical artifact" });
    await waitFor(() => expect(admission).not.toBeDisabled());
    const document = within(admission).getByLabelText("Canonical artifact JSON");
    fireEvent.change(document, { target: { value: '{"id":"a","id":"b"}' } });
    fireEvent.click(within(admission).getByRole("button", { name: "Admit artifact" }));

    const duplicateAlert = await screen.findByRole("alert");
    expect(duplicateAlert).toHaveTextContent("duplicate object key");
    await waitFor(() => expect(duplicateAlert).toHaveFocus());
    expect(admitCreationArtifact).not.toHaveBeenCalled();

    fireEvent.change(document, {
      target: {
        value: '{"format":"world-forge.asset_style","format_version":1,"style_id":"new_style"}',
      },
    });
    fireEvent.click(within(admission).getByRole("checkbox", { name: /puzzle_gamepack.*Active/iu }));
    fireEvent.click(within(admission).getByRole("checkbox", { name: /style.*Candidate/iu }));
    fireEvent.click(within(admission).getByRole("button", { name: "Admit artifact" }));

    await waitFor(() =>
      expect(admitCreationArtifact).toHaveBeenCalledWith({
        ...authorityParams(),
        document: {
          format: "world-forge.asset_style",
          format_version: 1,
          style_id: "new_style",
        },
        dependencyArtifactIds: ["artifact_gamepack", "artifact_style"],
      }),
    );
    expect(onSubmittedJob).toHaveBeenCalledWith(
      expect.objectContaining({ operation: "artifact.admit", job_id: "job_asset" }),
    );
    expect(onNavigationStateChange).toHaveBeenCalledWith({
      blocksNavigation: true,
      kind: "facet_buffer",
    });
    expect(screen.getByText(/candidate artifacts do not change active readiness/iu)).toBeInTheDocument();
  });

  it("reports a created job truthfully when local tracking fails afterward", async () => {
    const admitCreationArtifact = vi.fn().mockResolvedValue(
      v4("creation_job.create", { job: job("artifact.admit") }),
    );
    const onSubmittedJob = vi.fn().mockRejectedValue(
      new Error("Creation execution authority changed before job tracking"),
    );
    const api = pipelineApi({ admitCreationArtifact });

    renderPipeline({ api, onSubmittedJob });
    await waitFor(() =>
      expect(screen.getByRole("group", { name: "Admit canonical artifact" })).not.toBeDisabled(),
    );
    fireEvent.change(screen.getByLabelText("Canonical artifact JSON"), {
      target: { value: '{"format":"world-forge.asset_style"}' },
    });
    fireEvent.click(screen.getByRole("button", { name: "Admit artifact" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /job job_asset was submitted, but local tracking failed closed/iu,
    );
    expect(admitCreationArtifact).toHaveBeenCalledTimes(1);
  });

  it("submits exact asset processing parameters, generated IDs, and positional acceptance rows", async () => {
    const graph = assetGraph();
    const processCreationAsset = vi.fn().mockResolvedValue(
      v4("creation_job.create", { job: job("asset.process", { format_version: 2 }) }),
    );
    const onSubmittedJob = vi.fn().mockResolvedValue(undefined);
    const api = pipelineApi({
      inspectCreationArtifact: inspectionApi(graph.inspections),
      processCreationAsset,
    });

    renderPipeline({ api, census: graph.census, onSubmittedJob });
    const processing = screen.getByRole("group", { name: "Process licensed asset" });
    const option = await within(processing).findByRole("radio", {
      name: /board_ui.*Candidate.*1 license/iu,
    });
    fireEvent.click(option);
    fireEvent.change(within(processing).getByLabelText("Output ID suffix"), {
      target: { value: "reviewed" },
    });
    fireEvent.change(within(processing).getByLabelText("Criterion 1 SHA-256"), {
      target: { value: CRITERION },
    });
    fireEvent.change(within(processing).getByLabelText("Criterion 1 evidence SHA-256 values"), {
      target: { value: EVIDENCE },
    });
    fireEvent.click(within(processing).getByRole("button", { name: "Process selected asset" }));

    await waitFor(() =>
      expect(processCreationAsset).toHaveBeenCalledWith({
        ...authorityParams(),
        licenseArtifactIds: ["artifact_license"],
        recipeId: "board_ui_reviewed_recipe",
        processingReceiptId: "board_ui_reviewed_processing_receipt",
        qaReportId: "board_ui_reviewed_qa_report",
        acceptanceResults: [
          {
            criterionIndex: 0,
            criterionSha256: CRITERION,
            status: "passed",
            evidenceHashes: [EVIDENCE],
          },
        ],
      }),
    );
    expect(onSubmittedJob).toHaveBeenCalledWith(
      expect.objectContaining({ operation: "asset.process" }),
    );
  });

  it("suppresses an identical in-flight process and separates controlled analysis failure from execution", async () => {
    const graph = assetGraph();
    const existing = job("asset.process", {
      format_version: 2,
      job_id: "job_existing_process",
      state: "running",
      progress: "worker_started",
      operation_params: {
        license_artifact_ids: ["artifact_license"],
        recipe_id: "board_ui_studio_recipe",
        processing_receipt_id: "board_ui_studio_processing_receipt",
        qa_report_id: "board_ui_studio_qa_report",
        acceptance_results: [
          {
            criterion_index: 0,
            criterion_sha256: CRITERION,
            status: "passed",
            evidence_hashes: [EVIDENCE],
          },
        ],
      },
    });
    const listCreationJobs = vi.fn().mockImplementation((params: { state: string | null }) =>
      Promise.resolve(
        v4("creation_job.list", {
          jobs: params.state === "running" ? [existing] : [],
          next_sequence: null,
        }),
      ),
    );
    const processCreationAsset = vi.fn();
    const onObservedJob = vi.fn();
    const api = pipelineApi({
      inspectCreationArtifact: inspectionApi(graph.inspections),
      listCreationJobs,
      processCreationAsset,
    });
    const { rerender } = renderPipeline({ api, census: graph.census, onObservedJob });
    const processing = screen.getByRole("group", { name: "Process licensed asset" });
    fireEvent.click(
      await within(processing).findByRole("radio", { name: /board_ui.*Candidate/iu }),
    );
    fireEvent.change(within(processing).getByLabelText("Criterion 1 SHA-256"), {
      target: { value: CRITERION },
    });
    fireEvent.change(within(processing).getByLabelText("Criterion 1 evidence SHA-256 values"), {
      target: { value: EVIDENCE },
    });
    fireEvent.click(within(processing).getByRole("button", { name: "Process selected asset" }));

    expect(await screen.findByText(/already running; no duplicate was submitted/iu)).toBeInTheDocument();
    expect(processCreationAsset).not.toHaveBeenCalled();
    expect(onObservedJob).toHaveBeenCalledWith(
      expect.objectContaining({ job_id: "job_existing_process" }),
    );

    rerender(
      pipelineElement({
        api,
        census: graph.census,
        onObservedJob,
        observedJob: job("asset.process", {
          format_version: 2,
          state: "succeeded",
          progress: "committed",
          generation: 2,
          result: result("failed", ["processing_partial_publication"]),
        }),
      }),
    );
    expect(
      await screen.findByText(/execution succeeded, but controlled processing analysis failed/iu),
    ).toBeInTheDocument();
    expect(screen.getByText(/processing_partial_publication/u)).toBeInTheDocument();
  });

  it("renders QA candidate preview metadata in a separate v2 lane without sealed publication copy", async () => {
    const graph = assetGraph();
    const api = pipelineApi({
      inspectCreationArtifact: inspectionApi(graph.inspections),
      reviewCreationAssetQa: vi.fn(),
      authorizeCreationAssetRelease: vi.fn(),
    });

    renderPipeline({
      api,
      census: graph.census,
      authorityCapabilities: {
        protocolVersion: 5,
        asset_authority_reviews: true,
        asset_release_authority: true,
        runtime_headless_authority: true,
        creation_preview_pre_release: true,
      },
    });

    const lane = await screen.findByRole("region", { name: "QA candidate preview metadata" });
    expect(within(lane).getByText("qa_review_candidate")).toBeInTheDocument();
    expect(within(lane).getByText("Preview contract v2")).toBeInTheDocument();
    expect(within(lane).getByText("artifact_qa")).toBeInTheDocument();
    expect(within(lane).getByRole("button", { name: "Review selected QA candidate" })).toBeInTheDocument();
    expect(lane.textContent).not.toMatch(/sealed|published|release-ready/iu);
  });

  it("renders only v5 authority release controls and never exposes raw QA seal submission", async () => {
    const graph = assetGraph();
    const sealCreationAssetRelease = vi.fn();
    const api = pipelineApi({
      inspectCreationArtifact: inspectionApi(graph.inspections),
      sealCreationAssetRelease,
      authorizeCreationAssetRelease: vi.fn(),
      reviewCreationAssetQa: vi.fn(),
    });

    renderPipeline({
      api,
      census: graph.census,
      authorityCapabilities: {
        protocolVersion: 5,
        asset_authority_reviews: true,
        asset_release_authority: true,
        runtime_headless_authority: true,
        creation_preview_pre_release: true,
      },
    });

    expect(await screen.findByRole("group", { name: "Review QA candidate authority" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Seal asset release" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Seal selected asset release" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Authorize reviewed asset release" })).toBeDisabled();
    expect(sealCreationAssetRelease).not.toHaveBeenCalled();
  });

  it("keeps legacy v4 seal controls isolated when v5 authority capabilities are absent", async () => {
    const graph = assetGraph();
    const api = pipelineApi({ inspectCreationArtifact: inspectionApi(graph.inspections) });

    renderPipeline({ api, census: graph.census });

    expect(await screen.findByRole("group", { name: "Seal asset release" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Seal selected asset release" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Review QA candidate authority" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Authorize reviewed asset release" })).not.toBeInTheDocument();
  });

  it("selects grants through the native fixed dialog, treats cancel neutrally, revokes with CAS, and seals exact QA", async () => {
    const graph = assetGraph();
    const ready = grant();
    const published = grant({
      state: "published",
      generation: 2,
      publication: {
        format: "world-forge.assetpack",
        format_version: 1,
        id: "puzzle_assetpack",
        content_hash: "8".repeat(64),
        inventory_hash: "9".repeat(64),
      },
    });
    const selectCreationAssetpackOutput = vi
      .fn()
      .mockResolvedValueOnce(clientError("cancelled", "Asset pack output selection was cancelled"))
      .mockResolvedValueOnce(v4("creation_output_grant.create", { grant: ready }))
      .mockResolvedValueOnce(v4("creation_output_grant.create", { grant: ready }));
    const getCreationAssetpackOutput = vi
      .fn()
      .mockResolvedValueOnce(v4("creation_output_grant.get", { grant: ready }))
      .mockResolvedValueOnce(v4("creation_output_grant.get", { grant: published }));
    const revokeCreationAssetpackOutput = vi.fn().mockResolvedValue(
      v4("creation_output_grant.revoke", {
        grant: grant({ state: "revoked", generation: 1 }),
      }),
    );
    const sealCreationAssetRelease = vi.fn().mockResolvedValue(
      v4("creation_job.create", { job: job("asset.release.seal", { format_version: 3 }) }),
    );
    const api = pipelineApi({
      inspectCreationArtifact: inspectionApi(graph.inspections),
      selectCreationAssetpackOutput,
      getCreationAssetpackOutput,
      revokeCreationAssetpackOutput,
      sealCreationAssetRelease,
    });
    renderPipeline({ api, census: graph.census });
    const release = screen.getByRole("group", { name: "Seal asset release" });
    await within(release).findByRole("radio", { name: /1 passed QA.*Candidate/iu });
    fireEvent.click(within(release).getByRole("button", { name: "Select asset pack output" }));
    expect(await screen.findByText(/Output selection was cancelled; no grant was created/iu)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    fireEvent.click(within(release).getByRole("button", { name: "Select asset pack output" }));
    expect(await within(release).findAllByText("puzzle-assets")).toHaveLength(2);
    expect(within(release).getByText("generic_assetpack_directory")).toBeInTheDocument();
    expect(within(release).getAllByText("Ready")).toHaveLength(2);
    expect(within(release).queryByText("grant_assetpack")).not.toBeInTheDocument();
    fireEvent.click(within(release).getByRole("button", { name: "Revoke selected output" }));
    await waitFor(() =>
      expect(revokeCreationAssetpackOutput).toHaveBeenCalledWith({
        grantId: "grant_assetpack",
        expectedGeneration: 0,
      }),
    );

    fireEvent.click(within(release).getByRole("button", { name: "Select another asset pack output" }));
    fireEvent.click(
      await within(release).findByRole("radio", { name: /1 passed QA.*Candidate/iu }),
    );
    fireEvent.click(within(release).getByRole("button", { name: "Seal selected asset release" }));
    await waitFor(() =>
      expect(sealCreationAssetRelease).toHaveBeenCalledWith({
        ...authorityParams(),
        qaReportArtifactIds: ["artifact_qa"],
        manifestId: "puzzle_project_assetpack_1_cccccccccccccccc_cccccccccccccccc",
        targetGrantId: "grant_assetpack",
        expectedTargetGrantGeneration: 0,
      }),
    );
    expect(getCreationAssetpackOutput).toHaveBeenCalledWith("grant_assetpack");
  });

  it("rejects output authority that is not an exact pathless public grant", async () => {
    const leakedGrant = {
      ...grant(),
      native_path: "/private/assetpack",
    };
    const api = pipelineApi({
      selectCreationAssetpackOutput: vi.fn().mockResolvedValue(
        v4("creation_output_grant.create", { grant: leakedGrant }),
      ),
    });

    renderPipeline({ api });
    fireEvent.click(
      await screen.findByRole("button", { name: "Select asset pack output" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /invalid asset output grant/iu,
    );
    expect(document.body.textContent).not.toContain("/private/");
  });

  it("rejects impossible output grant timestamps even when their shape is UTC-like", async () => {
    const api = pipelineApi({
      selectCreationAssetpackOutput: vi.fn().mockResolvedValue(
        v4("creation_output_grant.create", {
          grant: grant({
            created_at: "2026-99-99T99:99:99Z",
            updated_at: "2026-99-99T99:99:99Z",
          }),
        }),
      ),
    });

    renderPipeline({ api });
    fireEvent.click(
      await screen.findByRole("button", { name: "Select asset pack output" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /invalid asset output grant/iu,
    );
  });

  it("keeps durable output authority out of the component-local discard state", async () => {
    const onNavigationStateChange = vi.fn();
    const api = pipelineApi({
      selectCreationAssetpackOutput: vi.fn().mockResolvedValue(
        v4("creation_output_grant.create", { grant: grant() }),
      ),
    });

    renderPipeline({ api, onNavigationStateChange });
    fireEvent.click(
      await screen.findByRole("button", { name: "Select asset pack output" }),
    );
    expect(await screen.findAllByText("puzzle-assets")).toHaveLength(2);

    await waitFor(() =>
      expect(onNavigationStateChange).toHaveBeenLastCalledWith({
        blocksNavigation: false,
        kind: "clean",
      }),
    );
  });

  it("enforces exact create and revoke grant transitions", async () => {
    const invalidCreateApi = pipelineApi({
      selectCreationAssetpackOutput: vi.fn().mockResolvedValue(
        v4("creation_output_grant.create", {
          grant: grant({ state: "reserved", generation: 1 }),
        }),
      ),
    });
    const first = renderPipeline({ api: invalidCreateApi });
    fireEvent.click(
      await screen.findByRole("button", { name: "Select asset pack output" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(/create|ready|generation/iu);
    first.unmount();

    const invalidRevokeApi = pipelineApi({
      selectCreationAssetpackOutput: vi.fn().mockResolvedValue(
        v4("creation_output_grant.create", { grant: grant() }),
      ),
      revokeCreationAssetpackOutput: vi.fn().mockResolvedValue(
        v4("creation_output_grant.revoke", {
          grant: grant({ state: "revoked", generation: 2 }),
        }),
      ),
    });
    renderPipeline({ api: invalidRevokeApi });
    fireEvent.click(
      await screen.findByRole("button", { name: "Select asset pack output" }),
    );
    expect(await screen.findAllByText("puzzle-assets")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Revoke selected output" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/revoke|generation/iu);
  });

  it("clears inline buffers while retaining live output authority across exact authority changes", async () => {
    const first = censusWith([], []);
    const second = censusWith([], []);
    second.authority = { ...second.authority, artifactSnapshotHash: "f".repeat(64) };
    const ready = grant();
    const api = pipelineApi({
      selectCreationAssetpackOutput: vi.fn().mockResolvedValue(
        v4("creation_output_grant.create", { grant: ready }),
      ),
    });
    const view = renderPipeline({ api, census: first });
    fireEvent.change(screen.getByLabelText("Canonical artifact JSON"), {
      target: { value: '{"format":"world-forge.asset_style"}' },
    });
    fireEvent.click(screen.getByRole("button", { name: "Select asset pack output" }));
    expect(await screen.findAllByText("puzzle-assets")).toHaveLength(2);

    view.rerender(pipelineElement({ api, census: second }));
    await waitFor(() => expect(screen.getByLabelText("Canonical artifact JSON")).toHaveValue(""));
    expect(screen.getAllByText("puzzle-assets")).toHaveLength(2);
    expect(screen.getByRole("status", { name: "Asset pipeline status" })).toHaveTextContent(
      /current authority/u,
    );
  });

  it("blocks navigation while a fixed native request is unresolved", async () => {
    let resolveSelection: ((value: ReturnType<typeof clientError>) => void) | undefined;
    const selectCreationAssetpackOutput = vi.fn().mockReturnValue(
      new Promise<ReturnType<typeof clientError>>((resolve) => {
        resolveSelection = resolve;
      }),
    );
    const onNavigationStateChange = vi.fn();
    const api = pipelineApi({ selectCreationAssetpackOutput });

    renderPipeline({ api, onNavigationStateChange });
    const select = await screen.findByRole("button", {
      name: "Select asset pack output",
    });
    fireEvent.click(select);
    await waitFor(() =>
      expect(onNavigationStateChange).toHaveBeenLastCalledWith({
        blocksNavigation: true,
        kind: "request_pending",
      }),
    );

    resolveSelection?.(
      clientError("cancelled", "Asset pack output selection was cancelled"),
    );
    await screen.findByText(/Output selection was cancelled/iu);
    await waitFor(() =>
      expect(onNavigationStateChange).toHaveBeenLastCalledWith({
        blocksNavigation: false,
        kind: "clean",
      }),
    );
  });

  it("integrates sealed v4 PNG preview candidates without free-form asset or grant IDs", async () => {
    const graph = assetGraph();
    const assetpack = artifact(
      "artifact_assetpack",
      "world-forge.assetpack",
      "candidate",
      ["artifact_selection", "artifact_license", "artifact_qa"],
      { id: "puzzle_assetpack", contentHash: "8".repeat(64) },
    );
    assetpack.producer = {
      kind: "future_candidate",
      phase_id: null,
      reference_id: "job_seal_preview",
    };
    graph.census.candidateArtifacts.push(assetpack);
    graph.census.selectableArtifacts.push(assetpack);
    graph.census.selectableById = new Map([
      ...graph.census.selectableById,
      [assetpack.artifact_id, assetpack],
    ]);
    graph.inspections.set(
      assetpack.artifact_id,
      inspection(
        assetpack,
        ["artifact_selection", "artifact_license", "artifact_qa"],
        { asset_count: 1 },
        null,
        graph.census,
      ),
    );
    const published = grant({
      state: "published",
      generation: 2,
      publication: {
        format: "world-forge.assetpack",
        format_version: 1,
        id: "puzzle_assetpack",
        content_hash: "8".repeat(64),
        inventory_hash: "9".repeat(64),
      },
    });
    const sealed = job("asset.release.seal", {
      format_version: 3,
      job_id: "job_seal_preview",
      state: "succeeded",
      progress: "committed",
      generation: 3,
      operation_params: {
        qa_report_artifact_ids: ["artifact_qa"],
        manifest_id: "puzzle_manifest",
        target_grant_id: "grant_assetpack",
        target_grant_generation: 2,
      },
      result: {
        output_artifact_ids: ["artifact_asset_manifest", "artifact_assetpack"],
        artifact_snapshot_hash: SNAPSHOT,
        analysis_status: "passed",
        reason_codes: [],
        cleanup_pending: false,
        publication: {
          grant_id: "grant_assetpack",
          grant_generation: 2,
          kind: "generic_assetpack_directory",
          state: "published",
          assetpack: published.publication,
        },
      },
    });
    const getCreationJob = vi.fn().mockResolvedValue(
      v4("creation_job.get", { job: sealed }),
    );
    const openCreationPreview = vi.fn();
    const api = pipelineApi({
      inspectCreationArtifact: inspectionApi(graph.inspections),
      getCreationJob,
      openCreationPreview,
    });

    const view = renderPipeline({ api, census: graph.census, grants: [published] });

    const previews = await screen.findByRole("region", { name: "Sealed asset previews" });
    expect(within(previews).getByLabelText("Verified sealed asset")).toHaveTextContent(
      "board_ui — PNG",
    );
    expect(within(previews).getByText("Service-bound and pathless")).toBeInTheDocument();
    expect(within(previews).queryByLabelText(/asset ID/iu)).not.toBeInTheDocument();
    expect(within(previews).queryByLabelText(/grant ID/iu)).not.toBeInTheDocument();
    expect(openCreationPreview).not.toHaveBeenCalled();
    expect(getCreationJob).toHaveBeenCalledWith("job_seal_preview");

    view.rerender(
      pipelineElement({
        api,
        census: graph.census,
        grants: [{ ...published, state: "revoked", generation: 3 }],
      }),
    );
    expect(within(previews).queryByLabelText("Verified sealed asset")).not.toBeInTheDocument();
    expect(
      within(previews).queryByRole("button", { name: "Open verified preview" }),
    ).not.toBeInTheDocument();
  });

  it("gates v5 QA authority review on closed main capability and sends only stable selectors", async () => {
    const graph = assetGraph();
    const reviewCreationAssetQa = vi.fn().mockResolvedValue(
      v5("creation_job.create", {
        job: job("asset.qa.review", {
          format_version: 10,
          operation_params: {
            qa_report_artifact_id: "artifact_qa",
            output_role: "primary",
            review_receipt_id: "review_receipt_01",
            decisions: ["approved"],
            blockers: [],
          },
          result: {
            output_artifact_ids: ["review_receipt_01"],
            artifact_snapshot_hash: SNAPSHOT,
            analysis_status: "passed",
            reason_codes: [],
            cleanup_pending: false,
            review_receipt: {
              format: "world-forge.asset_qa_review_receipt",
              format_version: 1,
              review_receipt_id: "review_receipt_01",
              content_hash: "3".repeat(64),
            },
            review_status: "approved",
          },
        }),
      }),
    );
    const api = pipelineApi({
      inspectCreationArtifact: inspectionApi(graph.inspections),
      reviewCreationAssetQa,
    });
    renderPipeline({
      api,
      census: graph.census,
      authorityCapabilities: {
        protocolVersion: 5,
        asset_authority_reviews: true,
        asset_release_authority: true,
        runtime_headless_authority: true,
        creation_preview_pre_release: true,
      },
    });

    const authority = await screen.findByRole("group", { name: "Review QA candidate authority" });
    fireEvent.click(within(authority).getByRole("radio", { name: /board_ui.*Pending QA authority/iu }));
    fireEvent.click(within(authority).getByRole("button", { name: "Review selected QA candidate" }));

    await waitFor(() =>
      expect(reviewCreationAssetQa).toHaveBeenCalledWith({
        workspaceId: "creation_workspace",
        qaReportArtifactId: "artifact_qa",
        outputRole: "primary",
      }),
    );
    expect(JSON.stringify(reviewCreationAssetQa.mock.calls[0]?.[0])).not.toMatch(/path|decision|blocker|hash/iu);
  });
});

function renderPipeline(overrides: Partial<React.ComponentProps<typeof CreationAssetPipeline>> = {}) {
  return render(pipelineElement(overrides));
}

function pipelineElement(overrides: Partial<React.ComponentProps<typeof CreationAssetPipeline>> = {}) {
  const census = overrides.census ?? censusWith([], []);
  return (
    <PipelineHarness
      api={overrides.api ?? pipelineApi()}
      workspace={overrides.workspace ?? workspace()}
      census={census}
      authorityCapabilities={overrides.authorityCapabilities}
      executionBusy={overrides.executionBusy ?? false}
      observedJob={overrides.observedJob ?? null}
      grants={overrides.grants}
      initialGrant={overrides.grant ?? null}
      onGrantChange={overrides.onGrantChange ?? vi.fn()}
      onNavigationStateChange={overrides.onNavigationStateChange ?? vi.fn()}
      onSubmittedJob={overrides.onSubmittedJob ?? vi.fn().mockResolvedValue(undefined)}
      onObservedJob={overrides.onObservedJob ?? vi.fn()}
    />
  );
}

function PipelineHarness({
  initialGrant,
  onGrantChange,
  ...props
}: Omit<React.ComponentProps<typeof CreationAssetPipeline>, "grant" | "onGrantChange"> & {
  initialGrant: StudioCreationOutputGrant | null;
  onGrantChange: (grant: StudioCreationOutputGrant | null) => void;
}) {
  const [grantState, setGrantState] = useState(initialGrant);
  return (
    <CreationAssetPipeline
      {...props}
      grant={grantState}
      onGrantChange={(next) => {
        setGrantState(next);
        onGrantChange(next);
      }}
    />
  );
}

function pipelineApi(overrides: Partial<ForgeStudioApi> = {}): ForgeStudioApi {
  return {
    inspectCreationArtifact: vi.fn(),
    listCreationJobs: vi.fn().mockResolvedValue(
      v4("creation_job.list", { jobs: [], next_sequence: null }),
    ),
    admitCreationArtifact: vi.fn(),
    processCreationAsset: vi.fn(),
    selectCreationAssetpackOutput: vi.fn(),
    getCreationAssetpackOutput: vi.fn(),
    revokeCreationAssetpackOutput: vi.fn(),
    sealCreationAssetRelease: vi.fn(),
    ...overrides,
  } as unknown as ForgeStudioApi;
}

function inspectionApi(inspections: Map<string, StudioCreationArtifactInspectResult>) {
  return vi.fn().mockImplementation((params: { artifactId: string }) => {
    const value = inspections.get(params.artifactId);
    if (!value) throw new Error(`missing inspection ${params.artifactId}`);
    return Promise.resolve(v4("creation_artifact.inspect", value as unknown as Record<string, unknown>));
  });
}

function assetGraph(): {
  census: CreationExecutionCensus;
  inspections: Map<string, StudioCreationArtifactInspectResult>;
} {
  const definitions = [
    ["artifact_gamepack", "world-forge.gamepack", []],
    ["artifact_subject", "world-forge.asset_subject", ["artifact_gamepack"]],
    ["artifact_target", "world-forge.asset_target", ["artifact_gamepack"]],
    ["artifact_style", "world-forge.asset_style", ["artifact_target"]],
    ["artifact_inventory", "world-forge.asset_inventory", ["artifact_subject", "artifact_target", "artifact_style"]],
    ["artifact_spec", "world-forge.asset_spec", ["artifact_inventory"]],
    ["artifact_request", "world-forge.asset_production_request", ["artifact_spec"]],
    ["artifact_receipt", "world-forge.asset_production_receipt", ["artifact_request"]],
    ["artifact_selection", "world-forge.asset_selection", ["artifact_receipt"]],
    ["artifact_provenance", "world-forge.asset_provenance_record", ["artifact_selection"]],
    ["artifact_license", "world-forge.asset_license_record", ["artifact_provenance"]],
    [
      "artifact_recipe",
      "world-forge.asset_processing_recipe",
      [
        "artifact_gamepack",
        "artifact_subject",
        "artifact_target",
        "artifact_style",
        "artifact_inventory",
        "artifact_spec",
        "artifact_request",
        "artifact_receipt",
        "artifact_selection",
        "artifact_provenance",
        "artifact_license",
      ],
    ],
    ["artifact_processing_receipt", "world-forge.asset_processing_receipt", ["artifact_recipe"]],
    ["artifact_qa", "world-forge.asset_qa_report", ["artifact_processing_receipt"]],
  ] as const;
  const artifacts = definitions.map(([id, format, dependencies]) =>
    artifact(id, format, "candidate", dependencies, { id: id.replace("artifact_", "") }),
  );
  const census = censusWith([], artifacts);
  const inspections = new Map<string, StudioCreationArtifactInspectResult>();
  for (const [id, , dependencies] of definitions) {
    const item = census.selectableById.get(id)!;
    const facts: Record<string, string | number | boolean | string[]> = {};
    let status: string | null = null;
    if (id === "artifact_inventory") facts.asset_count = 1;
    if (id === "artifact_receipt" || id === "artifact_processing_receipt") {
      facts.output_count = 1;
    }
    if (id === "artifact_selection") {
      facts.asset_id = "board_ui";
      facts.selected_output_bindings = ["board_ui_candidate:texture"];
    }
    if (id === "artifact_license" || id === "artifact_qa") facts.asset_id = "board_ui";
    if (id === "artifact_license") {
      facts.candidate_artifact_id = "board_ui_candidate";
      facts.candidate_role = "texture";
      facts.commercial_use = true;
      facts.redistribution = true;
    }
    if (id === "artifact_qa") {
      facts.blocker_count = 0;
      status = "passed";
    }
    inspections.set(id, inspection(item, [...dependencies], facts, status, census));
  }
  return { census, inspections };
}

function censusWith(
  activeArtifacts: StudioCreationArtifact[],
  candidateArtifacts: StudioCreationArtifact[],
): CreationExecutionCensus {
  const selectableArtifacts = [...activeArtifacts, ...candidateArtifacts];
  return {
    authority: {
      workspaceId: "creation_workspace",
      rootGeneration: 4,
      sourceRevision: SOURCE,
      workflowStatusHash: null,
      artifactSnapshotHash: SNAPSHOT,
    },
    evidence: {} as CreationExecutionCensus["evidence"],
    activeArtifacts,
    candidateArtifacts,
    selectableArtifacts,
    selectableById: new Map(selectableArtifacts.map((item) => [item.artifact_id, item])),
  };
}

function artifact(
  artifactId: string,
  format: string,
  lifecycle: "active" | "candidate",
  dependencies: readonly string[] = [],
  overrides: { id?: string; contentHash?: string } = {},
): StudioCreationArtifact {
  return {
    format: "world-forge.studio_creation_artifact",
    format_version: 1,
    artifact_id: artifactId,
    subject: {
      format,
      format_version: 1,
      id: overrides.id ?? artifactId.replace("artifact_", ""),
      content_hash: overrides.contentHash ?? "c".repeat(64),
    },
    lifecycle,
    roles: [format === "world-forge.gamepack" ? "compiled_logic" : "asset_lineage"],
    producer: { kind: "future_candidate", phase_id: null, reference_id: "job_asset" },
    references: { dependency_count: dependencies.length, dependent_count: 0 },
    authority: publicAuthority(),
    record_hash: RECORD,
  };
}

function inspection(
  item: StudioCreationArtifact,
  dependencies: string[],
  facts: Record<string, string | number | boolean | string[]>,
  status: string | null,
  census: CreationExecutionCensus,
): StudioCreationArtifactInspectResult {
  return {
    authority: item.authority,
    artifact_snapshot_hash: census.authority.artifactSnapshotHash,
    artifact: item,
    projection: {
      projection_kind: item.roles[0],
      title: item.subject.id,
      status,
      facts: Object.entries(facts).map(([key, value]) => ({ key, value })),
      lineage: dependencies.map((artifactId) => ({
        relation: "depends_on",
        artifact_id: artifactId,
        lifecycle: census.selectableById.get(artifactId)!.lifecycle,
      })),
    },
  };
}

function workspace(): StudioCreationWorkspace {
  return {
    format: "world-forge.studio_creation_workspace",
    format_version: 1,
    workspace_id: "creation_workspace",
    project_kind: "game",
    root_generation: 4,
    project: {
      format: "world-forge.project",
      format_version: 1,
      id: "puzzle_project",
      content_hash: "d".repeat(64),
    },
    source_revision: SOURCE,
    workflow_status_hash: null,
    created_at: "2026-08-04T00:00:00Z",
    updated_at: "2026-08-04T00:00:00Z",
  };
}

function authorityParams() {
  return {
    workspaceId: "creation_workspace",
    expectedRootGeneration: 4,
    expectedSourceRevision: SOURCE,
    expectedWorkflowStatusHash: null,
    expectedArtifactSnapshotHash: SNAPSHOT,
  };
}

function publicAuthority() {
  return {
    workspace_id: "creation_workspace",
    root_generation: 4,
    source_revision: SOURCE,
    workflow_status_hash: null,
  };
}

function job(operation: string, overrides: Record<string, unknown> = {}) {
  return {
    format: "world-forge.studio_creation_job",
    format_version: 1,
    job_id: "job_asset",
    workspace_id: "creation_workspace",
    operation,
    state: "queued",
    generation: 0,
    authority: {
      root_generation: 4,
      source_revision: SOURCE,
      workflow_status_hash: null,
      artifact_snapshot_hash: SNAPSHOT,
    },
    inputs: [],
    progress: "queued",
    result: null,
    error: null,
    created_at: "2026-08-04T00:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: "2026-08-04T00:00:00Z",
    record_hash: RECORD,
    ...overrides,
  };
}

function result(analysisStatus: string, reasonCodes: string[]) {
  return {
    analysis_status: analysisStatus,
    reason_codes: reasonCodes,
    output_artifact_ids: ["artifact_recipe", "artifact_processing_receipt"],
    artifact_snapshot_hash: "e".repeat(64),
    cleanup_pending: false,
  };
}

function grant(overrides: Partial<StudioCreationOutputGrant> = {}): StudioCreationOutputGrant {
  return {
    format: "world-forge.studio_creation_output_grant",
    format_version: 1,
    grant_id: "grant_assetpack",
    workspace_id: "creation_workspace",
    kind: "generic_assetpack_directory",
    display_name: "puzzle-assets",
    state: "ready",
    generation: 0,
    publication: null,
    created_at: "2026-08-04T00:00:00Z",
    updated_at: "2026-08-04T00:00:00Z",
    ...overrides,
  };
}

function v4(method: string, resultValue: Record<string, unknown>) {
  return {
    ok: true as const,
    value: {
      protocol: "rpg-world-forge.studio_protocol" as const,
      protocol_version: 4 as const,
      kind: "response" as const,
      request_id: "request_01",
      method,
      result: resultValue,
    },
  };
}

function v5(method: string, resultValue: Record<string, unknown>) {
  return {
    ok: true as const,
    value: {
      protocol: "rpg-world-forge.studio_protocol" as const,
      protocol_version: 5 as const,
      kind: "response" as const,
      request_id: "request_authority",
      method,
      result: resultValue,
    },
  };
}

function clientError(code: string, message: string) {
  return {
    ok: false as const,
    error: { code, message },
  };
}
