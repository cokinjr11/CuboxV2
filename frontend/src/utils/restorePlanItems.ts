import type { PackingResult, PlacedPiece, UnloadedItem, WindowItem } from "../types";

// Fase 5D: al reabrir un plan guardado, App.tsx no recibe un ImportPreview
// -solo el PackingResult ya colocado. Pero `items` (el estado de App.tsx)
// sigue siendo lo que Re-optimize (packContainer desde cero, sin piezas
// Locked) reenvia al backend; si quedara vacio, Re-optimize en un plan
// reabierto vaciaria el plan entero en vez de recalcularlo. Estas 2
// funciones reconstruyen WindowItem[] a partir de placed/unloaded -mismo
// criterio de "congelar" el override YA resuelto que ya usa el backend
// (api/routes.py:_window_item_from_placed/_window_item_from_unloaded) para
// Optimize Remaining, aplicado aca del lado del frontend para Re-optimize.

function placedPieceToWindowItem(p: PlacedPiece): WindowItem {
  return {
    code: p.code,
    description: p.description,
    width: p.source_width,
    height: p.source_height,
    thickness: p.source_thickness,
    weight: p.weight,
    quantity: 1,
    system: p.system,
    group: p.group,
    stackable: p.stackable,
    stackable_override: p.stackable,
    priority: p.priority,
    max_stack_weight: p.max_stack_weight,
    delivery_sequence: p.delivery_sequence,
    boxes_inside: p.boxes_inside,
    item_type: p.item_type,
    orientation_policy: p.orientation_policy,
    orientation_override: p.orientation_policy,
    // Tilt permanece deshabilitado en la UI (Fase 5D, seccion 53) -nunca se
    // reintroduce aca aunque el plan guardado tuviera piezas inclinadas de
    // antes de la desactivacion.
  };
}

function unloadedItemToWindowItem(u: UnloadedItem): WindowItem {
  return {
    code: u.code,
    description: u.description,
    width: u.width,
    height: u.height,
    thickness: u.thickness,
    weight: u.weight,
    quantity: 1,
    system: u.system,
    group: u.group,
    stackable: u.stackable,
    stackable_override: u.stackable,
    priority: u.priority,
    max_stack_weight: u.max_stack_weight,
    delivery_sequence: u.delivery_sequence,
    boxes_inside: u.boxes_inside,
    item_type: u.item_type,
    orientation_policy: u.orientation_policy,
    orientation_override: u.orientation_policy,
  };
}

export function windowItemsFromRestoredResult(result: PackingResult): WindowItem[] {
  return [...result.placed.map(placedPieceToWindowItem), ...result.unloaded.map(unloadedItemToWindowItem)];
}
