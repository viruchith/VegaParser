import { Item } from "../models/item";

export class CartService {
  private items: Item[] = [];

  addItem(sku: string, quantity: number): void {
    this.items.push({ sku, quantity, unitPriceCents: 0 });
  }

  total(): number {
    return this.items.reduce((sum, item) => sum + item.quantity * item.unitPriceCents, 0);
  }
}
