"""The one network helper the hotels_com module needs: the Accept-Language
its sessions send. Requests go out direct from your own connection (or
through HOTELS_COM_PROXY), so every session announces the plain English list
a desktop browser sends for hotels.com."""

ACCEPT_LANGUAGE = "en-US,en;q=0.9"


def site_accept_language(site=None):
    return ACCEPT_LANGUAGE


def accept_language(proxy=None, country=None):
    return ACCEPT_LANGUAGE
