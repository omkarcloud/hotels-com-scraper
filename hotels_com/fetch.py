"""hotels.com transport: plain curl_cffi with browser-impersonated TLS, no
browser. hotels.com is Expedia Group's shopping-pwa (the same stack as
vrbo/fetch.py, siteId 300000001 on www.hotels.com); every surface this
package reads is public and anonymous:

  * HOTEL      GET /ho<id>/  (bare path, no query string!) — the
               server-rendered hotel page whose `window.__APOLLO_STATE__`
               carries PropertyInfo:<expediaId> (name, star class, address,
               description, categorized amenities, fees & policies, FAQ,
               gallery, rating, what's nearby, tonight's headline price).
               Any query string on that URL (?chkin=) yields a client-rendered
               shell with no PropertyInfo, so the page is always fetched bare.
               GET /h<expediaId>.Hotel-Information 302s to the /ho<id>/<slug>/
               URL (the reverse id mapping); an unknown id 302s to the
               homepage.
  * SEARCH     POST /graphql  propertySearch — LodgingCards by
               resultsStartingIndex / resultsSize (50 default, 200 honoured),
               map markers with coordinates, the matched count; destination
               by regionId / free text / coordinates / mapBounds, filters as
               criteria selections; ~480 results cap per destination.
  * FILTERS    GET /Hotel-Search?…  — page 1's universalSortAndFilter block:
               the destination's filter groups with counts and the sort list.
  * ROOMS      POST /graphql  propertyOffers — every room type with its rate
               plans for a stay (RoomsAndRatesPropertyOffersQuery, trimmed).
  * REVIEWS    POST /graphql  propertyInfo.reviewInfo — up to 50 per call
               by startIndex / size, sorted and filtered.
  * CALENDAR   POST /graphql  propertyRatesDateSelector — the lowest nightly
               price per day for the months ahead.
  * TYPEAHEAD  GET /api/v4/typeahead/<q> — region + hotel suggestions
               (gaiaId = regionId, hotelId = Expedia id, coordinates).

GraphQL takes the full query text (no persisted hashes) with the
`client-info` / `x-page-id` headers and the cookie jar of ONE earlier page
load on the same storefront (DUAID + the POS cookies): without it the
endpoint answers the captcha page. A session warms itself with one bare
hotel page of the storefront (WARM_PATH; any hotel page load counts)
before its first GraphQL call — never the homepage, which sets no
point-of-sale cookies for an exit abroad. A /graphql POST is never
redirect-followed: a 302 means the exit landed on another storefront and
the call retries on a fresh exit.

Points of sale (refs.POS): each `country` is its own host + GraphQL context
and its own cookie jar (a jar from www.hotels.com pins the US POS), so
sessions are kept per storefront. Prices follow the storefront; the
context's currency field is ignored upstream.

Anti-bot (research 2026-11-07, same as Vrbo): the block is Expedia's own
"Bot or Not?" reCAPTCHA page served as HTTP 429 (on /graphql a JSON 429
"Provisioned request rate has been exceeded"; a few datacenter exits get
Akamai's 403 "Access Denied"), keyed on the exit IP's reputation and the
endpoint: the typeahead passes everywhere, the bare hotel page on most
exits, /graphql and /Hotel-Search only on exits the risk engine trusts. TLS
fingerprint matters: the generic chrome target, safari17_0 and edge101 are
refused on every exit; safari184_ios passes on clean exits and chrome150 on
some (pod probe 2026-11-07, curl_cffi 0.17). Rotating cookie jars changes nothing, rotating
the exit and the fingerprint does, so a block drops the session and the
retry rides a fresh exit on the other fingerprint — up to
config.HOTELS_COM_EXIT_ATTEMPTS times. Parallel sessions on ONE exit burn it
within a dozen calls; one request at a time per session is the pattern.

Sessions live in a process-wide identity_pool.Pool in exclusive mode (since
2026-11-09), one pool key per storefront: a request borrows a session of
its storefront for ONE call and hands it back, so the next request on any
of the pod's 64 threads reuses its warm-up (before, each thread kept a
session per storefront and warmed it per thread). A block or transport
failure marks the session dead (retired on give-back); a session is also
retired after HOTELS_COM_SESSION_USES calls or HOTELS_COM_SESSION_MAX_AGE
seconds. The fingerprint ladder (target index, direct-tier refusals) is
process-wide too: a mint runs on whichever thread borrows, so a blocked
thread's "next fingerprint" must apply to the next mint anywhere.

Egress (config.HOTELS_COM_EXIT, pod probe table in config.py): the pod's
own IP is refused on every surface but the typeahead (pod probe 2026-11-07),
so the US storefront rides one sticky datacenter proxy exit per
session (2/2 ports passed all five surfaces with safari184_ios); every other
storefront rides a residential exit in its own country
(config.hotels_com_pos_proxy), so the language the session announces is the
storefront's. A blocked call drops its session and the retry rides a fresh
exit (config.HOTELS_COM_EXIT_ATTEMPTS of them).

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  HotelsComUpstreamError   transport failure / 5xx / malformed payload  — retryable
  HotelsComBlocked         401/403/429 or the "Bot or Not?" page          — retryable
  HotelsComBadRequest      upstream 400                                  — never retried
  HotelsComNotFound        unknown hotel id / GraphQL "Invalid PropertyId" — never retried
"""
import json
import os
import re
import sys
import threading
import time
import uuid
from urllib.parse import quote, urlencode, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import egress
import identity_pool
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from hotels_com.refs import DEFAULT_POS, EXPEDIA_ID_RE, HOTEL_ID_RE, POS
from scraper_utils.pages import page_title

# curl_cffi impersonation targets, in the order a thread tries them: a block
# drops the session and its replacement rides the next one. Pod probe
# 2026-11-07 (scrapers-patchright-depl-74fddd498d-rfmk6, table in config.py):
# safari184_ios passed every surface on 2/2 datacenter and 2/2 US residential
# exits while chrome150 drew the 429 captcha on the same tiers (and the
# generic "chrome", safari17_0 and edge101 are refused everywhere, research
# 2026-11-07); vrbo's probe of 2026-11-02 saw exits disagree per fingerprint
# the other way round, hence the alternation.
IMPERSONATE_TARGETS = ("safari184_ios", "chrome150")
IMPERSONATE = IMPERSONATE_TARGETS[0]
PAGE_TIMEOUT = 45       # hotel pages are ~1.1 MB, search pages ~2 MB
GRAPHQL_TIMEOUT = 40    # a 50-card search answers in ~2 s, 200 cards in ~3 s
TYPEAHEAD_TIMEOUT = 15
BLOCK_RETRY_PAUSE = 0.3   # a block is cured by a fresh exit, not by waiting

PAGE_HEADERS = {
    "accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "upgrade-insecure-requests": "1",
}
GRAPHQL_HEADERS = {
    "accept": "*/*",
    "content-type": "application/json",
    "client-info": "shopping-pwa,unknown,unknown",
}
PAGE_ID_PROPERTY = "page.Hotels.Infosite.Information,H,20"
PAGE_ID_SEARCH = "page.Hotel-Search,H,20"

_TITLE_RE = re.compile(r"<title>([^<]*)</title>", re.I)
_CHALLENGE_TITLE = "Bot or Not?"


class HotelsComUpstreamError(UpstreamError):
    """Transport failure, 5xx or a malformed payload — retryable."""


class HotelsComBlocked(HotelsComUpstreamError, Blocked):
    """401/403/429 or the "Bot or Not?" page — retryable on a fresh exit."""


class HotelsComBadRequest(BadRequest):
    """Upstream 400 — never retried."""


class HotelsComNotFound(NotFound):
    """The hotel does not exist — never retried."""


# ---- pooled sessions ---------------------------------------------------------
# One pool, keyed by storefront (pos): a cookie jar pins the storefront it was
# minted on. A session keeps its jar (the GraphQL endpoint needs one page
# load's cookies) and its sticky exit for its lifetime; it owns ONE curl
# handle, so the pool lends it to one request at a time; a retryable failure
# marks it dead so the retry rides a fresh exit.
# Process-wide ladder (under _fallback_lock): after a block on the direct tier
# every new US session rides the residential fallback
# (config.hotels_com_fallback_proxy()) until _fallback_until; _target_index
# picks the fingerprint of the next mint; _direct_refusals counts direct-tier
# blocks towards the fallback.
_fallback_until = 0.0
_fallback_lock = threading.Lock()
_target_index = 0
_direct_refusals = 0


def _proxy(pos):
    if pos != DEFAULT_POS:
        return config.hotels_com_pos_proxy(POS[pos]["exit_country"])
    proxy = config.hotels_com_proxy()
    if proxy is None and time.time() < _fallback_until:
        proxy = config.hotels_com_fallback_proxy()
    return proxy


class _Session(identity_pool.Identity):
    """One curl session on one exit with one fingerprint for one storefront
    (`hc_pos`); `hc_warm` says a page of the storefront was loaded (its POS
    cookies are in the jar), `hc_proxy` is the exit (None = the pod's own
    IP, the direct tier)."""

    def __init__(self, pos, proxy, target=IMPERSONATE):
        from curl_cffi import requests as curl_requests
        super().__init__(exit=proxy)
        self.curl = curl_requests.Session(impersonate=target, use_thread_local_curl=False)
        if proxy:
            self.curl.proxies = {"http": proxy, "https": proxy}
        self.curl.headers["accept-language"] = egress.accept_language(proxy)
        self.hc_warm = False
        self.hc_proxy = proxy
        self.hc_pos = pos
        self.target = target
        self.closed = False

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.curl.close()
        except Exception:
            pass


def _mint(pos, hint=None):
    """The pool's mint: a cold session for the storefront on the tier of the
    moment with the ladder's current fingerprint (warmed by its first call)."""
    with _fallback_lock:
        target = IMPERSONATE_TARGETS[_target_index % len(IMPERSONATE_TARGETS)]
    return _Session(pos, _proxy(pos), target)


_sessions = identity_pool.Pool(
    "hotels_com", _mint, mode="exclusive",
    budget=lambda: config.HOTELS_COM_SESSION_USES,
    max_age=lambda: config.HOTELS_COM_SESSION_MAX_AGE,
    on_retire=lambda ident, reason: ident.close())


def _note_block(sess):
    """A block on `sess` moves the next mint to the next fingerprint; once
    every fingerprint has been refused on the direct tier, new US sessions
    ride the residential fallback for HOTELS_COM_BLOCK_COOLDOWN seconds
    (vrbo's ladder: on 2026-11-02 the pod IP refused one fingerprint and
    passed the other)."""
    global _fallback_until, _target_index, _direct_refusals
    with _fallback_lock:
        _target_index += 1
        if sess.hc_proxy is not None:
            return
        _direct_refusals += 1
        every_fingerprint_refused = _direct_refusals % len(IMPERSONATE_TARGETS) == 0
        if every_fingerprint_refused and config.hotels_com_proxy() is None and config.HOTELS_COM_BLOCK_COOLDOWN:
            _fallback_until = max(_fallback_until, time.time() + config.HOTELS_COM_BLOCK_COOLDOWN)


def _on_session(pos, fn):
    """fn(sess) on a borrowed session of the storefront. A block or any
    other retryable failure marks it dead (retired on give-back, so the
    retry rides a fresh exit); a block also advances the ladder."""
    sess = _sessions.borrow(pos)
    try:
        return fn(sess)
    except HotelsComBlocked:
        _note_block(sess)
        sess.dead = True
        raise
    except HotelsComUpstreamError:
        sess.dead = True
        raise
    finally:
        _sessions.give_back(sess)


def _retrying(fn, pos):
    """Run fn(sess) on a borrowed session of `pos` under the retry policy: a
    block gets config.HOTELS_COM_EXIT_ATTEMPTS tries on fresh exits with no
    backoff (the cure is the exit, not time), any other upstream failure
    config.MAX_RETRIES with backoff; NotFound / BadRequest surface at once.
    The failed call already retired its session (_on_session)."""
    last = None
    attempts = max(config.HOTELS_COM_EXIT_ATTEMPTS, config.MAX_RETRIES)
    plain_failures = 0
    for attempt in range(1, attempts + 1):
        try:
            return _on_session(pos, fn)
        except HotelsComBlocked as e:
            last = e
            if attempt < attempts:
                time.sleep(BLOCK_RETRY_PAUSE)
        except HotelsComUpstreamError as e:
            last = e
            plain_failures += 1
            if plain_failures >= config.MAX_RETRIES:
                break
            time.sleep(config.RETRY_BACKOFF * plain_failures)
    raise last


def is_challenge(text):
    return page_title(text) == _CHALLENGE_TITLE


def _classify(resp, label):
    """Map an HTTP response to the error taxonomy; return the body text."""
    if resp.status_code == 404:
        raise HotelsComNotFound(f"{label} not found")
    if resp.status_code == 400:
        raise HotelsComBadRequest(resp.text[:200])
    if resp.status_code in (401, 403, 429):
        raise HotelsComBlocked(f"HTTP {resp.status_code} for {label}")
    if resp.status_code != 200:
        raise HotelsComUpstreamError(f"HTTP {resp.status_code} for {label}")
    if is_challenge(resp.text):
        raise HotelsComBlocked(f"challenge page for {label}")
    return resp.text


def _host(pos):
    return POS[pos]["host"]


def _get(sess, pos, path, label, headers, timeout):
    try:
        resp = sess.curl.get(_host(pos) + path, headers=headers, timeout=timeout, allow_redirects=True)
    except Exception as e:
        raise HotelsComUpstreamError(f"request failed: {type(e).__name__}: {e}")
    text = _classify(resp, label)
    if headers is PAGE_HEADERS:
        sess.hc_warm = True     # any page load leaves the POS cookies in the jar (the typeahead JSON does not)
    return resp, text


_REDIRECTS = (301, 302, 303, 307, 308)
# The page a session loads before its first GraphQL call. A hotel page, never
# the homepage: from an exit outside the storefront's country the homepage
# answers a 302 to that country's storefront (www -> in.hotels.com from an
# Indian IP) and sets no point-of-sale cookies, and the next /graphql POST is
# then 302'd there too; the bare hotel page (or its 301 to the slug URL) sets
# tpid / iEAPID / linfo / currency for THIS storefront and the POST stays put
# (Mac IP A/B, 2026-11-07: homepage-only 302, hotel-page 200). The ids are
# the same on every storefront.
WARM_PATH = "/ho434012/"


def _warm(sess, pos):
    """One page load so the session holds the storefront's cookies (DUAID,
    the POS cookies) before its first GraphQL call. The redirect is not
    followed: the 301 already carries the cookies."""
    if getattr(sess, "hc_warm", False):
        return
    try:
        resp = sess.curl.get(_host(pos) + WARM_PATH, headers=PAGE_HEADERS, timeout=PAGE_TIMEOUT,
                             allow_redirects=False)
    except Exception as e:
        raise HotelsComUpstreamError(f"warm-up failed: {type(e).__name__}: {e}")
    if resp.status_code not in _REDIRECTS:
        _classify(resp, "warm-up page")
    sess.hc_warm = True


def _duaid(sess):
    try:
        return sess.curl.cookies.get("DUAID") or str(uuid.uuid4())
    except Exception:
        return str(uuid.uuid4())


def _context(sess, pos):
    p = POS[pos]
    return {
        "siteId": p["site_id"], "locale": p["locale"], "eapid": p["eapid"], "tpid": p["tpid"],
        "currency": p["currency"],
        "device": {"type": "DESKTOP"},
        "identity": {"duaid": _duaid(sess), "authState": "ANONYMOUS"},
        "privacyTrackingState": "CAN_TRACK",
        "debugContext": {"abacusOverrides": []},
    }


_NOT_FOUND_MARKERS = ("Invalid PropertyId", "Invalid parameter [parameterName=propertyId",
                      "Property not found")


def _graphql(pos, operation, query, variables, page_id, referer_path):
    """POST one operation to the storefront's /graphql; returns its `data`.
    GraphQL errors naming the property id are NotFound, others upstream
    failures."""
    def once(sess):
        _warm(sess, pos)
        variables["context"] = _context(sess, pos)
        body = [{"operationName": operation, "variables": variables, "query": query}]
        headers = dict(GRAPHQL_HEADERS, **{"x-page-id": page_id, "origin": _host(pos),
                                           "referer": _host(pos) + referer_path})
        try:
            resp = sess.curl.post(_host(pos) + "/graphql", json=body, headers=headers, timeout=GRAPHQL_TIMEOUT,
                                  allow_redirects=False)
        except Exception as e:
            raise HotelsComUpstreamError(f"request failed: {type(e).__name__}: {e}")
        if resp.status_code in _REDIRECTS:
            # a followed redirect turns the POST into a GET without a body,
            # which the endpoint answers 400 "Must provide query string."
            # (seen 2026-11-07): the exit sits on another storefront, so
            # the call is retried on a fresh one
            raise HotelsComBlocked(f"{operation}: redirected to {resp.headers.get('location')}")
        text = _classify(resp, operation)
        try:
            payload = resp.json()
        except Exception:
            raise HotelsComBlocked(f"non-JSON {operation} response: {text[:120]}")
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        if not isinstance(payload, dict):
            raise HotelsComUpstreamError(f"unexpected {operation} payload: {text[:120]}")
        errors = payload.get("errors") or []
        data = payload.get("data") or {}
        if errors:
            messages = "; ".join(str(e.get("message", ""))[:200] for e in errors if isinstance(e, dict))
            if any(marker in messages for marker in _NOT_FOUND_MARKERS):
                raise HotelsComNotFound("hotel not found")
            if not data or all(v is None for v in data.values()):
                raise HotelsComUpstreamError(f"{operation}: {messages[:300]}")
        return data
    return _retrying(once, pos)


# ---- hotel page ----------------------------------------------------------------

_FINAL_HOTEL_RE = re.compile(r"^/(?:[a-z]{2}/)?ho([0-9]{1,12})(?:[/?#]|$)", re.I)


def _candidate_paths(ref):
    """"ho434012" -> its bare page; a bare number is an Expedia id reached
    through /h<id>.Hotel-Information (which 302s to the /ho<id>/<slug>/ URL)."""
    match = HOTEL_ID_RE.match(ref)
    if match:
        return [f"/ho{match.group(1)}/"]
    if EXPEDIA_ID_RE.match(ref):
        return [f"/h{ref}.Hotel-Information"]
    raise HotelsComNotFound(f"hotel {ref} not found")


def fetch_hotel_page(ref, pos=DEFAULT_POS):
    """GET the bare hotel page for a hotels.com id / Expedia id; returns
    (html, hotel_id "ho<digits>"). Unknown ids 302 to the homepage ->
    HotelsComNotFound."""
    def once(sess):
        for path in _candidate_paths(ref):
            resp, text = _get(sess, pos, path, f"hotel {ref}", PAGE_HEADERS, PAGE_TIMEOUT)
            final = urlparse(getattr(resp, "url", "") or "")
            match = _FINAL_HOTEL_RE.match(final.path or "")
            if not match:          # redirected to the homepage or elsewhere = unknown id
                raise HotelsComNotFound(f"hotel {ref} not found")
            hotel_id = "ho" + match.group(1)
            if "PropertyInfo:" not in text and final.query:
                # the redirect landed on a dated URL (client-rendered shell):
                # the bare page carries the state
                resp, text = _get(sess, pos, f"/{hotel_id}/", f"hotel {ref}", PAGE_HEADERS, PAGE_TIMEOUT)
            if "PropertyInfo:" not in text:
                raise HotelsComUpstreamError(f"hotel {ref}: page without server-rendered state")
            return text, hotel_id
        raise HotelsComNotFound(f"hotel {ref} not found")
    return _retrying(once, pos)


# ---- typeahead ----------------------------------------------------------------------

def fetch_autocomplete(query, limit=10, pos=DEFAULT_POS):
    """GET /api/v4/typeahead/<q> -> the raw JSON (sr[] suggestions: regions
    with gaiaId, hotels with hotelId). regiontype=2047 (every region type)
    keeps the ranking sane (vrbo research 2026-10-30)."""
    p = POS[pos]
    params = {"client": "SearchForm", "lob": "HOTELS", "locale": p["locale"], "siteid": p["site_id"],
              "maxresults": limit, "format": "json", "regiontype": 2047}
    path = f"/api/v4/typeahead/{quote(query, safe='')}?{urlencode(params)}"

    def once(sess):
        resp, text = _get(sess, pos, path, "typeahead", {"accept": "application/json, text/plain, */*"},
                          TYPEAHEAD_TIMEOUT)
        try:
            payload = resp.json()
        except Exception:
            raise HotelsComBlocked(f"non-JSON typeahead response: {text[:120]}")
        if not isinstance(payload, dict):
            raise HotelsComUpstreamError(f"unexpected typeahead payload: {text[:120]}")
        return payload
    return _retrying(once, pos)


# ---- search ---------------------------------------------------------------------------

SEARCH_QUERY = """
query PropertySearchQuery($context: ContextInput!, $criteria: PropertySearchCriteriaInput!) {
  propertySearch(context: $context, criteria: $criteria) {
    summary { matchedPropertiesSize resultsHeading }
    pagination { paginationLabel subSets { nextSubSet { size startingIndex } previousSubset { size startingIndex } } }
    criteria { primary { destination { regionId regionName } dateRange { checkInDate { day month year } checkOutDate { day month year } } rooms { adults children { age } } } }
    propertySearchListings {
      __typename
      ... on LodgingCard {
        id
        headingSection {
          heading
          productRating { rating accessibility }
          messages { __typename ... on EGDSPlainText { text } ... on EGDSStylizedText { text } ... on EGDSParagraph { text } }
          featuredMessages { text theme }
          amenities { text icon { id } }
          locationInfo { primaryText { text } subText }
          standardBadges { text theme }
          titleBadges { text theme }
        }
        summarySections {
          ... on LodgingCardProductSummarySection {
            heading { text }
            amenities { text icon { id } }
            messages { __typename ... on EGDSPlainText { text } ... on EGDSStylizedText { text } ... on EGDSParagraph { text } }
            reviewSummary { graphic { ... on EGDSStandardBadge { text accessibility } } title { shoppingProductTitle { ... on EGDSStylizedText { text accessibility } } } subtexts { ... on ShoppingProductContentStylizedTexts { shoppingProductTitle { ... on EGDSStylizedText { text accessibility } } } ... on ProductTextInfoSection { title { text } accessibilityText } } }
            highlightMessages { items { text { text } subText } }
            footerMessages { listItems { ... on EGDSTextWithMarkListItem { text style } ... on EarnMessageContainerListItem { text style } } }
            descriptionSection { heading { text } description { text } }
          }
        }
        priceSection { priceSummary { displayMessages { lineItems { __typename ... on DisplayPrice { role price { formatted } } ... on LodgingEnrichedMessage { value state } } } options { displayPrice { formatted } strikeOut { formatted } accessibilityLabel } priceMessaging { value } } standardBadge { standardBadge { text } } badge { text theme } }
        dateSection { messages { ... on EGDSStylizedText { text } } }
        callOut { __typename ... on EGDSPlainText { text } ... on EGDSStylizedText { text } ... on EGDSParagraph { text } }
        cardLink { resource { value } }
        mediaSection { badges { primaryBadge { text theme } secondaryBadge { text theme } tertiaryBadge { text theme } } gallery { media { ... on LodgingMediaItem { id media { ... on Image { url description } } } } } }
      }
    }
    dynamicMap { map { markers { ... on EGDSMapFeature { id type markerStatus text name qualifiers markerPosition { latitude longitude } } } } }
  }
}"""

SEARCH_PAGE_SIZE = 50    # the site's own page size
SEARCH_MAX_SIZE = 200    # resultsSize 200 honoured (verified 2026-11-07)


def _date_input(iso):
    year, month, day = (int(x) for x in iso.split("-"))
    return {"day": day, "month": month, "year": year}


def date_range(checkin, checkout):
    if not (checkin and checkout):
        return None
    return {"checkInDate": _date_input(checkin), "checkOutDate": _date_input(checkout)}


def rooms(adults, children, count=1):
    room = {"adults": int(adults or 2), "children": [{"age": int(a)} for a in (children or [])]}
    return [dict(room, children=list(room["children"])) for _ in range(max(1, int(count or 1)))]


def fetch_search(destination, start, checkin=None, checkout=None, adults=2, children=None, room_count=1,
                 sort="RECOMMENDED", selections=None, ranges=None, booleans=None,
                 size=SEARCH_PAGE_SIZE, pos=DEFAULT_POS):
    """POST propertySearch for one page; `destination` is the GraphQL
    destination input ({regionId} | {regionName} | {coordinates} |
    {mapBounds}); `selections` [{id, value}], `ranges` [{id, min, max}],
    `booleans` [{id, value}] are the site's own criteria tokens."""
    criteria = {
        "primary": {"dateRange": date_range(checkin, checkout), "destination": destination,
                    "rooms": rooms(adults, children, room_count)},
        "secondary": {
            "booleans": list(booleans or []),
            "counts": [{"id": "resultsStartingIndex", "value": int(start)},
                       {"id": "resultsSize", "value": int(size)}],
            "ranges": list(ranges or []),
            "selections": [{"id": "sort", "value": sort}] + list(selections or []),
        },
    }
    data = _graphql(pos, "PropertySearchQuery", SEARCH_QUERY, {"criteria": criteria},
                    PAGE_ID_SEARCH, "/Hotel-Search")
    result = data.get("propertySearch")
    if not isinstance(result, dict):
        raise HotelsComUpstreamError("search: no propertySearch in the response")
    return result


def search_page_path(destination, checkin=None, checkout=None, adults=2):
    """The /Hotel-Search URL the filters endpoint loads (page 1 is
    server-rendered with the destination's filter vocabulary)."""
    params = {}
    if destination.get("regionId"):
        params["regionId"] = destination["regionId"]
    elif destination.get("regionName"):
        params["destination"] = destination["regionName"]
    elif destination.get("coordinates"):
        c = destination["coordinates"]
        params["latLong"] = f"{c['latitude']},{c['longitude']}"
    if checkin and checkout:
        params["startDate"], params["endDate"] = checkin, checkout
    params["adults"] = int(adults or 2)
    query = urlencode(params)
    if destination.get("mapBounds"):
        sw, ne = destination["mapBounds"]
        query += f"&mapBounds={sw['latitude']},{sw['longitude']}&mapBounds={ne['latitude']},{ne['longitude']}"
    return "/Hotel-Search?" + query


def fetch_search_page(destination, checkin=None, checkout=None, adults=2, pos=DEFAULT_POS):
    """GET the server-rendered search page (filters + sort vocabulary)."""
    path = search_page_path(destination, checkin, checkout, adults)

    def once(sess):
        _, text = _get(sess, pos, path, "search page", PAGE_HEADERS, PAGE_TIMEOUT)
        if "__APOLLO_STATE__" not in text:
            raise HotelsComUpstreamError("search page without server-rendered state")
        return text
    return _retrying(once, pos)


# ---- reviews ------------------------------------------------------------------------------

REVIEWS_QUERY = """
query ReviewsQuery($context: ContextInput!, $propertyId: String!, $searchCriteria: PropertySearchCriteriaInput!) {
  propertyInfo(propertyId: $propertyId, context: $context) {
    id
    reviewInfo(searchCriteria: $searchCriteria) {
      summary { totalCount { raw formatted } averageOverallRating { raw formatted } superlative paginateAction { text } reviewSummaryDetails { label ratingPercentage formattedRatingOutOfMax } propertyReviewCountDetails { shortDescription } }
      reviews {
        id superlative locale title brandType disclaimer text submissionTimeLocalized stayDuration
        reviewScoreWithDescription { label value }
        reviewAuthorAttribution { text }
        managementResponses { id header { text } response }
        photos { url description }
        propertyReviewSource { text { value } }
        translationInfo { targetLocale }
      }
      sortAndFilter { sortAndFilter { name label options { label optionValue isSelected } } }
    }
  }
}"""

REVIEWS_MAX_SIZE = 50   # the endpoint caps a call at 50 (verified 2026-11-07)


def fetch_reviews(expedia_id, start=0, size=20, sort="NEWEST_TO_OLDEST",
                  search_term=None, pos=DEFAULT_POS):
    selections = [{"id": "sortBy", "value": sort}]
    if search_term:
        selections.append({"id": "searchTerm", "value": search_term})
    variables = {"propertyId": str(expedia_id), "searchCriteria": {
        "primary": {"dateRange": None, "destination": {"regionId": None}, "rooms": [{"adults": 2}]},
        "secondary": {
            "booleans": [{"id": "includeRecentReviews", "value": True},
                         {"id": "includeRatingsOnlyReviews", "value": True}],
            "counts": [{"id": "startIndex", "value": int(start)}, {"id": "size", "value": int(size)}],
            "selections": selections,
        }}}
    data = _graphql(pos, "ReviewsQuery", REVIEWS_QUERY, variables, PAGE_ID_PROPERTY,
                    f"/h{expedia_id}.Hotel-Information")
    info = (data.get("propertyInfo") or {}).get("reviewInfo")
    if not isinstance(info, dict):
        raise HotelsComUpstreamError("reviews: no reviewInfo in the response")
    return info


# ---- calendar ------------------------------------------------------------------------------

CALENDAR_QUERY = """
query PropertyRatesDateSelectorQuery($context: ContextInput!, $eid: ID!, $dateRange: DateRangeInput) {
  propertyRatesDateSelector(context: $context, dateRange: $dateRange, eid: $eid) {
    configuration { currentDate { day month year } beginDate { day month year } endDate { day month year } availableByDefault calendarTemplateType }
    days { date { day month year } displayPrice available checkinValidity checkoutValidity stayConstraints { stayIncrementInDays minimumStayInDays maximumStayInDays } }
    validationMessage
  }
}"""


def fetch_calendar(expedia_id, pos=DEFAULT_POS):
    data = _graphql(pos, "PropertyRatesDateSelectorQuery", CALENDAR_QUERY,
                    {"eid": str(expedia_id), "dateRange": None}, PAGE_ID_PROPERTY,
                    f"/h{expedia_id}.Hotel-Information")
    selector = data.get("propertyRatesDateSelector")
    if not isinstance(selector, dict):
        raise HotelsComUpstreamError("calendar: no propertyRatesDateSelector in the response")
    return selector


# ---- rooms and rates ----------------------------------------------------------------------------
# A trimmed RoomsAndRatesPropertyOffersQuery (the site's own document pulls
# in ~90 fragments whose same-named variants differ across bundles, so a
# reassembled copy does not validate; this selection does, 26 units for a
# Tokyo hotel on 2026-11-07).

OFFERS_QUERY = """
query RoomsAndRatesPropertyOffersQuery($context: ContextInput!, $propertyId: String!, $searchCriteria: PropertySearchCriteriaInput, $referrer: String) {
  propertyOffers(context: $context, propertyId: $propertyId, searchCriteria: $searchCriteria, referrer: $referrer) {
    id
    listingsHeader { text subText }
    errorMessage { title { text } }
    categorizedListings {
      __typename
      ... on LodgingCategorizedUnit {
        unitId
        header { text }
        features { text graphic { __typename ... on Icon { id } } }
        generatedHighlights { text }
        highlightedMessages { __typename ... on LodgingEnrichedMessage { value state } ... on LodgingPlainMessage { value } }
        primarySelections {
          propertyUnit {
            id
            features { text }
            roomAmenities { header { text } bodySubSections { contents { __typename ... on PropertyContent { header { text } } } } }
            unitGallery { images { image { id description url } } }
            spaceDetails { header { text } spaces { name description } }
            detailsDialog { ... on PropertyUnitDetailsDialog { content { details { header { text subText } contents { heading items { text } } } } } }
            ratePlans {
              id name description
              highlightedMessages { __typename ... on LodgingEnrichedMessage { value state } ... on LodgingPlainMessage { value } ... on LodgingPlainDialog { trigger { value } content } ... on LodgingPolicyDialog { trigger { value } } }
              paymentPolicy { heading paymentType descriptions { heading items { text } } price { lead { formatted } } }
              priceDetails {
                optionTitle { text }
                paymentModel noCreditCard hotelCollect dealMarker
                availability { scarcityMessage }
                propertyNaturalKeys { id checkIn { month day year } checkOut { month day year } inventoryType ratePlanId roomTypeId ratePlanType rooms { childAges numberOfAdults } }
                price {
                  lead { formatted amount }
                  priceMessaging { value theme }
                  options { leadingCaption displayPrice { formatted amount } strikeOut { formatted amount } accessibilityLabel disclaimer { value } }
                  reassuranceMessage { value }
                }
              }
            }
          }
          primarySelection { description enabled optionId price subText selected }
          secondarySelections { recommendedSelection secondarySelection { description enabled optionId price subText selected } tertiarySelections { description price subText } }
        }
      }
      ... on MessageResult { title { text } subtitle { text } }
    }
  }
}"""


def fetch_offers(expedia_id, checkin, checkout, adults=2, children=None, room_count=1, pos=DEFAULT_POS):
    variables = {"propertyId": str(expedia_id), "searchCriteria": {
        "primary": {"dateRange": date_range(checkin, checkout), "destination": {"regionId": None},
                    "rooms": rooms(adults, children, room_count)},
        "secondary": {"booleans": [], "counts": [], "ranges": [], "selections": []}},
        "referrer": "HOT.HIS.Offers"}
    data = _graphql(pos, "RoomsAndRatesPropertyOffersQuery", OFFERS_QUERY, variables, PAGE_ID_PROPERTY,
                    f"/h{expedia_id}.Hotel-Information")
    offers = data.get("propertyOffers")
    if not isinstance(offers, dict):
        raise HotelsComUpstreamError("rooms: no propertyOffers in the response")
    return offers


__all__ = [name for name in dir() if name.startswith(("fetch_", "HotelsCom"))] + [
    "SEARCH_PAGE_SIZE", "SEARCH_MAX_SIZE", "REVIEWS_MAX_SIZE"]

if __name__ == "__main__":   # python hotels_com/fetch.py <hotel id>
    ref = sys.argv[1] if len(sys.argv) > 1 else "ho434012"
    html, hotel_id = fetch_hotel_page(ref)
    print(hotel_id, len(html), page_title(html))
    print(json.dumps(fetch_autocomplete("Paris", 3), indent=1)[:800])
