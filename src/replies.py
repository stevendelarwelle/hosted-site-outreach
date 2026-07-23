"""
Polls Gmail threads for leads in 'emailed'/'followed_up' status, classifies
any new reply with Claude, and drives the day-3/day-7 timer:
  - reply arrives at any point -> classify, status = replied_*, timer stops
  - no reply by day 3-4 -> one automated nudge, status = followed_up
  - no reply by day 7 -> status = expired, mockup file removed from
    vet-demo-sites

Designed to run every few hours (see .github/workflows/05_poll_replies.yml)
so a genuinely interested reply doesn't sit for a full day before anyone/
anything reacts to it.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic

from src import db, mailer, publisher

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "reply_classify_prompt.md"
CLASSIFY_PROMPT = PROMPT_PATH.read_text()

MODEL = "claude-haiku-4-5-20251001"
FOLLOWUP_AFTER_DAYS = 3
EXPIRE_AFTER_DAYS = 7

client = anthropic.Anthropic()


def classify_reply(reply_text: str) -> dict:
    prompt = CLASSIFY_PROMPT.format(reply_text=reply_text[:3000])
    message = client.messages.create(
        model=MODEL, max_tokens=150,
        messages=[{"role": "user", "content": prompt}],
    )
    block = next((b for b in message.content if b.type == "text"), None)
    text = (block.text if block else "{}").strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"classification": "unclear", "reason": "Claude response unparseable"}


TERMINAL_CLASSIFICATIONS = {"interested", "not_interested", "needs_info"}


def check_for_reply(lead: dict, dry_run: bool) -> bool:
    """Returns True if there's an unresolved reply this run should stop on
    (either freshly classified, or already classified on a prior run and
    just waiting on a human — either way, don't fall through to
    follow-up/expire logic below)."""
    reply = mailer.get_latest_reply(lead["email_thread_id"], lead["email_message_id"])
    if not reply:
        return False

    if reply["message_id"] == lead.get("last_reply_message_id"):
        # Already classified this exact message on a prior run — don't
        # re-spend a Claude call on it every 4 hours forever.
        return lead.get("reply_classification") in TERMINAL_CLASSIFICATIONS

    result = classify_reply(reply["body"])
    classification = result.get("classification", "unclear")
    print(f"    reply classified: {classification} ({result.get('reason', '')})")

    if dry_run:
        return True

    if classification in TERMINAL_CLASSIFICATIONS:
        new_status = f"replied_{classification}"
        expire_at = None  # stop the day-3/7 timer, this needs a human now
    else:
        # unclear (auto-reply/out-of-office/empty) — not a real reply, keep
        # waiting on the original 7-day clock as if nothing arrived.
        new_status = lead["status"]
        expire_at = lead.get("expire_at")

    db.update_lead(lead["place_id"], {
        "status": new_status,
        "reply_classification": classification,
        "last_reply_message_id": reply["message_id"],
        "expire_at": expire_at,
    })
    return classification in TERMINAL_CLASSIFICATIONS


def send_followup(lead: dict, dry_run: bool) -> None:
    if dry_run:
        print(f"    [DRY RUN] would send day-{FOLLOWUP_AFTER_DAYS} nudge to {lead.get('contact_email')}")
        return

    body = (
        f"Hey — just following up in case my last email got buried. "
        f"Here's the mockup again: {lead['site_url']}\n\nNo pressure either way, "
        f"just didn't want it to get lost.\n\n- Steve"
    )
    sent = mailer.send_followup(
        lead["contact_email"], f"Mockup for {lead['name']}", body, lead["email_thread_id"]
    )
    db.update_lead(lead["place_id"], {
        "status": "followed_up",
        "followup_sent_at": datetime.now(timezone.utc).isoformat(),
    })
    print(f"    -> nudge sent, message {sent['message_id']}")


def expire_lead(lead: dict, dry_run: bool) -> None:
    if dry_run:
        print(f"    [DRY RUN] would expire and unpublish {lead.get('site_filename')}")
        return

    if lead.get("site_filename"):
        try:
            publisher.unpublish_site(
                lead["site_filename"], commit_message=f"Expire mockup for {lead['name']}"
            )
        except Exception as e:
            print(f"    [WARN] failed to unpublish {lead['site_filename']}: {e}")

    db.update_lead(lead["place_id"], {"status": "expired"})
    print(f"    -> expired, mockup removed")


def process_lead(lead: dict, now: datetime, dry_run: bool) -> None:
    if check_for_reply(lead, dry_run):
        return

    sent_at = lead.get("email_sent_at")
    if not sent_at:
        return
    sent_dt = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
    days_since_send = (now - sent_dt).days

    if lead["status"] == "emailed" and days_since_send >= FOLLOWUP_AFTER_DAYS:
        send_followup(lead, dry_run)
    elif days_since_send >= EXPIRE_AFTER_DAYS:
        expire_lead(lead, dry_run)


def main():
    # Safe-by-default — see outreach.py for why this isn't "== 'true'".
    dry_run = os.environ.get("DRY_RUN", "true").lower() != "false"
    now = datetime.now(timezone.utc)

    leads = db.get_leads("emailed", limit=50) + db.get_leads("followed_up", limit=50)
    print(f"Checking {len(leads)} outstanding lead(s){' [DRY RUN]' if dry_run else ''}...")

    for lead in leads:
        print(f"  {lead['name']} ({lead['place_id']}) - {lead['status']}")
        try:
            process_lead(lead, now, dry_run)
        except Exception as e:
            print(f"    [ERROR] {e}")

    print("Done.")


if __name__ == "__main__":
    main()
