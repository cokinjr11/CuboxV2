import { useRef, useState } from "react";
import {
  TILT_MAX_ANGLE_DEG,
  type ContainerSpec,
  type CustomLoadSpaceRequestBody,
  type LoadingAnchor,
  type OptimizationMode,
  type OrientationPolicy,
  type WeightBalanceMode,
  type WindowItem,
} from "../types";
import { itemTypeNoun } from "../utils/itemTypeLabels";

const WEIGHT_BALANCE_OPTIONS: { value: WeightBalanceMode; label: string }[] = [
  { value: "ignore", label: "Ignore" },
  { value: "normal", label: "Normal" },
  { value: "important", label: "Important" },
];

// Fase 5B: default de plan para Orientation -PANEL_EDGE_ONLY nunca se
// ofrece aca (es una regla de seguridad fija de Panels & Fragile, no una
// preferencia de plan; ver core/handling_rules.py, seccion 14 del pedido).
const ORIENTATION_DEFAULT_OPTIONS: { value: OrientationPolicy; label: string }[] = [
  { value: "free", label: "Free Rotation" },
  { value: "upright", label: "Keep Upright" },
  { value: "fixed", label: "Fixed" },
];

const LOADING_ANCHOR_OPTIONS: { value: LoadingAnchor; label: string }[] = [
  { value: "back_right", label: "Back Right" },
  { value: "back_left", label: "Back Left" },
];

interface Props {
  containers: ContainerSpec[];
  items: WindowItem[];
  /** Fase 5 bugfix: cuando el workspace se abrio desde el wizard, los items
   * ya se importaron en el paso Import del wizard -esta seccion pasa a ser
   * solo informativa, sin boton de importar. */
  importedViaWizard?: boolean;
  selectedContainerId: string;
  /** Fase 5: cuando esta presente (Truck/Trailer/Custom Container definido
   * en el wizard), reemplaza el dropdown de contenedores por un valor de
   * solo lectura -es la unica fuente de verdad del Load Space activo. */
  customLoadSpace?: CustomLoadSpaceRequestBody;
  optimizationMode: OptimizationMode;
  enableCentralAisle: boolean;
  aisleWidthMm: number;
  clearanceMm: number;
  weightBalanceMode: WeightBalanceMode;
  loadingAnchor: LoadingAnchor;
  /** Fase 5B: defaults del PLAN para Handling Rules -editables aca para que
   * Scenario F (cambiar el default despues del import) sea posible sin
   * volver al wizard, ademas de reflejarse en cada pack/optimize-remaining. */
  defaultStackable: boolean;
  orientationPolicy: OrientationPolicy;
  /** Fase 5C: mismo criterio que defaultStackable/orientationPolicy -solo
   * tiene efecto real (y solo se muestra) cuando el plan es Panels & Fragile. */
  defaultAllowTilt: boolean;
  defaultMaxTiltAngle: number | null;
  loading: boolean;
  error: string;
  onFileSelected: (file: File) => void;
  onContainerChange: (id: string) => void;
  onOptimizationModeChange: (mode: OptimizationMode) => void;
  onEnableCentralAisleChange: (enabled: boolean) => void;
  onAisleWidthChange: (mm: number) => void;
  onClearanceChange: (mm: number) => void;
  onWeightBalanceModeChange: (mode: WeightBalanceMode) => void;
  onLoadingAnchorChange: (anchor: LoadingAnchor) => void;
  onDefaultStackableChange: (stackable: boolean) => void;
  onOrientationPolicyChange: (policy: OrientationPolicy) => void;
  onDefaultAllowTiltChange: (allow: boolean) => void;
  onDefaultMaxTiltAngleChange: (angle: number | null) => void;
  onPack: () => void;
}

export function ImportPanel({
  containers,
  items,
  importedViaWizard,
  selectedContainerId,
  customLoadSpace,
  optimizationMode,
  enableCentralAisle,
  aisleWidthMm,
  clearanceMm,
  weightBalanceMode,
  loadingAnchor,
  defaultStackable,
  orientationPolicy,
  defaultAllowTilt,
  defaultMaxTiltAngle,
  loading,
  error,
  onFileSelected,
  onContainerChange,
  onOptimizationModeChange,
  onEnableCentralAisleChange,
  onAisleWidthChange,
  onClearanceChange,
  onWeightBalanceModeChange,
  onLoadingAnchorChange,
  onDefaultStackableChange,
  onOrientationPolicyChange,
  onDefaultAllowTiltChange,
  onDefaultMaxTiltAngleChange,
  onPack,
}: Props) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) onFileSelected(file);
  }

  const totalQty = items.reduce((sum, i) => sum + i.quantity, 0);
  const unitsNoun = items[0]?.item_type === "pallet" ? itemTypeNoun("pallet", true).toLowerCase() : "piezas";

  return (
    <div className="panel">
      <h2>1. Importar Excel</h2>
      {importedViaWizard ? (
        <p className="hint">
          {items.length} lineas importadas — {totalQty} {unitsNoun} totales
          <br />
          Imported via the New Load Plan Wizard.
        </p>
      ) : (
        <>
          <button className="btn" onClick={() => fileInputRef.current?.click()}>
            Seleccionar archivo .xlsx
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".xlsx,.xlsm"
            style={{ display: "none" }}
            onChange={handleFileChange}
          />
          {items.length > 0 && (
            <p className="hint">
              {items.length} lineas importadas — {totalQty} {unitsNoun} totales
            </p>
          )}
        </>
      )}

      <h2>2. Load Space</h2>
      {customLoadSpace ? (
        <p className="hint">
          {customLoadSpace.name} <span className="badge-custom">Custom</span> ({customLoadSpace.load_space_type}) —{" "}
          {customLoadSpace.length} × {customLoadSpace.width} × {customLoadSpace.height} mm, max {customLoadSpace.max_weight} kg
          <br />
          Defined in the New Load Plan Wizard.
        </p>
      ) : (
        <select value={selectedContainerId} onChange={(e) => onContainerChange(e.target.value)}>
          {containers.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      )}

      <h2>3. Optimization Mode</h2>
      <div className="radio-group">
        <label>
          <input
            type="radio"
            checked={optimizationMode === "best_space"}
            onChange={() => onOptimizationModeChange("best_space")}
          />
          Best Space Utilization
        </label>
        <label>
          <input
            type="radio"
            checked={optimizationMode === "keep_groups"}
            onChange={() => onOptimizationModeChange("keep_groups")}
          />
          Keep Groups Together
        </label>
        <label>
          <input
            type="radio"
            checked={optimizationMode === "keep_systems"}
            onChange={() => onOptimizationModeChange("keep_systems")}
          />
          Keep Systems Together
        </label>
      </div>

      <button className="btn-link" onClick={() => setAdvancedOpen((v) => !v)}>
        {advancedOpen ? "▾" : "▸"} Advanced
      </button>
      {advancedOpen && (
        <div className="advanced-box">
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={defaultStackable}
              onChange={(e) => onDefaultStackableChange(e.target.checked)}
            />
            Default Stackable
          </label>
          <label className="inline-field">
            Default Orientation
            <select value={orientationPolicy} onChange={(e) => onOrientationPolicyChange(e.target.value as OrientationPolicy)}>
              {ORIENTATION_DEFAULT_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
          {items[0]?.item_type === "panel" && (
            <>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={defaultAllowTilt}
                  onChange={(e) => onDefaultAllowTiltChange(e.target.checked)}
                />
                Allow Tilt
              </label>
              {defaultAllowTilt && (
                <label className="inline-field">
                  Maximum Tilt Angle (°)
                  <input
                    type="number"
                    min={0}
                    max={TILT_MAX_ANGLE_DEG}
                    value={defaultMaxTiltAngle || ""}
                    onChange={(e) => {
                      const raw = Number(e.target.value);
                      onDefaultMaxTiltAngleChange(Number.isFinite(raw) ? Math.min(TILT_MAX_ANGLE_DEG, Math.max(0, raw)) : null);
                    }}
                  />
                </label>
              )}
            </>
          )}
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={enableCentralAisle}
              onChange={(e) => onEnableCentralAisleChange(e.target.checked)}
            />
            Enable Central Aisle
          </label>
          {enableCentralAisle && (
            <label className="inline-field">
              Aisle Width (mm)
              <input
                type="number"
                min={0}
                value={aisleWidthMm || ""}
                onChange={(e) => onAisleWidthChange(Number(e.target.value))}
              />
            </label>
          )}
          <label className="inline-field">
            Minimum Clearance (mm)
            <input
              type="number"
              min={0}
              value={clearanceMm || ""}
              onChange={(e) => onClearanceChange(Number(e.target.value))}
            />
          </label>
          <label className="inline-field">
            Weight Balance
            <select
              value={weightBalanceMode}
              onChange={(e) => onWeightBalanceModeChange(e.target.value as WeightBalanceMode)}
            >
              {WEIGHT_BALANCE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
          <label className="inline-field">
            Loading Start
            <select value={loadingAnchor} onChange={(e) => onLoadingAnchorChange(e.target.value as LoadingAnchor)}>
              {LOADING_ANCHOR_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      <button className="btn btn-primary" disabled={items.length === 0 || loading} onClick={onPack}>
        {loading ? "Calculando..." : "Optimize"}
      </button>

      {error && <p className="error">{error}</p>}
    </div>
  );
}
