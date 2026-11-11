"""Offline tests for the hotels.com parsers, reference resolvers and value
helpers.

    python -m pytest hotels_com/test_parsers.py -q

Fixtures are real hotels.com pages / GraphQL payloads captured 2026-11-07
(hotels_com/fixtures/, listed in parsers.py's docstring). No network.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hotels_com import api, parsers as P, refs  # noqa: E402
from hotels_com.fetch import is_challenge, page_title  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return json.load(f) if name.endswith(".json") else f.read()


def has_fixture(name):
    return os.path.exists(os.path.join(FX, name))


def gql(name, *path):
    data = fixture(name)
    data = data[0] if isinstance(data, list) else data
    data = data["data"]
    for key in path:
        data = data[key]
    return data


@pytest.fixture(scope="module")
def anastassiou():
    return P.parse_hotel_page(fixture("hotel_ho434012.html"), "ho434012")


# ---- refs ---------------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("ho434012", "ho434012"),
    ("HO434012", "ho434012"),
    ("6136437", "6136437"),
    ("https://www.hotels.com/ho434012/", "ho434012"),
    ("https://www.hotels.com/ho174047/villa-eugenie-paris-france/?chkin=2026-12-10&expediaPropertyId=558560", "ho174047"),
    ("//www.hotels.com/ho434012/hotel-anastassiou-kastoria/", "ho434012"),
    ("https://www.hotels.com/h6136437.Hotel-Information", "6136437"),
    ("https://www.expedia.com/Kastoria-Hotels-Hotel-Anastassiou.h6136437.Hotel-Information", "6136437"),
    ("https://www.hotels.com/Hotel-Search?expediaPropertyId=558560&regionId=2734", "558560"),
])
def test_resolve_hotel(value, expected):
    assert refs.resolve_hotel(value) == expected


@pytest.mark.parametrize("value", ["", "anastassiou", "ho", "https://www.booking.com/hotel/gr/x.html",
                                   "https://www.hotels.com/Hotel-Search?destination=Paris"])
def test_resolve_hotel_rejects(value):
    with pytest.raises(ValueError):
        refs.resolve_hotel(value)


def test_resolve_location():
    assert refs.resolve_location("Paris, France") == {"region_name": "Paris, France"}
    assert refs.resolve_location(" 2734 ") == {"region_id": "2734"}
    assert refs.resolve_location("https://www.hotels.com/Hotel-Search?destination=Paris&regionId=2734") == {"region_id": "2734"}
    assert refs.resolve_location("https://www.hotels.com/Hotel-Search?destination=Paris%2C+France") == {"region_name": "Paris, France"}
    assert refs.resolve_location("https://www.hotels.com/Hotel-Search?latLong=48.85,2.35") == {
        "coordinates": {"latitude": 48.85, "longitude": 2.35}}
    bounds = refs.resolve_location("https://www.hotels.com/Hotel-Search?mapBounds=48.81,2.22&mapBounds=48.9,2.47")
    assert bounds == {"bounds": [{"latitude": 48.81, "longitude": 2.22}, {"latitude": 48.9, "longitude": 2.47}]}
    for bad in ("", "https://www.hotels.com/Hotel-Search", "https://www.booking.com/?ss=Paris"):
        with pytest.raises(ValueError):
            refs.resolve_location(bad)


def test_parse_bounds():
    assert refs.parse_bounds("48.81, 2.22, 48.9, 2.47") == [{"latitude": 48.81, "longitude": 2.22},
                                                           {"latitude": 48.9, "longitude": 2.47}]
    for bad in ("1,2,3", "a,b,c,d", "48.9,2.22,48.81,2.47", "91,0,92,1"):
        with pytest.raises(ValueError):
            refs.parse_bounds(bad)


def test_links():
    assert refs.hotel_link("ho434012") == "https://www.hotels.com/ho434012/"
    assert refs.hotel_link("ho434012", "GB") == "https://uk.hotels.com/ho434012/"
    assert refs.expedia_link("6136437") == "https://www.hotels.com/h6136437.Hotel-Information"
    assert refs.hotel_id_from_link("https://www.hotels.com/ho174047/villa-eugenie/?chkin=2026-12-10") == "ho174047"
    assert refs.expedia_id_from_link("https://www.hotels.com/ho174047/x/?expediaPropertyId=558560") == "558560"
    assert refs.PUBLIC_COUNTRIES == ("US", "GB") and all(c in refs.POS for c in refs.PUBLIC_COUNTRIES)


# ---- value helpers -------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("$1,199", 1199.0), ("CNY2,296", 2296.0), ("£303 total", 303.0), ("8.104,64", 8104.64),
    ("1 353 €", 1353.0), ("+ CNY504", 504.0), ("- $20", -20.0), ("", None), (None, None), ("free", None),
])
def test_parse_amount(text, expected):
    assert P.parse_amount(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("$303", "USD"), ("CNY2,296", "CNY"), ("£303", "GBP"), ("€120", "EUR"), ("A$400", "AUD"), ("¥12,000", "JPY"),
    ("2,296 CNY", "CNY"), ("303", "USD"),
])
def test_currency_of(text, expected):
    assert P.currency_of(text) == expected


def test_text_helpers():
    assert P._text("<p>Hello&nbsp;<b>world</b></p><p>Second</p>") == "Hello world\nSecond"
    assert P._items_of_markup("<ul><li><strong>Breakfast</strong> EUR 5</li><li>Shuttle</li></ul>") == ["Breakfast EUR 5", "Shuttle"]
    assert P._items_of_markup("Plain text<br/>") == ["Plain text"]
    assert P._date({"day": 7, "month": 11, "year": 2026}) == "2026-11-07"
    assert P._date({}) is None
    assert P._date_text("Oct 21, 2026") == "2026-10-21"
    assert P._date_text("21 October 2026") == "2026-10-21"
    assert P._date_text("yesterday") is None
    assert P._score("8/10 Very good") == (8.0, "Very good")
    assert P._score("Excellent") == (None, "Excellent")


def test_unit_features():
    out = P._unit_features([{"text": "28 sq m • Sleeps 2 • 1 King Bed"}, {"text": "City view"}])
    assert out["size_sqm"] == 28.0 and out["size_sqft"] == 301.0 and out["sleeps"] == 2
    assert out["beds"] == ["1 King Bed"] and out["views"] == ["City view"] and out["other"] == []
    out = P._unit_features([{"text": "350 sq ft • Sleeps 4 • 2 Queen Beds • Non-smoking"}])
    assert out["size_sqft"] == 350.0 and out["size_sqm"] == 32.5 and out["beds"] == ["2 Queen Beds"]
    assert out["other"] == ["Non-smoking"]
    assert P._unit_features(None) == {"size_sqm": None, "size_sqft": None, "sleeps": None, "beds": [], "views": [], "other": []}


# ---- transport helpers ------------------------------------------------------------------------

def test_challenge_page():
    html = fixture("challenge_bot_or_not.html")
    assert page_title(html) == "Bot or Not?"
    assert is_challenge(html)
    assert P.apollo_state(html) is None
    assert P.parse_hotel_page(html) is None
    assert not is_challenge(fixture("hotel_ho434012.html"))


# ---- typeahead -------------------------------------------------------------------------------

def test_parse_autocomplete_regions():
    rows = P.parse_autocomplete(fixture("typeahead_paris.json"))
    assert rows and rows[0]["type"] == "city" and rows[0]["region_id"] == "2734"
    assert rows[0]["name"] == "Paris, France" and rows[0]["short_name"] == "Paris"
    assert rows[0]["country"] == {"name": "France", "code": "FR", "code_3": "FRA"}
    assert rows[0]["coordinates"] == {"latitude": 48.853564, "longitude": 2.348095}
    assert rows[0]["nearest_airport"] == "PAR" and rows[0]["id"] is None and rows[0]["address"] is None
    assert {r["type"] for r in rows} <= {"city", "neighborhood", "airport", "poi", "multicity", "hotel", "trainstation", "multiregion"}


def test_parse_autocomplete_hotels():
    rows = P.parse_autocomplete(fixture("typeahead_hilton_tokyo.json"))
    hotel = rows[0]
    assert hotel["type"] == "hotel" and hotel["id"] == "22597" and hotel["region_id"] is None
    assert hotel["short_name"] == "Hilton Tokyo" and hotel["link"] == "https://www.hotels.com/h22597.Hotel-Information"
    assert hotel["address"] == {"street": "6-6-2, Nishi-Shinjuku, Shinjuku-ku", "city": "Tokyo", "region": "Tokyo-to"}
    assert hotel["city_region_id"] == "3593" and hotel["country"]["code_3"] == "JPN"
    assert P.parse_autocomplete({}) == [] and P.parse_autocomplete({"sr": [None, 3]}) == []


# ---- search ------------------------------------------------------------------------------------

def _search_fixture():
    for name in ("graphql_search_las_vegas.json", "graphql_search_tokyo_dateless_old.json"):
        if has_fixture(name):
            return name
    pytest.skip("no search fixture")


def test_parse_search_page():
    parsed = P.parse_search(gql(_search_fixture(), "propertySearch"))
    assert parsed["location"]["region_id"] and parsed["location"]["name"]
    assert isinstance(parsed["total_count"], int) and parsed["total_count"] > 100
    assert parsed["has_next"] is True and parsed["next_start"] == 50
    rows = parsed["results"]
    assert len(rows) == 50
    first = rows[0]
    assert first["id"].isdigit() and first["hotel_id"].startswith("ho") and first["name"]
    assert first["link"] == f"https://www.hotels.com/{first['hotel_id']}/"
    assert first["rating"]["max"] == 10 and (first["rating"]["score"] is None or 0 < first["rating"]["score"] <= 10)
    assert first["rating"]["review_count"] is None or first["rating"]["review_count"] > 0
    assert first["coordinates"] and -90 <= first["coordinates"]["latitude"] <= 90
    assert first["photos"] and first["photos"][0]["link"].startswith("https://")
    priced = [r for r in rows if r["price"]["total"] is not None]
    assert len(priced) >= 40
    assert all(r["price"]["currency"] == "USD" for r in priced)
    assert all(r["price"]["nights"] == 2 for r in priced if r["price"]["nightly"])
    assert any(r["price"]["original_total"] for r in rows)                       # strikeout prices exist
    assert all(r["price"]["dates_text"] for r in priced)
    assert 0 < sum(1 for r in rows if r["is_sponsored"]) <= 15      # the "Ad" placements among the 50
    assert all("__typename" not in json.dumps(r) for r in rows)


def test_parse_search_las_vegas():
    parsed = P.parse_search(gql("graphql_search_las_vegas.json", "propertySearch"))
    assert parsed["location"]["name"] == "Las Vegas, Nevada, United States of America"
    assert parsed["total_count"] == 285 and parsed["has_next"] is True and len(parsed["results"]) == 50
    assert parsed["checkin"] == "2027-06-11" and parsed["checkout"] == "2027-06-13"
    rows = parsed["results"]
    assert all(r["area"] for r in rows)                                      # "Las Vegas Strip"
    assert sum(1 for r in rows if r["is_sponsored"]) == 10
    fontainebleau = next(r for r in rows if r["name"].startswith("Fontainebleau Las Vegas"))
    assert fontainebleau["area"] == "Las Vegas Strip"
    assert fontainebleau["rating"] == {"score": 9.4, "max": 10, "label": "Exceptional", "review_count": 19064, "is_new": False}
    assert fontainebleau["price"]["nightly"] == 263.0 and fontainebleau["price"]["total"] == 722.0
    assert fontainebleau["price"]["original_total"] == 871.0 and fontainebleau["price"]["is_taxes_included"] is True
    assert "VIP Access" in fontainebleau["badges"]
    assert "is_pay_later" not in fontainebleau                              # no such signal on the card
    henderson = next(r for r in rows if r["name"].startswith("SpringHill Suites by Marriott Las Vegas Henderson"))
    assert henderson["area"] == "Green Valley" and henderson["distance_text"] == "11.2 mi from Las Vegas"


def test_parse_search_past_end():
    if not has_fixture("graphql_search_past_end.json"):
        pytest.skip("pod capture missing")
    parsed = P.parse_search(gql("graphql_search_past_end.json", "propertySearch"))
    assert parsed["results"] == [] and parsed["has_next"] is False


def test_parse_search_homes_dateless():
    """A vacation-rental search (stay_type=homes, no dates): no pagination
    block, an unfilled date template in the heading, unit lines before the
    area (pod capture 2026-11-11, trimmed to 4 cards)."""
    parsed = P.parse_search(gql("graphql_search_homes_dateless.json", "propertySearch"))
    assert parsed["has_paging"] is False and parsed["has_next"] is False
    assert parsed["heading"] == "44 Properties in Las Vegas, Nevada, United States of America"
    rows = {r["id"]: r for r in parsed["results"]}
    assert len(rows) == 4 and all(r["area"] == "Paradise" for r in rows.values())
    assert rows["132544001"]["messages"][:2] == ["Entire apartment", "Sleeps 6 · 2 bedrooms · 2 bathrooms"]
    assert rows["138626768"]["messages"][0] == "Sleeps 4 · 1 bathroom"
    assert "Paradise" not in rows["132544001"]["messages"]
    assert P.parse_search(gql("graphql_search_las_vegas.json", "propertySearch"))["has_paging"] is True


def test_results_heading_template():
    assert P._results_heading("39 Properties in Las Vegas. ${startDate} through ${endDate}") == "39 Properties in Las Vegas"
    dated = "90 Properties in Las Vegas, Nevada, United States of America. June 11, 2027 through June 13, 2027"
    assert P._results_heading(dated) == dated
    assert P._results_heading("${count} Properties") is None and P._results_heading(None) is None


def test_unit_lines_are_not_areas():
    for line in ("Entire home", "Entire apartment", "Private room", "Sleeps 4 · 1 bathroom", "2 bedrooms · 1 bathroom"):
        assert P._UNIT_LINE_RE.search(line), line
    for line in ("Paradise", "Las Vegas Strip", "Green Valley, 11.2 mi from Las Vegas", "Entertainment District"):
        assert not P._UNIT_LINE_RE.search(line), line


def test_parse_search_sparse():
    parsed = P.parse_search({})
    assert parsed["results"] == [] and parsed["has_next"] is False and parsed["total_count"] is None
    card = P.parse_card({"__typename": "LodgingCard", "id": "1"})
    assert card["id"] == "1" and card["hotel_id"] is None and card["price"]["total"] is None
    assert card["rating"]["score"] is None and card["photos"] == [] and card["coordinates"] is None


# ---- filters ---------------------------------------------------------------------------------------

def test_parse_filters():
    state = P.apollo_state(fixture("search_page_las_vegas.html"))
    parsed = P.parse_filters(state)
    assert parsed["location"]["region_id"] == "2008" and parsed["total_count"] == 236
    ids = [f["id"] for f in parsed["filters"]]
    assert None not in ids and len(ids) == len(set(ids))
    assert {"star", "guestRating", "amenities", "lodging", "paymentType", "nightly_price", "mealPlan",
            "multi_neighborhood_group", "hotel_brand", "hotelName"} <= set(ids)
    stars = next(f for f in parsed["filters"] if f["id"] == "star")
    assert stars["type"] == "multi_select" and {o["value"] for o in stars["options"]} >= {"30", "40", "50"}
    five = next(o for o in stars["options"] if o["value"] == "50")
    assert five["label"] == "5 stars" and five["count"] == 18 and five["price_from"] == 293.0
    rating = next(f for f in parsed["filters"] if f["id"] == "guestRating")
    assert rating["type"] == "single_select" and {"45", "loved_by_guests"} <= {o["value"] for o in rating["options"]}
    assert next(o for o in rating["options"] if o["value"] == "ANY")["is_default"] is True
    price = next(f for f in parsed["filters"] if f["id"] == "nightly_price")
    assert price["type"] == "price_range" and price["options"][0]["min_label"] == "$30" and price["options"][0]["histogram"]
    assert [o["value"] for o in parsed["sort_options"]] == ["RECOMMENDED", "PRICE_LOW_TO_HIGH", "PRICE_HIGH_TO_LOW",
                                                            "BEST_DEAL", "DISTANCE", "REVIEW_RELEVANT", "PROPERTY_CLASS"]
    assert set(refs.GUEST_RATINGS.values()) <= {o["value"] for o in rating["options"]}
    assert P.parse_filters({}) == {"location": {"region_id": None, "name": None}, "total_count": None,
                                   "filters": [], "sort_options": []}


# ---- reviews ---------------------------------------------------------------------------------------

def _reviews_fixture():
    for name in ("graphql_reviews_140596.json", "graphql_reviews_22597_cn.json"):
        if has_fixture(name):
            return name
    pytest.skip("no reviews fixture")


def test_parse_reviews():
    parsed = P.parse_reviews(gql(_reviews_fixture(), "propertyInfo", "reviewInfo"))
    summary = parsed["summary"]
    assert summary["max"] == 10 and 0 < summary["score"] <= 10 and summary["review_count"] > 0 and summary["label"]
    assert "Cleanliness" in {c["label"] for c in summary["categories"]} and len(summary["categories"]) >= 3
    assert all(c["score"] and c["percentage"] for c in summary["categories"])
    reviews = parsed["reviews"]
    assert len(reviews) >= 10
    first = reviews[0]
    assert first["id"] and first["score"] and first["max"] == 10 and first["label"] and first["date"]
    assert first["is_verified"] is True and first["author"] and first["language"]
    assert first["stay"]["nights"] and first["stay"]["text"]
    assert first["title"] is None or isinstance(first["title"], str)        # empty titles are null
    assert {o["value"] for o in parsed["sort_options"]} == set(refs.REVIEW_SORTS.values())
    assert "traveler_types" not in parsed
    assert not {"travelers", "themes", "helpful_votes"} & set(first)        # hotels.com never fills them


def test_parse_review_sparse_and_responses():
    review = P.parse_review({"id": "x", "reviewScoreWithDescription": {"value": "10/10 Exceptional"},
                             "stayDuration": "Anna, 2-night trip", "submissionTimeLocalized": "Sept 3, 2026",
                             "managementResponses": [{"header": {"text": "Response from Front Desk on Oct 1, 2026"},
                                                      "response": "Thank you<br/>Come again"}]})
    assert review["score"] == 10.0 and review["label"] == "Exceptional"
    assert review["stay"] == {"text": "Anna, 2-night trip", "nights": 2}
    assert review["date"] == "2026-09-03"                                  # the UK storefront's "Sept"
    assert review["management_responses"] == [{"responder": "Front Desk", "date": "2026-10-01", "text": "Thank you\nCome again"}]
    assert review["text"] is None and review["photos"] == []
    assert P.parse_reviews({})["reviews"] == [] and P.parse_reviews(None)["summary"]["score"] is None

def test_parse_calendar_without_availability():
    parsed = P.parse_calendar(gql("graphql_calendar_without_availability.json", "propertyRatesDateSelector"))
    assert parsed["range"]["from"] == "2026-11-07" and parsed["range"]["to"] == "2027-07-05"
    assert parsed["has_prices"] is False and parsed["days"] == [] and parsed["summary"]["priced_days"] == 0
    assert parsed["message"] == "Maximum 28 nights"


def test_parse_calendar_with_prices():
    parsed = P.parse_calendar(gql("graphql_calendar_140596.json", "propertyRatesDateSelector"))
    assert parsed["summary"]["days"] == 241 and parsed["summary"]["priced_days"] == 231
    assert parsed["summary"]["min_price"] == 185.0 and parsed["summary"]["max_price"] == 1251.0
    assert parsed["summary"]["available_days"] is None                     # a price-only calendar
    assert all(d["is_available"] is None and d["can_check_in"] is None for d in parsed["days"])
    assert sum(1 for d in parsed["days"] if d["price"] is None) == 10       # "––" days
    assert parsed["has_prices"] is True and parsed["summary"]["priced_days"] > 30
    day = next(d for d in parsed["days"] if d["price"] is not None)
    assert day["currency"] == "USD" and day["date"] >= parsed["range"]["from"]
    assert parsed["summary"]["min_price"] <= parsed["summary"]["average_price"] <= parsed["summary"]["max_price"]
    assert parsed["summary"]["cheapest_date"]


def test_parse_calendar_sparse():
    parsed = P.parse_calendar({"days": [{"date": {"day": 1, "month": 1, "year": 2027}, "displayPrice": "$99", "available": True,
                                         "checkinValidity": "VALID", "checkoutValidity": "INVALID_DUE_TO_MIN_STAY_NOT_ACHIEVABLE"}]})
    day = parsed["days"][0]
    assert day["price"] == 99.0 and day["can_check_in"] and not day["can_check_out"]
    assert day["check_out_blocked_by"] == "min_stay_not_achievable"
    assert parsed["summary"]["cheapest_date"] == "2027-01-01" and parsed["range"]["from"] is None


# ---- rooms and rates ----------------------------------------------------------------------------------

def test_parse_offers_tokyo_cn():
    parsed = P.parse_offers(gql("graphql_offers_22597_cn.json", "propertyOffers"), "2026-12-10", "2026-12-12")
    assert parsed["is_available"] is True and parsed["room_count"] == 26 and parsed["nights"] == 2
    assert parsed["price"]["from"] == 2296.2 and parsed["price"]["currency"] == "CNY"
    room = parsed["rooms"][0]
    assert room["id"] == "416797" and room["name"] == "Room, 1 King Bed, Bathtub"
    assert room["size_sqm"] == 28.0 and room["sleeps"] == 2 and room["beds"] == ["1 King Bed"] and room["views"] == ["City view"]
    assert room["price"] == {"from": 2296.2, "currency": "CNY", "nights": 2}
    assert len(room["photos"]) == 7 and room["photos"][0]["link"].startswith("https://images.trvl-media.com/")
    assert "Bathroom" in room["amenity_categories"] and "Internet" in room["amenity_categories"]
    plans = room["rate_plans"]
    assert len(plans) >= 2
    assert plans[0]["price"]["amount"] == 2296.2 and plans[0]["payment_model"] == "PAY_NOW"
    assert plans[0]["inventory_type"] == "MERCHANT" and plans[0]["is_pay_at_property"] is False
    assert plans[0]["checkin"] == "2026-12-10" and plans[0]["checkout"] == "2026-12-12"
    later = next(p for p in plans if p["payment_model"] == "PAY_LATER")
    assert later["is_pay_at_property"] is True and later["inventory_type"] == "DIRECT_AGENCY"
    assert {o["type"] for o in later["payment_options"]} == {"PAY_NOW", "PAY_LATER", "PAY_LATER_WITH_DEPOSIT"}
    assert later["payment_options"][1]["notes"][0].startswith("Pay the property directly")
    assert any(r["refund_options"] for r in parsed["rooms"])
    refund = next(r for r in parsed["rooms"] if r["refund_options"])["refund_options"]
    assert all(o["label"] for o in refund) and any(o["is_selected"] for o in refund)
    assert all("__typename" not in json.dumps(r) for r in parsed["rooms"])


def test_parse_offers_us():
    parsed = P.parse_offers(gql("graphql_offers_140596.json", "propertyOffers"), "2027-06-11", "2027-06-13")
    assert parsed["room_count"] == 15 and parsed["price"] == {"from": 1096.17, "currency": "USD", "is_per_stay": True}
    room = parsed["rooms"][0]
    assert room["name"] == "Premier Two Queen" and room["size_sqft"] == 510.0 and room["sleeps"] == 4
    assert room["beds"] == ["2 Queen Beds"] and room["photos"]
    plan = room["rate_plans"][0]
    assert plan["price"]["amount"] == 1096.17 and plan["price"]["original_amount"] == 1666.72
    assert plan["payment_model"] == "PAY_NOW" and plan["checkin"] == "2027-06-11"
    labels = [o["label"] for o in room["refund_options"]]
    assert "Non-Refundable" in labels and any(l.startswith("Fully refundable before") for l in labels)
    refundable = next(o for o in room["refund_options"] if o["label"].startswith("Fully refundable"))
    assert refundable["price_difference"] == 51.0 and refundable["currency"] == "USD"


def test_parse_offers_sparse():
    parsed = P.parse_offers({"errorMessage": {"title": {"text": "No rooms for these dates"}}, "categorizedListings": [
        {"__typename": "MessageResult", "title": {"text": "Sold out"}, "subtitle": {"text": "Try other dates"}}]})
    assert parsed["is_available"] is False and parsed["rooms"] == [] and parsed["message"] == "No rooms for these dates"
    assert P.parse_offers(None)["room_count"] == 0
    unit = P.parse_unit({"unitId": "1", "header": {"text": "Suite"}})
    assert unit["is_available"] is False and unit["rate_plans"] == [] and unit["price"]["from"] is None


# ---- hotel page ---------------------------------------------------------------------------------------

def test_hotel_page_identity(anastassiou):
    r = anastassiou
    assert r["id"] == "6136437" and r["hotel_id"] == "ho434012" and r["name"] == "Hotel Anastassiou"
    assert r["link"] == "https://www.hotels.com/ho434012/" and r["star_rating"] == 2.0
    assert r["tagline"] == "Ski hotel with ski shuttle, near Kastoria Lake"
    assert r["description"].startswith("Placing you just steps from Kastoria Lake")
    assert r["rating"] == {"score": 8.0, "max": 10, "label": "Very good", "review_count": 76}


def test_hotel_page_location(anastassiou):
    loc = anastassiou["location"]
    assert loc["address"] == "1st km Highway Kastoria-Athens" and loc["city"] == "Kastoria"
    assert loc["region"] == "Western Macedonia" and loc["postal_code"] == "52100" and loc["country_code"] == "GRC"
    assert loc["region_id"] == "1928" and loc["coordinates"] == {"latitude": 40.498285, "longitude": 21.266815}
    nearby = {g["category"]: g["places"] for g in loc["nearby"]}
    assert nearby["What's nearby"][0] == {"name": "Kastoria Lake", "distance": "0.5 km", "travel_time": "5 min walk"}
    assert nearby["Getting around"][0]["name"] == "Kastoria (KSO-Aristoteles)" and nearby["Getting around"][0]["travel_time"] == "9 min drive"
    assert "Restaurants" in nearby


def test_hotel_page_content(anastassiou):
    r = anastassiou
    assert r["check_in_time"] == "1:00 PM" and r["check_out_time"] == "noon"
    assert r["price_from"]["nightly"] == 42.0 and r["price_from"]["total"] == 49.0 and r["price_from"]["currency"] == "USD"
    assert r["price_from"]["is_taxes_included"] is True and r["price_from"]["text"] == "from $49"
    assert "Free parking" in r["popular_amenities"]
    sections = {(g["section"], g["category"]): g["items"] for g in r["amenities"]}
    assert "Restaurant" in sections[("Property amenities", "Food and drink")]
    assert "Free WiFi" in sections[("Room amenities", "Stay connected")]
    assert len(r["amenities"]) == 15
    policies = r["policies"]
    assert len(policies["mandatory_fees"]) == 3 and policies["mandatory_fees"][1].startswith("A tax is imposed by the city: From 1 November")
    assert policies["optional_extras"][0].startswith("Buffet breakfast is offered for an extra charge")
    assert policies["pets"] == ["Service animals exempt from fees"]
    assert any("credit cards" in p.lower() for p in policies["policies"])
    assert "Anastassiou Hotel" in policies["also_known_as"]
    assert len(r["faq"]) == 9 and r["faq"][0]["question"].startswith("Does Hotel Anastassiou offer free cancellation")
    assert len(r["photos"]) == 41
    assert r["photos"][0]["link"].startswith("https://images.trvl-media.com/") and r["photos"][0]["thumbnail"]
    assert {p["category"] for p in r["photos"]} >= {"Rooms", "Exterior", "Dining"}
    assert "__typename" not in json.dumps(r)


def test_hotel_page_sparse():
    assert P.parse_hotel_page("") is None
    assert P.parse_hotel_page('<html>window.__APOLLO_STATE__ = JSON.parse("{\\"ROOT_QUERY\\":{}}");</html>') is None


def test_unknown_hotel_redirects_home():
    if not has_fixture("unknown_hotel.html"):
        pytest.skip("pod capture missing")
    html = fixture("unknown_hotel.html")
    assert "PropertyInfo:" not in html and P.parse_hotel_page(html) is None


# ---- api helpers (pure) ----------------------------------------------------------------------------------

def test_api_destination_and_criteria():
    assert api._destination({"region_id": "2734"}, None, None, None) == {"regionId": "2734"}
    assert api._destination({"region_name": "Tokyo"}, None, None, None) == {"regionName": "Tokyo"}
    assert api._destination(None, 48.8, 2.3, None) == {"coordinates": {"latitude": 48.8, "longitude": 2.3}}
    assert api._destination(None, None, None, [{"latitude": 1, "longitude": 2}, {"latitude": 3, "longitude": 4}]) == {
        "mapBounds": [{"latitude": 1, "longitude": 2}, {"latitude": 3, "longitude": 4}]}
    selections, ranges = api._criteria({
        "stars": ["40", "50"], "guest_rating": "40", "amenities": ["POOL"], "property_type": ["HOTEL"],
        "meal_plan": ["FREE_BREAKFAST"], "free_cancellation": True, "pay_later": True, "name": "Hilton",
        "neighborhood": ["123"], "brand": ["hilton"], "traveler_type": ["family_friendly", "adults_only"],
        "accessibility": ["ELEVATOR"], "stay_type": "vacation_rentals_option", "min_price": 100.0, "max_price": None})
    assert {"id": "star", "value": "40"} in selections and {"id": "star", "value": "50"} in selections
    assert {"id": "guestRating", "value": "40"} in selections and {"id": "amenities", "value": "POOL"} in selections
    assert {"id": "lodging", "value": "HOTEL"} in selections and {"id": "mealPlan", "value": "FREE_BREAKFAST"} in selections
    assert {"id": "propertyCancellation", "value": "FREE_CANCELLATION"} in selections
    assert {"id": "paymentType", "value": "FREE_CANCELLATION"} in selections and {"id": "paymentType", "value": "PAY_LATER"} in selections
    assert {"id": "hotelName", "value": "Hilton"} in selections and {"id": "multi_neighborhood_group", "value": "123"} in selections
    assert {"id": "hotel_brand", "value": "hilton"} in selections
    assert {"id": "travelerType", "value": "family_friendly"} in selections and {"id": "travelerType", "value": "adults_only"} in selections
    assert {"id": "stay_options_group", "value": "vacation_rentals_option"} in selections
    assert ranges == [{"id": "nightly_price", "min": 100, "max": -2}]
    assert api._criteria({}) == ([], [])
    assert api._storefront("GB") == {"country": "gb", "host": "https://uk.hotels.com", "currency": "GBP", "language": "en_GB"}


def _search_data(**extra):
    data = {"page": 1, "limit": 50, "country": "US", "adults": 2, "rooms": 1, "sort": "RECOMMENDED",
            "location": {"region_name": "Las Vegas"}, "children": None, "checkin": "2027-06-11", "checkout": "2027-06-13"}
    data.update(extra)
    return data


def test_api_search_totals(monkeypatch):
    """The site's matched count is kept unless the filter is one it ignores
    (brand, hotel name, loved_by_guests: null total, has_next only), and the
    last page makes the total exact."""
    full = gql("graphql_search_las_vegas.json", "propertySearch")
    monkeypatch.setattr(api, "fetch_search", lambda *a, **k: full)
    out = api._search(**_search_data())
    assert out["pagination"] == {"page": 1, "items_per_page": 50, "has_next": True, "total_pages": 6, "total_count": 285}
    out = api._search(**_search_data(brand=["2102"]))
    assert out["pagination"]["total_count"] is None and out["pagination"]["total_pages"] is None
    assert out["pagination"]["has_next"] is True
    out = api._search(**_search_data(guest_rating="loved_by_guests"))
    assert out["pagination"]["total_count"] is None
    last = dict(full, pagination=None, propertySearchListings=full["propertySearchListings"][:30])
    monkeypatch.setattr(api, "fetch_search", lambda *a, **k: last)
    cards = sum(1 for c in last["propertySearchListings"] if c.get("__typename") == "LodgingCard")
    out = api._search(**_search_data(page=2, brand=["2102"]))
    assert out["pagination"] == {"page": 2, "items_per_page": 50, "has_next": False, "total_pages": 2,
                                 "total_count": 50 + cards}


def test_api_search_without_pagination_block(monkeypatch):
    """No pagination block (homes searches, some filtered pages): the site's
    count is wrong (39 homes, then 50 more on page 2, 2026-11-11), so a full
    page says "maybe more" with no total, an over-full page is cut to the
    limit, and a short page ends the set."""
    full = gql("graphql_search_las_vegas.json", "propertySearch")
    cards = [c for c in full["propertySearchListings"] if c.get("__typename") == "LodgingCard"]
    homes = dict(full, pagination=None, summary={"matchedPropertiesSize": 39, "resultsHeading": "39 Properties"})
    monkeypatch.setattr(api, "fetch_search", lambda *a, **k: dict(homes, propertySearchListings=cards))
    out = api._search(**_search_data())
    assert out["pagination"] == {"page": 1, "items_per_page": 50, "has_next": True, "total_pages": None, "total_count": None}
    assert len(out["results"]) == 50
    out = api._search(**_search_data(page=2, limit=20))
    assert len(out["results"]) == 20 and out["pagination"]["has_next"] is True     # 50 cards for 20 asked
    monkeypatch.setattr(api, "fetch_search", lambda *a, **k: dict(homes, propertySearchListings=cards[:21]))
    out = api._search(**_search_data(page=2))
    assert out["pagination"] == {"page": 2, "items_per_page": 50, "has_next": False, "total_pages": 2, "total_count": 71}
    monkeypatch.setattr(api, "fetch_search", lambda *a, **k: dict(homes, propertySearchListings=[]))
    out = api._search(**_search_data(page=3))
    assert out["results"] == [] and out["pagination"]["has_next"] is False and out["pagination"]["total_count"] is None


def test_api_search_map_area_location(monkeypatch):
    full = gql("graphql_search_las_vegas.json", "propertySearch")
    monkeypatch.setattr(api, "fetch_search", lambda *a, **k: full)
    box = [{"latitude": 36.08, "longitude": -115.2}, {"latitude": 36.15, "longitude": -115.13}]
    out = api._search(**_search_data(location=None, bounds=box))
    assert out["location"] == {"region_id": None, "name": None} and len(out["results"]) == 50
    assert api._search(**_search_data())["location"]["region_id"]


def test_check_times_wordings():
    faq = lambda answer: [{"question": "What are the check-in and check-out times at X?", "answer": answer}]
    assert P._check_times(faq("You can check in starting at 1:00 PM. Check-out time is noon. Express check-in is available.")) == ("1:00 PM", "noon")
    assert P._check_times(faq("Check-in start time: 3:00 PM; Check-in end time: midnight. Check-out time is 11 AM. "
                              "Late check-out is available for a charge (subject to availability).")) == ("3:00 PM", "11 AM")
    assert P._check_times([]) == (None, None)


def test_nights_from_the_dates_line():
    assert P._nights_between("Dec 9 - Dec 10") == 1
    assert P._nights_between("Jun 11 - Jun 13") == 2
    assert P._nights_between("Dec 30 - Jan 2") == 3
    assert P._nights_between("tonight") is None and P._nights_between(None) is None
    # the line names no year, and February's length hangs on the check-in year
    assert P._nights_between("Feb 28 - Mar 1", 2027) == 1 and P._nights_between("Feb 28 - Mar 1", 2028) == 2
    assert P._nights_between("Feb 29 - Mar 2", 2028) == 2 and P._nights_between("Dec 30 - Jan 2", 2027) == 3
    assert P._nights_between("Feb 29 - Mar 1") == 1          # without a year: the next year that has the day
    # a search card takes the year from the search's own dates
    card = {"__typename": "LodgingCard", "id": "1", "priceSection": {"priceSummary": {"displayMessages": [
        {"lineItems": [{"__typename": "LodgingEnrichedMessage", "value": "Feb 28 - Mar 1"}]}]}}}
    nights = lambda year: P.parse_search({"propertySearchListings": [card], "criteria": {"primary": {"dateRange": {
        "checkInDate": {"day": 28, "month": 2, "year": year},
        "checkOutDate": {"day": 1, "month": 3, "year": year}}}}})["results"][0]["price"]["nights"]
    assert nights(2027) == 1 and nights(2028) == 2
    # resort fees and taxes: $136 nightly, $216 total, still ONE night
    block = {"displayMessages": [
        {"lineItems": [{"__typename": "LodgingEnrichedMessage", "value": "$136 nightly"}]},
        {"lineItems": [{"__typename": "DisplayPrice", "role": "LEAD", "price": {"formatted": "$216 total"}}]},
        {"lineItems": [{"__typename": "LodgingEnrichedMessage", "value": "Dec 9 - Dec 10"}]},
        {"lineItems": [{"__typename": "LodgingEnrichedMessage", "value": "Total with taxes and fees"}]}]}
    price = P._price_lines(block)
    assert price["nightly"] == 136.0 and price["total"] == 216.0 and price["nights"] == 1
    assert price["is_taxes_included"] is True and price["dates_text"] == "Dec 9 - Dec 10"


def test_nightly_price_is_the_nightly_line_only():
    """A loyalty line that mentions a night must not become the nightly rate
    (Luxor read "$8" from such a line, 2026-11-10)."""
    block = {"displayMessages": [
        {"lineItems": [{"__typename": "LodgingEnrichedMessage", "value": "$53 nightly"}]},
        {"lineItems": [{"__typename": "DisplayPrice", "role": "LEAD", "price": {"formatted": "$61 total"}}]},
        {"lineItems": [{"__typename": "LodgingEnrichedMessage", "value": "Earn $8 in OneKeyCash per night"}]},
        {"lineItems": [{"__typename": "LodgingEnrichedMessage", "value": "Nov 22 - Nov 23"}]}]}
    price = P._price_lines(block)
    assert price["nightly"] == 53.0 and price["total"] == 61.0 and price["nights"] == 1
    late = {"displayMessages": [{"lineItems": [{"__typename": "LodgingEnrichedMessage", "value": "$8 off per night"}]}]}
    assert P._price_lines(late)["nightly"] is None


def test_api_reviews_paging_follows_the_count(monkeypatch):
    """Keyword pages can be short (47 of 50); the next link follows the
    summary's count (the matching total), never a short page."""
    info = gql("graphql_reviews_140596.json", "propertyInfo", "reviewInfo")
    short = dict(info, summary=dict(info["summary"], totalCount={"raw": 337.0, "formatted": "337"}),
                 reviews=info["reviews"][:17])
    monkeypatch.setattr(api, "_record", lambda hotel, country: {"id": "140596", "hotel_id": "ho119566", "name": "Bellagio",
                                                                "link": "https://www.hotels.com/ho119566/"})
    monkeypatch.setattr(api, "fetch_reviews", lambda *a, **k: short)
    out = api._reviews(hotel="ho119566", page=1, limit=20, sort="NEWEST_TO_OLDEST", query="pool", country="US")
    assert out["pagination"] == {"page": 1, "items_per_page": 20, "has_next": True, "total_pages": 17, "total_count": 337}
    out = api._reviews(hotel="ho119566", page=17, limit=20, sort="NEWEST_TO_OLDEST", query="pool", country="US")
    assert out["pagination"]["has_next"] is False
    assert "traveler_type" not in out and "traveler_types" not in out
