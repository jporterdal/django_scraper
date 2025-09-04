import re
from urllib import parse
from search_scrape.search_scrape import SearchParser
import logging

logger = logging.getLogger(__name__)


class CCSearchParser(SearchParser):
    def _init_vars(self):
        super()._init_vars()
        self.url = "https://www.canada" + "computers.com/en/search?s=" + parse.quote_plus(self.term) + "&pickup=62"
        self.title_patterns.extend([
            "msi.*" + self.term.lower() + ".*",
            "asus.*" + self.term.lower() + ".*",
            "gigabyte.*" + self.term.lower() + ".*",
        ])

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



sources = {'cc': CCSearchParser}