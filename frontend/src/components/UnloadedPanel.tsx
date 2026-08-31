import type { UnloadedItem } from "../types";

interface Props {
  items: UnloadedItem[];
  insertingItemId: string | null;
  onStartPlacing: (item: UnloadedItem) => void;
  onCancelPlacing: () => void;
}

export function UnloadedPanel({ items, insertingItemId, onStartPlacing, onCancelPlacing }: Props) {
  return (
    <div className="panel">
      <h2>Unloaded Items ({items.length})</h2>
      {items.length === 0 ? (
        <p className="hint">Todas las piezas fueron cargadas.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Code</th>
                <th>Description</th>
                <th>Dimensions (mm)</th>
                <th>Weight</th>
                <th>Group</th>
                <th>System</th>
                <th>Priority</th>
                <th>Reason</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.id}>
                  <td>{it.code}</td>
                  <td>{it.description}</td>
                  <td>
                    {it.width} x {it.height} x {it.thickness}
                  </td>
                  <td>{it.weight} kg</td>
                  <td>{it.group || "-"}</td>
                  <td>{it.system || "-"}</td>
                  <td>{it.priority}</td>
                  <td className="reason">{it.reason}</td>
                  <td>
                    {insertingItemId === it.id ? (
                      <button className="btn-small" onClick={onCancelPlacing}>
                        Cancelar
                      </button>
                    ) : (
                      <button className="btn-small" onClick={() => onStartPlacing(it)}>
                        Colocar manualmente
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {insertingItemId && (
        <p className="hint">Arrastra la pieza resaltada en la vista 3D hasta un lugar valido y sueltala.</p>
      )}
    </div>
  );
}
