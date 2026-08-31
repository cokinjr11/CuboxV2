import { colorFromString } from "../utils/color";
import type { ColorByMode, PlacedPiece } from "../types";

interface Props {
  placed: PlacedPiece[];
  colorBy: ColorByMode;
}

const DIMENSION_LABEL: Record<Exclude<ColorByMode, "default">, string> = {
  group: "Group",
  system: "System",
  priority: "Priority",
};

export function ColorLegend({ placed, colorBy }: Props) {
  if (colorBy === "default") return null;

  const keyFn = (p: PlacedPiece) =>
    colorBy === "system" ? p.system : colorBy === "group" ? p.group : String(p.priority);

  const keys = Array.from(new Set(placed.map(keyFn).filter((k) => k !== ""))).sort();
  if (keys.length === 0) return null;

  return (
    <div className="panel">
      <h2>Leyenda ({DIMENSION_LABEL[colorBy]})</h2>
      <div className="legend-list">
        {keys.map((k) => (
          <div className="legend-item" key={k}>
            <span className="legend-swatch" style={{ background: colorFromString(k) }} />
            {k}
          </div>
        ))}
      </div>
    </div>
  );
}
