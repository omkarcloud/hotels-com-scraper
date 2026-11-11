# 🏨 Hotels Com Scraper

Hotels Com Scraper is a **free and open-source** scraper that gets you **unlimited** detailed Hotels.com hotel data for free.

## ✨ What Can I Get?

- 🏨 **Full details on any Hotels.com hotel** — stars, address, amenities, resort fees, policies, FAQ, check-in times & every photo
- 🔍 **Search any city, up to 200 hotels per page** — live prices for your dates, ratings, 14 filters, map-point & map-area search
- 🛏️ **Every room & rate for your dates** — size, beds, photos, refundable vs. non-refundable prices & price drops
- 📅 **8 months of daily prices & every guest review** — the cheapest night to book, scores, stay details & hotel replies

## 🏨 Example: A Full Hotels.com Hotel

```json
{
  "id": "140596",
  "hotel_id": "ho119566",
  "name": "Bellagio",
  "link": "https://www.hotels.com/ho119566/",
  "star_rating": 5.0,
  "tagline": "Luxury resort near LINQ Promenade",
  "rating": { "score": 9.0, "max": 10, "label": "Wonderful", "review_count": 15573 },
  "location": {
    "address": "3600 Las Vegas Blvd S",
    "city": "Las Vegas",
    "region": "NV",
    "postal_code": "89109",
    "coordinates": { "latitude": 36.112488, "longitude": -115.17673 }
  },
  "check_out_time": "11 AM",
  "price_from": { "nightly": 136.0, "total": 216.0, "currency": "USD", "is_taxes_included": true, "dates_text": "Dec 9 - Dec 10" },
  "popular_amenities": ["Pool", "Spa", "Parking available", "Restaurant", "Connecting rooms available", "Air conditioning"],
  "amenities": [{ "section": "Property amenities", "category": "Food and drink", "items": ["17 restaurants", "5 bars/lounges"] }],
  "policies": { "mandatory_fees": ["Resort fee: USD 62.36 per accommodation, per night"] },
  "faq": [
    { "question": "Is there a pool at Bellagio?", "answer": "Yes, there are 5 outdoor pools. Guests have pool access from 9:00 AM to 4:00 PM." }
  ],
  "photos": [
    {
      "link": "https://images.trvl-media.com/lodging/1000000/150000/140600/140596/d418cbfd.jpg?impolicy=resizecrop&rw=575&rh=575&ra=fill",
      "category": "Common areas"
    }
  ]
}
```

*Trimmed for readability.*

## 🚀 Unlimited Free Hotels.com Data — Get It in 60 Seconds

1️⃣ Clone and install:
```bash
git clone https://github.com/omkarcloud/hotels-com-scraper
cd hotels-com-scraper
python -m pip install -r requirements.txt
```

2️⃣ Start the API:
```bash
python run.py
```

If Hotels.com ever answers your IP with 429s, start it with `HOTELS_COM_PROXY=http://user:pass@host:port python run.py` and every request leaves through that proxy.

3️⃣ Get your first data:
```bash
curl "http://localhost:8000/hotels/details?hotel=ho119566"
```

```json
{
  "id": "140596",
  "hotel_id": "ho119566",
  "name": "Bellagio",
  "link": "https://www.hotels.com/ho119566/",
  "star_rating": 5.0,
  "tagline": "Luxury resort near LINQ Promenade",
  "rating": { "score": 9.0, "max": 10, "label": "Wonderful", "review_count": 15573 },
  "location": { "address": "3600 Las Vegas Blvd S", "city": "Las Vegas", "region": "NV", "postal_code": "89109", "country_code": "USA" },
  "check_in_time": "3:00 PM",
  "check_out_time": "11 AM",
  "price_from": { "nightly": 136.0, "total": 216.0, "nights": 1, "currency": "USD", "is_taxes_included": true, "dates_text": "Dec 9 - Dec 10" },
  "popular_amenities": ["Pool", "Spa", "Parking available", "Restaurant", "Connecting rooms available", "Air conditioning"]
}
```

All 7 endpoints are now live at `http://localhost:8000`.

## 📚 Endpoints

7 endpoints cover everything you need.

| Endpoint | Path | Returns |
|---|---|---|
| Hotel Details | `/hotels/details` | Everything about one hotel in one call: fees, policies, every photo |
| Location Autocomplete | `/locations/autocomplete` | Place or hotel names to region IDs, hotel IDs and coordinates |
| Search Hotels | `/hotels/search` | Up to 200 hotels per page with live prices; 14 filters |
| Search Filters | `/hotels/filters` | Every filter and sort for a destination, with live counts |
| Rooms and Rates | `/hotels/rooms` | Every room for your dates: beds, photos, refundable and non-refundable prices |
| Hotel Reviews | `/hotels/reviews` | Guest reviews with scores, stays and hotel replies, 50 per page |
| Price Calendar | `/hotels/calendar` | The lowest nightly price for every day, about 8 months ahead |

## 🔍 Exploring Parameters

The same API is published on RapidAPI, and its playground is the easiest place to try parameters and see raw responses. Once a request looks right, run it locally for **unlimited free** data.

1. [Subscribe to the free plan](https://rapidapi.com/OmkarCloud/api/best-hotels-com-scraper-free-1000-calls/pricing) — 1,000 calls/month, no credit card.
2. [Try the endpoints in the playground](https://rapidapi.com/OmkarCloud/api/best-hotels-com-scraper-free-1000-calls/playground) — every param is pre-filled, so you see real data in one click.
3. Copy the generated code and replace `https://best-hotels-com-scraper-free-1000-calls.p.rapidapi.com` with `http://localhost:8000`. It will now run against your local API.

```python
import requests

# generated by the playground, host swapped for the local API
response = requests.get(
    "http://localhost:8000/hotels/search",
    params={"location": "Las Vegas", "checkin": "2027-06-11", "checkout": "2027-06-13", "stars": "4,5", "sort": "price_low_to_high"},
)
print(response.json())
```

## 💬 Have Questions? We Have Answers.

You're a developer — we know how hard completing a project can be. So we offer full support: just message us and we'll reply ✅ with a solution within 1 working day.

[![Message Us on WhatsApp about Hotels Com Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/whatsapp-us.png)](https://www.omkar.cloud/l/hotels-com-scraper-github-whatsapp)

[![Ask Us by Email about Hotels Com Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/ask-on-email.png)](mailto:happy.to.help@omkar.cloud?subject=Help%20with%20Hotels%20Com%20Scraper%20API&body=I%20need%20help%20using%20the%20Hotels%20Com%20Scraper%20API.)

## ⚡ Popular Scrapers by Omkar Cloud

- [**Google Maps Scraper (3,100+ GitHub Stars)**](https://github.com/omkarcloud/google-maps-scraper) — type "dentists in New York", get every business as a ready-to-call lead list: phones, emails, websites & reviews. Up to 100K free leads/month.
- [**G2 Scraper**](https://www.omkar.cloud/tools/g2-scraper) — G2 product details, ratings & AI-found contacts
- [**Website Email Contact Scraper**](https://www.omkar.cloud/tools/website-email-contact-scraper) — emails, phones & socials from any website
- [**AliExpress Scraper**](https://www.omkar.cloud/tools/aliexpress-scraper-api) — live product details, SKU variants, stock & shipping
- [**Booking Scraper**](https://www.omkar.cloud/tools/booking-scraper) — Booking.com hotels: prices, ratings, rooms & amenities
- [**Agoda Scraper**](https://github.com/omkarcloud/agoda-scraper) — Agoda hotels: live prices, every room & rate plan, guest scores & reviews
- [**Google Hotels Scraper**](https://www.omkar.cloud/tools/google-hotels-scraper) — Google Hotels: live prices from every booking site, reviews & photos

## ⭐ Love It? [Star It ⭐!](https://github.com/omkarcloud/hotels-com-scraper)

Star the repo ⭐ and become my star hero!

It's just 1 click, but it means the world to me.

[![Star us on GitHub](https://raw.githubusercontent.com/omkarcloud/google-maps-scraper/master/screenshots/star-us.png)](https://github.com/omkarcloud/hotels-com-scraper)
