import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from search_scrape.search_scrape import SearchParser
import logging

logger = logging.getLogger(__name__)


def _normalize_for_match(value):
    """Lowercase and collapse whitespace, for term/title comparison."""
    return re.sub(r"\s+", " ", str(value).strip().lower())


class JSONSearchParser:
    """Base class for JSON API search parsers. Subclasses implement parse_data()."""
    data_keys = ["category", "title", "price", "instock"]

    def __init__(self, term=""):
        self.term = term
        self.url = None
        self.results = []

    def _init_vars(self):
        self.results = []

    def parse_response(self, response):
        self._init_vars()
        self.parse_data(response.json())

    def parse_next_page(self, response):
        """Parse an additional page, appending to self.results without resetting."""
        self.parse_data(response.json())

    def next_page_url(self, response, current_url, page_number):
        """Return the URL of the next page, or None when there is no next page.

        Default is single-page (no pagination); JSON subclasses override.
        """
        return None

    def next_page_body(self, response, current_body, page_number):
        """Return the POST body for the next page, or None when there is no next page.

        Default is single-page; POST JSON subclasses override for body-based pagination.
        """
        return None

    def parse_data(self, data):
        raise NotImplementedError

    def add_result(self, title, price, instock, category=""):
        term_normalized = _normalize_for_match(self.term)
        if term_normalized and term_normalized not in _normalize_for_match(title):
            logger.debug(
                "Dropping off-term result: title=%r does not contain term=%r",
                title, self.term,
            )
            return
        self.results.append({
            "title": str(title),
            "price": float(price),
            "instock": 1 if instock else 0,
            "category": category or "",
        })


class HTMLResponseParserMixin:
    """Gives submodule HTML parsers the uniform parse_response(response) contract."""
    def parse_response(self, response):
        self._init_vars()
        self.feed(response.text)

    def parse_next_page(self, response):
        """Feed an additional page without resetting parser state."""
        self.feed(response.text)

    def next_page_url(self, response, current_url, page_number):
        """HTML parsers stay single-page."""
        return None


class CCSearchParser(HTMLResponseParserMixin, SearchParser):
    def _init_vars(self):
        super()._init_vars()

    def check_within_item_object(self, element):
        return element.tag == "div" and element.is_class("product")

    def check_element_title(self, elt=None):
        cur = elt or self.dom[-1]  # Accept passed element or else check current element from DOM

        parent = cur.parent

        return cur.tag == "a" and parent is not None and parent.is_class("product-title")

    def check_element_price(self, elt=None):
        cur = elt or self.dom[-1]

        return cur.tag == "span" and cur.is_class("price")

    def check_element_instock(self, elt=None):
        cur = elt or self.dom[-1]

        if cur.tag == "b":
            for anc in cur.any_ancestor_tag("div"):
                if anc.is_class("available-tag"):
                    return True
        return False

    def read_price(self, data):
        try:
            self.price = float(re.match(".*\$([0-9\.\,]+)$", data.strip())[1].replace(",", ""))
        except TypeError:  # no match!
            # TODO: refactor this to handle price data being within an element containing other elements
            logger.error("Could not find price in element data!")
            logger.error(f" '{data.strip()}'")
            raise
            #self.price = float(data[1:].strip().replace(",", ""))

    def read_title(self, data):
        self.title = str(data.strip())


    def read_instock(self, data):
        pattern = ".*?(\S.*\S).*?"
        m = re.match(pattern, data, re.DOTALL)  # Instruct re to include endlines in [.]
        if not m:
            self.instock = False
            return False

        self.instock = m.group(1).lower() == "In Store - Available for Pickup".lower()
        return True



class ShopifyParser(JSONSearchParser):
    """Shopify prod-indexer (Elasticsearch-style) JSON search results."""

    def parse_data(self, data):
        for hit in data.get("hits", {}).get("hits", []):
            src = hit.get("_source", {})
            title = src.get("title", "")
            category = src.get("MTG_Set_Name") or src.get("Set") or ""
            for variant in src.get("variants", []):
                condition = ""
                for opt in variant.get("selectedOptions", []):
                    if opt.get("name") == "Condition":
                        condition = opt.get("value", "")
                display = f"{title} ({condition})" if condition else title
                self.add_result(
                    title=display,
                    price=variant.get("price", 0),
                    instock=variant.get("inventoryQuantity", 0) > 0,
                    category=category,
                )

    def next_page_url(self, response, current_url, page_number):
        """Increment the ``/page/{n}/`` segment until a page returns no hits."""
        data = response.json()
        if not data.get("hits", {}).get("hits", []):
            return None

        def _bump(match):
            return f"{match.group(1)}{int(match.group(2)) + 1}"

        new_url, replacements = re.subn(r"(/page/)(\d+)", _bump, current_url)
        if replacements == 0:
            logger.warning(
                "ShopifyParser.next_page_url: no /page/N/ segment in %s", current_url
            )
            return None
        return new_url


class WtFiltersParser(JSONSearchParser):
    """POST-JSON search API results (``data.results[]``).

    Search results collapse product variants into one row per product; product-level
    price and stock only — no secondary per-variant fetches.

    Configure the Source at runtime with ``http_method="POST"``, a
    ``request_body_template`` containing ``{term}`` (plain text), and
    ``request_headers`` with ``Origin`` plus a ``Referer`` such as
    ``https://…/search?q={term}`` (URL-encoded at fetch time — see
    ``Source.build_request_headers``). See ``tracking/docs/wt_investigation.md``
    for full configuration details.
    """

    def parse_data(self, data):
        for row in data.get("data", {}).get("results", []):
            self.add_result(
                title=row.get("title", ""),
                price=row.get("price", 0),
                instock=row.get("in_stock"),
                category=row.get("subcategory") or row.get("category", ""),
            )

    def next_page_body(self, response, current_body, page_number):
        return None


class StorepassParser(JSONSearchParser):
    """Storepass SaaS JSON search results."""

    def parse_data(self, data):
        for product in data.get("products", []):
            title = product.get("display_name") or product.get("name", "")
            pld = product.get("productLineData")
            category = pld.get("set", "") if isinstance(pld, dict) else ""
            for variant in product.get("variantInfo", []):
                condition = variant.get("title", "")
                display = f"{title} ({condition})" if condition else title
                self.add_result(
                    title=display,
                    price=variant.get("price", 0),
                    instock=variant.get("inventory_quantity", 0) > 0,
                    category=category,
                )

    def next_page_url(self, response, current_url, page_number):
        """Storepass reports ``current_page`` and total ``pages`` in the body.

        When another page exists, set/replace the ``page`` query parameter on the
        base search URL. (The fixture uses ``current_page``/``pages``; Storepass docs
        also mention ``nextPageParameters``, absent from the observed payload.)
        """
        data = response.json()
        pages = data.get("pages")
        current_page = data.get("current_page")
        if not isinstance(pages, int) or not isinstance(current_page, int):
            return None
        if current_page >= pages:
            return None

        parsed = urlparse(current_url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["page"] = [str(current_page + 1)]
        new_query = urlencode(query, doseq=True)
        return urlunparse(parsed._replace(query=new_query))


sources = {
    'cc': CCSearchParser,
    'shopify': ShopifyParser,
    'storepass': StorepassParser,
    'wtfilters': WtFiltersParser,
}