import type { ColorByMode } from "../types";

interface Props {
  value: ColorByMode;
  onChange: (mode: ColorByMode) => void;
}

const OPTIONS: { value: ColorByMode; label: string }[] = [
  { value: "default", label: "Default" },
  { value: "group", label: "Group" },
  { value: "system", label: "System" },
  { value: "priority", label: "Priority" },
];

export function ColorByControl({ value, onChange }: Props) {
  return (
    <label className="inline-field color-by">
      Color By
      <select value={value} onChange={(e) => onChange(e.target.value as ColorByMode)}>
        {OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}
