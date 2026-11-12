"""Application entry point."""

from app.api.routes import create_order_route
from app.config import load_settings


def main() -> None:
    """Start the orders service."""
    settings = load_settings()
    print(f"starting {settings.service_name}")
    create_order_route("demo", "guest")


if __name__ == "__main__":
    main()
