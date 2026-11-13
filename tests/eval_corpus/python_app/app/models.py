"""Domain models for orders."""

from dataclasses import dataclass, field


@dataclass
class Customer:
    """A customer who places orders."""

    customer_id: str
    name: str


@dataclass
class Order:
    """A customer order with line amounts in cents."""

    order_id: str
    customer: Customer
    amounts_cents: list[int] = field(default_factory=list)
