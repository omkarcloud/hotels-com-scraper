"""Reference resolvers, the points of sale and the site's filter / sort
vocabulary for /hotels-com/*.

ONE param per input (tripadvisor QueryOrIdField convention, never a
sibling `url` / `id` pair):

  * `hotel`      the hotels.com id as it appears in a hotel link
                 ("ho434012"), the Expedia property id for the same hotel
                 ("6136437": the id GraphQL, the search cards and the
                 typeahead's HOTEL rows use), OR any hotels.com / expedia.com
                 hotel link (https://www.hotels.com/ho434012/hotel-anastassiou-kastoria/?chkin=…,
                 https://www.hotels.com/h6136437.Hotel-Information,
                 https://www.expedia.com/Kastoria-Hotels-Hotel-Anastassiou.h6136437.Hotel-Information,
                 a search card link carrying ?expediaPropertyId=…). A bare
                 number is an Expedia id.
  * `location`   a destination name ("Paris, France"), a region id ("2734",
                 the gaiaId of /hotels-com/locations/autocomplete) OR a
                 hotels.com Hotel-Search link, whose regionId / destination /
                 latLong / mapBounds are taken over.
  * `country`    the point of sale: which hotels.com storefront answers
                 (POS below). Prices follow the storefront, never a currency
                 param (verified 2026-11-07: a currency in the GraphQL
                 context is ignored, uk.hotels.com prices in GBP).

Resolvers raise ValueError with a user-facing message (RefField -> 400).

Two ids, one hotel (verified 2026-11-07): the public URL carries the legacy
hotels.com id `ho<digits>`; the Expedia property id drives GraphQL and is
what a search card's `id` holds. `/h<expediaId>.Hotel-Information` 302s to
the `/ho<id>/<slug>/` URL and the bare `/ho<id>/` page server-renders
`PropertyInfo:<expediaId>`; an unknown id 302s to the homepage.

Filter tokens are hotels.com's own (the Hotel-Search page's
universalSortAndFilter, captured 2026-11-07 on a Paris search): the values
below are what the site accepts in the criteria selections; a token the
site does not know is ignored upstream, never an error.
"""
import re
from urllib.parse import parse_qs, urlparse

# ---- points of sale ----------------------------------------------------------------
# host + GraphQL context per storefront (read from each storefront's own
# homepage with a fresh cookie jar, 2026-11-07). The storefront decides the
# currency and the language of the content; `country` is the public param.
POS = {
    "US": {"host": "https://www.hotels.com", "site_id": 300000001, "tpid": 3001, "eapid": 1,
           "locale": "en_US", "currency": "USD", "exit_country": "us"},
    "GB": {"host": "https://uk.hotels.com", "site_id": 300000005, "tpid": 3101, "eapid": 5,
           "locale": "en_GB", "currency": "GBP", "exit_country": "gb"},
    "DE": {"host": "https://de.hotels.com", "site_id": 300000752, "tpid": 3102, "eapid": 752,
           "locale": "de_DE", "currency": "EUR", "exit_country": "de"},
    "AU": {"host": "https://au.hotels.com", "site_id": 300000035, "tpid": 3201, "eapid": 35,
           "locale": "en_AU", "currency": "AUD", "exit_country": "au"},
    "JP": {"host": "https://jp.hotels.com", "site_id": 300000037, "tpid": 3203, "eapid": 37,
           "locale": "ja_JP", "currency": "JPY", "exit_country": "jp"},
}
DEFAULT_POS = "US"
SITE = POS[DEFAULT_POS]["host"]
# The storefronts the public `country` param offers: the ones whose exits
# passed the pod probe of 2026-11-07 (US on datacenter 2/2, GB on GB
# residential 1/2). de / au / jp were refused on 2/2 residential exits of
# their country with both fingerprints that day and stay in POS, unexposed,
# until a probe passes them.
PUBLIC_COUNTRIES = ("US", "GB")

HOTEL_ID_RE = re.compile(r"^ho([0-9]{1,12})$", re.I)
EXPEDIA_ID_RE = re.compile(r"^[0-9]{1,12}$")
REGION_ID_RE = re.compile(r"^[0-9]{1,20}$")

_LINK_HOTEL_RE = re.compile(r"/ho([0-9]{1,12})(?:[/?#]|$)", re.I)
_LINK_EXPEDIA_RE = re.compile(r"\.h([0-9]{1,12})\.Hotel-Information", re.I)
_LINK_EXPEDIA_BARE_RE = re.compile(r"^/h([0-9]{1,12})\.Hotel-Information", re.I)

_HOTEL_HOSTS = ("hotels.com", "hoteles.com", "hoteis.com", "hotels.cn", "expedia.com", "expedia.co.uk",
                "expedia.de", "expedia.fr", "expedia.ca", "expedia.com.au", "expedia.co.jp", "expedia.co.in")


def _host_known(url):
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _HOTEL_HOSTS)


def _as_url(value):
    return "https:" + value if value.startswith("//") else value


def is_link(value):
    return (value or "").startswith(("http://", "https://", "//"))


def resolve_hotel(value):
    """Bare hotels.com id / Expedia id or a hotel link -> the reference
    hotels_com/fetch.py resolves: "ho434012" (legacy id) or "6136437"
    (Expedia id)."""
    value = (value or "").strip()
    match = HOTEL_ID_RE.match(value)
    if match:
        return "ho" + match.group(1)
    if EXPEDIA_ID_RE.match(value):
        return value
    if is_link(value):
        url = _as_url(value)
        if not _host_known(url):
            raise ValueError("Must be a hotels.com or expedia.com hotel link.")
        parsed = urlparse(url)
        match = _LINK_HOTEL_RE.search(parsed.path)
        if match:
            return "ho" + match.group(1)
        match = _LINK_EXPEDIA_RE.search(parsed.path) or _LINK_EXPEDIA_BARE_RE.match(parsed.path)
        if match:
            return match.group(1)
        expedia = (parse_qs(parsed.query).get("expediaPropertyId") or [None])[0]
        if expedia and EXPEDIA_ID_RE.match(expedia):
            return expedia
        raise ValueError("Must be a hotel link (https://www.hotels.com/ho434012/ or "
                         "https://www.hotels.com/h6136437.Hotel-Information).")
    raise ValueError("Must be a hotels.com id (ho434012), an Expedia property id (6136437) "
                     "or a hotels.com hotel link.")


def resolve_location(value):
    """Destination text / region id / Hotel-Search link -> a dict with
    exactly one of `region_name`, `region_id`, `coordinates`
    ({latitude, longitude}) or `bounds` ([sw, ne] as {latitude,
    longitude})."""
    value = " ".join((value or "").split())
    if not value:
        raise ValueError("Must not be empty.")
    if REGION_ID_RE.match(value):
        return {"region_id": value}
    if is_link(value):
        url = _as_url(value)
        if not _host_known(url):
            raise ValueError("Must be a hotels.com link.")
        query = parse_qs(urlparse(url).query)
        first = lambda key: (query.get(key) or [None])[0]   # noqa: E731
        region_id = first("regionId")
        if region_id and REGION_ID_RE.match(region_id):
            return {"region_id": region_id}
        bounds = query.get("mapBounds") or []
        if len(bounds) == 2:
            try:
                sw, ne = (tuple(float(x) for x in b.split(",")) for b in bounds)
                return {"bounds": [{"latitude": sw[0], "longitude": sw[1]},
                                   {"latitude": ne[0], "longitude": ne[1]}]}
            except (TypeError, ValueError):
                pass
        lat_long = first("latLong")
        if lat_long and "," in lat_long:
            try:
                lat, lng = (float(x) for x in lat_long.split(",", 1))
                return {"coordinates": {"latitude": lat, "longitude": lng}}
            except ValueError:
                pass
        destination = first("destination")
        if destination and destination.strip():
            return {"region_name": " ".join(destination.split())}
        raise ValueError("Must be a hotels.com search link with a destination, regionId, "
                         "latLong or mapBounds.")
    return {"region_name": value}


def parse_bounds(value):
    """'sw_lat,sw_lng,ne_lat,ne_lng' -> [sw, ne] as {latitude, longitude}."""
    parts = [p.strip() for p in str(value or "").split(",")]
    if len(parts) != 4:
        raise ValueError("Must be four numbers: south_lat,west_lng,north_lat,east_lng.")
    try:
        sw_lat, sw_lng, ne_lat, ne_lng = (float(p) for p in parts)
    except ValueError:
        raise ValueError("Must be four numbers: south_lat,west_lng,north_lat,east_lng.")
    for lat in (sw_lat, ne_lat):
        if not -90 <= lat <= 90:
            raise ValueError("Latitudes must be between -90 and 90.")
    for lng in (sw_lng, ne_lng):
        if not -180 <= lng <= 180:
            raise ValueError("Longitudes must be between -180 and 180.")
    if sw_lat >= ne_lat:
        raise ValueError("south_lat must be below north_lat.")
    return [{"latitude": sw_lat, "longitude": sw_lng}, {"latitude": ne_lat, "longitude": ne_lng}]


def hotel_link(hotel_id, pos=DEFAULT_POS):
    """The canonical bare hotel page ("ho434012" -> https://www.hotels.com/ho434012/)."""
    return f"{POS[pos]['host']}/{hotel_id}/"


def expedia_link(expedia_id, pos=DEFAULT_POS):
    return f"{POS[pos]['host']}/h{expedia_id}.Hotel-Information"


def hotel_id_from_link(link):
    """"https://www.hotels.com/ho174047/villa-eugenie-paris-france/?chkin=…" -> "ho174047"."""
    match = _LINK_HOTEL_RE.search(urlparse(link or "").path)
    return "ho" + match.group(1) if match else None


def expedia_id_from_link(link):
    query = parse_qs(urlparse(link or "").query)
    expedia = (query.get("expediaPropertyId") or [None])[0]
    if expedia and EXPEDIA_ID_RE.match(expedia):
        return expedia
    match = _LINK_EXPEDIA_RE.search(urlparse(link or "").path)
    return match.group(1) if match else None


# ---- search vocabulary (the site's own tokens) -------------------------------------

SORTS = {
    "recommended": "RECOMMENDED",
    "price_low_to_high": "PRICE_LOW_TO_HIGH",
    "price_high_to_low": "PRICE_HIGH_TO_LOW",
    "best_deal": "BEST_DEAL",
    "distance": "DISTANCE",
    "guest_rating": "REVIEW",
    "relevant_reviews": "REVIEW_RELEVANT",
    "star_rating": "PROPERTY_CLASS",
}

# criteria selection ids (the site's URL -> criteria mapper, verified 2026-11-07)
STAR = "star"                      # 10 .. 50 (one per star)
GUEST_RATING = "guestRating"       # 35 / 40 / 45 / loved_by_guests
AMENITIES = "amenities"
MEAL_PLAN = "mealPlan"
LODGING = "lodging"
PAYMENT_TYPE = "paymentType"       # PAY_LATER (+ FREE_CANCELLATION, the older encoding)
CANCELLATION = "propertyCancellation"   # FREE_CANCELLATION ("Fully refundable property", 2026-11-10 page)
STAY_OPTIONS = "stay_options_group"     # hotels_option / vacation_rentals_option
HOTEL_NAME = "hotelName"
NEIGHBORHOOD = "multi_neighborhood_group"
BRAND = "hotel_brand"
TRAVELER_TYPE = "travelerType"
ACCESSIBILITY = "accessibility"
AVAILABLE_ONLY = "availableFilter"
PRICE_RANGE = "nightly_price"      # range id; "price" is ignored upstream

GUEST_RATINGS = {"9+": "45", "8+": "40", "7+": "35", "loved_by_guests": "loved_by_guests"}

STARS = {"1": "10", "2": "20", "3": "30", "4": "40", "5": "50"}

REVIEW_SORTS = {
    "newest": "NEWEST_TO_OLDEST",
    "highest_rating": "HIGHEST_TO_LOWEST_RATED",
    "lowest_rating": "LOWEST_TO_HIGHEST_RATED",
    "relevant": "NEWEST_TO_OLDEST_BY_LANGUAGE",
}

# the search page's "Traveler experience" filter (criteria selection
# travelerType): the option values the Las Vegas Hotel-Search page offers
# (2026-11-10). The FAMILY / COUPLE / ... tokens used before were ignored
# upstream (same-session A/B from the pod: FAMILY = the unfiltered set,
# adults_only = 8 hotels, none of them in it).
SEARCH_TRAVELER_TYPES = {
    "family": "family_friendly", "business": "business_friendly", "luxury": "luxury_property",
    "budget": "budget_property", "adults_only": "adults_only", "romantic": "romantic", "wedding": "wedding",
    "lgbtq_welcoming": "lgbtq_welcoming", "eco_certified": "eco_certified",
}

# "Stay options": hotels only, or homes (vacation rentals) only
STAY_TYPES = {"hotels": "hotels_option", "homes": "vacation_rentals_option"}

# Filters whose matches the site's matchedPropertiesSize ignores: it keeps
# the destination's full total (Las Vegas 291 with brand=MGM or
# loved_by_guests, while the pages hold 25 / 15 hotels; 2026-11-10), so a
# search using one reports no total, only has_next. A hotel name only puts
# that hotel first (dated searches keep the whole list), so it is counted.
UNCOUNTED_FILTERS = ("brand",)
