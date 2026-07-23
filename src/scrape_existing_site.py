"""
Fetches a lead's existing website and reduces it to plain text for prompt
input. A network failure/timeout is itself a strong "worth pursuing" signal
(a business whose site doesn't even load is the best possible target) — the
caller (qualify.py) checks `reachable` for that, this module just reports it
rather than deciding what it means.
"""

import re
import requests
from html.parser import HTMLParser

TIMEOUT = 12
MAX_CHARS = 3000
SKIP_TAGS = {"script", "style", "noscript", "svg", "head"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self.chunks.append(text)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = " ".join(parser.chunks)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_CHARS]


EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
GENERIC_LOCAL_PARTS = {"webmaster", "postmaster", "noreply", "no-reply", "example"}


def find_contact_email(html: str) -> str | None:
    """Best-effort: Google Places never returns a business email, so this is
    the only automated source we have. Prefers a mailto: link; falls back to
    the first plausible email string in the page. Returns None (never
    guesses) if nothing is found — outreach.py skips leads with no email
    rather than fabricating one."""
    mailto_match = re.search(r'mailto:([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', html)
    if mailto_match:
        return mailto_match.group(1)

    for match in EMAIL_RE.finditer(html):
        candidate = match.group(0)
        local_part = candidate.split("@")[0].lower()
        if local_part in GENERIC_LOCAL_PARTS:
            continue
        if candidate.lower().endswith((".png", ".jpg", ".gif", ".svg", ".webp")):
            continue  # e.g. filenames like image@2x.png caught by the regex
        return candidate

    return None


def fetch_existing_site(url: str) -> dict:
    """Returns {"reachable": bool, "text": str, "email": str | None, "error": str | None}."""
    if not url:
        return {"reachable": False, "text": "", "email": None, "error": "no website on file"}

    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    try:
        resp = requests.get(
            url,
            timeout=TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 (compatible; HostedSiteOutreachBot/1.0)"},
        )
        if resp.status_code >= 400:
            return {"reachable": False, "text": "", "email": None, "error": f"HTTP {resp.status_code}"}
        return {
            "reachable": True,
            "text": html_to_text(resp.text),
            "email": find_contact_email(resp.text),
            "error": None,
        }
    except requests.RequestException as e:
        return {"reachable": False, "text": "", "email": None, "error": str(e)}
