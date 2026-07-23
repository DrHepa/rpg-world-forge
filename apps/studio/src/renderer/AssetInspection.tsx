import type {
  StudioAssetCatalogEntry,
  StudioAssetInspection,
} from "../shared/studio-api";
import {
  ASSET_CATALOG_CATEGORY_LABELS,
  ASSET_INSPECTION_KIND_LABELS,
} from "./asset-catalog-state";
import { AssetBinaryPreview } from "./AssetBinaryPreview";
import type { AssetBinaryPreviewContext } from "./asset-binary-preview-state";

const JSON_RENDER_MAX_NODES = 2_000;
const JSON_RENDER_MAX_DEPTH = 12;

export function AssetInspection({
  entry,
  inspection,
  pending,
  previewContext,
}: {
  entry: StudioAssetCatalogEntry | null;
  inspection: StudioAssetInspection | null;
  pending: boolean;
  previewContext?: AssetBinaryPreviewContext;
}) {
  return (
    <aside className="asset-detail" aria-labelledby="asset-detail-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Verified metadata and bounded preview</p>
          <h2 id="asset-detail-heading">Asset inspection</h2>
        </div>
        {inspection ? (
          <span>{ASSET_INSPECTION_KIND_LABELS[inspection.kind]}</span>
        ) : null}
      </div>

      {!entry ? (
        <p className="empty-state">
          Select an inspectable entry from this revision snapshot.
        </p>
      ) : (
        <>
          <dl className="asset-metadata">
            <Metadata label="Entry ID" value={entry.entry_id} code />
            <Metadata label="Asset ID" value={entry.asset_id ?? "Not assigned"} />
            <Metadata
              label="Category"
              value={ASSET_CATALOG_CATEGORY_LABELS[entry.category]}
            />
            <Metadata label="Role" value={entry.role ?? "Not reported"} />
            <Metadata label="Media type" value={entry.media_type ?? "Not reported"} />
            <Metadata label="Portable path" value={entry.path ?? "Identity only"} code />
            <Metadata label="SHA-256" value={entry.sha256} code />
            <Metadata label="Production selection" value={entry.selected ? "Selected" : "No"} />
            <Metadata label="Inspectable" value={entry.inspectable ? "Yes" : "No"} />
          </dl>
          {pending ? <p role="status">Inspecting verified metadata…</p> : null}
          {!pending && !inspection ? (
            <p className="empty-state">
              Inspection metadata has not been loaded for this entry.
            </p>
          ) : null}
          {inspection ? (
            <InspectionBody
              entry={entry}
              inspection={inspection}
              previewContext={previewContext}
            />
          ) : null}
        </>
      )}
    </aside>
  );
}

function InspectionBody({
  entry,
  inspection,
  previewContext,
}: {
  entry: StudioAssetCatalogEntry;
  inspection: StudioAssetInspection;
  previewContext: AssetBinaryPreviewContext | undefined;
}) {
  switch (inspection.kind) {
    case "json":
      return (
        <section className="asset-inspection-body" aria-labelledby="json-inspection-heading">
          <h3 id="json-inspection-heading">Semantic JSON tree</h3>
          <p className="bounded-note">
            Bounded to {JSON_RENDER_MAX_NODES.toLocaleString("en-US")} semantic nodes and
            depth {JSON_RENDER_MAX_DEPTH}. Values are displayed as inert text.
          </p>
          <JsonTree value={inspection.value} />
        </section>
      );
    case "glsl":
      return (
        <section className="asset-inspection-body" aria-labelledby="glsl-inspection-heading">
          <h3 id="glsl-inspection-heading">GLSL source</h3>
          <pre className="asset-code"><code>{inspection.content}</code></pre>
          <p className="bounded-note">
            Source is escaped text only. Shader compilation and rendering are unavailable.
          </p>
        </section>
      );
    case "png":
      return (
        <section className="asset-inspection-body" aria-labelledby="png-inspection-heading">
          <h3 id="png-inspection-heading">PNG structure</h3>
          <dl className="inspection-metrics">
            <Metric label="Dimensions" value={`${String(inspection.width)} × ${String(inspection.height)}`} />
            <Metric label="Bit depth" value={String(inspection.bit_depth)} />
            <Metric label="Color type" value={String(inspection.color_type)} />
            <Metric label="Interlaced" value={inspection.interlaced ? "Yes" : "No"} />
          </dl>
          <AssetBinaryPreview
            context={previewContext}
            entry={entry}
            inspection={inspection}
          />
        </section>
      );
    case "wav":
      return (
        <section className="asset-inspection-body" aria-labelledby="wav-inspection-heading">
          <h3 id="wav-inspection-heading">WAV structure</h3>
          <dl className="inspection-metrics">
            <Metric label="Channels" value={String(inspection.channels)} />
            <Metric label="Sample rate" value={`${String(inspection.sample_rate)} Hz`} />
            <Metric label="Sample width" value={`${String(inspection.sample_width_bits)} bits`} />
            <Metric label="Frames" value={inspection.frame_count.toLocaleString("en-US")} />
            <Metric label="Duration" value={`${String(inspection.duration_ms)} ms`} />
          </dl>
          <AssetBinaryPreview
            context={previewContext}
            entry={entry}
            inspection={inspection}
          />
        </section>
      );
    case "font":
      return (
        <section className="asset-inspection-body" aria-labelledby="font-inspection-heading">
          <h3 id="font-inspection-heading">Font structure</h3>
          <dl className="inspection-metrics">
            <Metric
              label="Flavor"
              value={inspection.flavor === "truetype" ? "TrueType" : "OpenType"}
            />
            <Metric label="Tables" value={String(inspection.table_count)} />
          </dl>
          <UnavailablePreview>
            Font loading and glyph preview are unavailable in this metadata-only cockpit.
          </UnavailablePreview>
        </section>
      );
    case "glb":
      return (
        <section className="asset-inspection-body" aria-labelledby="glb-inspection-heading">
          <h3 id="glb-inspection-heading">GLB structure</h3>
          <dl className="inspection-metrics">
            <Metric label="Bytes" value={inspection.byte_length.toLocaleString("en-US")} />
            <Metric label="JSON chunk" value={inspection.json_chunk_bytes.toLocaleString("en-US")} />
            <Metric label="Binary chunk" value={inspection.bin_chunk_bytes.toLocaleString("en-US")} />
            <Metric label="Nodes" value={String(inspection.metrics.nodes)} />
            <Metric label="Meshes" value={String(inspection.metrics.meshes)} />
            <Metric label="Materials" value={String(inspection.metrics.materials)} />
            <Metric label="Textures" value={String(inspection.metrics.textures)} />
            <Metric label="Skins" value={String(inspection.metrics.skins)} />
            <Metric label="Bones" value={String(inspection.metrics.bones)} />
            <Metric label="Influences" value={String(inspection.metrics.influences)} />
            <Metric label="Animations" value={String(inspection.metrics.animations)} />
            <Metric label="Vertices" value={inspection.metrics.vertices.toLocaleString("en-US")} />
            <Metric label="Triangles" value={inspection.metrics.triangles.toLocaleString("en-US")} />
            <Metric label="External URI count" value={String(inspection.metrics.external_uris)} />
            <Metric label="Embedded URI count" value={String(inspection.embedded_uris)} />
            <Metric
              label="Required extensions"
              value={String(inspection.extensions_required.length)}
            />
            <Metric
              label="Max texture dimension"
              value={String(inspection.max_texture_dimension)}
            />
          </dl>
          {inspection.extensions_used.length > 0 ? (
            <div className="inspection-list">
              <h4>Extensions used</h4>
              <ul>
                {inspection.extensions_used.map((extension) => (
                  <li key={extension}>{extension}</li>
                ))}
              </ul>
            </div>
          ) : null}
          <UnavailablePreview>
            3D scene rendering is unavailable in this metadata-only cockpit.
          </UnavailablePreview>
        </section>
      );
    case "unavailable":
      return (
        <section className="asset-inspection-body" aria-labelledby="unavailable-inspection-heading">
          <h3 id="unavailable-inspection-heading">Inspection unavailable</h3>
          <p className="empty-state">
            {inspection.reason === "identity_only"
              ? "This catalog record is identity-only and has no inspectable file."
              : "This verified media type does not have a metadata inspector."}
          </p>
        </section>
      );
  }
}

function JsonTree({ value }: { value: Record<string, unknown> }) {
  const budget = { remaining: JSON_RENDER_MAX_NODES };
  return (
    <div className="json-tree" aria-label="Bounded JSON value">
      {renderJsonValue(value, 0, budget, "root")}
    </div>
  );
}

function renderJsonValue(
  value: unknown,
  depth: number,
  budget: { remaining: number },
  key: string,
): React.ReactNode {
  if (budget.remaining <= 0 || depth > JSON_RENDER_MAX_DEPTH) {
    return <span className="json-limit">Bounded display limit reached</span>;
  }
  budget.remaining -= 1;
  if (value === null) return <span className="json-null">null</span>;
  if (typeof value === "string") return <span className="json-string">{value}</span>;
  if (typeof value === "number") return <span className="json-number">{String(value)}</span>;
  if (typeof value === "boolean") {
    return <span className="json-boolean">{value ? "true" : "false"}</span>;
  }
  if (Array.isArray(value)) {
    return (
      <ol className="json-array">
        {value.map((item, index) => (
          <li key={`${key}-${String(index)}`}>
            <span className="json-key">{String(index)}</span>
            {renderJsonValue(item, depth + 1, budget, `${key}-${String(index)}`)}
          </li>
        ))}
      </ol>
    );
  }
  if (typeof value === "object") {
    return (
      <dl className="json-object">
        {Object.entries(value).map(([property, child]) => (
          <div key={`${key}-${property}`}>
            <dt>{property}</dt>
            <dd>{renderJsonValue(child, depth + 1, budget, `${key}-${property}`)}</dd>
          </div>
        ))}
      </dl>
    );
  }
  return <span className="json-limit">Unsupported bounded value</span>;
}

function Metadata({
  label,
  value,
  code = false,
}: {
  label: string;
  value: string;
  code?: boolean;
}) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{code ? <code>{value}</code> : value}</dd>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function UnavailablePreview({ children }: { children: React.ReactNode }) {
  return <p className="asset-preview-unavailable">{children}</p>;
}
