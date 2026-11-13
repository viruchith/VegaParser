"""Tests for order totals."""

from app.models import Customer, Order
from app.services.orders import calculate_total


def test_calculate_total_sums_amounts():
    order = Order(order_id="o1", customer=Customer("c1", "Ada"), amounts_cents=[100, 250])
    assert calculate_total(order) == 350
