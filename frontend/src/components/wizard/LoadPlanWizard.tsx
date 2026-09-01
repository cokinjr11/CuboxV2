import { useEffect, useState } from "react";
import { fetchLoadSpaces } from "../../api/client";
import cuboxLogo from "../../assets/cubox-logo.png";
import type { LoadSpaceSpec } from "../../types";
import { PLANNING_MODE_ITEM_TYPE, createEmptyLoadPlanDraft, defaultHandlingRulesFor, type LoadPlanDraft } from "../../wizardTypes";
import { HandlingRulesStep } from "./HandlingRulesStep";
import { ImportStep } from "./ImportStep";
import { isLoadSpaceDraftValid, LoadSpaceStep } from "./LoadSpaceStep";
import { ReviewStep } from "./ReviewStep";
import { WhatAreYouLoadingStep } from "./WhatAreYouLoadingStep";
import { WizardStepper, type WizardStepDef } from "./WizardStepper";

const STEPS: WizardStepDef[] = [
  { key: "load", label: "Load" },
  { key: "load_space", label: "Load Space" },
  { key: "rules", label: "Rules" },
  { key: "import", label: "Import" },
  { key: "review", label: "Review" },
];

interface Props {
  onCancel: () => void;
  onComplete: (draft: LoadPlanDraft) => void;
}

export function LoadPlanWizard({ onCancel, onComplete }: Props) {
  const [stepIndex, setStepIndex] = useState(0);
  const [draft, setDraft] = useState<LoadPlanDraft>(createEmptyLoadPlanDraft);
  const [catalog, setCatalog] = useState<LoadSpaceSpec[]>([]);
  const [catalogError, setCatalogError] = useState("");

  useEffect(() => {
    fetchLoadSpaces()
      .then(setCatalog)
      .catch(() => setCatalogError("Could not load the Load Space catalog from the backend."));
  }, []);

  const itemType = draft.planningMode ? PLANNING_MODE_ITEM_TYPE[draft.planningMode] : undefined;

  function patchDraft(patch: Partial<LoadPlanDraft>) {
    setDraft((prev) => ({ ...prev, ...patch }));
  }

  function selectPlanningMode(mode: LoadPlanDraft["planningMode"]) {
    if (!mode) return;
    patchDraft({
      planningMode: mode,
      handlingRules: defaultHandlingRulesFor(mode),
      importPreview: null,
    });
  }

  const canContinue = (() => {
    switch (STEPS[stepIndex].key) {
      case "load":
        return draft.planningMode !== null && draft.planningMode !== "build_pallets";
      case "load_space":
        return isLoadSpaceDraftValid(draft.loadSpace);
      case "rules":
        return draft.handlingRules !== null;
      case "import":
        return draft.importPreview !== null && draft.importPreview.is_valid;
      default:
        return true;
    }
  })();

  function goBack() {
    if (stepIndex === 0) {
      onCancel();
      return;
    }
    setStepIndex((i) => i - 1);
  }

  function goContinue() {
    if (!canContinue) return;
    if (stepIndex === STEPS.length - 1) {
      onComplete(draft);
      return;
    }
    setStepIndex((i) => i + 1);
  }

  return (
    <div className="wizard-shell">
      <header className="wizard-header">
        <img src={cuboxLogo} alt="CUBOX" />
        <span className="wizard-title">New Load Plan</span>
      </header>

      <WizardStepper steps={STEPS} currentIndex={stepIndex} />

      <div className="wizard-content">
        {STEPS[stepIndex].key === "load" && (
          <WhatAreYouLoadingStep value={draft.planningMode} onChange={selectPlanningMode} />
        )}
        {STEPS[stepIndex].key === "load_space" && (
          <LoadSpaceStep
            value={draft.loadSpace}
            onChange={(loadSpace) => patchDraft({ loadSpace })}
            catalog={catalog}
            catalogError={catalogError}
          />
        )}
        {STEPS[stepIndex].key === "rules" && draft.planningMode && draft.handlingRules && (
          <HandlingRulesStep
            mode={draft.planningMode}
            value={draft.handlingRules}
            onChange={(patch) => patchDraft({ handlingRules: { ...draft.handlingRules!, ...patch } })}
          />
        )}
        {STEPS[stepIndex].key === "import" && itemType && (
          <ImportStep
            profile={itemType}
            value={draft.importPreview}
            onChange={(importPreview) => patchDraft({ importPreview })}
            defaults={
              draft.handlingRules
                ? { orientationPolicy: draft.handlingRules.orientationPolicy, stackable: draft.handlingRules.defaultStackable }
                : undefined
            }
          />
        )}
        {STEPS[stepIndex].key === "review" && <ReviewStep draft={draft} catalog={catalog} />}
      </div>

      <footer className="wizard-footer">
        <button type="button" className="btn-secondary" onClick={goBack}>
          Back
        </button>
        <button type="button" className="btn-primary" onClick={goContinue} disabled={!canContinue}>
          {stepIndex === STEPS.length - 1 ? "Create Load Plan" : "Continue"}
        </button>
      </footer>
    </div>
  );
}
