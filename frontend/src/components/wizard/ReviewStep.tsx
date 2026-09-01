import type { LoadSpaceSpec } from "../../types";
import type { LoadPlanDraft, PlanningMode } from "../../wizardTypes";

const MODE_LABELS: Record<PlanningMode, string> = {
  loose_boxes: "Loose Boxes",
  palletized_load: "Palletized Load",
  build_pallets: "Build Pallets",
  panels_fragile: "Panels & Fragile",
  custom_load: "Custom Load",
};

const ORIENTATION_LABELS: Record<string, string> = {
  free: "Free Rotation",
  upright: "Keep Upright",
  fixed: "Fixed",
  panel_edge_only: "Keep On Edge (panel face never a base)",
};

const WEIGHT_BALANCE_LABELS: Record<string, string> = { ignore: "Ignore", normal: "Normal", important: "Important" };

interface Props {
  draft: LoadPlanDraft;
  catalog: LoadSpaceSpec[];
}

export function ReviewStep({ draft, catalog }: Props) {
  const selectedContainer = draft.loadSpace?.containerId
    ? catalog.find((c) => c.id === draft.loadSpace!.containerId)
    : undefined;
  const custom = draft.loadSpace?.custom;
  const isCustomSpace = Boolean(custom);

  return (
    <div className="wizard-step-body">
      <h2>Review Load Plan</h2>
      <p className="step-subtitle">Confirm the details below before creating your load plan.</p>

      <div className="review-section">
        <h3>Load</h3>
        <div className="review-row">
          <span className="review-label">Load Type</span>
          <span>{draft.planningMode ? MODE_LABELS[draft.planningMode] : "—"}</span>
        </div>
        <div className="review-row">
          <span className="review-label">Total Units</span>
          <span>{draft.importPreview?.summary.total_units.toLocaleString() ?? "—"}</span>
        </div>
        <div className="review-row">
          <span className="review-label">Total Weight</span>
          <span>{draft.importPreview ? `${draft.importPreview.summary.total_weight.toLocaleString()} kg` : "—"}</span>
        </div>
      </div>

      <div className="review-section">
        <h3>Load Space</h3>
        {selectedContainer && (
          <>
            <div className="review-row">
              <span className="review-label">Name</span>
              <span>{selectedContainer.name}</span>
            </div>
            <div className="review-row">
              <span className="review-label">Type</span>
              <span>{selectedContainer.load_space_type ?? "container"}</span>
            </div>
            <div className="review-row">
              <span className="review-label">Dimensions</span>
              <span>
                {selectedContainer.length} × {selectedContainer.width} × {selectedContainer.height} mm
              </span>
            </div>
            <div className="review-row">
              <span className="review-label">Max Payload</span>
              <span>{selectedContainer.max_weight} kg</span>
            </div>
          </>
        )}
        {custom && (
          <>
            <div className="review-row">
              <span className="review-label">Name</span>
              <span>{custom.name || "—"}</span>
            </div>
            <div className="review-row">
              <span className="review-label">Type</span>
              <span>{custom.loadSpaceType}</span>
            </div>
            <div className="review-row">
              <span className="review-label">Dimensions</span>
              <span>
                {custom.length} × {custom.width} × {custom.height} mm
              </span>
            </div>
            <div className="review-row">
              <span className="review-label">Max Payload</span>
              <span>{custom.maxWeight} kg</span>
            </div>
          </>
        )}
        {!selectedContainer && !custom && (
          <div className="review-row">
            <span className="review-label">Load Space</span>
            <span>—</span>
          </div>
        )}
        {isCustomSpace && (
          <div className="wizard-note" style={{ marginTop: 10, marginBottom: 0 }}>
            Optimizing directly into a custom Truck/Trailer/Custom Space from the workspace is coming in a future
            update. For now the workspace will open with your imported items and you can select a container.
          </div>
        )}
      </div>

      <div className="review-section">
        <h3>Handling Rules</h3>
        {draft.handlingRules && (
          <>
            <div className="review-row">
              <span className="review-label">Orientation</span>
              <span>{ORIENTATION_LABELS[draft.handlingRules.orientationPolicy]}</span>
            </div>
            <div className="review-row">
              <span className="review-label">Default Stackable</span>
              <span>{draft.handlingRules.defaultStackable ? "Yes" : "No"}</span>
            </div>
            <div className="review-row">
              <span className="review-label">Minimum Clearance</span>
              <span>{draft.handlingRules.clearanceMm} mm</span>
            </div>
            <div className="review-row">
              <span className="review-label">Central Aisle</span>
              <span>
                {draft.handlingRules.enableCentralAisle ? `On (${draft.handlingRules.aisleWidthMm} mm)` : "Off"}
              </span>
            </div>
            <div className="review-row">
              <span className="review-label">Weight Balance</span>
              <span>{WEIGHT_BALANCE_LABELS[draft.handlingRules.weightBalanceMode]}</span>
            </div>
          </>
        )}
      </div>

      <div className="review-section">
        <h3>Import</h3>
        {draft.importPreview ? (
          <>
            <div className="review-row">
              <span className="review-label">Rows</span>
              <span>{draft.importPreview.summary.total_rows}</span>
            </div>
            <div className="review-row">
              <span className="review-label">Valid Rows</span>
              <span>{draft.importPreview.summary.valid_rows}</span>
            </div>
            <div className="review-row">
              <span className="review-label">Warnings</span>
              <span>{draft.importPreview.warnings.length}</span>
            </div>
            <div className="review-row">
              <span className="review-label">Errors</span>
              <span>{draft.importPreview.errors.length}</span>
            </div>
          </>
        ) : (
          <div className="review-row">
            <span className="review-label">Import</span>
            <span>—</span>
          </div>
        )}
      </div>
    </div>
  );
}
