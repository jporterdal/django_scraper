import re
import requests
from html.parser import HTMLParser
import logging

logger = logging.getLogger(__name__)


# ----- Class definitions -----

# Help with debugging
class TestParser(HTMLParser):
    def handle_starttag(self, tag, attrs):
        print(f"Start: {tag} - {attrs}")

    def handle_endtag(self, tag):
        print(f"End: {tag}")

    def handle_data(self, data):
        print(f"Data: {data}")


class SearchParser(HTMLParser):
    data_keys = ["category", "title", "price", "instock"]
    _ignore_tags = ["img", "input"]


    # Inner convenience representation for HTML elements
    class Element():
        def __init__(self, tag, attrs, parent=None):
            self.tag = tag.lower()  # element tag
            self.attrs = attrs  # list of attribute/value pairs represented as strings
            self.parent = parent  # parent Element, default None indicates root

        def __str__(self):
            return ("<" +
                    str(self.tag) +
                    (" " + " ".join([f"{a}" + f"='{v}'" if v else "" for (a,v) in self.attrs])
                     if self.attrs and len(self.attrs) > 0
                     else "")
                    + ">")

        def get_classes(self):
            return self.get_attr_values("class")

        def get_attr_values(self, attr):
            v = []
            for a in self.attrs:
                if a[0].strip().lower() == attr.lower():
                    v.extend([elt.lower() for elt in a[1].split()])
            return v

        def is_class(self, c):
            return c in self.get_classes()

        def has_attr(self, attr):
            for a in self.attrs:
                if a[0].strip().lower() == attr.lower():
                    return True
            return False

        def attr_has_value(self, attr, value):
            return value.lower() in self.get_attr_values(attr)

        def first_ancestor_tag(self, tag):
            p = self.parent
            while p is not None:
                if p.tag == tag:
                    return p
                p = p.parent
            return None

        def any_ancestor_tag(self, tag):
            p = self.parent
            while p is not None:
                if p.tag == tag:
                    yield p
                p = p.parent


    def __init__(self, *args, **kwargs):
        try:
            self.term = kwargs.pop('term')
        except KeyError:
            self.term = None
        #self.term = kwargs.get('term', None)
        super().__init__(*args, **kwargs)

    def _init_vars(self):
        self.within_item_object = False
        self.instock = False
        self.title_patterns = [self.term.lower() + "$"]

        self.results = []
        self.dom = []
        self.current_data_key = None  # TODO: implement this as a stack to allow nested elements with data

    def handle_starttag(self, tag, attrs):
        if tag in self._ignore_tags:
            return

        try:
            parent = self.dom[-1]
        except:
            parent = None

        # Build DOM stack
        cur_element = SearchParser.Element(tag, attrs, parent=parent)
        self.dom.append(cur_element)

        if self.check_within_item_object(cur_element):
            self.within_item_object = True

        if self.within_item_object:
            for dk in self.data_keys:
                fname = "check_element_" + dk
                if hasattr(self, fname) and getattr(self, fname)():
                    logger.debug(f"Processing {dk} for item object (term='{self.term}')")
                    self.current_data_key = dk

    def handle_endtag(self, tag):
        if tag in self._ignore_tags:
            return

        closing = self.dom.pop()

        # If True, we are handling the endtag for the current search result object

        if self.within_item_object:
            for dk in self.data_keys:
                fname = "check_element_" + dk

                if hasattr(self, fname) and getattr(self, fname)(elt=closing):
                    # TODO: could handle clearing self.current_data_key here
                    logger.debug(f"Finished {dk} for item object (term='{self.term}'): {getattr(self, dk)}")

            if self.check_within_item_object(closing):
                self.save_result()
                self.within_item_object = False

    def save_result(self):
        # Results are pulled from own attributes and saved into dict for later processing
        result = {}
        for k in self.data_keys:
            try:
                result[k] = getattr(self, k)
                setattr(self, k, None)
            except AttributeError:
                result[k] = None

        self.results.append(result)

    def handle_data(self, data):
        # Allows subclasses to provide special handler methods of the form 'read_keyname' for any 'keyname'
        if self.within_item_object and self.current_data_key is not None:

            # TODO: more pythonic to handle success/failure of methods with an exception
            if hasattr(self, "read_" + self.current_data_key):
                if getattr(self, "read_" + self.current_data_key)(data) is not False:  # Accept 'True' and 'None' as successes
                    self.current_data_key = None
            else:
                setattr(self, self.current_data_key, data.strip())
                self.current_data_key = None

            ##self.current_data_key = None

    def lowest_price(self):
        price, title, category, instock = float('inf'), "", "", False

        for r in self.results:
            if r['instock'] and self.match_title(r['title'].lower()) and r['price'] < price:
                price, title, category, instock = r['price'], r['title'], r['category'], r['instock']

        if not instock:
            title, price = self.term, 0.0

        # Safely string-ify returned values
        return (title if title else "",
                category if category else "",
                str(price) if price else "",
                )

    def available(self):
        return [r for r in self.results if r['instock'] and self.match_title(self.term.lower(), r['title'].lower())]

    def match_title(self, result_title):
        # Subclasses should append to self.title_patterns if variants on product title should be matched

        match = None
        for p in self.title_patterns:
            match = match or re.match(p, result_title)

        return match != None

    def check_within_item_object(self, element):
        # Should be implemented within a subclass
        raise NotImplementedError

    # Entry-point function
    def search(self, term=None):
        self.term = term or self.term  # Update search-term if given
        self._init_vars()

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:138.0) Gecko/20100101 Firefox/138.0"}

        print(f"'{self.term}' -", end="")
        result = requests.get(self.url, headers=headers)
        if result.status_code == 200:
            print("   200")

            try:
                self.feed(result.text)
            except:
                with open("error_page.html", "w") as err:
                    err.writelines(result.text)
                raise
        else:
            print(f" *** {result.status_code}")


# Entry-point function
def qqqsearch(parser_cls, search_term):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:138.0) Gecko/20100101 Firefox/138.0"}
    parser = parser_cls(term=search_term)

    print(f"'{search_term}' -", end="")
    result = requests.get(parser.url, headers=headers)
    if result.status_code == 200:
        print("   200")

        try:
            parser.feed(result.text)
        except:
            with open("error_page.html", "w") as err:
                err.writelines(result.text)
            raise
    else:
        print(f" *** {result.status_code}")
    return parser


if __name__ == "__main__":
    print("HTMLParser sanity check: \n")
    parser = TestParser()
    html = ("<html>\n<body><h2>Page Title</h2>\n\n\t<div class='mt-4 mb-3'>Lorem "
            "<a href='http://www.google.com'>Ipsum</a> \n dolor sit amet</div>"
            "</body></html>")
    parser.feed(html)

