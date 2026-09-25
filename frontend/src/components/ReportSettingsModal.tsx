import { useState } from "react";
import type { SortReportBy, StepMode } from "../types";

export type ReportType = "container" | "loading_guide" | "unloading_guide";
export type UnloadingGuideScope = "full" | "by_group";

const PIECES_PER_STEP_OPTIONS = [1, 2, 3, 5, 10] as const;

export interface ReportSettings {
  reportType: ReportType;
  sortBy: SortReportBy;
  stepMode: StepMode;
  piecesPerStep: number;
  includeOverviewImage: boolean;
  projectName: string;
  customer: string;
  unloadingGuideScope: UnloadingGuideScope;
  selectedGroups: string[];
}

interface Props {
  onClose: () => void;
  onGenerate: (settings: ReportSettings) => Promise<void>;
  generating: boolean;
  availableGroups: string[];
}

export function ReportSettingsModal({ onClose, onGenerate, generating, availableGroups }: Props) {
  const [reportType, setReportType] = useState<ReportType>("container");
  const [sortBy, setSortBy] = useState<SortReportBy>("group");
  const [stepMode, setStepMode] = useState<StepMode>("automatic");
  const [piecesPerStep, setPiecesPerStep] = useState(3);
  const [customPiecesPerStep, setCustomPiecesPerStep] = useState(3);
  const [useCustomPiecesPerStep, setUseCustomPiecesPerStep] = useState(false);
  const [includeOverviewImage, setIncludeOverviewImage] = useState(true);
  const [projectName, setProjectName] = useState("");
  const [customer, setCustomer] = useState("");
  const [unloadingGuideScope, setUnloadingGuideScope] = useState<UnloadingGuideScope>("full");
  const [selectedGroups, setSelectedGroups] = useState<string[]>([]);

  const isGuide = reportType === "loading_guide" || reportType === "unloading_guide";
  const isUnloadingByGroup = reportType === "unloading_guide" && unloadingGuideScope === "by_group";

  function toggleGroup(group: string) {
    setSelectedGroups((prev) => (prev.includes(group) ? prev.filter((g) => g !== group) : [...prev, group]));
  }

  async function handleGenerate() {
    await onGenerate({
      reportType,
      sortBy,
      stepMode,
      piecesPerStep: useCustomPiecesPerStep ? customPiecesPerStep : piecesPerStep,
      includeOverviewImage,
      projectName,
      customer,
      unloadingGuideScope,
      selectedGroups,
    });
  }

  const generateDisabled = generating || (isUnloadingByGroup && selectedGroups.length === 0);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Export / Report Settings</h2>
          <button className="btn modal-close-btn" onClick={onClose}>
            Cerrar
          </button>
        </div>

        <div className="settings-section">
          <h3>Report Type</h3>
          <div className="radio-group">
            <label>
              <input
                type="radio"
                checked={reportType === "container"}
                onChange={() => setReportType("container")}
              />
              Container Load Report
            </label>
            <label>
              <input
                type="radio"
                checked={reportType === "loading_guide"}
                onChange={() => setReportType("loading_guide")}
              />
              Loading Guide
            </label>
            <label>
              <input
                type="radio"
                checked={reportType === "unloading_guide"}
                onChange={() => setReportType("unloading_guide")}
              />
              Unloading Guide
            </label>
          </div>
        </div>

        {reportType === "container" && (
          <div className="settings-section">
            <h3>Sort Report By</h3>
            <div className="radio-group radio-row">
              <label>
                <input type="radio" checked={sortBy === "group"} onChange={() => setSortBy("group")} />
                Group
              </label>
              <label>
                <input type="radio" checked={sortBy === "system"} onChange={() => setSortBy("system")} />
                System
              </label>
            </div>
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={includeOverviewImage}
                onChange={(e) => setIncludeOverviewImage(e.target.checked)}
              />
              Include Overview Image
            </label>
          </div>
        )}

        {reportType === "unloading_guide" && (
          <div className="settings-section">
            <h3>Unloading Guide Scope</h3>
            <div className="radio-group">
              <label>
                <input
                  type="radio"
                  checked={unloadingGuideScope === "full"}
                  onChange={() => setUnloadingGuideScope("full")}
                />
                Full Unloading Guide (one PDF, whole plan)
              </label>
              <label>
                <input
                  type="radio"
                  checked={unloadingGuideScope === "by_group"}
                  onChange={() => setUnloadingGuideScope("by_group")}
                />
                By Group (one PDF per selected Group)
              </label>
            </div>
            {unloadingGuideScope === "by_group" && (
              <div className="checkbox-group">
                {availableGroups.length === 0 ? (
                  <p className="hint">No hay Groups definidos en este plan.</p>
                ) : (
                  availableGroups.map((group) => (
                    <label className="checkbox-row" key={group}>
                      <input
                        type="checkbox"
                        checked={selectedGroups.includes(group)}
                        onChange={() => toggleGroup(group)}
                      />
                      {group}
                    </label>
                  ))
                )}
              </div>
            )}
          </div>
        )}

        {isGuide && (
          <div className="settings-section">
            <h3>Step Mode</h3>
            <div className="radio-group radio-row">
              <label>
                <input type="radio" checked={stepMode === "automatic"} onChange={() => setStepMode("automatic")} />
                Automatic
              </label>
              <label>
                <input type="radio" checked={stepMode === "manual"} onChange={() => setStepMode("manual")} />
                Manual
              </label>
            </div>
            {stepMode === "manual" && (
              <label className="inline-field">
                Pieces per Step
                <select
                  value={useCustomPiecesPerStep ? "custom" : piecesPerStep}
                  onChange={(e) => {
                    if (e.target.value === "custom") {
                      setUseCustomPiecesPerStep(true);
                    } else {
                      setUseCustomPiecesPerStep(false);
                      setPiecesPerStep(Number(e.target.value));
                    }
                  }}
                >
                  {PIECES_PER_STEP_OPTIONS.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                  <option value="custom">Custom</option>
                </select>
              </label>
            )}
            {stepMode === "manual" && useCustomPiecesPerStep && (
              <label className="inline-field">
                Custom value
                <input
                  type="number"
                  min={1}
                  value={customPiecesPerStep}
                  onChange={(e) => setCustomPiecesPerStep(Math.max(1, Number(e.target.value)))}
                />
              </label>
            )}
          </div>
        )}

        <div className="settings-section">
          <h3>Project Info</h3>
          <label className="inline-field">
            Project Name
            <input type="text" value={projectName} onChange={(e) => setProjectName(e.target.value)} />
          </label>
          <label className="inline-field">
            Customer
            <input type="text" value={customer} onChange={(e) => setCustomer(e.target.value)} />
          </label>
        </div>

        <button className="btn btn-primary" onClick={handleGenerate} disabled={generateDisabled}>
          {generating ? "Generando PDF..." : "Generate PDF"}
        </button>
      </div>
    </div>
  );
}
