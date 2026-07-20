"""Clasa de bază pentru scrapere de magazine agricole."""
import time
import logging
import re
import json
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from dataclasses import dataclass, field
from typing import Optional
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

@dataclass
class Product:
    url: str
    name: str
    price: Optional[float]
    currency: str = "RON"
    in_stock: bool = True
    brand: Optional[str] = None
    sku: Optional[str] = None
    gtin: Optional[str] = None
    image: Optional[str] = None
    shop: str = ""

class BaseShopScraper:
    SHOP_NAME: str = ""
    BASE_URL: str = ""
    PRODUCT_URL_PATTERN: str = ""  # regex pentru URL-uri de produs
    RATE_LIMIT_SECONDS: float = 2.0
    USER_AGENT = "AgroMindBot/1.0 (+https://agromind.ro/bot)"

    def __init__(self):
        self._last_request: float = 0.0
        self._robot_parser: Optional[RobotFileParser] = None
        self.client = httpx.Client(
            headers={"User-Agent": self.USER_AGENT},
            follow_redirects=True,
            timeout=30.0,
        )

    def _wait(self):
        elapsed = time.time() - self._last_request
        if elapsed < self.RATE_LIMIT_SECONDS:
            time.sleep(self.RATE_LIMIT_SECONDS - elapsed)
        self._last_request = time.time()

    def _can_fetch(self, url: str) -> bool:
        """Verifică robots.txt."""
        if self._robot_parser is None:
            rp = RobotFileParser()
            robots_url = urljoin(self.BASE_URL, "/robots.txt")
            try:
                self._wait()
                resp = self.client.get(robots_url)
                rp.parse(resp.text.splitlines())
            except Exception:
                rp.parse([])
            self._robot_parser = rp
        return self._robot_parser.can_fetch(self.USER_AGENT, url)

    def get(self, url: str) -> Optional[httpx.Response]:
        if not self._can_fetch(url):
            logger.warning("robots.txt interzice: %s", url)
            return None
        self._wait()
        try:
            resp = self.client.get(url)
            resp.raise_for_status()
            return resp
        except Exception as e:
            logger.error("GET %s eșuat: %s", url, e)
            return None

    def discover_product_urls(self, limit: Optional[int] = None) -> list[str]:
        """Descoperă URL-uri de produse via sitemap XML."""
        sitemap_urls = self._find_sitemaps()
        product_urls = []
        for sitemap_url in sitemap_urls:
            urls = self._parse_sitemap(sitemap_url)
            product_urls.extend(urls)
            if limit and len(product_urls) >= limit:
                break
        product_urls = list(dict.fromkeys(product_urls))  # dedup
        if limit:
            product_urls = product_urls[:limit]
        if not product_urls:
            logger.warning("[%s] 0 produse găsite. Primele URL-uri din sitemap pentru diagnosticare.", self.SHOP_NAME)
        return product_urls

    def _find_sitemaps(self) -> list[str]:
        """Găsește sitemap-uri din robots.txt și locații standard."""
        sitemaps = []
        robots_url = urljoin(self.BASE_URL, "/robots.txt")
        try:
            resp = self.client.get(robots_url)
            for line in resp.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    sitemaps.append(line.split(":", 1)[1].strip())
        except Exception:
            pass
        for fallback in ["/sitemap.xml", "/sitemap_index.xml", "/sitemap-products.xml"]:
            url = urljoin(self.BASE_URL, fallback)
            if url not in sitemaps:
                sitemaps.append(url)
        return sitemaps

    def _parse_sitemap(self, sitemap_url: str) -> list[str]:
        """Parsează un sitemap XML și returnează URL-urile de produse."""
        resp = self.get(sitemap_url)
        if not resp:
            return []
        try:
            soup = BeautifulSoup(resp.content, "xml")
        except Exception:
            soup = BeautifulSoup(resp.text, "lxml-xml")

        # Sitemap index → recurse
        sitemaps = soup.find_all("sitemap")
        if sitemaps:
            all_urls = []
            for s in sitemaps:
                loc = s.find("loc")
                if loc:
                    all_urls.extend(self._parse_sitemap(loc.text.strip()))
            return all_urls

        # Sitemap simplu
        urls = []
        for loc in soup.find_all("loc"):
            url = loc.text.strip()
            if self.PRODUCT_URL_PATTERN and re.search(self.PRODUCT_URL_PATTERN, url):
                urls.append(url)
            elif not self.PRODUCT_URL_PATTERN:
                urls.append(url)
        return urls

    def extract_product(self, url: str) -> Optional[Product]:
        """Extrage datele unui produs dintr-o pagină."""
        resp = self.get(url)
        if not resp:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        product = self._extract_jsonld(soup, url)
        if not product:
            product = self._extract_meta_og(soup, url)
        if product:
            product.shop = self.SHOP_NAME
        return product

    def _extract_jsonld(self, soup: BeautifulSoup, url: str) -> Optional[Product]:
        """Extrage din JSON-LD schema.org/Product."""
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, list):
                    for item in data:
                        p = self._parse_jsonld_product(item, url)
                        if p:
                            return p
                else:
                    p = self._parse_jsonld_product(data, url)
                    if p:
                        return p
            except (json.JSONDecodeError, AttributeError):
                continue
        return None

    def _parse_jsonld_product(self, data: dict, url: str) -> Optional[Product]:
        types = data.get("@type", "")
        if isinstance(types, list):
            types = " ".join(types)
        if "Product" not in types:
            # check @graph
            for node in data.get("@graph", []):
                p = self._parse_jsonld_product(node, url)
                if p:
                    return p
            return None

        name = data.get("name", "").strip()
        if not name:
            return None

        price = None
        currency = "RON"
        in_stock = True

        offers = data.get("offers") or data.get("Offers")
        if offers:
            if isinstance(offers, list):
                offers = offers[0]
            price_raw = offers.get("price") or offers.get("lowPrice")
            if price_raw is not None:
                price = normalize_price(str(price_raw))
            currency = offers.get("priceCurrency", "RON")
            avail = offers.get("availability", "")
            in_stock = "OutOfStock" not in avail and "Discontinued" not in avail

        gtin = (data.get("gtin13") or data.get("gtin8") or
                data.get("gtin14") or data.get("gtin") or
                data.get("mpn"))

        image = data.get("image")
        if isinstance(image, list):
            image = image[0]
        if isinstance(image, dict):
            image = image.get("url")

        return Product(
            url=url,
            name=name,
            price=price,
            currency=currency,
            in_stock=in_stock,
            brand=data.get("brand", {}).get("name") if isinstance(data.get("brand"), dict) else data.get("brand"),
            sku=data.get("sku"),
            gtin=str(gtin).strip() if gtin else None,
            image=image,
        )

    def _extract_meta_og(self, soup: BeautifulSoup, url: str) -> Optional[Product]:
        """Fallback: extrage din meta OG."""
        name = (self._meta(soup, "og:title") or self._meta(soup, "title") or
                (soup.title.string if soup.title else None))
        price_raw = self._meta(soup, "og:price:amount") or self._meta(soup, "product:price:amount")
        if not name:
            return None
        price = normalize_price(price_raw) if price_raw else None
        image = self._meta(soup, "og:image")
        return Product(url=url, name=name.strip(), price=price, image=image)

    @staticmethod
    def _meta(soup: BeautifulSoup, prop: str) -> Optional[str]:
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        return tag.get("content") if tag else None

    def scan(self, limit: Optional[int] = None) -> list[Product]:
        """Scanează catalogul complet al magazinului."""
        logger.info("[%s] Descoperire produse...", self.SHOP_NAME)
        urls = self.discover_product_urls(limit=limit)
        logger.info("[%s] %d URL-uri găsite", self.SHOP_NAME, len(urls))
        products = []
        for i, url in enumerate(urls, 1):
            try:
                product = self.extract_product(url)
                if product and product.price is not None:
                    products.append(product)
                elif product:
                    logger.debug("[%s] Fără preț public: %s", self.SHOP_NAME, url)
            except Exception as e:
                logger.error("[%s] Eroare la %s: %s", self.SHOP_NAME, url, e)
            if i % 50 == 0:
                logger.info("[%s] Progres: %d/%d", self.SHOP_NAME, i, len(urls))
        logger.info("[%s] Finalizat: %d produse cu preț din %d URL-uri", self.SHOP_NAME, len(products), len(urls))
        return products


def normalize_price(raw: str) -> Optional[float]:
    """Normalizează formatul românesc de preț: '1.234,56' → 1234.56"""
    if not raw:
        return None
    raw = raw.strip().replace("RON", "").replace("lei", "").replace("€", "").strip()
    # Format românesc: punct ca separator mii, virgulă ca zecimal
    if re.search(r'\d\.\d{3},', raw):
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw and "." in raw:
        # ambii separatori — verifică ordinea
        if raw.index(",") > raw.index("."):
            raw = raw.replace(",", "X").replace(".", "").replace("X", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")
    raw = re.sub(r"[^\d.]", "", raw)
    try:
        return float(raw)
    except ValueError:
        return None
