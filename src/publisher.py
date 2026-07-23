"""
Publishes a generated lead site as a single flat HTML file committed to
`vet-demo-sites`'s main branch — following that repo's existing convention
(see asapdraincleaners.com.html): one file per business, no branches, no
Vercel, GitHub Pages serves it immediately at a predictable URL.
"""

import base64
import os

import requests

GH_API = "https://api.github.com"
PUBLISH_OWNER = os.environ.get("PUBLISH_REPO_OWNER", "stevendelarwelle")
PUBLISH_REPO = os.environ.get("PUBLISH_REPO_NAME", "vet-demo-sites")
PAGES_BASE_URL = os.environ.get("PAGES_BASE_URL", f"https://{PUBLISH_OWNER}.github.io/{PUBLISH_REPO}")


def _headers():
    token = os.environ["GH_TOKEN"]
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }


def _existing_sha(path: str) -> str | None:
    resp = requests.get(
        f"{GH_API}/repos/{PUBLISH_OWNER}/{PUBLISH_REPO}/contents/{path}",
        headers=_headers(), timeout=15,
    )
    if resp.status_code == 200:
        return resp.json().get("sha")
    return None


def publish_site(filename: str, html: str, commit_message: str) -> str:
    """Commits `filename` (e.g. 'somesite.com.html') to vet-demo-sites' main
    branch. Returns the live GitHub Pages URL."""
    path = filename
    sha = _existing_sha(path)

    body = {
        "message": commit_message,
        "content": base64.b64encode(html.encode("utf-8")).decode("ascii"),
        "branch": "main",
    }
    if sha:
        body["sha"] = sha

    resp = requests.put(
        f"{GH_API}/repos/{PUBLISH_OWNER}/{PUBLISH_REPO}/contents/{path}",
        headers=_headers(), json=body, timeout=30,
    )
    resp.raise_for_status()

    return f"{PAGES_BASE_URL}/{filename}"


def unpublish_site(filename: str, commit_message: str) -> bool:
    """Removes a lead's mockup file at day-7 expiry. Returns False (no-op)
    if the file is already gone rather than erroring."""
    sha = _existing_sha(filename)
    if not sha:
        return False

    resp = requests.delete(
        f"{GH_API}/repos/{PUBLISH_OWNER}/{PUBLISH_REPO}/contents/{filename}",
        headers=_headers(),
        json={"message": commit_message, "sha": sha, "branch": "main"},
        timeout=30,
    )
    resp.raise_for_status()
    return True
