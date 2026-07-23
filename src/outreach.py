"""
Drafts and sends the cold outreach email for site_generated leads that have
cleared the second human approval gate (approved_for_outreach). Gated behind
DRY_RUN — with DRY_RUN=true this drafts and logs the email but never calls
the Gmail API.
"""

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic

from src import db, mailer

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "email_draft_prompt.md"
EMAIL_PROMPT = PROMPT_PATH.read_text()

MODEL = "claude-opus-4-6"
FOLLOWUP_DAYS = 4
EXPIRE_DAYS = 7

client = anthropic.Anthropic()


def draft_email(lead: dict) -> str:
    prompt = EMAIL_PROMPT.format(
        business_name=lead["name"],
        website=lead.get("website") or "(no website on file)",
        preview_url=lead["site_url"],
    )
    message = client.messages.create(
        model=MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    block = next((b for b in message.content if b.type == "text"), None)
    return block.text.strip() if block else ""


def find_lead_email(lead: dict) -> str | None:
    """Places API doesn't return an email address — this pipeline needs a
    contact email sourced separately (e.g. scraped from the lead's existing
    site during the qualify step, or filled in manually during the approval
    gate). Returns None if we don't have one, and the caller skips the lead
    rather than guessing an address."""
    return lead.get("contact_email")


def main():
    # Safe-by-default: dry-run unless DRY_RUN is explicitly "false". This
    # matters because ${{ vars.DRY_RUN }} resolves to an EMPTY string (not
    # absent) if the repo variable was never created — an "== 'true'" check
    # would silently default to live-sending in that case.
    dry_run = os.environ.get("DRY_RUN", "true").lower() != "false"
    batch_size = int(os.environ.get("OUTREACH_BATCH_SIZE", "10"))

    leads = db.get_leads("site_generated", limit=batch_size, approved_for_outreach=True)
    print(f"Sending outreach for {len(leads)} lead(s){' [DRY RUN]' if dry_run else ''}...")

    for lead in leads:
        print(f"  {lead['name']} ({lead['place_id']})")
        to_email = find_lead_email(lead)
        if not to_email:
            print("    [SKIP] no contact email on file for this lead")
            continue

        try:
            body = draft_email(lead)
            subject = f"Built you a mockup of a new {lead['name']} site"

            if dry_run:
                print(f"    [DRY RUN] would email {to_email}:\n{body}\n")
                continue

            sent = mailer.send_email(to_email, subject, body)
            now = datetime.now(timezone.utc)

            db.update_lead(lead["place_id"], {
                "status": "emailed",
                "email_message_id": sent["message_id"],
                "email_thread_id": sent["thread_id"],
                "email_sent_at": now.isoformat(),
                "expire_at": (now + timedelta(days=EXPIRE_DAYS)).isoformat(),
            })
            print(f"    -> sent, thread {sent['thread_id']}")
        except Exception as e:
            db.update_lead(lead["place_id"], {"error": str(e)})
            print(f"    [ERROR] {e}")
        time.sleep(1)

    print("Done.")


if __name__ == "__main__":
    main()
