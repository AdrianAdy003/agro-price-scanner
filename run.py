"""CLI principal pentru AgroMind Price Scanner."""
import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from scrapers.shops import ALL_SCRAPERS

# Forțează UTF-8 pe stdout/stderr indiferent de setările locale Windows (cp1250)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1)
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr = open(sys.stderr.fileno(), mode="w", encoding="utf-8", buffering=1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("run")

def scan_shop(shop_name: str, limit: int | None, save_db: bool) -> dict:
    """Scanează un singur magazin. Returnează statistici."""
    scraper_class = ALL_SCRAPERS.get(shop_name)
    if not scraper_class:
        logger.error("Magazin necunoscut: %s", shop_name)
        return {"shop": shop_name, "error": "necunoscut"}

    try:
        scraper = scraper_class()
        products = scraper.scan(limit=limit)
    except Exception as e:
        logger.error("[%s] Eroare fatală la scanare: %s", shop_name, e)
        return {"shop": shop_name, "error": str(e), "products": 0}

    stats = {
        "shop": shop_name,
        "products": len(products),
        "with_gtin": sum(1 for p in products if p.gtin),
        "with_price": sum(1 for p in products if p.price is not None),
        "error": None,
    }

    # Salvare locală JSON (debug/fallback)
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_file = data_dir / f"{shop_name}_{timestamp}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump([vars(p) for p in products], f, ensure_ascii=False, indent=2)
    logger.info("[%s] Salvat local: %s", shop_name, out_file)

    # Upsert Supabase
    if save_db and products:
        try:
            from db import save_products
            db_stats = save_products(products)
            stats.update(db_stats)
            logger.info("[%s] DB: %s", shop_name, db_stats)
        except Exception as e:
            logger.error("[%s] Eroare DB: %s", shop_name, e)
            stats["db_error"] = str(e)

    return stats

def main():
    parser = argparse.ArgumentParser(description="AgroMind Price Scanner")
    parser.add_argument("--shop", default="all", help="Magazin (sau 'all')")
    parser.add_argument("--limit", type=int, default=None, help="Limită produse per magazin")
    parser.add_argument("--no-db", action="store_true", help="Nu scrie în Supabase")
    args = parser.parse_args()

    save_db = not args.no_db
    if save_db and not os.getenv("SUPABASE_URL"):
        logger.warning("SUPABASE_URL lipsește — rulare fără DB (--no-db implicit)")
        save_db = False

    shops = list(ALL_SCRAPERS.keys()) if args.shop == "all" else [args.shop]

    logger.info("=== AgroMind Price Scanner ===")
    logger.info("Magazine: %s | Limit: %s | DB: %s", shops, args.limit, save_db)

    all_stats = []
    for shop in shops:
        logger.info("--- Scanare: %s ---", shop)
        stats = scan_shop(shop, limit=args.limit, save_db=save_db)
        all_stats.append(stats)

    # Raport final
    print("\n" + "="*50)
    print("RAPORT FINAL")
    print("="*50)
    total_products = 0
    for s in all_stats:
        status = "OK" if not s.get("error") else "ERR"
        print(f"[{status}] {s['shop']:15} {s.get('products', 0):6} produse  "
              f"(GTIN: {s.get('with_gtin', 0)})  "
              f"{'EROARE: ' + s['error'] if s.get('error') else ''}")
        total_products += s.get("products", 0)
    print(f"\nTotal: {total_products} produse")
    print("="*50)

if __name__ == "__main__":
    main()
