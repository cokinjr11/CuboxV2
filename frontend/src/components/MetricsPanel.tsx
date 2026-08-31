import type { PackingMetrics } from "../types";

export function MetricsPanel({ metrics }: { metrics: PackingMetrics }) {
  return (
    <div className="panel">
      <h2>Resultados</h2>
      <div className="metrics-grid">
        <Stat label="Total piezas" value={metrics.total_pieces} />
        <Stat label="Cargadas" value={metrics.loaded_pieces} />
        <Stat label="No cargadas" value={metrics.unloaded_pieces} />
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
