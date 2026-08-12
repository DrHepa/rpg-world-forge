import { describe, expect, it } from "vitest";

import {
  CREATION_PHASE_CATALOG,
  creationPhaseValidationFingerprint,
  parseCreationArtifactRegistryJson,
  parseCreationPhaseReportJson,
  summarizeCreationPhaseStates,
} from "../../src/renderer/creation-phases";

describe("creation phase authoring state", () => {
  it("keeps the canonical P00-P14 order and distinguishes reviewed not-applicable", () => {
    expect(CREATION_PHASE_CATALOG).toHaveLength(15);
    expect(CREATION_PHASE_CATALOG[0].id).toBe("p00_brief");
    expect(CREATION_PHASE_CATALOG.at(-1)?.id).toBe("p14_handoff");
    const states = summarizeCreationPhaseStates({
      format: "world-forge.creation_workflow_status",
      format_version: 1,
      current_phase: "p04_timeline",
      completed_phases: ["p00_brief", "p01_genre_style", "p02_world_laws", "p03_geography"],
      reports: [
        reportReference("p00_brief", "ready"),
        reportReference("p01_genre_style", "ready"),
        reportReference("p02_world_laws", "ready"),
        reportReference("p03_geography", "not_applicable"),
      ],
      invalidated_reports: [],
    });
    expect(states.find((phase) => phase.id === "p03_geography")?.state).toBe("not_applicable");
    expect(states.find((phase) => phase.id === "p04_timeline")?.state).toBe("current");
    expect(states.find((phase) => phase.id === "p05_societies")?.state).toBe("locked");
  });

  it("parses strict phase reports and bounded artifact registries", () => {
    const report = parseCreationPhaseReportJson(JSON.stringify({
      format: "world-forge.phase_report",
      format_version: 3,
      phase: "p00_brief",
      status: "ready",
    }), "p00_brief");
    expect(report.phase).toBe("p00_brief");
    expect(parseCreationArtifactRegistryJson('[{"artifact_id":"brief"}]')).toEqual([{ artifact_id: "brief" }]);
    expect(() => parseCreationArtifactRegistryJson('[{"id":1,"id":2}]')).toThrow(/duplicate/u);
    expect(() => parseCreationPhaseReportJson('{"format":"world-forge.phase_report","format_version":3,"phase":"p01_genre_style","status":"ready"}', "p00_brief")).toThrow(/phase/u);
  });

  it("invalidates validation evidence after any report, registry, or authority change", () => {
    const report = { format: "world-forge.phase_report", format_version: 3, phase: "p00_brief", status: "ready" };
    const fingerprint = creationPhaseValidationFingerprint({
      expectedRootGeneration: 4,
      expectedSourceRevision: "a".repeat(64),
      expectedWorkflowStatusHash: "b".repeat(64),
    }, report, []);
    expect(fingerprint).toBe(creationPhaseValidationFingerprint({
      expectedRootGeneration: 4,
      expectedSourceRevision: "a".repeat(64),
      expectedWorkflowStatusHash: "b".repeat(64),
    }, { ...report }, []));
    expect(fingerprint).not.toBe(creationPhaseValidationFingerprint({
      expectedRootGeneration: 5,
      expectedSourceRevision: "a".repeat(64),
      expectedWorkflowStatusHash: "b".repeat(64),
    }, report, []));
  });
});

function reportReference(phase: string, status: "ready" | "not_applicable") {
  return { phase, status, content_hash: "a".repeat(64), invalidation_dependencies: [{}] };
}
