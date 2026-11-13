"""Order creation and totals."""

from app.models import Customer, Order


def calculate_total(order: Order) -> int:
    """Return the order total in cents."""
    return sum(order.amounts_cents)


class OrderService:
    """Creates and cancels orders."""

    def __init__(self) -> None:
        self._orders: dict[str, Order] = {}

    def create_order(self, order_id: str, customer: Customer, amounts_cents: list[int]) -> Order:
        """Store a new order and return it."""
        order = Order(order_id=order_id, customer=customer, amounts_cents=list(amounts_cents))
        self._orders[order_id] = order
        return order

    def cancel_order(self, order_id: str) -> bool:
        """Remove an order; return True if it existed."""
        return self._orders.pop(order_id, None) is not None
