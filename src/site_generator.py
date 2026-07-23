"""
Generates ONE self-contained landing-page mockup per lead, grounded in their
real Places data + their existing site's own copy (never fabricated).

Adapted from lead-gen-pipeline/content_generator.py's prompt-engineering
style (inline CSS, CSS custom properties, mobile-first), scoped down from a
25-page programmatic-SEO site to a single noindex mockup page — this isn't
meant to rank, it's meant to be a pitch a business owner opens once.
"""

import re
from pathlib import Path

import anthropic

from src.scrape_existing_site import fetch_existing_site

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "site_gen_prompt.md"
SITE_GEN_PROMPT = PROMPT_PATH.read_text()

MODEL = "claude-opus-4-6"

client = anthropic.Anthropic()


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def filename_for(lead: dict) -> str:
    """Mirrors vet-demo-sites' existing convention (asapdraincleaners.com.html):
    prefer the lead's real domain if we have one, else a name-city slug."""
    website = (lead.get("website") or "").strip()
    if website:
        domain = re.sub(r"^https?://", "", website).split("/")[0]
        domain = re.sub(r"^www\.", "", domain).strip().lower()
        if domain:
            return f"{domain}.html"

    city = lead.get("location_query", "").split(",")[0]
    slug = slugify(f"{lead['name']}-{city}")
    return f"{slug}.html"


def generate_site_html(lead: dict) -> str:
    existing = fetch_existing_site(lead.get("website") or "")
    existing_text = existing["text"] or "(no existing site content available — use conservative, generic copy for the business category)"

    phone = lead.get("phone") or ""
    phone_tel = "+1" + re.sub(r"\D", "", phone) if phone else ""

    prompt = SITE_GEN_PROMPT.format(
        business_name=lead["name"],
        business_type=lead["business_type"],
        address=lead.get("address") or "",
        phone=phone,
        phone_tel=phone_tel,
        rating=lead.get("rating") or "no rating yet",
        review_count=lead.get("review_count") or 0,
        existing_site_text=existing_text,
        place_id=lead["place_id"],
    )

    message = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )
    block = next((b for b in message.content if b.type == "text"), None)
    html = block.text.strip() if block else ""
    html = html.removeprefix("```html").removeprefix("```").removesuffix("```").strip()
    return html


def main():
    import os
    import time
    from datetime import datetime, timezone

    from src import db, publisher

    # Safe-by-default — see outreach.py for why this isn't "== 'true'".
    dry_run = os.environ.get("DRY_RUN", "true").lower() != "false"
    batch_size = int(os.environ.get("SITE_GEN_BATCH_SIZE", "10"))

    leads = db.get_leads("qualified", limit=batch_size, approved_for_site=True)
    print(f"Generating {len(leads)} site(s){' [DRY RUN]' if dry_run else ''}...")

    for lead in leads:
        print(f"  {lead['name']} ({lead['place_id']})")
        try:
            html = generate_site_html(lead)
            filename = filename_for(lead)

            if dry_run:
                print(f"    [DRY RUN] would publish {filename} ({len(html)} chars)")
                continue

            url = publisher.publish_site(
                filename, html,
                commit_message=f"Add mockup site for {lead['name']}",
            )
            db.update_lead(lead["place_id"], {
                "status": "site_generated",
                "site_filename": filename,
                "site_url": url,
                "site_generated_at": datetime.now(timezone.utc).isoformat(),
            })
            print(f"    -> {url}")
        except Exception as e:
            db.update_lead(lead["place_id"], {"error": str(e)})
            print(f"    [ERROR] {e}")
        time.sleep(1)

    print("Done.")


if __name__ == "__main__":
    main()
