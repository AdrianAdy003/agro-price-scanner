import pytest
from bs4 import BeautifulSoup
from scrapers.base import BaseShopScraper

MOCK_JSONLD_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Product",
  "name": "Nicogan 40 SC 1L",
  "brand": {"@type": "Brand", "name": "FitoFarm"},
  "sku": "FIT-NIC-1L",
  "gtin13": "5901234567890",
  "offers": {
    "@type": "Offer",
    "price": "89.99",
    "priceCurrency": "RON",
    "availability": "https://schema.org/InStock"
  }
}
</script>
</head><body></body></html>
"""

def test_extract_jsonld_product():
    scraper = BaseShopScraper()
    soup = BeautifulSoup(MOCK_JSONLD_HTML, "html.parser")
    product = scraper._extract_jsonld(soup, "https://example.ro/produs/nicogan")
    assert product is not None
    assert product.name == "Nicogan 40 SC 1L"
    assert product.price == 89.99
    assert product.gtin == "5901234567890"
    assert product.brand == "FitoFarm"
    assert product.in_stock is True

MOCK_MISSING_PRICE_HTML = """
<html><head>
<script type="application/ld+json">
{"@type": "Product", "name": "Produs fara pret"}
</script>
</head><body></body></html>
"""

def test_extract_product_without_price():
    scraper = BaseShopScraper()
    soup = BeautifulSoup(MOCK_MISSING_PRICE_HTML, "html.parser")
    product = scraper._extract_jsonld(soup, "https://example.ro/produs/test")
    assert product is not None
    assert product.price is None
