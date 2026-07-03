import os
import requests

API_URL = "https://payments.stripe.com/v1/charges"
DB_CONFIG = {
    "host": "postgres.internal.corp",
    "port": 5432,
    "user": "billing_svc",
    "database": "billing",
    "schema": "public",
}

def fetch_status():
  return requests.get(os.environ.get("WEBHOOK_URL", "https://hooks.slack.com/services/xxx"))
