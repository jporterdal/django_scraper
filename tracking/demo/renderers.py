"""Render neutral demo products into each demo parser's vendor JSON shape (design D7).

Each renderer produces exactly the structure its parser in
``tracking/parsers.py`` reads, so demo searches exercise the real parsers.
The round-trip tests in ``tracking/tests/test_demo_replay.py`` guard that
these stay in sync with the parsers.
"""


def render_shopify(products):
    hits = []
    for product in products:
        hits.append({
            "_source": {
                "title": product["title"],
                "MTG_Set_Name": product["category"],
                "General_Game_Type": product["product_line"],
                "variants": [
                    {
                        "price": variant["price"],
                        "inventoryQuantity": 4 if variant["instock"] else 0,
                        "selectedOptions": (
                            [{"name": "Condition", "value": variant["condition"]}]
                            if variant["condition"]
                            else []
                        ),
                    }
                    for variant in product["variants"]
                ],
            }
        })
    return {"hits": {"total": {"value": len(hits)}, "hits": hits}}


def render_storepass(products):
    return {
        "current_page": 1,
        "pages": 1,
        "products": [
            {
                "display_name": product["title"],
                "vendor": product["product_line"],
                "productLineData": {"set": product["category"]},
                "variantInfo": [
                    {
                        "title": variant["condition"],
                        "price": variant["price"],
                        "inventory_quantity": 3 if variant["instock"] else 0,
                    }
                    for variant in product["variants"]
                ],
            }
            for product in products
        ],
    }


def render_wtfilters(products):
    # wt-filters collapses variants to one product-level price/stock row.
    results = []
    for product in products:
        variant = product["variants"][0]
        results.append({
            "title": product["title"],
            "price": variant["price"],
            "in_stock": bool(variant["instock"]),
            "category": product["product_line"],
            "subcategory": product["category"],
        })
    return {"data": {"total": len(results), "results": results}}


RENDERERS = {
    "shopify": render_shopify,
    "storepass": render_storepass,
    "wtfilters": render_wtfilters,
}
