"""Upsert în Supabase pentru AgroMind Price Scanner."""
import os
import logging
from datetime import datetime, timezone
from typing import Optional
from supabase import create_client, Client
from scrapers.base import Product

logger = logging.getLogger(__name__)

def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)

def upsert_product(client: Client, product: Product) -> Optional[str]:
    """Upsert produs pe URL. Returnează product_id."""
    now = datetime.now(timezone.utc).isoformat()
    data = {
        "shop": product.shop,
        "url": product.url,
        "name": product.name,
        "brand": product.brand,
        "sku": product.sku,
        "gtin": product.gtin,
        "image": product.image,
        "last_seen": now,
    }
    try:
        res = client.table("scanner_products").upsert(
            data, on_conflict="url"
        ).execute()
        return res.data[0]["id"] if res.data else None
    except Exception as e:
        logger.error("upsert_product eșuat pentru %s: %s", product.url, e)
        return None

def record_price(client: Client, product_id: str, product: Product) -> bool:
    """Inserează preț nou DOAR dacă s-a schimbat față de ultima înregistrare."""
    try:
        last = client.table("scanner_prices") \
            .select("price, in_stock") \
            .eq("product_id", product_id) \
            .order("scanned_at", desc=True) \
            .limit(1).execute()

        if last.data:
            prev_price = float(last.data[0]["price"])
            prev_stock = last.data[0]["in_stock"]
            if abs(prev_price - (product.price or 0)) < 0.01 and prev_stock == product.in_stock:
                # actualizează doar last_seen
                client.table("scanner_products") \
                    .update({"last_seen": datetime.now(timezone.utc).isoformat()}) \
                    .eq("id", product_id).execute()
                return False  # fără rând nou

        client.table("scanner_prices").insert({
            "product_id": product_id,
            "price": product.price,
            "in_stock": product.in_stock,
        }).execute()
        return True
    except Exception as e:
        logger.error("record_price eșuat pentru %s: %s", product_id, e)
        return False

def save_products(products: list[Product]) -> dict:
    """Salvează o listă de produse. Returnează statistici."""
    client = get_client()
    stats = {"upserted": 0, "price_changes": 0, "errors": 0}
    for product in products:
        if product.price is None:
            continue
        product_id = upsert_product(client, product)
        if not product_id:
            stats["errors"] += 1
            continue
        stats["upserted"] += 1
        if record_price(client, product_id, product):
            stats["price_changes"] += 1
    return stats
