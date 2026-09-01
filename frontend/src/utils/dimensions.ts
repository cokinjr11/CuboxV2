import type { Dimensions3D, ItemType } from "../types";

// CUBOX 2.0 Fase 5: para PANEL (legacy/window), width/height/thickness
// siguen siendo el vocabulario correcto para el usuario. Para BOX/PALLET/
// CUSTOM, esos mismos numeros son una remapeo interno sin significado
// fisico (ver backend app/models/schemas.py:legacy_from_dimensions) -hay
// que mostrar las Dimensions3D canonicas (length/width/height) en su lugar.
// item_type=undefined (dato legacy sin el campo) se trata como panel, que
// es el comportamiento de siempre.
function isPanel(itemType: ItemType | undefined): boolean {
  return itemType === undefined || itemType === "panel";
}

export function dimensionsLabel(itemType: ItemType | undefined): string {
  return isPanel(itemType) ? "Dimensions (W x H x T)" : "Dimensions (L x W x H)";
}

export function formatDimensions(
  itemType: ItemType | undefined,
  canonical: Dimensions3D | undefined,
  legacyWidth: number,
  legacyHeight: number,
  legacyThickness: number
): string {
  if (!isPanel(itemType) && canonical) {
    return `${canonical.length} x ${canonical.width} x ${canonical.height}`;
  }
  return `${legacyWidth} x ${legacyHeight} x ${legacyThickness}`;
}
