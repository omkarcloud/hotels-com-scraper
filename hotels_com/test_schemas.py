"""Offline tests for the /hotels-com/* request schemas.

    python -m pytest hotels_com/test_schemas.py -q
"""
import os
import sys
from datetime import date, timedelta

import pytest
from marshmallow import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hotels_com import schemas  # noqa: E402

IN = (date.today() + timedelta(days=30)).isoformat()
OUT = (date.today() + timedelta(days=34)).isoformat()
PAST = (date.today() - timedelta(days=3)).isoformat()


def _error(schema, data):
    with pytest.raises(ValidationError) as info:
        schema().load(data)
    return info.value.normalized_messages()


# ---- locations/autocomplete -------------------------------------------------------------

def test_autocomplete_schema():
    assert schemas.AutocompleteSchema().load({"query": " Paris "}) == {"query": "Paris", "limit": 10, "country": "US"}
    assert schemas.AutocompleteSchema().load({"query": "Paris", "country": "gb"})["country"] == "GB"
    assert "query" in _error(schemas.AutocompleteSchema, {})
    assert "limit" in _error(schemas.AutocompleteSchema, {"query": "x", "limit": "21"})
    errors = _error(schemas.AutocompleteSchema, {"query": "x", "country": "de"})
    assert "country" in errors and "us, gb" in errors["country"][0]
    assert "bogus" in _error(schemas.AutocompleteSchema, {"query": "x", "bogus": "1"})


# ---- hotels/search ----------------------------------------------------------------------

def test_search_schema_location_forms():
    loaded = schemas.SearchSchema().load({"location": "Paris, France"})
    assert loaded["location"] == {"region_name": "Paris, France"}
    assert loaded["sort"] == "RECOMMENDED" and loaded["page"] == 1 and loaded["adults"] == 2
    assert loaded["limit"] == 50 and loaded["rooms"] == 1 and loaded["checkin"] is None and loaded["country"] == "US"
    assert schemas.SearchSchema().load({"location": "2734"})["location"] == {"region_id": "2734"}
    loaded = schemas.SearchSchema().load({"location": "https://www.hotels.com/Hotel-Search?destination=Paris&regionId=2734"})
    assert loaded["location"] == {"region_id": "2734"}
    loaded = schemas.SearchSchema().load({"latitude": "48.85", "longitude": "2.35"})
    assert loaded["latitude"] == 48.85 and loaded["longitude"] == 2.35 and loaded["location"] is None
    loaded = schemas.SearchSchema().load({"bounds": "48.81,2.22,48.90,2.47"})
    assert loaded["bounds"][1] == {"latitude": 48.90, "longitude": 2.47}


def test_search_schema_filters():
    loaded = schemas.SearchSchema().load({
        "location": "Paris", "checkin": IN, "checkout": OUT, "adults": "3", "children": "5, 9", "rooms": "2",
        "sort": "Price_Low_To_High", "page": "3", "limit": "200", "min_price": "100", "max_price": "400",
        "stars": "4,5", "guest_rating": "8+", "amenities": "pool, wifi", "property_type": "hotel,apartment",
        "meal_plan": "free_breakfast", "free_cancellation": "yes", "pay_later": "0", "name": "Hilton",
        "neighborhood": "6240961", "brand": "hilton", "traveler_type": "Family, luxury", "accessibility": "elevator",
        "stay_type": "Homes", "country": "GB"})
    assert loaded["children"] == [5, 9] and loaded["adults"] == 3 and loaded["rooms"] == 2
    assert loaded["sort"] == "PRICE_LOW_TO_HIGH" and loaded["page"] == 3 and loaded["limit"] == 200
    assert loaded["stars"] == ["40", "50"] and loaded["guest_rating"] == "40"
    assert loaded["amenities"] == ["POOL", "WIFI"] and loaded["property_type"] == ["HOTEL", "APARTMENT"]
    assert loaded["meal_plan"] == ["FREE_BREAKFAST"] and loaded["free_cancellation"] is True and loaded["pay_later"] is False
    assert loaded["name"] == "Hilton" and loaded["neighborhood"] == ["6240961"] and loaded["brand"] == ["hilton"]
    assert loaded["traveler_type"] == ["family_friendly", "luxury_property"] and loaded["accessibility"] == ["ELEVATOR"]
    assert loaded["stay_type"] == "vacation_rentals_option" and loaded["country"] == "GB"


def test_search_schema_rejections():
    assert "location" in _error(schemas.SearchSchema, {})
    assert "location" in _error(schemas.SearchSchema, {"location": "Paris", "latitude": "1", "longitude": "2"})
    assert "latitude" in _error(schemas.SearchSchema, {"latitude": "48.8"})
    assert "bounds" in _error(schemas.SearchSchema, {"bounds": "1,2,3"})
    assert "checkout" in _error(schemas.SearchSchema, {"location": "Paris", "checkin": IN})
    assert "checkout" in _error(schemas.SearchSchema, {"location": "Paris", "checkin": OUT, "checkout": IN})
    assert "checkin" in _error(schemas.SearchSchema, {"location": "Paris", "checkin": PAST, "checkout": IN})
    assert "sort" in _error(schemas.SearchSchema, {"location": "Paris", "sort": "cheapest"})
    assert "stars" in _error(schemas.SearchSchema, {"location": "Paris", "stars": "6"})
    assert "guest_rating" in _error(schemas.SearchSchema, {"location": "Paris", "guest_rating": "6+"})
    assert "children" in _error(schemas.SearchSchema, {"location": "Paris", "children": "5,18"})
    assert "max_price" in _error(schemas.SearchSchema, {"location": "Paris", "min_price": "500", "max_price": "100"})
    assert "limit" in _error(schemas.SearchSchema, {"location": "Paris", "limit": "201"})
    assert "page" in _error(schemas.SearchSchema, {"location": "Paris", "page": "21"})
    assert "rooms" in _error(schemas.SearchSchema, {"location": "Paris", "rooms": "9"})
    errors = _error(schemas.SearchSchema, {"location": "Paris", "traveler_type": "couple"})
    assert "traveler_type" in errors and "adults_only" in errors["traveler_type"][0]
    assert "stay_type" in _error(schemas.SearchSchema, {"location": "Paris", "stay_type": "villas"})
    assert "free_cancellation" in _error(schemas.SearchSchema, {"location": "Paris", "free_cancellation": "maybe"})
    assert "location" in _error(schemas.SearchSchema, {"location": "https://www.booking.com/searchresults?ss=Paris"})


# ---- hotels/filters ---------------------------------------------------------------------

def test_filters_schema():
    loaded = schemas.FiltersSchema().load({"location": "Paris", "checkin": IN, "checkout": OUT})
    assert loaded["location"] == {"region_name": "Paris"} and loaded["adults"] == 2 and loaded["country"] == "US"
    assert "location" in _error(schemas.FiltersSchema, {})
    assert "checkout" in _error(schemas.FiltersSchema, {"location": "Paris", "checkin": IN})


# ---- hotels/details / rooms / reviews / calendar -----------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("ho434012", "ho434012"),
    ("HO434012", "ho434012"),
    ("6136437", "6136437"),
    ("https://www.hotels.com/ho434012/", "ho434012"),
    ("https://www.hotels.com/ho174047/villa-eugenie-paris-france/?chkin=2026-12-10&expediaPropertyId=558560", "ho174047"),
    ("https://uk.hotels.com/ho434012/hotel-anastassiou-kastoria/", "ho434012"),
    ("https://www.hotels.com/h6136437.Hotel-Information", "6136437"),
    ("https://www.expedia.com/Kastoria-Hotels-Hotel-Anastassiou.h6136437.Hotel-Information?chkin=2026-12-01", "6136437"),
    ("https://www.hotels.com/Hotel-Search?expediaPropertyId=558560", "558560"),
])
def test_hotel_ref_forms(value, expected):
    assert schemas.HotelDetailsSchema().load({"hotel": value})["hotel"] == expected


@pytest.mark.parametrize("value", [
    "", "hotel anastassiou", "ho", "https://www.booking.com/hotel/gr/anastassiou.html",
    "https://www.hotels.com/Hotel-Search?destination=Paris",
])
def test_hotel_ref_rejected(value):
    errors = _error(schemas.HotelDetailsSchema, {"hotel": value})
    assert "hotel" in errors
    assert "hotels.com" in errors["hotel"][0] or "empty" in errors["hotel"][0].lower()


def test_rooms_schema():
    loaded = schemas.HotelRoomsSchema().load({"hotel": "ho434012", "checkin": IN, "checkout": OUT, "children": "4"})
    assert loaded["adults"] == 2 and loaded["children"] == [4] and loaded["rooms"] == 1 and loaded["country"] == "US"
    assert "checkin" in _error(schemas.HotelRoomsSchema, {"hotel": "ho434012"})
    assert "checkout" in _error(schemas.HotelRoomsSchema, {"hotel": "ho434012", "checkin": IN})
    far = (date.today() + timedelta(days=30 + 29)).isoformat()
    assert "checkout" in _error(schemas.HotelRoomsSchema, {"hotel": "ho434012", "checkin": IN, "checkout": far})
    assert "adults" in _error(schemas.HotelRoomsSchema, {"hotel": "ho434012", "checkin": IN, "checkout": OUT, "adults": "15"})


def test_reviews_schema():
    loaded = schemas.HotelReviewsSchema().load({"hotel": "6136437", "sort": "highest_rating",
                                               "limit": "50", "page": "2", "query": "breakfast"})
    assert loaded["sort"] == "HIGHEST_TO_LOWEST_RATED"
    assert loaded["limit"] == 50 and loaded["page"] == 2 and loaded["query"] == "breakfast"
    defaults = schemas.HotelReviewsSchema().load({"hotel": "6136437"})
    assert defaults["sort"] == "NEWEST_TO_OLDEST" and defaults["limit"] == 20 and "traveler_type" not in defaults
    assert "limit" in _error(schemas.HotelReviewsSchema, {"hotel": "6136437", "limit": "51"})
    assert "sort" in _error(schemas.HotelReviewsSchema, {"hotel": "6136437", "sort": "oldest"})
    # hotels.com ignores the review traveler-type filter, so the API does not offer it
    assert "traveler_type" in _error(schemas.HotelReviewsSchema, {"hotel": "6136437", "traveler_type": "couples"})


def test_calendar_schema():
    assert schemas.HotelCalendarSchema().load({"hotel": "ho434012"}) == {"hotel": "ho434012", "country": "US"}
    assert "hotel" in _error(schemas.HotelCalendarSchema, {})
    assert "start_date" in _error(schemas.HotelCalendarSchema, {"hotel": "ho434012", "start_date": IN})
