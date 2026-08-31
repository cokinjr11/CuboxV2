import type { PackingResult } from "../types";

type SequenceMode = "load" | "unload";

interface Props {
  result: PackingResult;
  show: boolean;
  onToggleShow: () => void;
  mode: SequenceMode;
  onModeChange: (mode: SequenceMode) => void;
  stepIndex: number | null;
  onStepChange: (index: number | null) => void;
}

export function SequencePanel({ result, show, onToggleShow, mode, onModeChange, stepIndex, onStepChange }: Props) {
  const sequence = mode === "load" ? result.load_sequence : result.unload_sequence;
  const piecesById = new Map(result.placed.map((p) => [p.id, p]));

  function handlePrevious() {
    onStepChange(stepIndex === null ? 0 : Math.max(0, stepIndex - 1));
  }

  function handleNext() {
    onStepChange(stepIndex === null ? 0 : Math.min(sequence.length - 1, stepIndex + 1));
  }

  return (
    <div className="panel">
      <h2>Secuencia</h2>
      <label className="checkbox-row">
        <input type="checkbox" checked={show} onChange={onToggleShow} />
        Show Load Sequence
      </label>
      <div className="radio-group radio-row">
        <label>
          <input type="radio" checked={mode === "load"} onChange={() => onModeChange("load")} />
          Load Order
        </label>
        <label>
          <input type="radio" checked={mode === "unload"} onChange={() => onModeChange("unload")} />
          Unload Order
        </label>
      </div>

      {show && (
        <>
          <div className="undo-redo-row">
            <button className="btn" onClick={handlePrevious} disabled={stepIndex !== null && stepIndex <= 0}>
              Previous
            </button>
            <button
              className="btn"
              onClick={handleNext}
              disabled={stepIndex !== null && stepIndex >= sequence.length - 1}
            >
              Next
            </button>
          </div>
          {stepIndex !== null && (
            <p className="hint">
              Paso {stepIndex + 1} / {sequence.length}
            </p>
          )}
        </>
      )}

      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Order</th>
              <th>Code</th>
              <th>Description</th>
              <th>Group</th>
              <th>System</th>
            </tr>
          </thead>
          <tbody>
            {sequence.map((id, i) => {
              const p = piecesById.get(id);
              if (!p) return null;
              return (
                <tr key={id} className={stepIndex === i ? "current-step" : undefined}>
                  <td>{i + 1}</td>
                  <td>{p.code}</td>
                  <td>{p.description}</td>
                  <td>{p.group || "-"}</td>
                  <td>{p.system || "-"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
