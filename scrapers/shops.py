"""Adaptoare per magazin pentru AgroMind Price Scanner."""
import logging
import re
from typing import Optional
from bs4 import BeautifulSoup
from .base import BaseShopScraper, Product, normalize_price

logger = logging.getLogger(__name__)


class VerdonScraper(BaseShopScraper):
    """verdon.ro — PrestaShop"""
    SHOP_NAME = "verdon"
    BASE_URL = "https://www.verdon.ro"
    PRODUCT_URL_PATTERN = r"/[a-z0-9-]+-p\d+\.html|/produs/"

    def _extract_css_fallback(self, soup: BeautifulSoup, url: str) -> Optional[Product]:
        # PrestaShop specific selectors
        name_el = soup.select_one("h1.product-name, h1[itemprop='name']")
        price_el = soup.select_one(".current-price span[itemprop='price'], .price[data-product-price]")
        if not name_el:
            return None
        price = None
        if price_el:
            price = normalize_price(price_el.get("content") or price_el.text)
        return Product(url=url, name=name_el.text.strip(), price=price)

    def extract_product(self, url: str) -> Optional[Product]:
        resp = self.get(url)
        if not resp:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        product = self._extract_jsonld(soup, url) or self._extract_css_fallback(soup, url)
        if product:
            product.shop = self.SHOP_NAME
        return product


class DiaplantScraper(BaseShopScraper):
    """diaplant.ro — WooCommerce"""
    SHOP_NAME = "diaplant"
    BASE_URL = "https://www.diaplant.ro"
    PRODUCT_URL_PATTERN = r"/produs[a-z]*/|/product/"

    def _extract_css_fallback(self, soup: BeautifulSoup, url: str) -> Optional[Product]:
        name_el = soup.select_one("h1.product_title, h1.entry-title")
        price_el = soup.select_one(".price ins .amount, .price .woocommerce-Price-amount")
        if not name_el:
            return None
        price = normalize_price(price_el.text) if price_el else None
        return Product(url=url, name=name_el.text.strip(), price=price)

    def extract_product(self, url: str) -> Optional[Product]:
        resp = self.get(url)
        if not resp:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        product = self._extract_jsonld(soup, url) or self._extract_css_fallback(soup, url)
        if product:
            product.shop = self.SHOP_NAME
        return product


class CropMarketScraper(BaseShopScraper):
    """cropmarket.ro — WooCommerce cu slug-uri plate (fara prefix /produs/).
    URL-uri de tip: /fungicid-topas-100ec-250ml/ (slug direct dupa radacina).
    Pretul vine din meta product:price:amount.
    Sitemap-ul include si pagini de blog cu acelasi format de slug — filtrare necesara.
    """
    SHOP_NAME = "cropmarket"
    BASE_URL = "https://www.cropmarket.ro"
    # Sitemap-ul WooCommerce include toate URL-urile — filtram prin product-sitemap*.xml
    # si excludem cele fara product:price:amount
    PRODUCT_URL_PATTERN = r"cropmarket\.ro/[a-z0-9][a-z0-9-]+/?$"
    _EXCLUDE_PATHS = re.compile(
        r"/(produse|cos|checkout|cont|blog|contact|despre|politica|termeni|sitemap|category|tag|author)/"
    )

    def _find_sitemaps(self) -> list[str]:
        """Folosim doar product-sitemap*.xml, nu post-sitemap sau page-sitemap."""
        sitemaps = []
        # Sitemap index via robots.txt
        robots_url = "https://www.cropmarket.ro/robots.txt"
        try:
            resp = self.client.get(robots_url)
            for line in resp.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    sitemaps.append(line.split(":", 1)[1].strip())
        except Exception:
            pass
        # Fallback
        if not sitemaps:
            sitemaps.append("https://www.cropmarket.ro/sitemap_index.xml")
        return sitemaps

    def _parse_sitemap(self, sitemap_url: str) -> list[str]:
        """Override: sare peste post-sitemap si page-sitemap (contin spam/blog)."""
        resp = self.get(sitemap_url)
        if not resp:
            return []
        try:
            soup = BeautifulSoup(resp.content, "xml")
        except Exception:
            soup = BeautifulSoup(resp.text, "lxml-xml")

        # Sitemap index — recurse doar pe product-sitemap
        sitemaps = soup.find_all("sitemap")
        if sitemaps:
            all_urls = []
            for s in sitemaps:
                loc = s.find("loc")
                if loc:
                    loc_url = loc.text.strip()
                    # Sari blog, pages, local, category sitemaps
                    if any(skip in loc_url for skip in ["post-sitemap", "page-sitemap", "local-sitemap",
                                                          "category-sitemap", "product_cat"]):
                        continue
                    all_urls.extend(self._parse_sitemap(loc_url))
            return all_urls

        urls = []
        for loc in soup.find_all("loc"):
            url = loc.text.strip()
            if re.search(self.PRODUCT_URL_PATTERN, url) and not self._EXCLUDE_PATHS.search(url):
                urls.append(url)
        return urls

    def extract_product(self, url: str) -> Optional[Product]:
        resp = self.get(url)
        if not resp:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        # Verifica rapid ca e pagina de produs (nu blog post)
        if not self._meta(soup, "product:price:amount"):
            return None
        product = self._extract_jsonld(soup, url) or self._extract_meta_og(soup, url)
        if product:
            product.shop = self.SHOP_NAME
        return product


class AgroCenterScraper(BaseShopScraper):
    """agrocenter.ro"""
    SHOP_NAME = "agrocenter"
    BASE_URL = "https://www.agrocenter.ro"
    PRODUCT_URL_PATTERN = r"/produs[a-z]*/|/articol/|/[a-z0-9-]+-p\d+"

    def extract_product(self, url: str) -> Optional[Product]:
        resp = self.get(url)
        if not resp:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        product = self._extract_jsonld(soup, url) or self._extract_meta_og(soup, url)
        if product:
            product.shop = self.SHOP_NAME
        return product


class AgricoverScraper(BaseShopScraper):
    """magazin.agricover.ro — Dynamicweb CMS cu API JSON public.

    API: GET {categorie_url}?feed=true → JSON cu produse + paginare.
    Preturile sunt publice fara login.
    """
    SHOP_NAME = "agricover"
    BASE_URL = "https://magazin.agricover.ro"
    PRODUCT_URL_PATTERN = ""  # nu folosim sitemap de produse

    # Categorii cu produse (din sitemap)
    _PRODUCT_CATEGORIES = [
        "/produse/ingrasaminte",
        "/produse/seminte",
        "/produse/erbicide",
        "/produse/fungicide",
        "/produse/insecticide",
        "/produse/ingrasamant-foliar",
        "/produse/biostimulatori",
        "/produse/tratament-samanta",
        "/produse/adjuvanti",
        "/produse/regulatori-de-crestere",
        "/produse/igiena-publica",
        "/produse/produse-bio",
        "/produse/promotii",
    ]

    def _fetch_category_page(self, feed_url: str) -> Optional[dict]:
        """Apeleaza un URL feed=true si returneaza primul element JSON."""
        self._wait()
        try:
            resp = self.client.get(
                feed_url,
                headers={"User-Agent": self.USER_AGENT, "Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data[0] if isinstance(data, list) and data else None
        except Exception as e:
            logger.error("[agricover] Feed eșuat %s: %s", feed_url, e)
            return None

    def _parse_products_from_feed(self, page_data: dict) -> list[Product]:
        """Extrage produse din raspunsul JSON al unui feed de categorie."""
        products = []
        for container in page_data.get("ProductsContainer", []):
            for p in container.get("Product", []):
                name = (p.get("name") or "").strip()
                if not name:
                    continue
                price_raw = p.get("priceDouble")
                try:
                    price = float(price_raw) if price_raw is not None else None
                except (ValueError, TypeError):
                    price = None
                if price is None or price <= 0:
                    continue

                link = p.get("link", "")
                url = self.BASE_URL + link if link.startswith("/") else link
                in_stock = not p.get("isOutOfStock", False)
                sku = str(p.get("number", "")).strip() or None
                image_path = p.get("image", "")
                if image_path:
                    from urllib.parse import unquote
                    image = self.BASE_URL + unquote(image_path)
                else:
                    image = None

                products.append(Product(
                    url=url,
                    name=name,
                    price=price,
                    currency=p.get("currency", "RON"),
                    in_stock=in_stock,
                    sku=sku,
                    image=image,
                    shop=self.SHOP_NAME,
                ))
        return products

    def scan(self, limit: Optional[int] = None) -> list[Product]:
        """Scanează catalogul Agricover via API JSON cu paginare."""
        all_products: dict[str, Product] = {}  # dedup pe URL

        for cat_path in self._PRODUCT_CATEGORIES:
            feed_url = self.BASE_URL + cat_path + "?feed=true"
            logger.info("[agricover] Categorie: %s", cat_path)

            while feed_url:
                page_data = self._fetch_category_page(feed_url)
                if not page_data:
                    break

                products = self._parse_products_from_feed(page_data)
                for p in products:
                    if p.url not in all_products:
                        all_products[p.url] = p

                # Paginare
                next_page = page_data.get("nextPage", "")
                if next_page and page_data.get("nextdisabled", "") != "u-hidden":
                    feed_url = self.BASE_URL + next_page if next_page.startswith("/") else next_page
                else:
                    feed_url = None

                if limit and len(all_products) >= limit:
                    break

            if limit and len(all_products) >= limit:
                break

        result = list(all_products.values())
        if limit:
            result = result[:limit]
        logger.info("[agricover] Total: %d produse unice cu preț", len(result))
        return result


class FitomagScraper(BaseShopScraper):
    """fitomag.ro — 5000+ produse fitosanitare.
    URL-uri de produs: /magazin-brasov/<categorie>/<slug> (fara extensie .html).
    Sitemap-ul include si imagini (.jpg/.png/.webp) pe care le excludem.
    """
    SHOP_NAME = "fitomag"
    BASE_URL = "https://www.fitomag.ro"
    # Pattern: /magazin-brasov/<cat>/<slug> — exclude imagini si categorii goale
    PRODUCT_URL_PATTERN = r"/magazin-brasov/[a-z0-9-]+/[a-z0-9-]+"

    def discover_product_urls(self, limit=None) -> list[str]:
        urls = super().discover_product_urls(limit=None)
        # Exclude imagini (.jpg/.png/.webp/.gif) si URL-uri prea scurte
        import re
        product_urls = [
            u for u in urls
            if not re.search(r'\.(jpg|jpeg|png|webp|gif|svg)$', u, re.I)
            and len(u.split("/")) >= 6  # cel putin /magazin-brasov/<cat>/<slug>
        ]
        if limit:
            product_urls = product_urls[:limit]
        return product_urls

    def _extract_css_fallback(self, soup: BeautifulSoup, url: str) -> Optional[Product]:
        name_el = soup.select_one("h1.product-title, h1[class*='product']")
        price_el = soup.select_one("[class*='price'] [class*='amount'], .price-box .price")
        if not name_el:
            return None
        price = normalize_price(price_el.text) if price_el else None
        return Product(url=url, name=name_el.text.strip(), price=price)

    def extract_product(self, url: str) -> Optional[Product]:
        resp = self.get(url)
        if not resp:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        product = self._extract_jsonld(soup, url) or self._extract_css_fallback(soup, url)
        if product:
            product.shop = self.SHOP_NAME
        return product


class AgrolandScraper(BaseShopScraper):
    """Agroland shop online — PrestaShop.

    Sitemap-ul principal: agroland.ro/sitemapIndex_Shop_1.xml
    Sub-sitemap-urile pointeaza la shop-agroland.ro (cert SSL expirat).
    Rescriem URL-urile catre agroland.ro.
    URL produse: /<categorie>/<id>-<slug>.html
    Sitemap include si imagini (img/p/...) pe care le excludem.
    """
    SHOP_NAME = "agroland"
    BASE_URL = "https://agroland.ro"
    # Pattern produs PrestaShop: /<categ>/<id>-<slug>.html (exclude /img/)
    PRODUCT_URL_PATTERN = r"agroland\.ro/[a-z0-9-]+/\d+-[a-z0-9-]+\.html$"
    _EXPIRED_SUBDOMAIN = "shop-agroland.ro"
    _MAIN_DOMAIN = "agroland.ro"

    def _rewrite_url(self, url: str) -> str:
        """Rescrie shop-agroland.ro -> agroland.ro."""
        return url.replace("https://" + self._EXPIRED_SUBDOMAIN, "https://" + self._MAIN_DOMAIN)

    def _find_sitemaps(self) -> list[str]:
        return [
            "https://agroland.ro/sitemapIndex_Shop_1.xml",
            "https://agroland.ro/sitemap.xml",
        ]

    def _parse_sitemap(self, sitemap_url: str) -> list[str]:
        """Override: rescrie URL-urile sub-sitemap-urilor cu cert expirat."""
        sitemap_url = self._rewrite_url(sitemap_url)
        resp = self.get(sitemap_url)
        if not resp:
            return []
        try:
            soup = BeautifulSoup(resp.content, "xml")
        except Exception:
            soup = BeautifulSoup(resp.text, "lxml-xml")

        # Sitemap index — recurse cu URL-uri rescrise
        sitemaps = soup.find_all("sitemap")
        if sitemaps:
            all_urls = []
            for s in sitemaps:
                loc = s.find("loc")
                if loc:
                    rewritten = self._rewrite_url(loc.text.strip())
                    all_urls.extend(self._parse_sitemap(rewritten))
            return all_urls

        import re
        urls = []
        for loc in soup.find_all("loc"):
            url = self._rewrite_url(loc.text.strip())
            # Exclude imagini
            if re.search(r'\.(jpg|jpeg|png|webp|gif)$', url, re.I):
                continue
            if self.PRODUCT_URL_PATTERN and re.search(self.PRODUCT_URL_PATTERN, url):
                urls.append(url)
        return urls

    def extract_product(self, url: str) -> Optional[Product]:
        url = self._rewrite_url(url)
        resp = self.get(url)
        if not resp:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        product = self._extract_jsonld(soup, url) or self._extract_meta_og(soup, url)
        if product:
            product.shop = self.SHOP_NAME
        return product


# Registry toate magazinele
ALL_SCRAPERS: dict[str, type[BaseShopScraper]] = {
    "verdon": VerdonScraper,
    "diaplant": DiaplantScraper,
    "cropmarket": CropMarketScraper,
    "agrocenter": AgroCenterScraper,
    "agricover": AgricoverScraper,
    "fitomag": FitomagScraper,
    "agroland": AgrolandScraper,
}
