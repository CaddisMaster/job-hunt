"""Text from the web, made safe to keep: markup in, plain text out (#44, #63).

A saved job's description is stored as plain text, never markup: it's shown escaped
on pages and later read by a model. `plain_text` removes tags (and the contents of
script, style and the like), decodes entities and tidies whitespace; `tidy` makes a
short field one clean line; `clip` caps a length.
"""

import re
from html import unescape
from html.parser import HTMLParser

DESCRIPTION_MAX = 50_000  # saved_jobs' check constraint


BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table",
    "section", "article", "header", "footer", "blockquote", "pre", "hr", "dd", "dt",
}  # fmt: skip
DROP_TAGS = {"script", "style", "noscript", "template", "iframe", "object", "svg", "head"}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.dropping = 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_TAGS:
            self.dropping += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "li" and not self.dropping:
            self.parts.append("• ")

    def handle_startendtag(self, tag, attrs):
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in DROP_TAGS:
            self.dropping = max(0, self.dropping - 1)
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.dropping:
            self.parts.append(data)


_SPACES = re.compile(r"[ \t\r\f\v ]+")
_BLANK_LINES = re.compile(r"\n\s*\n\s*\n+")


def plain_text(html: str | None, *, escaped: bool = False) -> str:
    """A description as plain text: tags removed (and the contents of script, style
    and the like), entities decoded, whitespace tidied, capped at DESCRIPTION_MAX.

    `escaped` is for HTML sent entity-encoded ("&lt;p&gt;…"), as some
    pages' JSON-LD does. What comes out is text to show escaped, never markup: a
    "<script>" that survives as words stays words."""
    if not html:
        return ""
    if escaped:
        html = unescape(html)
    parser = _Text()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    lines = (_SPACES.sub(" ", line).strip() for line in text.split("\n"))
    text = _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
    return clip(text, DESCRIPTION_MAX)


def clip(text: str | None, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def tidy(text: object, limit: int) -> str:
    """A short field (title, location) as one clean line: only strings count."""
    if not isinstance(text, str):
        return ""
    return clip(_SPACES.sub(" ", unescape(text)).replace("\n", " "), limit)
