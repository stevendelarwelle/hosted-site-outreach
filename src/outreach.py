"""
Drafts the cold outreach email for site_generated leads that have cleared
the second human approval gate (approved_for_outreach), and creates it as a
Gmail draft rather than sending it directly — a human reviews and hits send
themselves from within Gmail. Gated behind DRY_RUN — with DRY_RUN=true this
drafts the copy and logs it but never calls the Gmail API at all.

Sending directly through the API previously triggered Gmail's "this message
isn't authenticated" warning on the recipient end for reasons that turned
out not to be fixable from this codebase (see mailer.py's history) — having
a human actually hit send from Gmail's own UI sidesteps that entirely, at
the cost of losing full hands-off automation on this one step. See
replies.py for how the day-3/7 reply-tracking clock picks up once a draft
is actually sent, rather than when it was drafted.

Unlike the other pipeline scripts, this one commits+pushes data/leads.csv
after EVERY successful draft creation, not once at the end of the batch. A
crash after Gmail confirms the draft exists but before that status lands in
git would otherwise mean the next run has no record of it and creates a
duplicate draft. Committing per-draft bounds that risk to "at most one
in-flight draft" instead of the whole batch.
"""

import os
import time
from pathlib import Path

import anthropic

from src import db, mailer

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "email_draft_prompt.md"
EMAIL_PROMPT = PROMPT_PATH.read_text()

MODEL = "claude-opus-4-6"

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
    print(f"Drafting outreach for {len(leads)} lead(s){' [DRY RUN]' if dry_run else ''}...")

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
                print(f"    [DRY RUN] would draft to {to_email}:\n{body}\n")
                continue

            draft = mailer.create_draft(to_email, subject, body)

            db.update_lead(lead["place_id"], {
                "status": "draft_created",
                "draft_id": draft["draft_id"],
                "email_message_id": draft["message_id"],
                "email_thread_id": draft["thread_id"],
            })
            db.commit_and_push(f"outreach: drafted email for {lead['name']} ({lead['place_id']})")
            print(f"    -> draft created, {draft['draft_id']} — review and send from Gmail")
        except Exception as e:
            db.update_lead(lead["place_id"], {"error": str(e)})
            print(f"    [ERROR] {e}")
        time.sleep(1)

    print("Done.")


if __name__ == "__main__":
    main()
