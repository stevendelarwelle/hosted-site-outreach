"""
The "crappy website" filter — highest-leverage step in the pipeline.

For leads with no website on file: auto-qualify, zero cost.
For leads whose site doesn't load at all: auto-qualify as worth_pursuing
  (a broken site is the best possible target) — no screenshot/Claude call.
For leads with a reachable site: Playwright screenshots (desktop + mobile)
  written to screenshots/{place_id}/ and committed to the repo (so gate-1
  review can see them straight from GitHub), then a single Claude vision
  call scores modernity/mobile/cta and returns worth_pursuing.

LOW overall_score = bad existing site = worth pursuing. This is the inverse
of lead-gen-pipeline's scanner.py opportunity scoring, where high = better.
"""

import base64
import json
import os
import time
from pathlib import Path

import anthropic
from playwright.sync_api import sync_playwright

from src import db
from src.scrape_existing_site import fetch_existing_site

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "qualify_prompt.md"
QUALIFY_PROMPT = PROMPT_PATH.read_text()

MODEL = "claude-opus-4-6"  # vision call — use the strongest model, low volume/lead
SCORE_THRESHOLD = 5  # overall_score <= this advances the lead
BATCH_SIZE = 20

SCREENSHOTS_DIR = Path(__file__).parent.parent / "screenshots"

client = anthropic.Anthropic()


def screenshot_pair(url: str, place_id: str) -> tuple[bytes, bytes]:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            desktop = browser.new_page(viewport={"width": 1440, "height": 900})
            desktop.goto(url, wait_until="domcontentloaded", timeout=20000)
            desktop_png = desktop.screenshot(timeout=10000)
            desktop.close()

            mobile = browser.new_page(viewport={"width": 390, "height": 844})
            mobile.goto(url, wait_until="domcontentloaded", timeout=20000)
            mobile_png = mobile.screenshot(timeout=10000)
            mobile.close()
        finally:
            browser.close()
    return desktop_png, mobile_png


def save_screenshot(png_bytes: bytes, place_id: str, label: str) -> str:
    """Writes to screenshots/{place_id}/{label}.png, returns the repo-relative
    path (stored in the CSV, committed alongside it so gate-1 review can open
    it straight from GitHub)."""
    lead_dir = SCREENSHOTS_DIR / place_id
    lead_dir.mkdir(parents=True, exist_ok=True)
    file_path = lead_dir / f"{label}.png"
    file_path.write_bytes(png_bytes)
    return str(file_path.relative_to(SCREENSHOTS_DIR.parent))


def score_with_claude(lead: dict, desktop_png: bytes, mobile_png: bytes) -> dict:
    prompt = QUALIFY_PROMPT.format(
        business_name=lead["name"],
        business_type=lead["business_type"],
        rating=lead.get("rating") or "no rating",
        review_count=lead.get("review_count") or 0,
    )
    message = client.messages.create(
        model=MODEL,
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "text", "text": "Desktop screenshot:"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                              "data": base64.b64encode(desktop_png).decode()}},
                {"type": "text", "text": "Mobile screenshot:"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                              "data": base64.b64encode(mobile_png).decode()}},
            ],
        }],
    )
    block = next((b for b in message.content if b.type == "text"), None)
    text = block.text.strip() if block else "{}"
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"overall_score": 5, "worth_pursuing": False, "notes": "Claude response unparseable"}


def qualify_lead(lead: dict) -> None:
    place_id = lead["place_id"]
    website = lead.get("website") or ""

    if not website:
        db.update_lead(place_id, {"status": "qualified", "qualify_worth_pursuing": True,
                                   "qualify_notes": "no website on file"})
        return

    fetched = fetch_existing_site(website)
    if not fetched["reachable"]:
        db.update_lead(place_id, {
            "status": "qualified", "qualify_score": 1, "qualify_worth_pursuing": True,
            "qualify_notes": f"site unreachable: {fetched['error']}",
        })
        return

    try:
        desktop_png, mobile_png = screenshot_pair(website, place_id)
    except Exception as e:
        db.update_lead(place_id, {
            "status": "qualified", "qualify_score": 1, "qualify_worth_pursuing": True,
            "qualify_notes": f"screenshot failed (treated as broken site): {e}",
        })
        return

    desktop_path = save_screenshot(desktop_png, place_id, "desktop")
    mobile_path = save_screenshot(mobile_png, place_id, "mobile")

    result = score_with_claude(lead, desktop_png, mobile_png)
    overall = result.get("overall_score", 5)
    worth_pursuing = bool(result.get("worth_pursuing")) and overall <= SCORE_THRESHOLD

    db.update_lead(place_id, {
        "status": "qualified" if worth_pursuing else "disqualified",
        "qualify_score": overall,
        "qualify_worth_pursuing": worth_pursuing,
        "qualify_notes": result.get("notes", ""),
        "screenshot_desktop_path": desktop_path,
        "screenshot_mobile_path": mobile_path,
        "contact_email": fetched.get("email"),
    })


def main():
    leads = db.get_leads("new", limit=BATCH_SIZE)
    print(f"Qualifying {len(leads)} lead(s)...")
    for lead in leads:
        print(f"  {lead['name']} ({lead['place_id']})")
        try:
            qualify_lead(lead)
        except Exception as e:
            db.update_lead(lead["place_id"], {"status": "new", "error": str(e)})
            print(f"    [ERROR] {e}")
        time.sleep(1)

    db.commit_and_push(
        f"qualify: {len(leads)} lead(s) processed",
        extra_paths=[str(SCREENSHOTS_DIR)],
    )
    print("Done.")


if __name__ == "__main__":
    main()
