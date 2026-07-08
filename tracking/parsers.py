import re
from search_scrape.search_scrape import SearchParser
import logging

logger = logging.getLogger(__name__)


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

    def parse_data(self, data):
        raise NotImplementedError

    def add_result(self, title, price, instock, category=""):
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
        try:
            self.instock = m[1].lower() == "In Store - Available for Pickup".lower()
        except:
            return False  # Not successful

        return True  # Successful



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


sources = {'cc': CCSearchParser, 'shopify': ShopifyParser, 'storepass': StorepassParser}