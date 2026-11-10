"""Offline tests for the hotels.com transport decisions (no network): the
error taxonomy, the id -> page path candidates, the unknown-id redirect,
the challenge page, the exit ladder, and the GraphQL error mapping.

    python -m pytest hotels_com/test_fetch.py -q
"""
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from hotels_com import fetch as F  # noqa: E402
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
CHALLENGE = open(os.path.join(FX, "challenge_bot_or_not.html"), encoding="utf-8").read()
HOTEL_PAGE = open(os.path.join(FX, "hotel_ho434012.html"), encoding="utf-8").read()


class Resp:
    def __init__(self, status, text="", url="https://www.hotels.com/", payload=None):
        self.status_code, self.text, self.url, self._payload = status, text, url, payload
        self.content = text.encode()

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """The curl stand-in inside a pooled F._Session: scripted responses,
    every URL asked recorded."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}
        self.cookies = {}
        self.proxies = {}
        self.closed = False

    def get(self, url, **kw):
        self.calls.append(("GET", url))
        return self.responses.pop(0)

    def post(self, url, **kw):
        self.calls.append(("POST", url))
        return self.responses.pop(0)

    def close(self):
        self.closed = True


@pytest.fixture
def scripted(monkeypatch):
    """Fake the pool's mint: each minted session (on exit "http://x") pops
    the next list of responses (one list per exit the ladder tries). Returns
    the FakeSessions made, in mint order."""
    made = []

    def install(*per_session):
        queue = [list(r) for r in per_session]

        def mint(pos, hint=None):
            sess = F._Session(pos, "http://x")
            sess.curl.close()
            sess.curl = FakeSession(queue.pop(0) if queue else [])
            made.append(sess.curl)
            return sess
        F._sessions._mint_fn = mint
        monkeypatch.setattr(F.time, "sleep", lambda s: None)
        return made
    F._sessions.clear()
    yield install
    F._sessions.clear()
    F._sessions._mint_fn = F._mint


def test_error_taxonomy():
    assert issubclass(F.HotelsComBlocked, Blocked) and issubclass(F.HotelsComBlocked, UpstreamError)
    assert issubclass(F.HotelsComUpstreamError, UpstreamError)
    assert issubclass(F.HotelsComBadRequest, BadRequest) and issubclass(F.HotelsComNotFound, NotFound)
    assert F.IMPERSONATE_TARGETS == ("safari184_ios", "chrome150")


def test_classify():
    with pytest.raises(F.HotelsComNotFound):
        F._classify(Resp(404), "x")
    with pytest.raises(F.HotelsComBadRequest):
        F._classify(Resp(400, "bad"), "x")
    for status in (401, 403, 429):
        with pytest.raises(F.HotelsComBlocked):
            F._classify(Resp(status), "x")
    with pytest.raises(F.HotelsComUpstreamError):
        F._classify(Resp(502), "x")
    with pytest.raises(F.HotelsComBlocked):          # the captcha page comes as 200 on some exits
        F._classify(Resp(200, CHALLENGE), "x")
    assert F._classify(Resp(200, "<html><title>Hotel</title></html>"), "x").startswith("<html>")
    assert F.is_challenge(CHALLENGE) and not F.is_challenge(HOTEL_PAGE)


def test_candidate_paths():
    assert F._candidate_paths("ho434012") == ["/ho434012/"]
    assert F._candidate_paths("6136437") == ["/h6136437.Hotel-Information"]
    with pytest.raises(F.HotelsComNotFound):
        F._candidate_paths("bogus")


def test_hotel_page_happy(scripted):
    scripted([Resp(200, HOTEL_PAGE, "https://www.hotels.com/ho434012/")])
    html, hotel_id = F.fetch_hotel_page("ho434012")
    assert hotel_id == "ho434012" and "PropertyInfo:" in html


def test_expedia_id_follows_redirect_then_bare_page(scripted):
    made = scripted([Resp(200, "<html>shell</html>", "https://www.hotels.com/ho434012/hotel-anastassiou/?chkin=2026-12-01"),
                     Resp(200, HOTEL_PAGE, "https://www.hotels.com/ho434012/")])
    html, hotel_id = F.fetch_hotel_page("6136437")
    assert hotel_id == "ho434012" and "PropertyInfo:" in html
    assert [u for _, u in made[0].calls] == ["https://www.hotels.com/h6136437.Hotel-Information",
                                             "https://www.hotels.com/ho434012/"]


def test_unknown_id_redirects_home_is_not_found(scripted):
    made = scripted([Resp(200, "<html><title>Hotels.com</title></html>", "https://www.hotels.com/")])
    with pytest.raises(F.HotelsComNotFound):
        F.fetch_hotel_page("ho99999999999")
    assert len(made) == 1 and len(made[0].calls) == 1       # never retried


def test_block_rides_fresh_exits_then_gives_up(scripted, monkeypatch):
    monkeypatch.setattr(config, "HOTELS_COM_EXIT_ATTEMPTS", 3)
    made = scripted(*[[Resp(429, CHALLENGE, "https://www.hotels.com/ho434012/")] for _ in range(3)])
    with pytest.raises(F.HotelsComBlocked):
        F.fetch_hotel_page("ho434012")
    assert len(made) == 3 and all(s.closed for s in made)


def test_block_then_clean_exit_succeeds(scripted):
    made = scripted([Resp(200, CHALLENGE, "https://www.hotels.com/ho434012/")],
                    [Resp(200, HOTEL_PAGE, "https://www.hotels.com/ho434012/")])
    html, hotel_id = F.fetch_hotel_page("ho434012")
    assert hotel_id == "ho434012" and len(made) == 2 and made[0].closed and not made[1].closed


def test_graphql_mapping(scripted):
    made = scripted([Resp(200, "<html>home</html>", "https://www.hotels.com/"),       # warm-up
                     Resp(200, "{}", payload=[{"data": {"propertyInfo": None},
                                               "errors": [{"message": "Invalid PropertyId: 999"}]}])])
    with pytest.raises(F.HotelsComNotFound):
        F.fetch_reviews("999")
    assert made[0].calls[0] == ("GET", "https://www.hotels.com/ho434012/") and made[0].calls[1][0] == "POST"
    F._sessions.clear()
    scripted([Resp(200, "<html>home</html>"), Resp(429, '{"error":"Too Many Requests"}', payload={"error": "Too Many Requests"})],
             [Resp(200, "<html>home</html>"), Resp(200, "{}", payload=[{"data": {"propertyRatesDateSelector": {"days": []}}}])])
    assert F.fetch_calendar("6136437") == {"days": []}


def test_graphql_other_storefront_uses_its_host(scripted):
    made = scripted([Resp(200, "<html>home</html>"), Resp(200, "{}", payload=[{"data": {"propertySearch": {"summary": {}}}}])])
    F.fetch_search({"regionId": "2734"}, 0, pos="GB")
    assert made[0].calls == [("GET", "https://uk.hotels.com/ho434012/"), ("POST", "https://uk.hotels.com/graphql")]


class RedirectResp(Resp):
    def __init__(self, status, location):
        super().__init__(status, "")
        self.headers = {"location": location}


def test_warm_up_301_counts_and_redirected_post_is_a_block(scripted):
    """A hotel-page 301 already carries the point-of-sale cookies; a POST
    redirected to another storefront (an exit abroad) is a block retried on
    a fresh exit, never a 400 from the followed GET (2026-11-07)."""
    made = scripted([RedirectResp(301, "https://www.hotels.com/ho434012/hotel-anastassiou/"),
                     RedirectResp(302, "https://in.hotels.com/graphql?locale=en_IN&siteid=300000036")],
                    [Resp(200, "<html>hotel</html>"),
                     Resp(200, "{}", payload=[{"data": {"propertyRatesDateSelector": {"days": []}}}])])
    assert F.fetch_calendar("140596") == {"days": []}
    assert len(made) == 2 and made[0].closed
    assert made[0].calls == [("GET", "https://www.hotels.com/ho434012/"), ("POST", "https://www.hotels.com/graphql")]


def test_three_threads_share_one_warmed_session_and_a_block_retires_it(scripted):
    """Three sequential GraphQL calls on three different threads ride ONE
    pooled session of the storefront: the warm-up page is loaded once
    (before: once per thread). A 429 then retires it (closed, nothing idle);
    a session of another storefront is never shared."""
    made = scripted([Resp(200, "<html>home</html>")]
                    + [Resp(200, "{}", payload=[{"data": {"propertyRatesDateSelector": {"days": [i]}}}]) for i in (1, 2, 3)]
                    + [Resp(429, '{"error":"Too Many Requests"}', payload={"error": "Too Many Requests"})],
                    [Resp(200, "<html>home</html>"), Resp(200, "{}", payload=[{"data": {"propertyRatesDateSelector": {"days": []}}}])],
                    [Resp(200, "<html>home</html>"), Resp(200, "{}", payload=[{"data": {"propertyRatesDateSelector": {"days": []}}}])])
    results = []
    for _ in range(3):
        t = threading.Thread(target=lambda: results.append(F.fetch_calendar("6136437")))
        t.start()
        t.join()
    assert [r["days"] for r in results] == [[1], [2], [3]]
    assert len(made) == 1 and made[0].calls[0] == ("GET", "https://www.hotels.com/ho434012/")
    assert [m for m, _ in made[0].calls].count("GET") == 1          # one warm-up for three calls
    assert F._sessions.stats("US")["idle"] == 1 and not made[0].closed
    assert F.fetch_calendar("6136437") == {"days": []}             # the 429 retires it; the retry mints another
    assert made[0].closed and len(made) == 2 and F._sessions.stats("US")["idle"] == 1
    F.fetch_calendar("6136437", pos="GB")                           # another storefront: its own session
    assert len(made) == 3 and made[2].calls[0] == ("GET", "https://uk.hotels.com/ho434012/")


def test_criteria_builders():
    assert F.date_range("2026-12-10", "2026-12-12") == {"checkInDate": {"day": 10, "month": 12, "year": 2026},
                                                        "checkOutDate": {"day": 12, "month": 12, "year": 2026}}
    assert F.date_range(None, None) is None
    assert F.rooms(3, [5, 9], 2) == [{"adults": 3, "children": [{"age": 5}, {"age": 9}]}] * 2
    assert F.search_page_path({"regionId": "2734"}, "2026-12-10", "2026-12-12", 2) == \
        "/Hotel-Search?regionId=2734&startDate=2026-12-10&endDate=2026-12-12&adults=2"
    assert F.search_page_path({"regionName": "Paris"}).startswith("/Hotel-Search?destination=Paris&adults=2")


def test_pos_exit_choice(monkeypatch):
    """The US storefront rides config.hotels_com_proxy(), every other one
    config.hotels_com_pos_proxy(<its exit country>)."""
    monkeypatch.setattr(config, "hotels_com_proxy", lambda: "http://us-exit")
    monkeypatch.setattr(config, "hotels_com_pos_proxy", lambda country: f"http://{country}-exit")
    assert F._proxy("US") == "http://us-exit"
    assert F._proxy("GB") == "http://gb-exit"
    monkeypatch.setattr(config, "hotels_com_proxy", lambda: None)
    assert F._proxy("US") is None
