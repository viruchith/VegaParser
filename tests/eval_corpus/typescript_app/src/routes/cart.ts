import { CartService } from "../services/cart";

const cart = new CartService();

export function registerCartRoutes(port: number): void {
  console.log(`cart routes on port ${port}`);
  cart.addItem("sku-1", 2);
}
