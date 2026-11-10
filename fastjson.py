"""The JSON helpers the hotels_com module imports, on Python's own json.

    import fastjson
    obj = fastjson.loads(text_or_bytes)
    text = fastjson.dumps(obj)          # non-ASCII kept as-is, compact
"""
import json as _json


def loads(s):
    """Parse JSON text or bytes."""
    return _json.loads(s)


def dumps(obj):
    """Serialise compactly, keeping non-ASCII characters as they are."""
    return _json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
