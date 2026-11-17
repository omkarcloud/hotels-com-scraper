"""Raw hotels.com pages / GraphQL payloads -> clean dicts. Pure functions,
no I/O.

Fixtures (hotels_com/fixtures/, captured 2026-11-07 with curl_cffi):
  hotel_ho434012.html              bare hotel page (Hotel Anastassiou, Kastoria,
                                   2 stars, 76 reviews, 41 photos, 9 FAQs; a
                                   datacenter proxy exit, safari184_ios)
  unknown_hotel.html               where an unknown ho id lands (302 -> the homepage)
  challenge_bot_or_not.html        Expedia's "Bot or Not?" captcha page (HTTP 429)
  search_page_las_vegas.html       server-rendered /Hotel-Search page 1 for Las
                                   Vegas: 22 filter groups + the 7 sorts (USD)
  graphql_search_las_vegas.json    propertySearch "Las Vegas", Jun 11-13 2027,
                                   50 of 285 cards + markers (USD)
  graphql_search_past_end.json     the same search from resultsStartingIndex 5000
  graphql_search_homes_dateless.json  propertySearch "Las Vegas" stay_type=homes, no
                                   dates (2026-11-11): no pagination block, 4 of 50
                                   cards (vacation-rental unit lines before the area)
  graphql_search_tokyo_dateless_old.json  propertySearch "Tokyo", no dates (the
                                   card selection before the 2026-11-07 extension)
  graphql_offers_140596.json       propertyOffers: Bellagio, 15 room types, Jun 11-13 2027
  graphql_reviews_140596.json      reviewInfo: Bellagio, 20 of 15,528 reviews
  graphql_calendar_140596.json     propertyRatesDateSelector: Bellagio, 241 days,
                                   231 with a lowest nightly price
  graphql_offers_22597_cn.json     propertyOffers: Hilton Tokyo, 26 room types
                                   (www.hotels.cn storefront, CNY)
  graphql_reviews_22597_cn.json    reviewInfo, 10 of 1,000 reviews (www.hotels.cn)
  graphql_calendar_without_availability.json  a storefront that serves no day prices
  typeahead_paris.json             /api/v4/typeahead/Paris (regions)
  typeahead_new_york.json          /api/v4/typeahead/New York (regions, from the pod)
  typeahead_hilton_tokyo.json      /api/v4/typeahead/Hilton Tokyo (hotel rows)
The two page fixtures (hotel_ho434012.html, search_page_las_vegas.html) keep
the page title and the window.__APOLLO_STATE__ script, the only parts the
parsers read; the site's other inline scripts (webpack chunk maps) were cut.
The Las Vegas / Bellagio payloads came from a Mac IP on www.hotels.com with
chrome150 (the US storefront's cookies from a hotel-page warm-up; prices in
USD); the pod's exits were refused that afternoon.

Page state: every server-rendered page embeds
`window.__APOLLO_STATE__ = JSON.parse("<JSON-escaped JSON>")`; apollo_state()
unescapes it. Entities are keyed "<Type>:<id>" and referenced as
{"__ref": key}; ROOT_QUERY holds the operation results keyed by
"<field>(<args json>)" or "<field>:<args json>".

The value helpers (parse_amount, apollo_state, …) are the same as
vrbo/parsers.py's: copied, not imported, so the package stands on its own
(the open-source kit ships one site at a time, vrbo's precedent).
"""
import json
import re
from datetime import datetime
from html import unescape
from urllib.parse import urlparse

import fastjson

from hotels_com.refs import DEFAULT_POS, POS, expedia_id_from_link, hotel_id_from_link, hotel_link
from scraper_utils.values import bool_or_none as _flag

# ---- generic helpers -------------------------------------------------------------
_AMOUNT_START_RE = re.compile(r"\d[\d.,  \s]*")


def parse_amount(text):
    """Localized price text -> float: "$1,199" -> 1199.0, "CNY2,296" ->
    2296.0, "8.104,64" -> 8104.64, "1 353 €" -> 1353.0. None when no number."""
    if not text:
        return None
    text = str(text)
    m = _AMOUNT_START_RE.search(text)
    if not m:
        return None
    negative = bool(re.search(r"[-−–]\s*[^\d]*$", text[:m.start()]))
    s = re.sub(r"[\s  ]", "", m.group())
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        if "," not in head and len(tail) in (1, 2):
            s = head + "." + tail
        else:
            s = s.replace(",", "")
    elif s.count(".") > 1 or (s.count(".") == 1 and len(s.rpartition(".")[2]) == 3):
        s = s.replace(".", "")
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if negative else value


_APOLLO_RE = re.compile(r'window\.__APOLLO_STATE__\s*=\s*JSON\.parse\("(.*?)"\);', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\xa0]+")


def apollo_state(html):
    """The page's Apollo cache as a dict, or None when the page carries none."""
    match = _APOLLO_RE.search(html or "")
    if not match:
        return None
    try:
        return fastjson.loads(fastjson.loads('"' + match.group(1) + '"'))
    except ValueError:
        return None


def _deref(state, value):
    if isinstance(value, dict) and "__ref" in value and len(value) == 1:
        return state.get(value["__ref"])
    return value


def _root(state, field):
    """ROOT_QUERY entries whose field name is `field` (any arguments)."""
    out = []
    for key, value in (state.get("ROOT_QUERY") or {}).items():
        name = re.split(r"[(:]", key, 1)[0]
        if name == field:
            out.append(_deref(state, value))
    return out


def _first_root(state, field, predicate=None):
    for value in _root(state, field):
        if value is not None and (predicate is None or predicate(value)):
            return value
    return None


def _get(obj, *path, default=None):
    for key in path:
        if isinstance(obj, dict):
            obj = obj.get(key)
        elif isinstance(obj, list) and isinstance(key, int) and -len(obj) <= key < len(obj):
            obj = obj[key]
        else:
            return default
        if obj is None:
            return default
    return obj


def _text(markup):
    """HTML markup -> plain text with newlines kept for <br> / </p> / </li>."""
    if not markup:
        return None
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>", "\n", str(markup))
    text = unescape(_TAG_RE.sub("", text))
    text = "\n".join(_WS_RE.sub(" ", line).strip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _items_of_markup(markup):
    """"<ul><li>a</li><li>b</li></ul>" -> ["a", "b"]; plain markup -> [text]."""
    if not markup:
        return []
    items = re.findall(r"(?is)<li[^>]*>(.*?)</li>", str(markup))
    if items:
        return [t for t in (_text(i) for i in items) if t]
    text = _text(markup)
    return [text] if text else []


_BIDI_RE = re.compile(r"[‎‏‪-‮⁦-⁩]")


def _clean(value):
    if value is None:
        return None
    value = _WS_RE.sub(" ", _BIDI_RE.sub("", str(value))).strip()
    return value or None


def _int(text):
    match = re.search(r"\d[\d,]*", str(text or ""))
    return int(match.group().replace(",", "")) if match else None


def _float(text):
    match = re.search(r"-?\d+(?:\.\d+)?", str(text or ""))
    return float(match.group()) if match else None


_CURRENCY_CODES = ("USD", "EUR", "GBP", "AUD", "CAD", "NZD", "SGD", "JPY", "CNY", "HKD", "INR", "BRL", "MXN",
                   "CHF", "SEK", "NOK", "DKK", "KRW", "THB", "MYR", "IDR", "PHP", "TWD", "AED", "SAR", "ZAR",
                   "PLN", "CZK", "HUF", "TRY", "ILS", "ARS", "CLP", "COP", "VND")
_CURRENCY_SYMBOLS = (("A$", "AUD"), ("C$", "CAD"), ("NZ$", "NZD"), ("S$", "SGD"), ("R$", "BRL"), ("HK$", "HKD"),
                     ("MX$", "MXN"), ("US$", "USD"), ("$", "USD"), ("€", "EUR"), ("£", "GBP"), ("₹", "INR"),
                     ("¥", "JPY"), ("₩", "KRW"), ("฿", "THB"), ("kr", "SEK"), ("CHF", "CHF"), ("zł", "PLN"))


def currency_of(text, default="USD"):
    text = str(text or "")
    for code in _CURRENCY_CODES:
        if re.search(rf"(?<![A-Za-z]){code}(?![A-Za-z])", text):   # "CNY2,296", "2,296 CNY"
            return code
    for symbol, code in _CURRENCY_SYMBOLS:
        if symbol in text:
            return code
    return default


def _money(text, default_currency="USD"):
    """"$1,199" -> (1199.0, "USD"); None when no number."""
    amount = parse_amount(text)
    if amount is None:
        return None, None
    return amount, currency_of(text, default_currency)


def _date(parts):
    """{day, month, year} -> ISO date."""
    try:
        return f"{int(parts['year']):04d}-{int(parts['month']):02d}-{int(parts['day']):02d}"
    except (TypeError, KeyError, ValueError):
        return None


def _date_text(text):
    """"Sep 2, 2026" -> "2026-09-02"; the UK storefront writes "Sept"."""
    value = re.sub(r"\bSept\b", "Sep", _clean(text) or "")
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _nights(text):
    match = re.search(r"(\d+)[\s-]*night", str(text or ""))
    return int(match.group(1)) if match else None


def _texts(items):
    """[{text} | {value} | str] -> [clean strings]."""
    out = []
    for item in items or []:
        if isinstance(item, dict):
            value = _clean(item.get("text") or item.get("value"))
        else:
            value = _clean(item)
        if value:
            out.append(value)
    return out


def _currency_for(pos):
    return POS.get(pos, POS[DEFAULT_POS])["currency"]


# ---- typeahead ---------------------------------------------------------------------

def parse_autocomplete(payload, pos=DEFAULT_POS):
    """typeahead JSON -> [{type, region_id | id, name, short_name, country, coordinates, ...}]:
    regions (CITY / NEIGHBORHOOD / AIRPORT / POI / MULTICITY / TRAINSTATION)
    carry region_id (the search `location`), hotels carry the Expedia id
    (the `hotel` reference) and an address."""
    out = []
    for item in (payload or {}).get("sr") or []:
        if not isinstance(item, dict):
            continue
        names = item.get("regionNames") or {}
        coords = item.get("coordinates") or {}
        hierarchy = item.get("hierarchyInfo") or {}
        country = hierarchy.get("country") or {}
        airport = hierarchy.get("airport") or {}
        lat, lng = _float(coords.get("lat")), _float(coords.get("long"))
        kind = (_clean(item.get("type")) or "").lower() or None
        row = {
            "type": kind,
            "region_id": None,
            "id": None,
            "name": _clean(names.get("fullName")),
            "short_name": _clean(names.get("shortName")),
            "country": {"name": _clean(country.get("name")), "code": _clean(country.get("isoCode2")),
                        "code_3": _clean(country.get("isoCode3"))},
            "coordinates": {"latitude": lat, "longitude": lng} if lat is not None and lng is not None else None,
            "nearest_airport": _clean(airport.get("airportCode")),
            "link": None,
            "address": None,
            "city_region_id": None,
        }
        if kind == "hotel":
            expedia_id = _clean(item.get("hotelId"))
            address = item.get("hotelAddress") or {}
            row.update({
                "id": expedia_id,
                "link": f"{POS[pos]['host']}/h{expedia_id}.Hotel-Information" if expedia_id else None,
                "address": {"street": _clean(address.get("street")), "city": _clean(address.get("city")),
                            "region": _clean(address.get("province"))} if address else None,
                "city_region_id": _clean(item.get("cityId")),
            })
        else:
            row["region_id"] = _clean(item.get("gaiaId"))
            if kind == "airport":
                row["is_major_airport"] = item.get("isMinorAirport") == "false"
        out.append(row)
    return out


# ---- search cards ------------------------------------------------------------------

_REVIEW_COUNT_RE = re.compile(r"(\d[\d,]*)\s+reviews?\b", re.I)
_AREA_DISTANCE_RE = re.compile(r"^(.*?),\s*(\d[\d.,]*\s*(?:mi|km|miles?|kilometers?)\b.*)$", re.I)
# A vacation-rental card lists its unit before its area: "Entire apartment",
# "Sleeps 4 · 1 bedroom · 1 bathroom", then "Paradise" (2026-11-11).
_UNIT_LINE_RE = re.compile(r"^(?:(?:entire|private|shared)(?:\s+[a-z]+){1,3}$|sleeps\s+\d|\d+\s+(?:bed|bedroom|bath|bathroom)s?\b)", re.I)
_NIGHTLY_LINE_RE = re.compile(r"^\s*\D{0,5}\d[\d,.]*\s*(?:nightly|per night|/\s*night)\s*$", re.I)
_DATES_RE = re.compile(r"\b[A-Z][a-z]{2} \d{1,2}\s*-\s*[A-Z][a-z]{2} \d{1,2}\b")


def _price_lines(price_summary, default_currency="USD", year=None):
    """A PropertyPrice block -> {nightly, total, original_total, nights,
    currency, is_taxes_included, dates_text}. hotels.com cards show
    "$120 nightly" as a qualifier line and the LEAD price as "$303 total"
    with the STRIKEOUT beside it. `year` is the check-in year when the
    caller knows it (the dates line names none, see _nights_between)."""
    out = {"nightly": None, "total": None, "original_total": None, "nights": None,
           "currency": None, "is_taxes_included": None, "dates_text": None}
    if not isinstance(price_summary, dict):
        return out
    lines = []
    for message in price_summary.get("displayMessages") or []:
        for item in (message or {}).get("lineItems") or []:
            if not isinstance(item, dict):
                continue
            if item.get("__typename") == "DisplayPrice" or "role" in item:
                formatted = _get(item, "price", "formatted")
                role = item.get("role")
                amount, cur = _money(formatted, default_currency)
                if role == "LEAD" and amount is not None:
                    out["total"], out["currency"] = amount, cur
                    if "night" in str(formatted).lower() and "total" not in str(formatted).lower():
                        out["nightly"], out["total"] = amount, None
                elif role == "STRIKEOUT" and amount is not None:
                    out["original_total"] = amount
                elif formatted:
                    lines.append(formatted)
            else:
                value = item.get("value")
                if value:
                    lines.append(value)
    for option in price_summary.get("options") or []:
        amount, cur = _money(_get(option, "displayPrice", "formatted"), default_currency)
        if amount is not None and out["total"] is None and out["nightly"] is None:
            out["total"], out["currency"] = amount, cur
        strike, _ = _money(_get(option, "strikeOut", "formatted"), default_currency)
        if strike is not None and out["original_total"] is None:
            out["original_total"] = strike
    for value in (price_summary.get("priceMessaging") or []):
        if isinstance(value, dict) and value.get("value"):
            lines.append(value["value"])
    for line in lines:
        low = line.lower()
        if "nightly" in low or "per night" in low:
            # only a line that IS a nightly price ("$53 nightly"), never a
            # loyalty / discount line that mentions a night ("earn $8 ... per
            # night" read as the nightly rate on Luxor, 2026-11-10); first wins
            amount, cur = _money(line, default_currency)
            if amount is not None and out["nightly"] is None and _NIGHTLY_LINE_RE.match(line):
                out["nightly"] = amount
                out["currency"] = out["currency"] or cur
        elif "taxes and fees" in low or "includes taxes" in low:
            out["is_taxes_included"] = "total with taxes" in low or "includes" in low
        elif re.match(r"^for\s+\d+\s+nights?", low):
            out["nights"] = _nights(line)
        elif _DATES_RE.search(line):
            out["dates_text"] = line
    if out["nights"] is None:
        # from the stay's own dates ("Dec 9 - Dec 10" = 1 night); never from
        # total / nightly, which resort fees and taxes skew (Bellagio: $136
        # nightly, $216 total for ONE night, 2026-11-10)
        out["nights"] = _nights_between(out["dates_text"], year)
    return out


_DATES_SPAN_RE = re.compile(r"\b([A-Z][a-z]{2}) (\d{1,2})\s*-\s*([A-Z][a-z]{2}) (\d{1,2})\b")


def _nights_between(text, year=None):
    """"Dec 9 - Dec 10" -> 1, "Dec 30 - Jan 2" -> 3; None when no span.
    The line names no year and February's length hangs on it ("Feb 28 -
    Mar 1" is one night, two in a leap year): `year` is the check-in year
    when the caller knows it (a search echoes its dates), else the stay is
    taken as the next one from today."""
    match = _DATES_SPAN_RE.search(str(text or ""))
    if not match:
        return None
    today = datetime.now()
    for candidate in ([year] if year else range(today.year, today.year + 9)):
        try:
            start = datetime.strptime(f"{match.group(1)} {match.group(2)} {candidate}", "%b %d %Y")
            end = datetime.strptime(f"{match.group(3)} {match.group(4)} {candidate}", "%b %d %Y")
            if end <= start:
                end = end.replace(year=candidate + 1)      # the stay crosses New Year
        except ValueError:
            continue                                       # Feb 29 outside a leap year
        if year or (today - start).days <= 1:              # a day of slack for the site's own "today"
            return (end - start).days or None
    return None


def _neighborhood(location_info):
    """locationInfo.primaryText "Le Marais" + subText "0.9 mi from city center"."""
    primary = _clean(_get(location_info, "primaryText", "text"))
    sub = _clean(_get(location_info, "subText"))
    return primary, sub


def parse_card(card, markers=None, pos=DEFAULT_POS, year=None):
    """One LodgingCard (+ its map marker) -> a result row. `year` is the
    search's check-in year (for the nights of the card's dates line)."""
    markers = markers or {}
    default_currency = _currency_for(pos)
    expedia_id = _clean(card.get("id"))
    link = _get(card, "cardLink", "resource", "value")
    hotel_id = hotel_id_from_link(link)
    expedia_id = expedia_id or expedia_id_from_link(link)
    heading = card.get("headingSection") or {}
    messages = _texts(heading.get("messages"))
    featured = _texts(heading.get("featuredMessages"))
    heading_amenities = _texts(heading.get("amenities"))
    neighborhood, distance_text = _neighborhood(heading.get("locationInfo"))
    star = _get(heading, "productRating", "rating")
    review = None
    summary_amenities, summary_messages, highlights, footers, description = [], [], [], [], None
    for section in card.get("summarySections") or []:
        if not isinstance(section, dict):
            continue
        if section.get("reviewSummary") and review is None:
            review = section["reviewSummary"]
        summary_amenities += _texts(section.get("amenities"))
        summary_messages += _texts(section.get("messages"))
        for item in _get(section, "highlightMessages", "items") or []:     # EGDSSpannableListItem
            text = _clean(_get(item, "text", "text")) if isinstance(item, dict) else None
            if text:
                highlights.append(text)
        footers += _texts(_get(section, "footerMessages", "listItems"))
        description = description or _clean(_get(section, "descriptionSection", "description", "text"))
    score_text = _get(review, "graphic", "text")
    title_text = _get(review, "title", "shoppingProductTitle", "text")
    score = _float(score_text) if score_text and score_text.lower() != "new" else None
    label = _clean(title_text) if title_text and not _REVIEW_COUNT_RE.search(title_text) else None
    review_count = None
    texts = [title_text, _get(review, "title", "shoppingProductTitle", "accessibility")]
    for sub in (review or {}).get("subtexts") or []:
        texts += [_get(sub, "shoppingProductTitle", "text"), _get(sub, "shoppingProductTitle", "accessibility"),
                  _get(sub, "title", "text"), _get(sub, "accessibilityText")]
    match = next((m for m in (_REVIEW_COUNT_RE.search(t or "") for t in texts) if m), None)
    if match:
        review_count = int(match.group(1).replace(",", ""))
    price = _price_lines(_get(card, "priceSection", "priceSummary"), default_currency, year)
    discount_badge = _clean(_get(card, "priceSection", "standardBadge", "standardBadge", "text")
                            or _get(card, "priceSection", "badge", "text"))
    media_badges = [b.get("text") for b in ((_get(card, "mediaSection", "badges") or {}).values())
                    if isinstance(b, dict) and b.get("text")]
    badges = [t for t in media_badges + _texts(heading.get("standardBadges")) + _texts(heading.get("titleBadges"))
              if t and t != discount_badge]
    photos = []
    for item in _get(card, "mediaSection", "gallery", "media") or []:
        url = _get(item, "media", "url")
        if url:
            photos.append({"link": url, "caption": _clean(_get(item, "media", "description"))})
    marker = markers.get(expedia_id) or {}
    position = marker.get("markerPosition") or {}
    lines = messages + featured + summary_messages
    # The first message under the heading is the area the card places the
    # hotel in ("Las Vegas Strip"), sometimes with its distance from the
    # searched place ("Green Valley, 11.2 mi from Las Vegas", 2026-11-07).
    # Vacation rentals put their unit lines first; those stay in `messages`.
    area = None
    first = next((m for m in messages if not _UNIT_LINE_RE.search(m)), None)
    if first and not neighborhood and not re.search(r"refundable|cancell|pay ", first, re.I):
        match = _AREA_DISTANCE_RE.match(first)
        if match:
            area, distance_text = match.group(1).strip(), distance_text or match.group(2).strip()
        else:
            area = first
        lines.remove(first)
    all_amenities = []
    for text in heading_amenities + summary_amenities:
        if text not in all_amenities:
            all_amenities.append(text)
    free_cancellation = next((t for t in lines + highlights + footers
                              if "free cancellation" in t.lower() or "fully refundable" in t.lower()), None)
    return {
        "id": expedia_id,
        "hotel_id": hotel_id,
        "name": _clean(heading.get("heading")) or _clean(marker.get("name")),
        "link": hotel_link(hotel_id, pos) if hotel_id else link,
        "star_rating": float(star) if isinstance(star, (int, float)) else _float(star),
        "area": area or neighborhood,
        "neighborhood": neighborhood,
        "distance_text": distance_text,
        "description": description,
        "rating": {"score": score, "max": 10, "label": label, "review_count": review_count,
                   "is_new": (score_text or "").lower() == "new"},
        "price": {"nightly": price["nightly"], "total": price["total"], "original_total": price["original_total"],
                  "nights": price["nights"], "currency": price["currency"],
                  "is_taxes_included": price["is_taxes_included"], "dates_text": price["dates_text"],
                  "discount_badge": discount_badge},
        "amenities": all_amenities,
        "highlights": [t for t in highlights if t != free_cancellation],
        "free_cancellation_text": free_cancellation,
        "is_sponsored": any(b.lower() in ("ad", "sponsored") for b in media_badges),
        "badges": badges,
        "coordinates": ({"latitude": position.get("latitude"), "longitude": position.get("longitude")}
                        if position.get("latitude") is not None else None),
        "is_available": (marker.get("markerStatus") == "AVAILABLE") if marker.get("markerStatus") else None,
        "photos": photos,
        "messages": lines + footers,
    }


def _results_heading(text):
    """The results heading without the site's unfilled date template: a
    dateless search reads "44 Properties in Las Vegas, Nevada, United States
    of America. ${startDate} through ${endDate}" (2026-11-11)."""
    text = _clean(text)
    if not text or "${" not in text:
        return text
    kept = [part for part in re.split(r"(?<=\.)\s+", text) if "${" not in part]
    return _clean(" ".join(kept).rstrip(".")) or None


def parse_search(result, pos=DEFAULT_POS):
    """propertySearch payload -> {location, checkin, checkout, total_count,
    has_next, has_paging, next_start, results}. `has_paging` is False when the
    payload carries no pagination block at all (vacation-rental searches,
    some filtered pages): has_next then says nothing."""
    result = result or {}
    markers = {}
    for marker in _get(result, "dynamicMap", "map", "markers") or []:
        if isinstance(marker, dict) and marker.get("id"):
            markers[str(marker["id"])] = marker
    criteria = _get(result, "criteria", "primary") or {}
    destination = criteria.get("destination") or {}
    dates = criteria.get("dateRange") or {}
    year = _get(dates, "checkInDate", "year")
    year = year if isinstance(year, int) and not isinstance(year, bool) else None
    rows = [parse_card(card, markers, pos, year) for card in result.get("propertySearchListings") or []
            if isinstance(card, dict) and card.get("__typename") == "LodgingCard"]
    pagination = result.get("pagination") or {}
    next_subset = _get(pagination, "subSets", "nextSubSet") or {}
    return {
        "location": {"region_id": _clean(destination.get("regionId")),
                     "name": _clean(destination.get("regionName"))},
        "checkin": _date(dates.get("checkInDate")) if dates else None,
        "checkout": _date(dates.get("checkOutDate")) if dates else None,
        "total_count": _get(result, "summary", "matchedPropertiesSize"),
        "heading": _results_heading(_get(result, "summary", "resultsHeading")),
        "has_next": bool(next_subset.get("startingIndex") is not None),
        "has_paging": isinstance(result.get("pagination"), dict),
        "next_start": next_subset.get("startingIndex"),
        "results": rows,
    }


# ---- filters (server-rendered search page) ------------------------------------------

def _search_root(state):
    return _first_root(state, "propertySearch", lambda v: isinstance(v, dict) and v.get("universalSortAndFilter"))


def _option(state, opt):
    """One filter option. `primary` carries the count ("Pet friendly (122)"),
    `secondary` the cheapest nightly price among its matches ("$263"),
    shown on the site as "From $263"."""
    opt = _deref(state, opt) or {}
    label = _clean(opt.get("primary"))
    count = None
    match = re.search(r"\((\d[\d,]*)\)\s*$", label or "")
    if match:
        count = int(match.group(1).replace(",", ""))
        label = label[:match.start()].strip()
    return {"value": _clean(opt.get("value")), "label": label, "count": count,
            "price_from": parse_amount(opt.get("secondary")),
            "is_default": bool(opt.get("default")), "is_selected": bool(opt.get("selected"))}


def _field_id(state, field):
    """A field's criteria id: its own `id`, or (ShoppingSelectionField, whose
    field carries none) the id every one of its options repeats."""
    if field.get("id"):
        return field["id"]
    for opt in field.get("options") or []:
        opt = _deref(state, opt) or {}
        if opt.get("id"):
            return opt["id"]
    return None


def _sort_options(state, usf):
    """The sort dropdown sits in sortSections (hotels.com), or among the
    filter sections (vrbo's layout)."""
    sections = list(usf.get("sortSections") or []) + list(usf.get("filterSections") or [])
    for section in sections:
        for field in (section or {}).get("fields") or []:
            field = _deref(state, field) or {}
            if field.get("__typename") == "ShoppingDropdownField":
                return [{"value": _clean(o.get("value")), "label": _clean(o.get("primary")),
                         "is_default": bool(o.get("default"))}
                        for o in (_deref(state, o) or {} for o in field.get("options") or []) if o.get("value")]
    return []


def parse_filters(state):
    """The search page's universalSortAndFilter -> {location, total_count,
    filters: [...], sort_options: [...]}."""
    root = _search_root(state) or {}
    usf = root.get("universalSortAndFilter") or {}
    filters = []
    for section in usf.get("filterSections") or []:
        for field in (section or {}).get("fields") or []:
            field = _deref(state, field) or {}
            kind = field.get("__typename")
            if kind in ("ShoppingMultiSelectionField", "ShoppingSelectionField"):
                filters.append({"id": _field_id(state, field), "label": _clean(field.get("primary")),
                                "type": "multi_select" if kind == "ShoppingMultiSelectionField" else "single_select",
                                "options": [_option(state, o) for o in field.get("options") or []]})
            elif kind == "ShoppingRangeTextInputField":
                chars = _get(field, "range", "characteristics") or {}
                filters.append({"id": field.get("id"), "label": _clean(field.get("primary")), "type": "price_range",
                                "options": [{"value": field.get("id"), "label": None, "min": chars.get("min"),
                                             "max": chars.get("max"), "step": chars.get("step"),
                                             "min_label": _clean(chars.get("minLabel")),
                                             "max_label": _clean(chars.get("maxLabel")),
                                             "histogram": _get(field, "histogram", "buckets")}]})
            elif kind == "ShoppingStepInputField":
                filters.append({"id": field.get("id"), "label": _clean(field.get("primary")),
                                "type": "minimum_count", "min": field.get("min"), "max": field.get("max"),
                                "step": field.get("step"), "options": []})
            elif kind == "ShoppingRangeField":
                rng = field.get("range") or {}
                chars = rng.get("characteristics") or {}
                filters.append({"id": field.get("id"), "label": _clean(field.get("primary")), "type": "price_range",
                                "options": [{"value": field.get("id"), "label": None, "min": chars.get("min"),
                                             "max": chars.get("max"), "step": chars.get("step"),
                                             "max_label": _clean(chars.get("maxLabel")),
                                             "histogram": _get(field, "histogram", "buckets")}]})
            elif kind == "ShoppingMutexField":
                options = []
                for choice in field.get("options") or []:
                    option = (choice or {}).get("option") or {}
                    rng = option.get("range") or {}
                    chars = rng.get("characteristics") or {}
                    options.append({"value": option.get("id"), "label": None,
                                    "min": chars.get("min"), "max": chars.get("max"),
                                    "step": chars.get("step"),
                                    "max_label": _clean(chars.get("maxLabel")),
                                    "histogram": _get(option, "histogram", "buckets")})
                filters.append({"id": field.get("id"), "label": _clean(field.get("primary")),
                                "type": "price_range", "options": options})
            elif kind == "ShoppingTextInputField":
                filters.append({"id": field.get("id"),
                                "label": _clean(field.get("primary")) or _clean(field.get("secondary")),
                                "type": "text", "options": []})
    criteria = _get(root, "criteria", "primary") or {}
    destination = criteria.get("destination") or {}
    return {
        "location": {"region_id": _clean(destination.get("regionId")),
                     "name": _clean(destination.get("regionName"))},
        "total_count": _get(root, "summary", "matchedPropertiesSize"),
        "filters": filters,
        "sort_options": _sort_options(state, usf),
    }


# ---- reviews --------------------------------------------------------------------------

_RESPONSE_HEADER_RE = re.compile(r"^Response from (.+?) on (.+)$")
_STAY_RE = re.compile(r"(\d+)-night\b", re.I)


def _score(value):
    """"8/10 Very good" -> (8.0, "Very good")."""
    match = re.match(r"\s*(\d+(?:\.\d+)?)\s*/\s*(\d+)\s*(.*)$", str(value or ""))
    if not match:
        return None, _clean(value)
    return float(match.group(1)), _clean(match.group(3)) or None


def parse_review(review):
    score, label = _score(_get(review, "reviewScoreWithDescription", "value"))
    responses = []
    for resp in review.get("managementResponses") or []:
        if not isinstance(resp, dict) or not resp.get("response"):
            continue
        header = _clean(_get(resp, "header", "text")) or ""
        match = _RESPONSE_HEADER_RE.match(header)
        responses.append({"responder": match.group(1) if match else None,
                          "date": _date_text(match.group(2)) if match else None,
                          "text": _text(resp.get("response"))})
    stay = _clean(review.get("stayDuration"))     # "Jongwoong, 3-night trip"
    stay_match = _STAY_RE.search(stay or "")
    author = _clean(_get(review, "reviewAuthorAttribution", "text"))
    return {
        "id": review.get("id"),
        "score": score,
        "max": 10,
        "label": _clean(review.get("superlative")) or label,
        "title": _clean(review.get("title")),
        "text": _text(review.get("text")),
        "author": author,
        "date": _date_text(review.get("submissionTimeLocalized")),
        "stay": {"text": stay, "nights": int(stay_match.group(1)) if stay_match else None},
        "is_verified": (review.get("disclaimer") or "").lower() == "verified review",
        "language": review.get("locale"),
        "source": _clean(_get(review, "propertyReviewSource", "text", "value")),
        "photos": [{"link": p.get("url"), "caption": _clean(p.get("description"))}
                   for p in review.get("photos") or [] if isinstance(p, dict) and p.get("url")],
        "management_responses": responses,
    }


def parse_reviews(info):
    """reviewInfo payload -> {summary, reviews, sort_options}.

    hotels.com leaves the review's travelers, themes and helpful votes empty
    (about 700 reviews of 6 hotels, 2026-11-10) and ignores the review
    overlay's traveler-type filter (the same reviews come back), so neither
    is read or offered."""
    info = info or {}
    summary = info.get("summary") or {}
    categories = []
    for detail in summary.get("reviewSummaryDetails") or []:
        if not isinstance(detail, dict):
            continue
        score, _ = _score(detail.get("formattedRatingOutOfMax"))
        categories.append({"label": _clean(detail.get("label")), "score": score,
                           "percentage": detail.get("ratingPercentage")})
    total = _get(summary, "totalCount", "raw")
    avg = _get(summary, "averageOverallRating", "raw")
    sorts = []
    for block in _get(info, "sortAndFilter", "sortAndFilter") or []:
        if (block or {}).get("name") == "sortBy":
            sorts = [{"value": o.get("optionValue"), "label": _clean(o.get("label"))}
                     for o in block.get("options") or [] if isinstance(o, dict)]
    return {
        "summary": {
            "score": round(float(avg), 1) if avg is not None else None,
            "max": 10,
            "label": _clean(summary.get("superlative")),
            "review_count": int(total) if total is not None else None,
            "categories": categories,
        },
        "reviews": [parse_review(r) for r in info.get("reviews") or [] if isinstance(r, dict)],
        "sort_options": sorts,
    }


# ---- calendar -----------------------------------------------------------------------------

def _validity(value):
    value = str(value or "")
    if value == "VALID":
        return None
    return value.replace("INVALID_DUE_TO_", "").lower() or None


def _valid(value):
    return None if value is None else value == "VALID"


def parse_calendar(selector, default_currency="USD"):
    """propertyRatesDateSelector -> {range, has_prices, summary, days[]}.

    hotels.com's calendar is a PRICE calendar (template WITHOUT_AVAILABILITY,
    verified 2026-11-07 on Bellagio: 241 days, a lowest nightly price on 231,
    "––" on the rest): `available`, the check-in / check-out validity and the
    stay constraints come back null for hotels, so those keys are null here
    too — never a guessed false. A storefront that serves no day prices at
    all (hotels.cn) returns no days."""
    selector = selector or {}
    conf = selector.get("configuration") or {}
    days = []
    for day in selector.get("days") or []:
        if not isinstance(day, dict):
            continue
        iso = _date(day.get("date") or {})
        if not iso:
            continue
        amount, cur = _money(day.get("displayPrice"), default_currency)
        constraints = day.get("stayConstraints") or {}
        max_stay = constraints.get("maximumStayInDays")
        days.append({
            "date": iso,
            "price": amount,
            "currency": cur if amount is not None else None,
            "is_available": _flag(day.get("available")),
            "can_check_in": _valid(day.get("checkinValidity")),
            "can_check_out": _valid(day.get("checkoutValidity")),
            "check_in_blocked_by": _validity(day.get("checkinValidity")),
            "check_out_blocked_by": _validity(day.get("checkoutValidity")),
            "min_stay": constraints.get("minimumStayInDays"),
            "max_stay": max_stay if max_stay else None,
        })
    priced = [d for d in days if d["price"] is not None]
    prices = [d["price"] for d in priced]
    reports_availability = any(d["is_available"] is not None for d in days)
    return {
        "range": {"from": _date(conf.get("beginDate") or {}), "to": _date(conf.get("endDate") or {}),
                  "today": _date(conf.get("currentDate") or {})},
        "has_prices": bool(priced),
        "summary": {
            "days": len(days),
            "priced_days": len(priced),
            "available_days": len([d for d in days if d["is_available"]]) if reports_availability else None,
            "min_price": min(prices) if prices else None,
            "max_price": max(prices) if prices else None,
            "average_price": round(sum(prices) / len(prices), 2) if prices else None,
            "cheapest_date": min(priced, key=lambda d: d["price"])["date"] if priced else None,
            "currency": next((d["currency"] for d in priced), None),
        },
        "message": _clean(selector.get("validationMessage")),
        "days": days,
    }


# ---- rooms and rates ----------------------------------------------------------------------------

_FEATURE_SPLIT = re.compile(r"\s*[•·|]\s*")
_AREA_RE = re.compile(r"(\d[\d.,]*)\s*(sq\s*m|m²|sq\s*ft|ft²)", re.I)
_SLEEPS_RE = re.compile(r"sleeps\s*(\d+)", re.I)
_BED_RE = re.compile(r"^\d+\s+\S.*\b(bed|beds|futon|sofa|bunk|cot|crib)s?\b", re.I)


def _unit_features(features):
    """["28 sq m • Sleeps 2 • 1 King Bed", "City view"] -> size / sleeps /
    beds / views / the rest."""
    out = {"size_sqm": None, "size_sqft": None, "sleeps": None, "beds": [], "views": [], "other": []}
    for text in _texts(features):
        for part in _FEATURE_SPLIT.split(text):
            part = part.strip()
            if not part:
                continue
            area = _AREA_RE.search(part)
            sleeps = _SLEEPS_RE.search(part)
            if area:
                value = parse_amount(area.group(1))
                if "ft" in area.group(2).lower():
                    out["size_sqft"] = value
                    out["size_sqm"] = out["size_sqm"] or (round(value * 0.09290304, 1) if value else None)
                else:
                    out["size_sqm"] = value
                    out["size_sqft"] = out["size_sqft"] or (round(value * 10.7639, 0) if value else None)
            elif sleeps:
                out["sleeps"] = int(sleeps.group(1))
            elif _BED_RE.match(part):
                out["beds"].append(part)
            elif part.lower().endswith("view"):
                out["views"].append(part)
            else:
                out["other"].append(part)
    return out


def _enriched(messages):
    """[LodgingEnrichedMessage | LodgingPlainMessage | dialog] -> [text]."""
    out = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        text = _clean(item.get("value") or _get(item, "trigger", "value") or item.get("text"))
        if text and text not in out:
            out.append(text)
    return out


def _rate_price(detail, default_currency):
    price = detail.get("price") or {}
    option = next((o for o in price.get("options") or [] if isinstance(o, dict)), None) or {}
    amount = _get(option, "displayPrice", "amount")
    formatted = _get(option, "displayPrice", "formatted") or _get(price, "lead", "formatted")
    if amount is None:
        amount = _get(price, "lead", "amount")
    if amount is None:
        amount, _ = _money(formatted, default_currency)
    strike = _get(option, "strikeOut", "amount")
    if strike is None:
        strike, _ = _money(_get(option, "strikeOut", "formatted"), default_currency)
    currency = currency_of(formatted, default_currency) if formatted else default_currency
    return {
        "amount": float(amount) if isinstance(amount, (int, float)) else amount,
        "original_amount": float(strike) if isinstance(strike, (int, float)) else strike,
        "currency": currency if amount is not None else None,
        "caption": _clean(option.get("leadingCaption")),
        "disclaimer": _clean(_get(option, "disclaimer", "value")),
        "message": " ".join(_enriched(price.get("priceMessaging"))) or None,
        "reassurance": _clean(_get(price, "reassuranceMessage", "value")),
    }


def _payment_options(plan, detail):
    out = []
    for policy in plan.get("paymentPolicy") or []:
        if not isinstance(policy, dict):
            continue
        notes = []
        for desc in policy.get("descriptions") or []:
            notes += _texts((desc or {}).get("items"))
        out.append({"type": _clean(policy.get("paymentType")), "label": _clean(policy.get("heading")),
                    "price": _clean(_get(policy, "price", "lead", "formatted")), "notes": notes})
    return out


def _rate_plan(plan, default_currency):
    details = [d for d in plan.get("priceDetails") or [] if isinstance(d, dict)]
    detail = details[0] if details else {}
    key = next((k for k in detail.get("propertyNaturalKeys") or [] if isinstance(k, dict)), None) or {}
    messages = _enriched(plan.get("highlightedMessages"))
    cancellation = next((m for m in messages if re.search(r"refund|cancel", m, re.I)), None)
    return {
        "id": _clean(plan.get("id")) or _clean(key.get("ratePlanId")),
        "name": _clean(plan.get("name")),
        "description": _clean(plan.get("description")),
        "price": _rate_price(detail, default_currency),
        "payment_model": _clean(detail.get("paymentModel")),
        "is_pay_at_property": bool(detail.get("hotelCollect")) if detail else None,
        "is_card_required": (not detail.get("noCreditCard")) if detail else None,
        "inventory_type": _clean(key.get("inventoryType")),
        "rate_plan_type": _clean(key.get("ratePlanType")),
        "deal": _clean(detail.get("dealMarker")),
        "scarcity_message": _clean(_get(detail, "availability", "scarcityMessage")),
        "cancellation_policy": cancellation,
        "messages": [m for m in messages if m != cancellation],
        "payment_options": _payment_options(plan, detail),
        "other_prices": [_rate_price(d, default_currency) | {"payment_model": _clean(d.get("paymentModel"))}
                         for d in details[1:]],
        "checkin": _date(key.get("checkIn")) if key.get("checkIn") else None,
        "checkout": _date(key.get("checkOut")) if key.get("checkOut") else None,
    }


def _selection(option, default_currency):
    option = option or {}
    amount, cur = _money(option.get("price"), default_currency)
    return {"id": _clean(option.get("optionId")), "label": _clean(option.get("description")),
            "note": _clean(option.get("subText")), "price_difference": amount,
            "price_difference_text": _clean(option.get("price")),
            "currency": cur if amount is not None else None,
            "is_selected": bool(option.get("selected")), "is_available": option.get("enabled") is not False}


def parse_unit(unit, default_currency="USD"):
    """One LodgingCategorizedUnit -> a room type with its rate plans and
    the refundability / extras choices."""
    features = _unit_features(unit.get("features"))
    photos, amenity_groups, space, details_items = [], [], [], []
    plans, refund_options, extras = [], [], []
    selections = [s for s in unit.get("primarySelections") or [] if isinstance(s, dict)]
    for sel in selections:
        pu = sel.get("propertyUnit") or {}
        for image in _get(pu, "unitGallery", "images") or []:
            url = _get(image, "image", "url")
            if url and all(p["link"] != url for p in photos):
                photos.append({"link": url, "caption": _clean(_get(image, "image", "description"))})
        for sub in _get(pu, "roomAmenities", "bodySubSections") or []:
            for content in (sub or {}).get("contents") or []:
                name = _clean(_get(content, "header", "text"))
                if name and name not in amenity_groups:
                    amenity_groups.append(name)
        for sp in _get(pu, "spaceDetails", "spaces") or []:
            if isinstance(sp, dict) and sp.get("name"):
                space.append({"name": _clean(sp.get("name")), "description": _clean(sp.get("description"))})
        for block in _get(pu, "detailsDialog", "content", "details", "contents") or []:
            items = _texts((block or {}).get("items"))
            if items:
                details_items.append({"category": _clean(block.get("heading")), "items": items})
        for plan in pu.get("ratePlans") or []:
            if isinstance(plan, dict):
                plans.append(_rate_plan(plan, default_currency))
        for group in sel.get("secondarySelections") or []:
            if not isinstance(group, dict):
                continue
            option = _selection(group.get("secondarySelection"), default_currency)
            option["is_recommended"] = bool(group.get("recommendedSelection"))
            refund_options.append(option)
            for extra in group.get("tertiarySelections") or []:
                extras.append(_selection(extra, default_currency))
    prices = [p["price"]["amount"] for p in plans if p["price"]["amount"] is not None]
    lead = min(plans, key=lambda p: p["price"]["amount"] if p["price"]["amount"] is not None else float("inf")) if plans else None
    return {
        "id": _clean(unit.get("unitId")),
        "name": _clean(_get(unit, "header", "text")),
        "size_sqm": features["size_sqm"],
        "size_sqft": features["size_sqft"],
        "sleeps": features["sleeps"],
        "beds": features["beds"],
        "views": features["views"],
        "features": features["other"],
        "highlights": _texts(unit.get("generatedHighlights")) + _enriched(unit.get("highlightedMessages")),
        "price": {"from": min(prices) if prices else None,
                  "currency": lead["price"]["currency"] if lead else None,
                  "nights": None},
        "is_available": bool(plans),
        "rate_plans": plans,
        "refund_options": refund_options,
        "extras": extras,
        "amenity_categories": amenity_groups,
        "details": details_items,
        "spaces": space,
        "photos": photos,
    }


def parse_offers(offers, checkin=None, checkout=None, pos=DEFAULT_POS):
    """propertyOffers -> {is_available, message, rooms[]}."""
    offers = offers or {}
    default_currency = _currency_for(pos)
    rooms, messages = [], []
    for listing in offers.get("categorizedListings") or []:
        if not isinstance(listing, dict):
            continue
        if listing.get("__typename") == "LodgingCategorizedUnit":
            rooms.append(parse_unit(listing, default_currency))
        else:
            text = " ".join(t for t in (_clean(_get(listing, "title", "text")), _clean(_get(listing, "subtitle", "text"))) if t)
            if text:
                messages.append(text)
    nights = None
    if checkin and checkout:
        try:
            nights = (datetime.fromisoformat(checkout) - datetime.fromisoformat(checkin)).days or None
        except ValueError:
            nights = None
    for room in rooms:
        room["price"]["nights"] = nights
    prices = [r["price"]["from"] for r in rooms if r["price"]["from"] is not None]
    return {
        "is_available": bool(prices),
        "message": _clean(_get(offers, "errorMessage", "title", "text")) or (" ".join(messages) or None),
        "heading": _clean(_get(offers, "listingsHeader", "text")),
        "checkin": checkin,
        "checkout": checkout,
        "nights": nights,
        "price": {"from": min(prices) if prices else None,
                  "currency": next((r["price"]["currency"] for r in rooms if r["price"]["currency"]), None),
                  "is_per_stay": True},
        "room_count": len(rooms),
        "rooms": rooms,
    }


# ---- hotel page ---------------------------------------------------------------------------

def _property_info(state):
    for key, value in state.items():
        if key.startswith("PropertyInfo:") and isinstance(value, dict):
            return key.split(":", 1)[1], value
    return None, None


def _content_groups(info):
    """The propertyContentSectionGroups entry carrying policies /
    importantInfo / aboutThisProperty (the dateless search-criteria one)."""
    best = None
    for key, value in (info or {}).items():
        if key.startswith("propertyContentSectionGroups") and isinstance(value, dict):
            if value.get("policies") or value.get("aboutThisProperty"):
                return value
            best = best or value
    return best or {}


def _elements(group):
    """Every PropertyContent element of a content group, in order, as
    {header, texts (plain lines), markup_items (bullets)}."""
    out = []
    for section in (group or {}).get("sections") or []:
        for sub in (section or {}).get("bodySubSections") or []:
            for egroup in (sub or {}).get("elementsV2") or []:
                for element in (egroup or {}).get("elements") or []:
                    if not isinstance(element, dict):
                        continue
                    header = _clean(_get(element, "header", "text"))
                    texts, bullets = [], []
                    for item in element.get("items") or []:
                        if not isinstance(item, dict):
                            continue
                        content = item.get("content") or {}
                        if content.get("markupType") or "markupType" in content:
                            bullets.extend(_items_of_markup(content.get("text")))
                        elif content.get("primary") is not None:
                            texts.append(_clean(_get(content, "primary", "value")))
                            if _get(content, "secondary", "value"):
                                texts.append(_clean(_get(content, "secondary", "value")))
                        for part in item.get("contents") or []:
                            texts.append(_clean(_get(part, "primary", "value")))
                    out.append({"section": _clean(section.get("sectionName")) or _clean(_get(section, "header", "text")),
                                "header": header, "texts": [t for t in texts if t], "bullets": bullets})
    return out


def _policies(group):
    """The "Fees & policies" group -> {mandatory_fees, optional_extras,
    pets, policies, also_known_as, other}."""
    out = {"mandatory_fees": [], "optional_extras": [], "pets": [], "policies": [], "also_known_as": [], "other": []}
    for element in _elements(group):
        header = (element["header"] or "").lower()
        lines = element["bullets"] + element["texts"]
        if "mandatory" in header or header.startswith("fees"):
            out["mandatory_fees"] += [t for t in lines if not t.lower().startswith("you'll be asked")]
        elif "optional" in header:
            out["optional_extras"] += lines
        elif header.startswith("pet"):
            out["pets"] += lines
        elif header == "policies" or "policy" in header:
            out["policies"] += lines
        elif "known as" in header:
            out["also_known_as"] += lines
        else:
            out["other"] += ([element["header"] + ": " + " ".join(lines)] if element["header"] and lines else lines)
    return out


def _amenity_groups(block):
    """summary.amenities -> ([{section, category, items}], popular[])."""
    groups, popular = [], []
    for section in (block or {}).get("amenities") or []:
        section_name = _clean(_get(section, "header", "text"))
        for content in (section or {}).get("contents") or []:
            items = [_clean(i.get("text")) for i in (content or {}).get("infoItems") or [] if isinstance(i, dict) and i.get("text")]
            if items:
                groups.append({"section": section_name, "category": _clean(_get(content, "header", "text")), "items": items})
    for item in _get(block, "topAmenities", "infoItems") or []:
        if isinstance(item, dict) and item.get("text"):
            popular.append(_clean(item["text"]))
    return groups, popular


def _amenities(info):
    block = None
    for key, value in (_get(info, "summary") or {}).items():
        if key.startswith("amenities") and isinstance(value, dict):
            block = value
            break
    return _amenity_groups(block)


def _photos(state):
    gallery = _first_root(state, "productGallery")
    categories = {}
    flat = []
    for cat in (gallery or {}).get("categorizedImages") or []:
        name = _clean(cat.get("categoryName"))
        images = cat.get("images") or []
        if cat.get("categoryId") == "all" or (name or "").lower() == "all photos":
            flat = images
            continue
        for image in images:
            url = _get(image, "image", "url")
            if url:
                categories.setdefault(url, name)
    out = []
    for image in flat or []:
        url = _get(image, "image", "url")
        if not url:
            continue
        out.append({"link": url, "caption": _clean(_get(image, "image", "description")),
                    "category": categories.get(url),
                    "thumbnail": _get(image, "thumbnail", "url")})
    return out


def _nearby(info):
    """whatsAround.nearbyPOIs -> [{category, places[{name, distance, travel_time}]}]."""
    out = []
    for group in _get(info, "summary", "location", "whatsAround", "nearbyPOIs") or []:
        places = []
        for item in (group or {}).get("infoItems") or []:
            if not isinstance(item, dict) or not item.get("text"):
                continue
            name = _clean(item["text"])
            travel = _clean(item.get("moreInfo"))
            if travel is None and " - " in name:
                name, travel = (p.strip() for p in name.rsplit(" - ", 1))
            places.append({"name": name, "distance": _clean(item.get("distance")), "travel_time": travel})
        if places:
            out.append({"category": _clean(group.get("title")), "places": places})
    return out


def _seo(state):
    headline = _first_root(state, "productHeadline") or {}
    try:
        return fastjson.loads(headline.get("seoStructuredData") or "{}") or {}
    except ValueError:
        return {}


def _region_id(state):
    for key in state.get("ROOT_QUERY") or {}:
        if key.startswith("propertyInfo"):
            match = re.search(r'"regionId":"(\d+)"', key)
            if match:
                return match.group(1)
    return None


def _description(state):
    description = _first_root(state, "productDescription") or {}
    paragraphs = []
    for sub in _get(description, "content", "subSections") or []:
        for egroup in (sub or {}).get("elements") or []:
            for element in (egroup or {}).get("elements") or []:
                paragraphs += _texts((element or {}).get("items"))
    return "\n\n".join(paragraphs) or None


def _faq(info):
    out = []
    for faq in _get(info, "frequentlyAskedQuestions", "faqs") or []:
        question = _clean(_get(faq, "question", "text"))
        answer = _clean(_get(faq, "answer", "text"))
        if question and answer:
            out.append({"question": question, "answer": answer})
    return out


# "You can check in starting at 1:00 PM." / "Check-in start time: 3:00 PM;
# Check-in end time: midnight." (Bellagio, 2026-11-10)
_CHECK_IN_RE = re.compile(r"check[- ]?in (?:starting at|from|after|start time:?)\s+([^.;]+?)[.;]", re.I)
_CHECK_OUT_RE = re.compile(r"check-?out time is\s+([^.]+?)\.", re.I)


def _check_times(faqs):
    """The FAQ answer "You can check in starting at 1:00 PM. Check-out time
    is noon." -> ("1:00 PM", "noon")."""
    for faq in faqs:
        if "check-in" in faq["question"].lower() or "check in" in faq["question"].lower():
            cin = _CHECK_IN_RE.search(faq["answer"])
            cout = _CHECK_OUT_RE.search(faq["answer"])
            return (_clean(cin.group(1)) if cin else None), (_clean(cout.group(1)) if cout else None)
    return None, None


def _headline_price(state, default_currency):
    offers = _first_root(state, "propertyOffers", lambda v: isinstance(v, dict) and v.get("stickyBar")) or {}
    sticky = offers.get("stickyBar") or {}
    price = _price_lines(sticky.get("propertyPrice"), default_currency)
    if price["total"] is None and price["nightly"] is None:
        price["total"], price["currency"] = _money(_get(sticky, "price", "formattedDisplayPrice"), default_currency)
    return {"nightly": price["nightly"], "total": price["total"], "nights": price["nights"],
            "currency": price["currency"], "is_taxes_included": price["is_taxes_included"],
            "dates_text": price["dates_text"],
            "text": _clean(_get(sticky, "structuredData", "content"))}


def parse_hotel_page(html, hotel_id=None, pos=DEFAULT_POS):
    """The bare hotel page -> the full hotel record (None when the page
    carries no PropertyInfo, i.e. a shell or the homepage)."""
    state = apollo_state(html)
    if not state:
        return None
    expedia_id, info = _property_info(state)
    if not info:
        return None
    default_currency = _currency_for(pos)
    seo = _seo(state)
    address = seo.get("address") or {}
    headline = _first_root(state, "productHeadline") or {}
    rating_block = _first_root(state, "productRatingSummary") or {}
    rating_summary = rating_block.get("summary") or {}
    seo_rating = {d.get("itemprop"): d.get("content") for d in _get(rating_block, "metadata", "seoData") or []
                  if isinstance(d, dict)}
    score = _float(rating_summary.get("primary")) or _float(seo_rating.get("ratingValue"))
    review_count = _int(seo_rating.get("reviewCount"))
    if review_count is None:
        for msg in rating_summary.get("supportingMessages") or []:
            review_count = review_count or _int(_get(msg, "link", "text"))
    amenities, popular = _amenities(info)
    groups = _content_groups(info)
    faqs = _faq(info)
    check_in, check_out = _check_times(faqs)
    star = _get(headline, "rating", "rating")
    lat, lng = seo.get("latitude"), seo.get("longitude")
    editorial = _get(info, "summary", "location", "whatsAround", "editorial", "content") or []
    return {
        "id": expedia_id,
        "hotel_id": hotel_id,
        "name": _clean(headline.get("primary")) or _clean(seo.get("name")),
        "link": hotel_link(hotel_id, pos) if hotel_id else None,
        "star_rating": float(star) if isinstance(star, (int, float)) else _float(_get(seo, "starRating", "ratingValue")),
        "tagline": _clean(headline.get("secondary")) or " ".join(_texts(headline.get("supportingMessages"))) or None,
        "description": _description(state) or _clean(seo.get("description")),
        "rating": {"score": score, "max": 10, "label": _clean(rating_summary.get("secondary")),
                   "review_count": review_count},
        "location": {
            "address": _clean(address.get("streetAddress")),
            "city": _clean(address.get("addressLocality")),
            "region": _clean(address.get("addressRegion")),
            "postal_code": _clean(address.get("postalCode")),
            "country_code": _clean(address.get("addressCountry")),
            "region_id": _region_id(state),
            "coordinates": {"latitude": lat, "longitude": lng} if lat is not None and lng is not None else None,
            "neighborhood_text": "\n".join(_clean(t) for t in editorial if _clean(t)) or None,
            "nearby": _nearby(info),
        },
        "check_in_time": check_in,
        "check_out_time": check_out,
        "price_from": _headline_price(state, default_currency),
        "popular_amenities": popular,
        "amenities": amenities,
        "policies": _policies(groups.get("policies")),
        "faq": faqs,
        "photos": _photos(state),
    }
