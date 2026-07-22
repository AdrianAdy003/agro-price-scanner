"""Îmbogățire AI: extrage substanța activă din numele produsului (o singură dată per produs)."""
import os
import json
import logging
import time
import anthropic
from supabase import create_client
from collections import Counter

logger = logging.getLogger(__name__)

ENRICH_SYSTEM_PROMPT = """Ești expert în produse fitosanitare, semințe și îngrășăminte pentru agricultura română.
Din numele comercial al unui produs agricol, extrage informațiile structurate.

Răspunde EXCLUSIV cu JSON valid (fără markdown):
{"product_type": "erbicid|fungicid|insecticid|ingrasamant|samanta|altul", "active_substance": "substanta_activa_lowercase_fara_diacritice", "concentration": "40 g/l", "target_crops": ["porumb", "grau"]}

Reguli:
- active_substance: lowercase, fără diacritice, fără număr de înregistrare
- Dacă nu poți determina cu certitudine un câmp → null (NU ghici)
- Produse non-fitosanitare (utilaje, echipamente) → product_type: "altul", restul null
- target_crops: culturile principale menționate în nume, array gol [] dacă nespecificat"""

def run_enrichment():
    supabase_url = os.environ["SUPABASE_URL"]
    supabase_key = os.environ["SUPABASE_SERVICE_KEY"]
    api_key = os.environ["ANTHROPIC_API_KEY"]

    db = create_client(supabase_url, supabase_key)
    ai = anthropic.Anthropic(api_key=api_key)

    # Selectează produse neîmbogățite (filtru Python — evită sintaxa PostgREST is.null)
    res = db.table("scanner_products") \
        .select("id, name, brand, enriched_at") \
        .limit(2000) \
        .execute()
    res_data = [r for r in res.data if not r.get("enriched_at")]

    products = res_data
    if not products:
        logger.info("Nicio îmbogățire necesară.")
        return {"enriched": 0}

    logger.info("De îmbogățit: %d produse", len(products))

    # Batch-uri de 20
    all_results = {}
    for i in range(0, len(products), 20):
        batch_products = products[i:i+20]
        requests = []
        for p in batch_products:
            text = p["name"]
            if p.get("brand"):
                text = f"{p['brand']} {text}"
            requests.append({
                "custom_id": p["id"],
                "params": {
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 150,
                    "system": [{"type": "text", "text": ENRICH_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                    "messages": [{"role": "user", "content": text}]
                }
            })

        batch = ai.messages.batches.create(requests=requests)
        logger.info("Batch enrichment %d/%d trimis: %s", i//20+1, (len(products)+19)//20, batch.id)

        # Polling
        while True:
            status = ai.messages.batches.retrieve(batch.id)
            if status.processing_status == "ended":
                break
            time.sleep(60)

        for result in ai.messages.batches.results(batch.id):
            if result.result.type != "succeeded":
                continue
            try:
                data = json.loads(result.result.message.content[0].text)
                all_results[result.custom_id] = data
            except Exception:
                all_results[result.custom_id] = {}

    # Scrie rezultatele în DB
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    enriched_count = 0

    for product_id, data in all_results.items():
        try:
            db.table("scanner_products").update({
                "product_type": data.get("product_type"),
                "active_substance": data.get("active_substance"),
                "concentration": data.get("concentration"),
                "target_crops": data.get("target_crops") or [],
                "enriched_at": now,
            }).eq("id", product_id).execute()
            enriched_count += 1
        except Exception as e:
            logger.error("Eroare update produs %s: %s", product_id, e)

    # Raport top substanțe active
    substances = [r.get("active_substance") for r in all_results.values() if r.get("active_substance")]
    top = Counter(substances).most_common(10)
    logger.info("Top 10 substanțe active: %s", top)
    logger.info("Îmbogățite: %d produse", enriched_count)

    return {"enriched": enriched_count, "top_substances": top}

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_enrichment()
