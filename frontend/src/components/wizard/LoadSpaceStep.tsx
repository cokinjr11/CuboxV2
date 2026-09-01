import type { LoadSpaceSpec } from "../../types";
import type { CustomLoadSpaceDraft, LoadSpaceCategory, LoadSpaceDraft } from "../../wizardTypes";
import { OptionCard } from "./OptionCard";

const CATEGORIES: { category: LoadSpaceCategory; title: string; description: string }[] = [
  { category: "container", title: "Container", description: "Maritime container from the CUBOX catalog." },
  { category: "truck", title: "Truck", description: "Enter your truck's internal dimensions." },
  { category: "trailer", title: "Trailer", description: "Enter your trailer's internal dimensions." },
  { category: "custom", title: "Custom Space", description: "Any other load space with known dimensions." },
];

const EMPTY_CUSTOM: CustomLoadSpaceDraft = { name: "", loadSpaceType: "truck", length: 0, width: 0, height: 0, maxWeight: 0 };

interface Props {
  value: LoadSpaceDraft | null;
  onChange: (draft: LoadSpaceDraft) => void;
  catalog: LoadSpaceSpec[];
  catalogError: string;
}

export function LoadSpaceStep({ value, onChange, catalog, catalogError }: Props) {
  const category = value?.category ?? null;
  const containers = catalog.filter((c) => (c.load_space_type ?? "container") === "container");

  function selectCategory(next: LoadSpaceCategory) {
    if (next === "container") {
      onChange({ category: next, containerId: undefined, custom: undefined });
    } else {
      onChange({ category: next, containerId: undefined, custom: { ...EMPTY_CUSTOM, loadSpaceType: next } });
    }
  }

  function selectContainer(id: string) {
    onChange({ category: "container", containerId: id, custom: undefined });
  }

  function selectCustomContainer() {
    onChange({ category: "container", containerId: undefined, custom: { ...EMPTY_CUSTOM, loadSpaceType: "container" } });
  }

  function updateCustom(patch: Partial<CustomLoadSpaceDraft>) {
    if (!value) return;
    const base = value.custom ?? { ...EMPTY_CUSTOM, loadSpaceType: category === "container" ? "container" : (category ?? "truck") };
    onChange({ ...value, custom: { ...base, ...patch } });
  }

  const showCustomForm = value?.custom !== undefined;

  return (
    <div className="wizard-step-body">
      <h2>Where are you loading it?</h2>
      <p className="step-subtitle">Choose the load space that will receive your items.</p>

      {catalogError && <div className="wizard-error-banner">{catalogError}</div>}

      <div className="option-card-grid" style={{ marginBottom: 24 }}>
        {CATEGORIES.map((c) => (
          <OptionCard
            key={c.category}
            title={c.title}
            description={c.description}
            selected={category === c.category}
            onSelect={() => selectCategory(c.category)}
          />
        ))}
      </div>

      {category === "container" && (
        <>
          <div className="option-card-grid" style={{ marginBottom: 16 }}>
            {containers.map((c) => (
              <OptionCard
                key={c.id}
                title={c.name}
                description={`${c.length} × ${c.width} × ${c.height} mm — max ${c.max_weight} kg`}
                selected={value?.containerId === c.id}
                onSelect={() => selectContainer(c.id)}
              />
            ))}
            <OptionCard
              title="Custom Container"
              description="Define your own container dimensions."
              selected={showCustomForm}
              onSelect={selectCustomContainer}
            />
          </div>
        </>
      )}

      {showCustomForm && value?.custom && (
        <CustomLoadSpaceForm value={value.custom} onChange={updateCustom} />
      )}
    </div>
  );
}

function CustomLoadSpaceForm({ value, onChange }: { value: CustomLoadSpaceDraft; onChange: (patch: Partial<CustomLoadSpaceDraft>) => void }) {
  return (
    <div>
      <div className="wizard-field">
        <label htmlFor="ls-name">Name</label>
        <input id="ls-name" type="text" value={value.name} onChange={(e) => onChange({ name: e.target.value })} placeholder="e.g. Warehouse Truck 01" />
      </div>
      <div className="wizard-field-row">
        <div className="wizard-field">
          <label htmlFor="ls-length">Internal Length (mm)</label>
          <input id="ls-length" type="number" min={0} value={value.length || ""} onChange={(e) => onChange({ length: Number(e.target.value) })} />
        </div>
        <div className="wizard-field">
          <label htmlFor="ls-width">Internal Width (mm)</label>
          <input id="ls-width" type="number" min={0} value={value.width || ""} onChange={(e) => onChange({ width: Number(e.target.value) })} />
        </div>
        <div className="wizard-field">
          <label htmlFor="ls-height">Internal Height (mm)</label>
          <input id="ls-height" type="number" min={0} value={value.height || ""} onChange={(e) => onChange({ height: Number(e.target.value) })} />
        </div>
        <div className="wizard-field">
          <label htmlFor="ls-maxweight">Max Payload (kg)</label>
          <input id="ls-maxweight" type="number" min={0} value={value.maxWeight || ""} onChange={(e) => onChange({ maxWeight: Number(e.target.value) })} />
        </div>
      </div>
    </div>
  );
}

export function isLoadSpaceDraftValid(draft: LoadSpaceDraft | null): boolean {
  if (!draft) return false;
  if (draft.category === "container" && draft.containerId) return true;
  if (draft.custom) {
    const c = draft.custom;
    return c.name.trim() !== "" && c.length > 0 && c.width > 0 && c.height > 0 && c.maxWeight > 0;
  }
  return false;
}
