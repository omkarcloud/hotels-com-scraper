"""Cache TTL per /hotels-com/* endpoint (cache.py, keyed on the validated
params). The hotel endpoints share ONE cached page parse (api._record,
hotels-com.hotel): details + rooms + reviews + calendar on the same hotel
cost one hotel-page fetch for the id mapping and the static content.
"""
from datetime import timedelta

# The hotel page: description, amenities, policies, FAQ, photos. Hotels edit
# their content rarely; the headline price is refreshed by the dated endpoints.
HOTEL_CACHE = timedelta(hours=12)
# Region ids never change; the suggestion ranking barely does.
AUTOCOMPLETE_CACHE = timedelta(days=30)
# Search results carry live availability and nightly prices for the dates.
SEARCH_CACHE = timedelta(hours=6)
# The filter vocabulary + counts for a destination.
FILTERS_CACHE = timedelta(hours=24)
# Rooms and rates for a stay are the most perishable thing here.
ROOMS_CACHE = timedelta(hours=3)
# Reviews only grow; a new one waits at most a day.
REVIEWS_CACHE = timedelta(hours=24)
# The per-day price calendar moves with every booking.
CALENDAR_CACHE = timedelta(hours=6)
