"""A small pool of warmed sessions, the part of the identity pool API the
hotels_com module uses.

A warmed session (one curl session whose cookie jar already holds the
storefront's cookies) is expensive to make and cheap to reuse, so requests
borrow one, use it for ONE call and give it back; the next request reuses it
instead of warming a new one.

    pool = identity_pool.Pool("hotels_com", mint, mode="exclusive",
                              budget=lambda: 40, max_age=lambda: 1800,
                              on_retire=lambda ident, reason: ident.close())
    ident = pool.borrow("US")          # one caller at a time per session
    try:
        ...                            # set ident.dead = True if it was refused
    finally:
        pool.give_back(ident)

A session is retired (on_retire runs) when it comes back dead, after
`budget` uses, or `max_age` seconds after it was made.
"""
import threading
import time
from contextlib import contextmanager


def _val(knob):
    return knob() if callable(knob) else knob


class Identity:
    """What the pool lends. `value` is any payload (None when a subclass
    carries its own fields); `exit` is the proxy URL it uses."""

    def __init__(self, value=None, *, exit=None, expires_at=None):
        self.value = value
        self.exit = exit
        self.expires_at = expires_at
        self.key = ""
        self.minted_at = time.monotonic()
        self.uses = 0
        self.dead = False          # refused: never lent again
        self.retired = False       # the pool is done with it (on_retire ran)

    def __repr__(self):
        return f"<Identity key={self.key!r} uses={self.uses} dead={self.dead} exit={self.exit}>"


class Pool:
    def __init__(self, name, mint, *, mode="exclusive", budget=None, max_age=None, on_retire=None, **_ignored):
        """mint(key, hint) -> Identity | value. budget / max_age may be
        callables read on use; None = unlimited."""
        if mode != "exclusive":
            raise ValueError(f"{name}: this pool lends exclusively (mode='exclusive')")
        self.name = name
        self._mint_fn = mint
        self._budget = budget
        self.max_age = max_age
        self.on_retire = on_retire
        self._lock = threading.Lock()
        self._idle = {}         # key -> [Identity], oldest first
        self._lent = {}         # key -> {Identity}

    # ---- life cycle --------------------------------------------------------------

    def exhausted(self, ident):
        if ident.dead:
            return True
        budget = _val(self._budget)
        if budget is not None and ident.uses >= budget:
            return True
        max_age = _val(self.max_age)
        if max_age is not None and time.monotonic() - ident.minted_at >= max_age:
            return True
        return ident.expires_at is not None and time.time() >= ident.expires_at

    def _retire(self, ident, reason):
        if ident.retired:
            return
        ident.retired = True
        if self.on_retire is not None:
            try:
                self.on_retire(ident, reason)
            except Exception:
                pass

    # ---- lending -----------------------------------------------------------------

    def borrow(self, key="", kind=None):
        """Lend one session of `key` for ONE call; give_back() it."""
        stale = []
        with self._lock:
            idle = self._idle.setdefault(key, [])
            ident = None
            while idle:
                candidate = idle.pop(0)
                if self.exhausted(candidate):
                    stale.append(candidate)
                    continue
                ident = candidate
                break
            if ident is not None:
                ident.uses += 1
                self._lent.setdefault(key, set()).add(ident)
        for old in stale:
            self._retire(old, "spent")
        if ident is not None:
            return ident
        made = self._mint_fn(key, None)
        ident = made if isinstance(made, Identity) else Identity(made)
        ident.key = key
        with self._lock:
            ident.uses += 1
            self._lent.setdefault(key, set()).add(ident)
        return ident

    def give_back(self, ident):
        """Return a borrowed session: back to the pool, or retired when it
        was refused (ident.dead), spent or aged out."""
        with self._lock:
            lent = self._lent.get(ident.key, set())
            if ident not in lent:
                return                     # given back twice: never duplicate it
            lent.discard(ident)
            retire = self.exhausted(ident)
            if not retire:
                self._idle.setdefault(ident.key, []).append(ident)
        if retire:
            self._retire(ident, "refused" if ident.dead else "spent")

    @contextmanager
    def lend(self, key="", kind=None):
        ident = self.borrow(key, kind)
        try:
            yield ident
        finally:
            self.give_back(ident)

    def retire(self, ident, reason="refused"):
        """Never lend `ident` again (retired now if idle, on give-back if lent)."""
        with self._lock:
            idle = self._idle.get(ident.key, [])
            if ident in idle:
                idle.remove(ident)
            elif ident in self._lent.get(ident.key, ()):
                ident.dead = True
                return True
            else:
                return False
        self._retire(ident, reason)
        return True

    def clear(self):
        """Retire everything (tests)."""
        with self._lock:
            idents = [i for idents in self._idle.values() for i in idents]
            self._idle.clear()
            for ident in [i for idents in self._lent.values() for i in idents]:
                ident.dead = True
        for ident in idents:
            self._retire(ident, "clear")

    def stats(self, key=""):
        with self._lock:
            return {"idle": len(self._idle.get(key, [])), "lent": len(self._lent.get(key, ()))}
