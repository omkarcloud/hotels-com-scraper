"""Marshmallow request schemas for every /hotels-com/* route.

ONE param per input (tripadvisor QueryOrIdField convention, never a sibling
`url` / `id` pair):

    hotel        hotels.com id ("ho434012"), Expedia property id ("6136437")
                 OR any hotels.com / expedia.com hotel link
    location     destination name, region id OR a hotels.com Hotel-Search link
    latitude + longitude / bounds   the two other ways to pick a search area
    country      the storefront (point of sale) that answers: us (default)
                 or gb — it decides the currency (refs.PUBLIC_COUNTRIES)

Filter tokens are hotels.com's own (hotels_com/refs.py; the live list per
destination comes from /hotels-com/hotels/filters).
"""
from datetime import date

from marshmallow import ValidationError, fields, missing, validate, validates_schema

from schema_fields import (BaseSchema, ChoiceField, CommaListField, DateField, Flag, LimitField,
                           PageField, PageSizeField, PositiveInt, Price, QueryField, RefField,
                           StrippedString)
from hotels_com import refs
from hotels_com.fetch import REVIEWS_MAX_SIZE, SEARCH_MAX_SIZE, SEARCH_PAGE_SIZE

MAX_SEARCH_PAGE = 20      # the site caps a destination at ~480 results
MAX_REVIEW_PAGE = 500
MAX_NIGHTS = 28           # the site's own stay limit
MAX_ADULTS = 14           # per room, the site's own limit
MAX_CHILDREN = 6
MAX_ROOMS = 8


class HotelRefField(RefField):
    resolver = staticmethod(refs.resolve_hotel)


class LocationRefField(RefField):
    resolver = staticmethod(refs.resolve_location)


class BoundsField(StrippedString):
    """'south_lat,west_lng,north_lat,east_lng' -> [sw, ne] coordinates."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return None
        try:
            return refs.parse_bounds(value)
        except ValueError as e:
            raise ValidationError(str(e))


class ChildAgesField(CommaListField):
    """Comma-separated child ages (0-17), one per child, up to 6 per room."""

    def __init__(self, **kwargs):
        super().__init__(upper=False, max_items=MAX_CHILDREN, **kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        items = super()._deserialize(value, attr, data, **kwargs)
        if items is None:
            return None
        ages = []
        for item in items:
            if not item.isdigit() or not 0 <= int(item) <= 17:
                raise ValidationError("Child ages must be whole numbers from 0 to 17 (e.g. 5,9).")
            ages.append(int(item))
        return ages


class Coordinate(fields.Float):
    def __init__(self, limit, **kwargs):
        kwargs.setdefault("load_default", None)
        kwargs.setdefault("validate", validate.Range(min=-limit, max=limit))
        super().__init__(**kwargs)


def _check_stay(data, required=False):
    checkin, checkout = data.get("checkin"), data.get("checkout")
    if required and not (checkin and checkout):
        raise ValidationError("checkin and checkout are both required.", "checkin")
    if bool(checkin) != bool(checkout):
        raise ValidationError("checkin and checkout go together.", "checkout" if checkin else "checkin")
    if checkin and checkout:
        nights = (date.fromisoformat(checkout) - date.fromisoformat(checkin)).days
        if nights < 1:
            raise ValidationError("checkout must be after checkin.", "checkout")
        if nights > MAX_NIGHTS:
            raise ValidationError(f"Stays are at most {MAX_NIGHTS} nights on hotels.com.", "checkout")


_CHECKIN = dict(bound="future", metadata={"description": "check-in date, YYYY-MM-DD (today or later)"})
_CHECKOUT = dict(bound="future", metadata={"description": "check-out date, YYYY-MM-DD, at most 28 nights after check-in"})
_ADULTS = dict(max_value=MAX_ADULTS, load_default=2, metadata={"description": "adult guests per room (default 2)"})
_CHILDREN = dict(metadata={"description": "child ages per room, comma-separated, one per child (e.g. 5,9)"})
_ROOMS = dict(max_value=MAX_ROOMS, load_default=1, metadata={"description": "rooms to book, 1-8 (default 1), each with the same guests"})
_COUNTRY = dict(load_default="US", metadata={
    "description": "hotels.com storefront that answers, which sets the currency: us (default, USD) or gb (GBP)"})
_HOTEL = dict(metadata={
    "description": "hotels.com id (ho434012), Expedia property id (6136437) or a hotels.com / expedia.com hotel link"})


def country_field():
    return ChoiceField({code.lower(): code for code in refs.PUBLIC_COUNTRIES}, **_COUNTRY)


# ---- locations/autocomplete -------------------------------------------------------------

class AutocompleteSchema(BaseSchema):
    query = QueryField(max_length=100, metadata={
        "description": "place or hotel name as a traveler would type it (Paris, Times Square, Hilton Tokyo)"})
    limit = LimitField(default=10, max_size=20, metadata={"description": "suggestions to return, 1-20"})
    country = country_field()


# ---- hotels/search ----------------------------------------------------------------------

class SearchSchema(BaseSchema):
    location = LocationRefField(required=False, load_default=None, metadata={
        "description": "destination name (Paris, France), a region id from "
                       "/hotels-com/locations/autocomplete, or a hotels.com Hotel-Search link"})
    latitude = Coordinate(90, metadata={"description": "search around this point (with longitude) instead of a location"})
    longitude = Coordinate(180, metadata={"description": "search around this point (with latitude)"})
    bounds = BoundsField(metadata={
        "description": "map area to search instead of a location: south_lat,west_lng,north_lat,east_lng"})
    checkin = DateField(**_CHECKIN)
    checkout = DateField(**_CHECKOUT)
    adults = PositiveInt(**_ADULTS)
    children = ChildAgesField(**_CHILDREN)
    rooms = PositiveInt(**_ROOMS)
    sort = ChoiceField(refs.SORTS, load_default="RECOMMENDED", metadata={
        "description": "recommended (default), price_low_to_high, price_high_to_low, best_deal, distance, "
                       "guest_rating, relevant_reviews, star_rating"})
    page = PageField(max_page=MAX_SEARCH_PAGE, metadata={"description": "result page"})
    limit = PageSizeField(default=SEARCH_PAGE_SIZE, max_size=SEARCH_MAX_SIZE, metadata={
        "description": "hotels per page, 1-200 (default 50)"})
    stars = CommaListField(allowed=list(refs.STARS), value_map=refs.STARS, max_items=5, metadata={
        "description": "star classes to keep, comma-separated (e.g. 4,5)"})
    guest_rating = ChoiceField(refs.GUEST_RATINGS, metadata={
        "description": "minimum guest rating: 9+, 8+, 7+, or loved_by_guests (the site's top-rated pick)"})
    min_price = Price(metadata={"description": "lowest nightly price to keep, in the storefront's currency"})
    max_price = Price(metadata={"description": "highest nightly price to keep, in the storefront's currency"})
    amenities = CommaListField(max_items=20, metadata={
        "description": "comma-separated amenity tokens every result must have, hotels.com's own (POOL, WIFI, PETS, "
                       "PARKING, FREE_AIRPORT_TRANSPORTATION, GYM, SPA_ON_SITE, RESTAURANT_IN_HOTEL, AIR_CONDITIONING, "
                       "KITCHEN_KITCHENETTE, WASHER_DRYER, OCEAN_VIEW, HOT_TUB, ELECTRIC_CAR, ...); see /hotels-com/hotels/filters"})
    property_type = CommaListField(metadata={
        "description": "comma-separated property types, hotels.com's own tokens (HOTEL, HOTEL_RESORT, APARTMENT, "
                       "APART_HOTEL, BED_AND_BREAKFAST, HOSTEL_BACKPACKER, VACATION_HOME, VILLA, CONDO, ...); "
                       "see /hotels-com/hotels/filters"})
    meal_plan = CommaListField(metadata={
        "description": "comma-separated meal plans (FREE_BREAKFAST, ALL_INCLUSIVE, HALF_BOARD, FULL_BOARD); "
                       "see /hotels-com/hotels/filters"})
    free_cancellation = Flag(metadata={"description": "true keeps only fully refundable properties"})
    pay_later = Flag(metadata={"description": "true keeps only reserve-now-pay-later properties"})
    name = StrippedString(required=False, load_default=None, validate=validate.Length(max=100), metadata={
        "description": "a hotel name to put first in the results (Bellagio); the site may also narrow to it"})
    neighborhood = CommaListField(upper=False, metadata={
        "description": "comma-separated neighborhood region ids (from /hotels-com/hotels/filters)"})
    brand = CommaListField(upper=False, metadata={
        "description": "comma-separated hotel brand ids (from /hotels-com/hotels/filters)"})
    traveler_type = CommaListField(value_map=refs.SEARCH_TRAVELER_TYPES, max_items=len(refs.SEARCH_TRAVELER_TYPES), metadata={
        "description": "comma-separated traveler experiences to keep: family, business, luxury, budget, adults_only, "
                       "romantic, wedding, lgbtq_welcoming, eco_certified"})
    stay_type = ChoiceField(refs.STAY_TYPES, metadata={
        "description": "hotels keeps hotels only, homes keeps vacation rentals only"})
    accessibility = CommaListField(metadata={
        "description": "comma-separated accessibility tokens (from /hotels-com/hotels/filters)"})
    country = country_field()

    @validates_schema
    def _one_area(self, data, **kwargs):
        has_point = data.get("latitude") is not None or data.get("longitude") is not None
        if has_point and (data.get("latitude") is None or data.get("longitude") is None):
            raise ValidationError("latitude and longitude go together.", "latitude")
        ways = sum(1 for present in (data.get("location"), has_point, data.get("bounds")) if present)
        if ways == 0:
            raise ValidationError("Give a location, or latitude + longitude, or bounds.", "location")
        if ways > 1:
            raise ValidationError("Give only one of location, latitude + longitude, or bounds.", "location")
        _check_stay(data)
        lo, hi = data.get("min_price"), data.get("max_price")
        if lo is not None and hi is not None and lo > hi:
            raise ValidationError("min_price must not exceed max_price.", "max_price")


# ---- hotels/filters ---------------------------------------------------------------------

class FiltersSchema(BaseSchema):
    location = LocationRefField(metadata={
        "description": "destination name, a region id, or a hotels.com Hotel-Search link"})
    checkin = DateField(**_CHECKIN)
    checkout = DateField(**_CHECKOUT)
    adults = PositiveInt(**_ADULTS)
    country = country_field()

    @validates_schema
    def _stay(self, data, **kwargs):
        _check_stay(data)


# ---- hotels/details ---------------------------------------------------------------------

class HotelDetailsSchema(BaseSchema):
    hotel = HotelRefField(**_HOTEL)
    country = country_field()


# ---- hotels/rooms -----------------------------------------------------------------------

class HotelRoomsSchema(BaseSchema):
    hotel = HotelRefField(**_HOTEL)
    checkin = DateField(required=True, load_default=missing, **_CHECKIN)
    checkout = DateField(required=True, load_default=missing, **_CHECKOUT)
    adults = PositiveInt(**_ADULTS)
    children = ChildAgesField(**_CHILDREN)
    rooms = PositiveInt(**_ROOMS)
    country = country_field()

    @validates_schema
    def _stay(self, data, **kwargs):
        _check_stay(data, required=True)


# ---- hotels/reviews ---------------------------------------------------------------------

class HotelReviewsSchema(BaseSchema):
    hotel = HotelRefField(**_HOTEL)
    page = PageField(max_page=MAX_REVIEW_PAGE, metadata={"description": "review page"})
    limit = PageSizeField(default=20, max_size=REVIEWS_MAX_SIZE, metadata={
        "description": "reviews per page, 1-50 (default 20)"})
    sort = ChoiceField(refs.REVIEW_SORTS, load_default="NEWEST_TO_OLDEST", metadata={
        "description": "newest (default), highest_rating, lowest_rating or relevant"})
    query = StrippedString(required=False, load_default=None, validate=validate.Length(max=100), metadata={
        "description": "only reviews mentioning this word or phrase"})
    country = country_field()


# ---- hotels/calendar --------------------------------------------------------------------

class HotelCalendarSchema(BaseSchema):
    hotel = HotelRefField(**_HOTEL)
    country = country_field()
