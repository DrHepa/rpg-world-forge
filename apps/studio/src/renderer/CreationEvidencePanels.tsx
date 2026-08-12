import type {
  StudioCreationArtifact,
  StudioCreationArtifactInspectResult,
  StudioCreationEvidence,
} from "../shared/studio-api";
import type { ReactNode } from "react";

export type CreationEvidencePanelMode = "assets" | "compatibility" | "materialize";

export interface CreationEvidencePanelsProps {
  mode: CreationEvidencePanelMode;
  evidence: StudioCreationEvidence;
  artifacts: readonly StudioCreationArtifact[];
  inspection: StudioCreationArtifactInspectResult | null;
  inspectionPending: boolean;
  inspectionError: string | null;
  onInspect: (artifactId: string) => void;
  assetPipeline?: ReactNode;
  runtimePipeline?: ReactNode;
  materializationPipeline?: ReactNode;
}

export function CreationEvidencePanels(props: CreationEvidencePanelsProps) {
  if (props.mode === "assets") return <AssetEvidence {...props} />;
  if (props.mode === "compatibility") return <CompatibilityEvidence {...props} />;
  return <MaterializationEvidence {...props} />;
}

function AssetEvidence({
  evidence,
  artifacts,
  inspection,
  inspectionPending,
  inspectionError,
  onInspect,
  assetPipeline,
}: CreationEvidencePanelsProps) {
  const assets = evidence.assets;
  return (
    <div className="creation-evidence-layout">
      <section className="creation-card" aria-labelledby="creation-asset-evidence-heading">
        <p className="eyebrow">Active immutable closure</p>
        <h3 id="creation-asset-evidence-heading">Asset evidence</h3>
        <dl className="creation-readiness">
          <EvidenceFact label="Pipeline state" value={humanize(evidence.dimensions.assets)} />
          <EvidenceFact
            label="Active artifact records"
            value={`${String(artifacts.length)} of ${String(evidence.artifact_counts.active)}`}
          />
          <EvidenceFact label="Inventory assets" value={assets.inventory_assets} />
          <EvidenceFact
            label="Complete lineage"
            value={`${String(assets.lineage_complete)} of ${String(assets.inventory_assets)}`}
          />
          <EvidenceFact label="Partial lineage" value={assets.lineage_partial} />
          <EvidenceFact label="QA passed" value={assets.qa_passed} />
          <EvidenceFact label="QA failed" value={assets.qa_failed} />
          <EvidenceFact label="Licensed" value={assets.licensed} />
        </dl>
        <p className="creation-evidence-note">
          This immutable evidence remains inspection-only. Fixed asset production controls
          operate separately below and create candidate evidence. Verified sealed PNG and WAV
          previews use service-bound leases below; unsupported media remains metadata-only.
        </p>
      </section>

      {assetPipeline}

      <section className="creation-card" aria-labelledby="creation-asset-identities-heading">
        <p className="eyebrow">Content-addressed bindings</p>
        <h3 id="creation-asset-identities-heading">Asset identities</h3>
        <dl className="creation-facts">
          <EvidenceFact label="Subject" value={assets.subject_artifact_id ?? "Not present"} />
          <EvidenceFact label="Target" value={assets.target_artifact_id ?? "Not present"} />
          <EvidenceFact label="Style" value={assets.style_artifact_id ?? "Not present"} />
          <EvidenceFact label="Inventory" value={assets.inventory_artifact_id ?? "Not present"} />
          <EvidenceFact label="Asset pack" value={assets.assetpack_artifact_id ?? "Not present"} />
        </dl>
      </section>

      <section className="creation-card creation-evidence-wide" aria-labelledby="active-artifacts-heading">
        <p className="eyebrow">Pathless census</p>
        <h3 id="active-artifacts-heading">Active artifact closure</h3>
        {artifacts.length === 0 ? (
          <p>No active artifact evidence is present.</p>
        ) : (
          <ul className="creation-artifact-list">
            {artifacts.map((artifact) => (
              <li key={artifact.artifact_id}>
                <div>
                  <strong>{artifact.subject.id}</strong>
                  <span>{artifact.subject.format} v{String(artifact.subject.format_version)}</span>
                  <code>{artifact.subject.content_hash}</code>
                  <dl className="creation-facts" aria-label={`${artifact.subject.id} artifact evidence`}>
                    <EvidenceFact label="Lifecycle" value={humanize(artifact.lifecycle)} />
                    <EvidenceFact label="Roles" value={artifact.roles} />
                    <EvidenceFact label="Producer" value={artifact.producer.kind} />
                    <EvidenceFact
                      label="Producer phase"
                      value={artifact.producer.phase_id ?? "Not present"}
                    />
                    <EvidenceFact label="Producer reference" value={artifact.producer.reference_id} />
                    <EvidenceFact
                      label="Dependencies"
                      value={artifact.references.dependency_count}
                    />
                    <EvidenceFact
                      label="Dependents"
                      value={artifact.references.dependent_count}
                    />
                  </dl>
                </div>
                <button
                  type="button"
                  disabled={inspectionPending}
                  onClick={() => onInspect(artifact.artifact_id)}
                >
                  Inspect {artifact.subject.id}
                </button>
              </li>
            ))}
          </ul>
        )}
        {inspectionPending ? <p role="status">Inspecting artifact evidence…</p> : null}
        {inspectionError ? <p role="alert" className="inline-error">{inspectionError}</p> : null}
        {inspection ? <ArtifactProjection inspection={inspection} /> : null}
      </section>
    </div>
  );
}

function ArtifactProjection({
  inspection,
}: {
  inspection: StudioCreationArtifactInspectResult;
}) {
  return (
    <section className="creation-artifact-inspection" aria-labelledby="artifact-projection-heading">
      <p className="eyebrow">Redacted projection</p>
      <h4 id="artifact-projection-heading">{inspection.projection.title}</h4>
      <dl className="creation-facts">
        <EvidenceFact label="Status" value={inspection.projection.status ?? "Not declared"} />
        <EvidenceFact label="Projection kind" value={inspection.projection.projection_kind} />
        {inspection.projection.facts.map((fact) => (
          <EvidenceFact key={fact.key} label={fact.key} value={formatFact(fact.value)} />
        ))}
        <EvidenceFact label="Lineage edges" value={inspection.projection.lineage.length} />
      </dl>
      <h5>Lineage</h5>
      {inspection.projection.lineage.length === 0 ? (
        <p>No lineage edges are present in this projection.</p>
      ) : (
        <ul className="creation-prerequisite-list" aria-label="Artifact lineage edges">
          {inspection.projection.lineage.slice(0, 128).map((edge) => (
            <li key={`${edge.relation}\u0000${edge.artifact_id}`}>
              <strong>{edge.relation}</strong>
              <code>{edge.artifact_id}</code>
              <span>{humanize(edge.lifecycle)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function CompatibilityEvidence({ evidence, runtimePipeline }: CreationEvidencePanelsProps) {
  const dimensions = evidence.dimensions;
  const mechanics = evidence.mechanics;
  return (
    <div className="creation-evidence-layout">
      {runtimePipeline}
      <section className="creation-card" aria-labelledby="runtime-compatibility-heading">
        <p className="eyebrow">Independent support dimensions</p>
        <h3 id="runtime-compatibility-heading">Runtime compatibility evidence</h3>
        <dl className="creation-readiness">
          <EvidenceFact label="Authoring" value={humanize(dimensions.authoring)} />
          <EvidenceFact label="Compilation" value={humanize(dimensions.compilation)} />
          <EvidenceFact label="Assets" value={humanize(dimensions.assets)} />
          <EvidenceFact label="Adapter" value={humanize(dimensions.adapter)} />
          <EvidenceFact label="Packaging" value={humanize(dimensions.packaging)} />
          <EvidenceFact label="Release" value={humanize(dimensions.release)} />
        </dl>
        <p className="creation-evidence-note">
          Authoring validity is not runtime support. Release remains blocked until every required
          dimension is verified.
        </p>
      </section>

      <section className="creation-card" aria-labelledby="runtime-adapter-heading">
        <p className="eyebrow">Capability resolution</p>
        <h3 id="runtime-adapter-heading">Runtime adapter</h3>
        <dl className="creation-facts">
          <EvidenceFact
            label="Requested adapter"
            value={evidence.runtime.requested_adapter ?? "Not requested"}
          />
          <EvidenceFact
            label="Resolved adapter"
            value={evidence.runtime.resolved_adapter ?? "Not resolved"}
          />
        </dl>
        <IdentifierList
          label="Runtime required features"
          values={evidence.runtime.required_features}
          empty="No runtime features are required."
        />
        <IdentifierList
          label="Runtime missing features"
          values={evidence.runtime.missing_features}
          empty="No required runtime features are missing."
        />
      </section>

      <section className="creation-card" aria-labelledby="mechanic-ledger-heading">
        <p className="eyebrow">Mechanic capability ledger</p>
        <h3 id="mechanic-ledger-heading">Mechanic coverage</h3>
        <dl className="creation-facts">
          <EvidenceFact
            label="Mechanic ledger artifact"
            value={mechanics.artifact_id ?? "Not present"}
          />
          <EvidenceFact label="Total" value={mechanics.total} />
          <EvidenceFact label="Supported current" value={mechanics.status_counts.supported_current} />
          <EvidenceFact
            label="Extension verified"
            value={mechanics.status_counts.game_extension_verified}
          />
          <EvidenceFact label="Authoring only" value={mechanics.status_counts.authoring_only} />
          <EvidenceFact label="Blocked" value={mechanics.status_counts.blocked} />
        </dl>
        <IdentifierList
          label="Ledger required features"
          values={mechanics.required_features}
          empty="The mechanic ledger declares no required features."
        />
        <IdentifierList
          label="Ledger missing features"
          values={mechanics.missing_features}
          empty="The mechanic ledger has no missing features."
        />
      </section>

      <section className="creation-card" aria-labelledby="platform-evidence-heading">
        <p className="eyebrow">Per-platform execution</p>
        <h3 id="platform-evidence-heading">Execution evidence</h3>
        {evidence.runtime.platforms.length === 0 ? (
          <p>No platform execution evidence is present.</p>
        ) : (
          <ul className="creation-prerequisite-list" aria-label="Platform execution evidence">
            {evidence.runtime.platforms.map((row) => (
              <li key={row.platform}>
                <strong>{row.platform}</strong>
                <span>{humanize(row.status)}</span>
                {row.evidence_ids.length === 0 ? (
                  <span>No evidence IDs are present.</span>
                ) : (
                  <ul aria-label={`${row.platform} evidence IDs`}>
                    {row.evidence_ids.slice(0, 128).map((evidenceId) => (
                      <li key={evidenceId}><code>{evidenceId}</code></li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        )}
        {evidence.blocker_reason_codes.length > 0 ? (
          <div className="creation-blockers">
            <strong>Blocker reason codes</strong>
            <ul>
              {evidence.blocker_reason_codes.map((code) => <li key={code}><code>{code}</code></li>)}
            </ul>
          </div>
        ) : (
          <p>No blocker reason codes are present.</p>
        )}
      </section>
    </div>
  );
}

function MaterializationEvidence({
  evidence,
  materializationPipeline,
}: CreationEvidencePanelsProps) {
  return (
    <div className="creation-evidence-layout">
      <section className="creation-card creation-evidence-wide" aria-labelledby="materialization-heading">
        <p className="eyebrow">Active readiness inspection</p>
        <h3 id="materialization-heading">Materialization readiness</h3>
        <p>
          This immutable view reports active reviewed readiness. Fixed pathless controls below
          create candidate artifacts without changing this evidence or claiming release support.
        </p>
        <dl className="creation-facts">
          <EvidenceFact label="State" value={humanize(evidence.materialization.state)} />
          <EvidenceFact label="Release" value={humanize(evidence.dimensions.release)} />
          <EvidenceFact label="Handoff" value={evidence.handoff.id} />
          <EvidenceFact label="Handoff hash" value={evidence.handoff.content_hash} />
        </dl>
        {evidence.materialization.prerequisites.length === 0 ? (
          <p>No materialization prerequisites are declared.</p>
        ) : (
          <ul className="creation-prerequisite-list">
            {evidence.materialization.prerequisites.slice(0, 128).map((item) => (
              <li key={item.code} data-satisfied={String(item.satisfied)}>
                <strong>{item.satisfied ? "Satisfied" : "Blocked"}</strong>
                <span>{item.message}</span>
                <code>{item.code}</code>
              </li>
            ))}
          </ul>
        )}
      </section>
      {materializationPipeline}
    </div>
  );
}

function IdentifierList({
  label,
  values,
  empty,
}: {
  label: string;
  values: readonly string[];
  empty: string;
}) {
  return (
    <div className="creation-blockers">
      <strong>{label}</strong>
      {values.length === 0 ? (
        <p>{empty}</p>
      ) : (
        <ul aria-label={label}>
          {values.slice(0, 128).map((value) => <li key={value}><code>{value}</code></li>)}
        </ul>
      )}
    </div>
  );
}

function EvidenceFact({
  label,
  value,
}: {
  label: string;
  value: string | number | boolean | readonly string[];
}) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{Array.isArray(value) ? (value.length > 0 ? value.join(", ") : "None") : String(value)}</dd>
    </div>
  );
}

function formatFact(
  value: string | number | boolean | null | string[],
): string | number | boolean | string[] {
  if (value === null) return "Not declared";
  return value;
}

function humanize(value: string): string {
  const normalized = value.replaceAll("_", " ");
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}
