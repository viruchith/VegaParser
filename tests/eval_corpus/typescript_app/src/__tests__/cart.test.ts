import { CartService } from "../services/cart";

test("total is zero for an empty cart", () => {
  expect(new CartService().total()).toBe(0);
});
