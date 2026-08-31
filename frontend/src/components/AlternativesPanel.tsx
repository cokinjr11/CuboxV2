import type { AlternativeSolution } from "../types";

interface Props {
  alternatives: AlternativeSolution[];
  onSelect: (alt: AlternativeSolution) => void;
}

export function AlternativesPanel({ alternatives, onSelect }: Props) {
  if (alternatives.length <= 1) return null;

  return (
    <div className="panel">
      <h2>Alternative Solutions</h2>
      <div className="alt-cards">
        {alternatives.map((alt) => (
          <div className="alt-card" key={alt.strategy}>
            <div className="alt-card-title">{alt.strategy}</div>
            <div className="alt-card-stats">
              {alt.result.metrics.loaded_pieces}/{alt.result.metrics.total_pieces} cargadas · {alt.result.metrics.used_volume_pct}% volumen · score {alt.score.toFixed(1)}
            </div>
            {Object.keys(alt.breakdown).length > 0 && (
              <div className="alt-card-breakdown">
                {Object.entries(alt.breakdown).map(([key, pct]) => (
                  <span key={key} className="breakdown-chip">
                    {key} {pct.toFixed(0)}%
                  </span>
                ))}
              </div>
            )}
            <button className="btn-small" onClick={() => onSelect(alt)}>
              Ver esta solucion
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
