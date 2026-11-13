const STOCK_API = "https://inventory.example.com/api/stock";

export class InventoryClient {
  async stockLevel(sku: string): Promise<number> {
    const response = await fetch(`${STOCK_API}/${sku}`);
    const body = (await response.json()) as { level: number };
    return body.level;
  }
}
