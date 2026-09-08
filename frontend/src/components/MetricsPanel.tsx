import type { ItemType, PackingMetrics } from "../types";
import { itemTypeNoun } from "../utils/itemTypeLabels";

interface Props {
  metrics: PackingMetrics;
  /** Fase 6.1: cuando el plan activo es Palletized Load, los contadores
   * genericos (piezas/cargadas/no cargadas) se relabelean con vocabulario de
   * pallet y se agrega Pallet Utilization -reusando los MISMOS 3 numeros
   * que ya devuelve el packer para cualquier ItemType (nada nuevo calculado
   * en el backend, ver seccion 5-6 del pedido). */
  activeItemType?: ItemType;
}

export function MetricsPanel({ metrics, activeItemType }: Props) {
  const isPallet = activeItemType === "pallet";
  const utilizationPct = metrics.total_pieces > 0 ? Math.round((metrics.loaded_pieces / metrics.total_pieces) * 100) : 100;

  return (
    <div className="panel">
      <h2>Resultados</h2>
      <div className="metrics-grid">
        <Stat label={isPallet ? `Total ${itemTypeNoun("pallet", true)}` : "Total piezas"} value={metrics.total_pieces} />
        <Stat label={isPallet ? `${itemTypeNoun("pallet", true)} Loaded` : "Cargadas"} value={metrics.loaded_pieces} />
        <Stat label={isPallet ? `${itemTypeNoun("pallet", true)} Unloaded` : "No cargadas"} value={metrics.unloaded_pieces} />
        {isPallet && (
          <Stat label="Pallet Utilization" value={`${metrics.loaded_pieces} / ${metrics.total_pieces} (${utilizationPct}%)`} />
        )}
        <Stat label="Volumen usado" value={`${metrics.used_volume_pct}%`} />
        <Stat label="Uso de piso" value={`${metrics.floor_utilization_pct}%`} />
        <Stat label="Peso total" value={`${metrics.total_weight} kg`} />
        <Stat label="Peso maximo" value={`${metrics.max_payload} kg`} />
        <Stat label="Uso de peso" value={`${metrics.weight_utilization_pct}%`} />
        <Stat label="Groups" value={metrics.number_of_groups} />
        <Stat label="Systems" value={metrics.number_of_systems} />
        <Stat label="Balance de peso" value={`${metrics.weight_balance_pct}%`} />
      </div>
      <h3>Distribucion de peso</h3>
      <div className="metrics-grid">
        <Stat label="Izquierda" value={`${metrics.left_weight_kg} kg (${metrics.left_weight_pct}%)`} />
        <Stat label="Derecha" value={`${metrics.right_weight_kg} kg (${metrics.right_weight_pct}%)`} />
        <Stat label="Adelante (puerta)" value={`${metrics.front_weight_kg} kg (${metrics.front_weight_pct}%)`} />
        <Stat label="Atras (fondo)" value={`${metrics.back_weight_kg} kg (${metrics.back_weight_pct}%)`} />
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}
