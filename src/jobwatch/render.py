"""Escape untrusted source strings at the Markdown output boundary."""
import html
import re
from urllib.parse import quote
from .feeds import public_url


def text(value) -> str:
    value = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(value))
    value = html.escape(value, quote=False)
    return re.sub(r"([\\`*_{}\[\]()#!|~])", r"\\\1", value)


def link(title, url) -> str:
    if not public_url(url):
        return text(title) + " (invalid URL; manual review required)"
    return f"[{text(title)}]({quote(url, safe=':/?=&%+#@;,-._~')})"
