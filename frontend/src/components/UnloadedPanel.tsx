import type { ItemType, UnloadedItem } from "../types";
import { formatDimensions } from "../utils/dimensions";
import { itemTypeNoun } from "../utils/itemTypeLabels";
import { loadPriorityLabel } from "../utils/loadPriority";

interface Props {
  items: UnloadedItem[];
  insertingItemId: string | null;
  activeItemType?: ItemType;
  onStartPlacing: (item: UnloadedItem) => void;
  onCancelPlacing: () => void;
}

export function UnloadedPanel({ items, insertingItemId, activeItemType, onStartPlacing, onCancelPlacing }: Props) {
  const isPalletPlan = activeItemType === "pallet";
  const heading = isPalletPlan ? `Unloaded ${itemTypeNoun("pallet", true)}` : "Unloaded Items";
  return (
    <div className="panel">
      <h2>{heading} ({items.length})</h2>
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
                <th>Load Priority</th>
                {isPalletPlan && <th>Boxes Inside</th>}
                <th>Reason</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.id}>
                  <td>{it.code}</td>
                  <td>{it.description}</td>
                  <td>{formatDimensions(it.item_type, it.dimensions, it.width, it.height, it.thickness)}</td>
                  <td>{it.weight} kg</td>
                  <td>{it.group || "-"}</td>
                  <td>{it.system || "-"}</td>
                  <td>{loadPriorityLabel(it.priority)}</td>
                  {isPalletPlan && <td>{it.boxes_inside ?? "-"}</td>}
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
