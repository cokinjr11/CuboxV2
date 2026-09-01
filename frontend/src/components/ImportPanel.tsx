import { useRef, useState } from "react";
import type { ContainerSpec, CustomLoadSpaceRequestBody, LoadingAnchor, OptimizationMode, WeightBalanceMode, WindowItem } from "../types";

const CLEARANCE_OPTIONS = [0, 5, 10, 20];

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
  containers: ContainerSpec[];
  items: WindowItem[];
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
  onPack: () => void;
}

export function ImportPanel({
  containers,
  items,
  selectedContainerId,
  customLoadSpace,
  optimizationMode,
  enableCentralAisle,
  aisleWidthMm,
  clearanceMm,
  weightBalanceMode,
  loadingAnchor,
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
  onPack,
}: Props) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) onFileSelected(file);
  }

  const totalQty = items.reduce((sum, i) => sum + i.quantity, 0);

  return (
    <div className="panel">
      <h2>1. Importar Excel</h2>
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
          {items.length} lineas importadas — {totalQty} piezas totales
        </p>
      )}

      <h2>2. Load Space</h2>
      {customLoadSpace ? (
        <p className="hint">
          {customLoadSpace.name} ({customLoadSpace.load_space_type}) — {customLoadSpace.length} × {customLoadSpace.width} ×{" "}
          {customLoadSpace.height} mm, max {customLoadSpace.max_weight} kg
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
                value={aisleWidthMm}
                onChange={(e) => onAisleWidthChange(Number(e.target.value))}
              />
            </label>
          )}
          <label className="inline-field">
            Minimum Clearance (mm)
            <select value={clearanceMm} onChange={(e) => onClearanceChange(Number(e.target.value))}>
              {CLEARANCE_OPTIONS.map((mm) => (
                <option key={mm} value={mm}>
                  {mm} mm
                </option>
              ))}
            </select>
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
