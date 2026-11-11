"""The /hotels-com/* endpoint functions (route_glue's route_table calls them
with the schema's loaded kwargs).

The four hotel endpoints share ONE cached page parse: cache.cached_call
stores parse_hotel_page's record under hotels-com.hotel keyed on the
`hotel` reference and the storefront, so details + rooms + reviews +
calendar on the same hotel cost one hotel-page fetch (the id mapping
hotels.com id -> Expedia property id lives only on that page; GraphQL
speaks Expedia ids).
"""
import math

from cache import cached_call
from hotels_com import cache_config as ttl
from hotels_com import parsers, refs
from hotels_com.fetch import (REVIEWS_MAX_SIZE, HotelsComUpstreamError, fetch_autocomplete, fetch_calendar,
                              fetch_hotel_page, fetch_offers, fetch_reviews, fetch_search, fetch_search_page)

NS = "hotels-com"


def _storefront(country):
    pos = refs.POS[country]
    return {"country": country.lower(), "host": pos["host"], "currency": pos["currency"], "language": pos["locale"]}


# ---- locations/autocomplete --------------------------------------------------------

def _autocomplete(query, limit, country):
    results = parsers.parse_autocomplete(fetch_autocomplete(query, limit, pos=country), pos=country)[:limit]
    return {"query": query, "storefront": _storefront(country), "count": len(results), "results": results}


def locations_autocomplete(**data):
    return cached_call(f"{NS}.autocomplete", data, _autocomplete, ttl.AUTOCOMPLETE_CACHE)


# ---- the search area ----------------------------------------------------------------

def _destination(location, latitude, longitude, bounds):
    """The GraphQL destination input from the three ways to pick an area."""
    if bounds:
        return {"mapBounds": bounds}
    if latitude is not None and longitude is not None:
        return {"coordinates": {"latitude": latitude, "longitude": longitude}}
    location = location or {}
    if location.get("region_id"):
        return {"regionId": location["region_id"]}
    if location.get("coordinates"):
        return {"coordinates": location["coordinates"]}
    if location.get("bounds"):
        return {"mapBounds": location["bounds"]}
    return {"regionName": location.get("region_name")}


def _criteria(data):
    """Filters -> the site's criteria selections / ranges."""
    selections, ranges = [], []
    for token in data.get("stars") or []:
        selections.append({"id": refs.STAR, "value": token})
    if data.get("guest_rating"):
        selections.append({"id": refs.GUEST_RATING, "value": data["guest_rating"]})
    for key, sel_id in (("amenities", refs.AMENITIES), ("property_type", refs.LODGING), ("meal_plan", refs.MEAL_PLAN),
                        ("neighborhood", refs.NEIGHBORHOOD), ("brand", refs.BRAND), ("accessibility", refs.ACCESSIBILITY)):
        for token in data.get(key) or []:
            selections.append({"id": sel_id, "value": str(token)})
    if data.get("free_cancellation"):
        # the site carries "fully refundable" in two encodings: its own
        # propertyCancellation group (the 2026-11-10 page) and the older
        # paymentType token (verified 2026-11-07); one is ignored, never both
        selections.append({"id": refs.CANCELLATION, "value": "FREE_CANCELLATION"})
        selections.append({"id": refs.PAYMENT_TYPE, "value": "FREE_CANCELLATION"})
    if data.get("pay_later"):
        selections.append({"id": refs.PAYMENT_TYPE, "value": "PAY_LATER"})
    if data.get("name"):
        selections.append({"id": refs.HOTEL_NAME, "value": data["name"]})
    for token in data.get("traveler_type") or []:
        selections.append({"id": refs.TRAVELER_TYPE, "value": token})
    if data.get("stay_type"):
        selections.append({"id": refs.STAY_OPTIONS, "value": data["stay_type"]})
    lo, hi = data.get("min_price"), data.get("max_price")
    if lo is not None or hi is not None:
        ranges.append({"id": refs.PRICE_RANGE,
                       "min": int(lo) if lo is not None else 0,
                       "max": int(hi) if hi is not None else -2})   # -2 = the site's "and above"
    return selections, ranges


# ---- hotels/search ----------------------------------------------------------------------

def _search(**data):
    page, limit, country = data["page"], data["limit"], data["country"]
    start = (page - 1) * limit
    destination = _destination(data.get("location"), data.get("latitude"), data.get("longitude"), data.get("bounds"))
    selections, ranges = _criteria(data)
    parsed = parsers.parse_search(fetch_search(
        destination, start, checkin=data.get("checkin"), checkout=data.get("checkout"),
        adults=data["adults"], children=data.get("children"), room_count=data["rooms"], sort=data["sort"],
        selections=selections, ranges=ranges, size=limit, pos=country), pos=country)
    results, has_next, total = parsed["results"], parsed["has_next"], parsed["total_count"]
    if not parsed["has_paging"]:
        # No pagination block (vacation-rental searches, some filtered pages,
        # 2026-11-11): the site's count is wrong then too (39 homes, and the
        # next page held 50 more), and a page can carry more cards than asked
        # for (88 for 50). A full page means "maybe more"; the rest is cut so
        # the next page doesn't repeat it.
        has_next, total = len(results) >= limit, None
        results = results[:limit]
    if not has_next and results:
        total = start + len(results)                # the last page: the whole set is known
    elif any(data.get(key) for key in refs.UNCOUNTED_FILTERS) or data.get("guest_rating") == "loved_by_guests":
        total = None                                # the site's count ignores these filters
    total_pages = max(1, math.ceil(total / limit)) if isinstance(total, (int, float)) and total else None
    location = parsed["location"]
    if "mapBounds" in destination:
        # a map-area search echoes an unrelated region in its criteria (a Las
        # Vegas box came back as "Midtown West, New York", 2026-11-10)
        location = {"region_id": None, "name": None}
    return {
        "location": location,
        "heading": parsed["heading"],
        "checkin": parsed["checkin"] or data.get("checkin"),
        "checkout": parsed["checkout"] or data.get("checkout"),
        "adults": data["adults"],
        "children": data.get("children") or [],
        "rooms": data["rooms"],
        "sort": next((k for k, v in refs.SORTS.items() if v == data["sort"]), data["sort"].lower()),
        "storefront": _storefront(country),
        "results": results,
        "pagination": {
            "page": page,
            "items_per_page": limit,
            "has_next": has_next,
            "total_pages": total_pages,
            "total_count": int(total) if isinstance(total, (int, float)) else None,
        },
    }


def hotels_search(**data):
    return cached_call(f"{NS}.search", data, _search, ttl.SEARCH_CACHE)


# ---- hotels/filters ---------------------------------------------------------------------

def _filters(location, checkin, checkout, adults, country):
    destination = _destination(location, None, None, None)
    html = fetch_search_page(destination, checkin, checkout, adults, pos=country)
    state = parsers.apollo_state(html)
    parsed = parsers.parse_filters(state or {})
    if not parsed["filters"]:
        raise HotelsComUpstreamError("search page without a filter block")
    return {"checkin": checkin, "checkout": checkout, "adults": adults, "storefront": _storefront(country), **parsed}


def hotels_filters(**data):
    return cached_call(f"{NS}.filters", data, _filters, ttl.FILTERS_CACHE)


# ---- the shared hotel record ----------------------------------------------------------------

def _hotel_record(hotel, country):
    html, hotel_id = fetch_hotel_page(hotel, pos=country)
    record = parsers.parse_hotel_page(html, hotel_id, pos=country)
    if not record or not record.get("id"):
        raise HotelsComUpstreamError(f"hotel {hotel}: page without a hotel record")
    return record


def _record(hotel, country):
    return cached_call(f"{NS}.hotel", {"hotel": hotel, "country": country}, _hotel_record, ttl.HOTEL_CACHE)


def _identity(record):
    return {"id": record["id"], "hotel_id": record["hotel_id"], "name": record["name"], "link": record["link"]}


# ---- hotels/details --------------------------------------------------------------------------

def hotel_details(hotel, country):
    """Everything on the public hotel page."""
    record = _record(hotel, country)
    return {**_identity(record), "storefront": _storefront(country),
            **{k: v for k, v in record.items() if k not in ("id", "hotel_id", "name", "link")}}


# ---- hotels/rooms ----------------------------------------------------------------------------

def _rooms(hotel, checkin, checkout, adults, children, rooms, country):
    record = _record(hotel, country)
    offers = fetch_offers(record["id"], checkin, checkout, adults, children, rooms, pos=country)
    parsed = parsers.parse_offers(offers, checkin, checkout, pos=country)
    return {**_identity(record), "storefront": _storefront(country), "adults": adults,
            "children": children or [], "rooms_requested": rooms, **parsed}


def hotel_rooms(**data):
    return cached_call(f"{NS}.rooms", data, _rooms, ttl.ROOMS_CACHE)


# ---- hotels/reviews --------------------------------------------------------------------------

def _reviews(hotel, page, limit, sort, query, country):
    record = _record(hotel, country)
    start = (page - 1) * limit
    parsed = parsers.parse_reviews(fetch_reviews(record["id"], start=start, size=limit, sort=sort,
                                                 search_term=query, pos=country))
    # The summary's count is the total for the request: the hotel's reviews,
    # or with a `query` the matching ones (Bellagio: 15,573, "pool": 337,
    # 2026-11-10). Pages can hold a few reviews fewer than asked (47 of 50
    # for "pool"), so the next page follows the count, never a short page.
    total = parsed["summary"]["review_count"]
    if total is None:
        total_pages, total_count = None, None
        has_next = len(parsed["reviews"]) >= limit
    else:
        total_pages, total_count = max(1, math.ceil(total / limit)), total
        has_next = start + limit < total
    return {
        **_identity(record),
        "storefront": _storefront(country),
        "sort": next((k for k, v in refs.REVIEW_SORTS.items() if v == sort), sort),
        "query": query,
        "summary": parsed["summary"],
        "reviews": parsed["reviews"],
        "sort_options": parsed["sort_options"],
        "pagination": {"page": page, "items_per_page": limit, "has_next": has_next,
                       "total_pages": total_pages, "total_count": total_count},
    }


def hotel_reviews(**data):
    return cached_call(f"{NS}.reviews", data, _reviews, ttl.REVIEWS_CACHE)


# ---- hotels/calendar --------------------------------------------------------------------------

def _calendar(hotel, country):
    record = _record(hotel, country)
    parsed = parsers.parse_calendar(fetch_calendar(record["id"], pos=country), refs.POS[country]["currency"])
    return {**_identity(record), "storefront": _storefront(country), **parsed}


def hotel_calendar(**data):
    return cached_call(f"{NS}.calendar", data, _calendar, ttl.CALENDAR_CACHE)
