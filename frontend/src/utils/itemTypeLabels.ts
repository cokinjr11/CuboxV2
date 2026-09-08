import type { ItemType } from "../types";

// item_type=undefined es el dato legacy (ventanas/paneles de antes de que
// este campo existiera) -mismo criterio ya usado en utils/dimensions.ts.
const NOUNS: Record<ItemType, { singular: string; plural: string }> = {
  box: { singular: "Box", plural: "Boxes" },
  pallet: { singular: "Pallet", plural: "Pallets" },
  panel: { singular: "Panel", plural: "Panels" },
  custom: { singular: "Load Unit", plural: "Load Units" },
};

export function itemTypeNoun(itemType: ItemType | undefined, plural = false): string {
  const entry = NOUNS[itemType ?? "panel"];
  return plural ? entry.plural : entry.singular;
}
