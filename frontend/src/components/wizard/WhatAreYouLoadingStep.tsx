import { OptionCard } from "./OptionCard";
import type { PlanningMode } from "../../wizardTypes";

interface ModeOption {
  mode: PlanningMode;
  title: string;
  description: string;
  comingSoon?: boolean;
}

const OPTIONS: ModeOption[] = [
  { mode: "loose_boxes", title: "Loose Boxes", description: "Individual boxes loaded directly into the load space." },
  { mode: "palletized_load", title: "Palletized Load", description: "Load units that are already built pallets." },
  {
    mode: "build_pallets",
    title: "Build Pallets",
    description: "Group loose boxes into pallets before loading.",
    comingSoon: true,
  },
  { mode: "panels_fragile", title: "Panels & Fragile", description: "Flat panels or glass that must never lie on their face." },
  { mode: "custom_load", title: "Custom Load", description: "Define your own item shape and handling rules." },
];

interface Props {
  value: PlanningMode | null;
  onChange: (mode: PlanningMode) => void;
}

export function WhatAreYouLoadingStep({ value, onChange }: Props) {
  return (
    <div className="wizard-step-body">
      <h2>What are you loading?</h2>
      <p className="step-subtitle">Choose the load type that best describes your items.</p>

      <div className="option-card-grid">
        {OPTIONS.map((option) => (
          <OptionCard
            key={option.mode}
            title={option.title}
            description={option.comingSoon ? undefined : option.description}
            badge={option.comingSoon ? "Coming Soon" : undefined}
            selected={value === option.mode}
            disabled={option.comingSoon}
            onSelect={() => onChange(option.mode)}
          >
            {option.comingSoon && <span className="option-card-desc">{option.description}</span>}
          </OptionCard>
        ))}
      </div>
    </div>
  );
}
