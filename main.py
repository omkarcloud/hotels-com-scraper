"""Use the scraper straight from Python — no server needed.

    python main.py

Every function returns the same JSON the API does; results are written to
output/*.json. Params go through the same schemas the API uses, so defaults
are filled in and a hotel ID or a hotels.com link both work.
"""
import json
import os

from hotels_com import api, schemas

os.makedirs("output", exist_ok=True)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


def run(schema_cls, fn, **params):
    return fn(**schema_cls().load(params))


if __name__ == "__main__":
    # a hotels.com hotel ID or any hotels.com hotel link — the whole hotel in one call
    save("hotel_ho119566.json",
         run(schemas.HotelDetailsSchema, api.hotel_details, hotel="ho119566"))

    # up to 200 hotels per page with live prices for your dates
    save("search_las_vegas.json",
         run(schemas.SearchSchema, api.hotels_search, location="Las Vegas",
             checkin="2027-06-11", checkout="2027-06-13"))

    # every room and rate for a stay
    save("rooms_ho119566.json",
         run(schemas.HotelRoomsSchema, api.hotel_rooms, hotel="ho119566",
             checkin="2027-06-11", checkout="2027-06-13"))
