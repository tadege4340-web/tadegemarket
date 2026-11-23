
import requests
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

API_URL = "https://api.frankfurter.dev/v2/rates"

CURRENCIES = {
    "ETB": {"name": "Ethiopian Birr", "symbol": "Br"},
    "USD": {"name": "US Dollar", "symbol": "$"},
    "EUR": {"name": "Euro", "symbol": "€"},
    "GBP": {"name": "British Pound", "symbol": "£"},
    "KES": {"name": "Kenyan Shilling", "symbol": "KSh"},
}

DEFAULT_CURRENCY = "ETB"


def get_exchange_rates():
    """Get ETB exchange rates from the online API."""
    try:
        response = requests.get(
            API_URL,
            params={
                "base": "ETB",
                "quotes": "USD,EUR,GBP,KES"
            },
            timeout=10
        )

        response.raise_for_status()
        rows = response.json()

        rates = {"ETB": Decimal("1")}

        for row in rows:
            rates[row["quote"]] = Decimal(str(row["rate"]))

        return {
            "rates": rates,
            "date": rows[0]["date"] if rows else None
        }

    except (requests.RequestException, ValueError, KeyError):
        return None


def convert_price(amount, currency, rates):
    """Convert an ETB price into the selected currency."""
    try:
        amount = Decimal(str(amount))
        rate = rates.get(currency)

        if rate is None:
            return amount

        return amount * rate

    except (InvalidOperation, TypeError):
        return Decimal("0.00")


def format_price(amount, currency):
    """Format a price with the selected currency symbol."""
    symbol = CURRENCIES.get(
        currency,
        CURRENCIES[DEFAULT_CURRENCY]
    )["symbol"]

    return f"{symbol} {amount:,.2f}"


def get_rate_update_date():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
