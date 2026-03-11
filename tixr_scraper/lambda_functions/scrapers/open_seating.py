from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List


def scrape_open_seating(raw_event_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse GA/open-seating ticket categories from /api/events/{id} JSON.

    Returns a list of ticket category dicts suitable for InventoryResult.ticket_categories.
    """

    sales = raw_event_data.get("sales") or []
    categories: List[Dict[str, Any]] = []

    for sale in sales:
        if not isinstance(sale, dict):
            continue
        if sale.get("state") not in {"OPEN", "QUEUED"}:
            continue

        price_levels = sale.get("priceLevels") or []
        for pl in price_levels:
            if not isinstance(pl, dict):
                continue

            name = pl.get("name") or pl.get("label") or ""
            amount = pl.get("price") or pl.get("amount") or 0
            fee = pl.get("serviceFee") or pl.get("fee") or 0

            try:
                price = float(Decimal(str(amount)))
            except Exception:  # noqa: BLE001
                price = 0.0

            try:
                service_fee = float(Decimal(str(fee)))
            except Exception:  # noqa: BLE001
                service_fee = 0.0

            categories.append(
                {
                    "category_name": name,
                    "category_id": pl.get("id"),
                    "price": price,
                    "service_fee": service_fee,
                    "current_stock": pl.get("remainingQuantity"),
                    "is_out_of_stock": pl.get("remainingQuantity") in (0, "0"),
                }
            )

    return categories

