"""Configuration for the Hotels Com Scraper. Everything can be set with an
environment variable; the defaults work out of the box.

    PORT              port the API listens on (default 8000)
    HOTELS_COM_PROXY  proxy URL for every request, e.g. http://user:pass@host:port
                      (default: none — direct). hotels.com answers most home
                      and office connections; if yours starts answering 429
                      ("Bot or Not?") to every call, its IP has been flagged —
                      point HOTELS_COM_PROXY at a US residential proxy and
                      requests leave through it.

Everything else below is a plain constant with a working default — edit it
here if you need to.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

# Retry policy for transport errors (every request).
MAX_RETRIES = 3
RETRY_BACKOFF = 2          # seconds, multiplied by the attempt number

# A blocked request (hotels.com's 429 "Bot or Not?" page) is retried this
# many times on a fresh session, alternating the two browser fingerprints the
# site accepts (hotels_com/fetch.py IMPERSONATE_TARGETS).
HOTELS_COM_EXIT_ATTEMPTS = 4

# The hosted service moves to a pool of residential exits for a while after
# both fingerprints are refused; this kit has one connection, so it stays on
# it (0 = no fallback window).
HOTELS_COM_BLOCK_COOLDOWN = 0
HOTELS_COM_EXIT = "direct"

# A warmed session (its cookies already loaded) serves this many calls, or
# lives this many seconds, before a fresh one replaces it.
HOTELS_COM_SESSION_USES = 40
HOTELS_COM_SESSION_MAX_AGE = 1800

HOTELS_COM_PROXY = os.environ.get("HOTELS_COM_PROXY") or None


def hotels_com_proxy():
    """The proxy for one session on the US storefront (None = direct)."""
    return os.environ.get("HOTELS_COM_PROXY") or None


def hotels_com_fallback_proxy():
    """The exit used after both fingerprints are refused (None = keep the
    same connection)."""
    return os.environ.get("HOTELS_COM_PROXY") or None


def hotels_com_pos_proxy(country):
    """The proxy for the other storefronts (uk.hotels.com for country=gb).
    Same knob: one connection serves every storefront here."""
    return os.environ.get("HOTELS_COM_PROXY") or None
