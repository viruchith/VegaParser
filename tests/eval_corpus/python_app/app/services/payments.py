"""Client for the external payment provider."""

import urllib.request

from app.config import load_settings

PAYMENT_API_URL = "https://payments.example.com/v1/charge"


class PaymentClient:
    """Sends charge requests to the payment provider."""

    def __init__(self) -> None:
        self.settings = load_settings()

    def charge(self, order_id: str, amount_cents: int) -> bool:
        """Charge the customer for an order."""
        payload = f"{order_id}:{amount_cents}".encode("utf-8")
        request = urllib.request.Request(PAYMENT_API_URL, data=payload, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status == 200
