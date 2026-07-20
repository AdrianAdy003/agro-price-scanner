import pytest
import re
from collections import defaultdict

STOP_WORDS = {"de", "si", "cu", "la", "un", "o", "al", "ale", "din", "pe", "pentru", "kg", "l", "ml", "g"}

def significant_words(text: str) -> set:
    words = set(re.sub(r"[^a-z0-9]", " ", text.lower()).split())
    return words - STOP_WORDS - {w for w in words if len(w) < 3}

def test_significant_words():
    words = significant_words("Nicogan 40 SC 1L")
    assert "nicogan" in words
    assert "l" not in words  # filtrat (lungime < 3 sau stop word)

def test_matching_candidates_same_shop():
    # Produse cu ≥2 cuvinte semnificative (≥3 caractere) în comun
    products = [
        {"id": "1", "shop": "fitomag", "name": "Roundup Turbo erbicid 1L"},
        {"id": "2", "shop": "fitomag", "name": "Roundup Turbo erbicid 5L"},
        {"id": "3", "shop": "verdon", "name": "Roundup Turbo erbicid 1L"},
    ]
    word_index = defaultdict(list)
    for p in products:
        for w in significant_words(p["name"]):
            word_index[w].append(p)

    candidates = set()
    for word, rows in word_index.items():
        for i in range(len(rows)):
            for j in range(i+1, len(rows)):
                a, b = rows[i], rows[j]
                if a["shop"] == b["shop"]:
                    continue
                wa, wb = significant_words(a["name"]), significant_words(b["name"])
                if len(wa & wb) >= 2:
                    candidates.add(tuple(sorted([a["id"], b["id"]])))

    assert ("1", "3") in candidates  # cross-shop match
    assert ("1", "2") not in candidates  # same shop, exclus
