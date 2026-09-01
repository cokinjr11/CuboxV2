export interface WizardStepDef {
  key: string;
  label: string;
}

interface Props {
  steps: WizardStepDef[];
  currentIndex: number;
}

export function WizardStepper({ steps, currentIndex }: Props) {
  return (
    <nav className="wizard-stepper" aria-label="Load plan steps">
      {steps.map((step, index) => (
        <div key={step.key} style={{ display: "contents" }}>
          <div
            className={`wizard-step${index === currentIndex ? " active" : ""}${index < currentIndex ? " done" : ""}`}
            aria-current={index === currentIndex ? "step" : undefined}
          >
            <span className="wizard-step-index">{index < currentIndex ? "✓" : index + 1}</span>
            <span>{step.label}</span>
          </div>
          {index < steps.length - 1 && <span className="wizard-step-sep" aria-hidden="true" />}
        </div>
      ))}
    </nav>
  );
}
