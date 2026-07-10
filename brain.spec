This project is intended to scrape website search results for pricing information


## Project Outline

Plan and develop an app that will track item pricing over time. The app uses Django/Python and should run equally well whether hosted locally or remotely.

### Item Details

The items being tracked may vary so the project should be able to handle some flexibility and abstractness at the initial stages. Eventually, the app should be equally usable for tracking prices on computer hardware (such as graphics cards) or collectibles (such as Magic the Gathering cards). The app is primarily focused on the pricing data for items and will rely on an external source (like a future app or a website) for specific features of the item (for example, the manufacturer for a GPU, or the oracle text for a magic card). The app should be able to store some information that is only relevant within the app, such as tags to permit the user to group items into categories for sorting or prioritization.


### Pricing data

The app should be capable of fetching price data for individual items and groups of items. There may be multiple data sources for pricing data, each corresponding to a separate online vendor/store. Price sources will be provided by the user.

Pricing data should be stored locally and always be associated with a timestamp so that changes may be observed over time.


#### Pricing sources

Since storefront websites can not be expected to provide an API for their search results, the price source may need to be a search URL that will lead to a website containing price information corresponding to the item's name.

If a search page URL is used, then the app must be capable of parsing the search results (HTML) for the correct result and also identifying whether the item is in stock or not. This is because item names are not a unique identifier of an item (for example, there may be multiple special printings of a magic card with name "Foo Bar", there may be multiple manufacturers making a GPU with model name 2468) and different item variants may have different prices.


#### Price fetching

The app should dynamically fetch prices for an item or set of items based on user interaction (like a button press). The user should be able to select an item or set of items (such as using custom tag categories) that will have their prices updated. Price data should be fetched and stored locally using a local-TZ timestamp. Since price data will typically be fetched over standard HTTP requests, a reasonable delay should be used between requests to avoid overburdening vendor search pages or triggering bot detection methods like CAPTCHAs or DDOS prevention.