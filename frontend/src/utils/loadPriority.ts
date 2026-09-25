// Load Organization Model Cleanup: espejo de backend/app/core/load_priority.py
// (load_priority_label) -solo presentacion, el valor interno sigue siendo el
// mismo entero 1..5 de siempre.
export function loadPriorityLabel(value: number): string {
  if (value >= 1 && value <= 2) return "High";
  if (value >= 4 && value <= 5) return "Low";
  return "Normal";
}
