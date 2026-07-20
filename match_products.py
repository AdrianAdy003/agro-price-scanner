"""Matching produse identice între magazine (GTIN + AI Batch)."""
import os
import json
import logging
import re
import time
from collections import defaultdict
import anthropic
from supabase import create_client

logger = logging.getLogger(__name__)

MATCH_SYSTEM_PROMPT = """Ești un expert în produse agricole românești. Sarcina ta: determină dacă două produse din magazine diferite sunt IDENTICE (același produs, aceeași concentrație, același volum/greutate).

Reguli stricte:
- Răspunde DOAR cu JSON valid: {"match": true} sau {"match": false}
- "match": true DOAR dacă ești sigur că sunt același produs fizic
- Diferențele de ambalaj (1L vs 5L) = produse DIFERITE
- Același ingredient activ dar brand diferit = produse DIFERITE dacă nu e același produs relabeled
- Când ai dubii → false"""

def get_gtin_matches(client) -> list[dict]:
    """Pasul 1: match direct pe GTIN."""
    res = client.table("scanner_products") \
        .select("id, gtin, shop, name") \
        .not_.is_("gtin", "null") \
        .execute()

    gtin_groups = defaultdict(list)
    for row in res.data:
        gtin_groups[row["gtin"]].append(row)

    new_matches = []
    for gtin, products in gtin_groups.items():
        shops = {p["shop"] for p in products}
        if len(shops) > 1:
            # Verifică dacă match-ul există deja
            ids = [p["id"] for p in products]
            existing = client.table("scanner_match_members") \
                .select("product_id") \
                .in_("product_id", ids).execute()
            existing_ids = {r["product_id"] for r in existing.data}
            if not any(i in existing_ids for i in ids):
                new_matches.append({"products": products, "method": "gtin"})
    return new_matches

def get_ai_candidates(client) -> list[tuple]:
    """Pasul 2: candidați cu ≥2 cuvinte semnificative comune între magazine diferite."""
    res = client.table("scanner_products") \
        .select("id, shop, name, brand") \
        .execute()

    STOP_WORDS = {"de", "si", "cu", "la", "un", "o", "al", "ale", "din", "pe", "pentru", "kg", "l", "ml", "g"}

    def significant_words(text: str) -> set:
        words = set(re.sub(r"[^a-z0-9]", " ", text.lower()).split())
        return words - STOP_WORDS - {w for w in words if len(w) < 3}

    # Index per cuvânt
    word_index = defaultdict(list)
    for row in res.data:
        words = significant_words(row["name"])
        for word in words:
            word_index[word].append(row)

    candidates = set()
    for word, rows in word_index.items():
        if len(rows) < 2:
            continue
        for i in range(len(rows)):
            for j in range(i+1, len(rows)):
                a, b = rows[i], rows[j]
                if a["shop"] == b["shop"]:
                    continue
                words_a = significant_words(a["name"])
                words_b = significant_words(b["name"])
                common = words_a & words_b
                if len(common) >= 2:
                    key = tuple(sorted([a["id"], b["id"]]))
                    candidates.add((key[0], key[1], a["name"], b["name"]))

    # Filtrează deja procesate
    existing_members = client.table("scanner_match_members").select("product_id").execute()
    already_matched = {r["product_id"] for r in existing_members.data}

    return [(a, b, na, nb) for a, b, na, nb in candidates
            if a not in already_matched and b not in already_matched]

def run_ai_matching(candidates: list[tuple]) -> list[dict]:
    """Trimite candidații la Claude Haiku via Batch API."""
    if not candidates:
        return []

    api_key = os.environ["ANTHROPIC_API_KEY"]
    client = anthropic.Anthropic(api_key=api_key)

    requests = []
    for i, (id_a, id_b, name_a, name_b) in enumerate(candidates[:500]):  # max 500/batch
        requests.append({
            "custom_id": f"{id_a}|{id_b}",
            "params": {
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 20,
                "system": [
                    {
                        "type": "text",
                        "text": MATCH_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"}
                    }
                ],
                "messages": [{"role": "user", "content": f'Produs A: "{name_a}"\nProdus B: "{name_b}"'}]
            }
        })

    logger.info("Trimit batch de matching: %d perechi", len(requests))
    batch = client.messages.batches.create(requests=requests)

    # Polling pentru rezultate (max 24h, verificare la 60s)
    while True:
        status = client.messages.batches.retrieve(batch.id)
        if status.processing_status == "ended":
            break
        logger.info("Batch %s în procesare... (%s/%s)",
                   batch.id, status.request_counts.processing, len(requests))
        time.sleep(60)

    matches = []
    for result in client.messages.batches.results(batch.id):
        if result.result.type != "succeeded":
            continue
        try:
            content = result.result.message.content[0].text
            data = json.loads(content)
            if data.get("match"):
                id_a, id_b = result.custom_id.split("|")
                matches.append({"id_a": id_a, "id_b": id_b, "method": "ai"})
        except Exception:
            continue

    return matches

def save_matches(supabase_client, matches: list[dict]) -> int:
    """Salvează match-urile în DB."""
    saved = 0
    for m in matches:
        try:
            res = supabase_client.table("scanner_matches").insert({"method": m["method"]}).execute()
            match_id = res.data[0]["id"]
            members = [
                {"match_id": match_id, "product_id": m.get("id_a") or p["id"]}
                for p in m.get("products", [])
            ] if "products" in m else [
                {"match_id": match_id, "product_id": m["id_a"]},
                {"match_id": match_id, "product_id": m["id_b"]},
            ]
            supabase_client.table("scanner_match_members").insert(members).execute()
            saved += 1
        except Exception as e:
            logger.error("save_matches eroare: %s", e)
    return saved

def run_matching():
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    client = create_client(url, key)

    # Pasul 1: GTIN
    gtin_matches = get_gtin_matches(client)
    gtin_saved = save_matches(client, gtin_matches)
    logger.info("Match-uri GTIN noi: %d", gtin_saved)

    # Pasul 2: AI
    candidates = get_ai_candidates(client)
    logger.info("Candidați AI: %d perechi de verificat", len(candidates))
    if candidates:
        ai_matches = run_ai_matching(candidates)
        ai_saved = save_matches(client, ai_matches)
        logger.info("Match-uri AI noi: %d din %d candidați", ai_saved, len(candidates))

    return {"gtin": gtin_saved, "ai": len(candidates)}

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_matching()
