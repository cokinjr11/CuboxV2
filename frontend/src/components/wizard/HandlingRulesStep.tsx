import type { LoadingAnchor, OrientationPolicy, WeightBalanceMode } from "../../types";
import type { HandlingRulesDraft, PlanningMode } from "../../wizardTypes";

const ORIENTATION_OPTIONS: { value: OrientationPolicy; label: string }[] = [
  { value: "free", label: "Free Rotation" },
  { value: "upright", label: "Keep Upright" },
  { value: "fixed", label: "Fixed" },
];

const WEIGHT_BALANCE_OPTIONS: { value: WeightBalanceMode; label: string }[] = [
  { value: "ignore", label: "Ignore" },
  { value: "normal", label: "Normal" },
  { value: "important", label: "Important" },
];

const LOADING_ANCHOR_OPTIONS: { value: LoadingAnchor; label: string }[] = [
  { value: "back_right", label: "Back Right" },
  { value: "back_left", label: "Back Left" },
];

interface Props {
  mode: PlanningMode;
  value: HandlingRulesDraft;
  onChange: (patch: Partial<HandlingRulesDraft>) => void;
}

export function HandlingRulesStep({ mode, value, onChange }: Props) {
  return (
    <div className="wizard-step-body">
      <h2>Handling Rules</h2>
      <p className="step-subtitle">These are plan defaults. Values explicitly set in your Excel file will always win over these defaults.</p>

      {mode === "loose_boxes" && <OrientationField value={value.orientationPolicy} onChange={(v) => onChange({ orientationPolicy: v })} />}

      {mode === "palletized_load" && (
        <FloorRotationField
          value={value.orientationPolicy}
          onChange={(v) => onChange({ orientationPolicy: v })}
        />
      )}

      {mode === "panels_fragile" && (
        <div className="wizard-note">
          <strong>Keep On Edge is enabled and locked.</strong> The main panel face (Width × Height) can never be used as
          the supporting base.
        </div>
      )}

      {mode === "custom_load" && <OrientationField value={value.orientationPolicy} onChange={(v) => onChange({ orientationPolicy: v })} />}

      <div className="wizard-checkbox-row">
        <input
          id="hr-stackable"
          type="checkbox"
          checked={value.defaultStackable}
          onChange={(e) => onChange({ defaultStackable: e.target.checked })}
        />
        <label htmlFor="hr-stackable">{mode === "palletized_load" ? "Pallet Stacking Allowed" : "Default Stackable"}</label>
      </div>

      <div className="wizard-field" style={{ maxWidth: 220 }}>
        <label htmlFor="hr-clearance">Minimum Clearance (mm)</label>
        <input
          id="hr-clearance"
          type="number"
          min={0}
          value={value.clearanceMm || ""}
          onChange={(e) => onChange({ clearanceMm: Number(e.target.value) })}
        />
      </div>

      <div className="wizard-checkbox-row">
        <input
          id="hr-aisle"
          type="checkbox"
          checked={value.enableCentralAisle}
          onChange={(e) => onChange({ enableCentralAisle: e.target.checked })}
        />
        <label htmlFor="hr-aisle">Central Aisle</label>
      </div>
      {value.enableCentralAisle && (
        <div className="wizard-field" style={{ maxWidth: 220 }}>
          <label htmlFor="hr-aisle-width">Aisle Width (mm)</label>
          <input
            id="hr-aisle-width"
            type="number"
            min={0}
            value={value.aisleWidthMm || ""}
            onChange={(e) => onChange({ aisleWidthMm: Number(e.target.value) })}
          />
        </div>
      )}

      <div className="wizard-field">
        <label>Weight Balance</label>
        <div className="wizard-radio-row">
          {WEIGHT_BALANCE_OPTIONS.map((opt) => (
            <label key={opt.value} className="wizard-radio-option">
              <input
                type="radio"
                name="weight-balance"
                checked={value.weightBalanceMode === opt.value}
                onChange={() => onChange({ weightBalanceMode: opt.value })}
              />
              {opt.label}
            </label>
          ))}
        </div>
      </div>

      {mode === "panels_fragile" && (
        <div className="wizard-field">
          <label>Loading Start</label>
          <div className="wizard-radio-row">
            {LOADING_ANCHOR_OPTIONS.map((opt) => (
              <label key={opt.value} className="wizard-radio-option">
                <input
                  type="radio"
                  name="loading-anchor"
                  checked={value.loadingAnchor === opt.value}
                  onChange={() => onChange({ loadingAnchor: opt.value })}
                />
                {opt.label}
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// Fase 6: Floor Rotation es el MISMO campo orientationPolicy que ya usan
// Loose Boxes/Custom Load (HandlingRulesDraft.orientationPolicy) -no es un
// campo nuevo, solo una UI mas simple (Yes/No) para un item que solo admite
// 2 politicas: UPRIGHT (puede rotar 90 grados en el piso) o FIXED (ninguna).
function FloorRotationField({ value, onChange }: { value: OrientationPolicy; onChange: (v: OrientationPolicy) => void }) {
  const allowed = value !== "fixed";
  return (
    <div className="wizard-field">
      <label>Floor Rotation</label>
      <div className="wizard-radio-row">
        <label className="wizard-radio-option">
          <input type="radio" name="floor-rotation" checked={allowed} onChange={() => onChange("upright")} />
          Allowed (Length × Width or Width × Length, Height stays vertical)
        </label>
        <label className="wizard-radio-option">
          <input type="radio" name="floor-rotation" checked={!allowed} onChange={() => onChange("fixed")} />
          Not Allowed (Fixed orientation, no rotation)
        </label>
      </div>
    </div>
  );
}

function OrientationField({ value, onChange }: { value: OrientationPolicy; onChange: (v: OrientationPolicy) => void }) {
  return (
    <div className="wizard-field">
      <label>Default Orientation</label>
      <div className="wizard-radio-row">
        {ORIENTATION_OPTIONS.map((opt) => (
          <label key={opt.value} className="wizard-radio-option">
            <input type="radio" name="orientation" checked={value === opt.value} onChange={() => onChange(opt.value)} />
            {opt.label}
          </label>
        ))}
      </div>
    </div>
  );
}
