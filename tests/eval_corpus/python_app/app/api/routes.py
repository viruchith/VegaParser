"""HTTP routes for the orders API."""

from app.models import Customer
from app.services.orders import OrderService

service = OrderService()


def list_orders() -> list[str]:
    """Return the ids of all known orders."""
    return list(service._orders)


def create_order_route(order_id: str, customer_name: str) -> str:
    """Create an order from request parameters."""
    customer = Customer(customer_id="anonymous", name=customer_name)
    return service.create_order(order_id, customer, []).order_id
