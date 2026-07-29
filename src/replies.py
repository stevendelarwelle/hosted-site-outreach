"""
Everything this pipeline puts in front of a real person goes through a
human hitting send in Gmail's own UI — see outreach.py for why direct API
sends were dropped. That means every step here that used to "just send"
now creates a draft and waits to notice you've sent it. Four jobs, run
together each pass:

1. 'draft_created' leads: outreach.py created the initial pitch as a Gmail
   draft. Check whether you've sent it yet — once you have, promote to
   'emailed' and start the day-3/7 clock from the REAL send time, not
   draft-creation time (a draft can sit unsent for days).

2. 'followup_draft_created' leads: same idea for the day-3 nudge draft
   (see draft_followup() below) — once you've sent it, promote to
   'followed_up'. Distinguishing "the nudge got sent" from "the original
   email is still sitting there sent" needs care since both live in the
   same Gmail thread — see mailer.get_new_sent_message()'s known_message_ids
   parameter.

3. 'emailed' / 'followup_draft_created' / 'followed_up' leads: poll for a
   reply, classify with Claude. Any terminal classification
   (interested/not_interested/needs_info) stops the clock — status=replied_*.

4. Timer-driven, off the ORIGINAL send time (email_sent_at), regardless of
   whether the nudge draft has been sent yet: day 3+ with status still
   'emailed' -> draft the day-3 nudge (does not send it). Day 7+ with no
   reply, any of the three statuses above -> status=expired, mockup file
   removed from vet-demo-sites.

Designed to run every few hours (see .github/workflows/05_poll_replies_followup.yml)
so a genuinely interested reply — or a draft you just sent — doesn't sit for
a full day before anyone/anything reacts to it.
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


def check_draft_sent(lead: dict, dry_run: bool) -> None:
    """Detects whether a human has sent the initial-outreach Gmail draft
    outreach.py created. No-ops if it's still sitting unsent (or was
    deleted without ever being sent — indistinguishable from "still
    drafting," and low-stakes either way; it just stays in draft_created
    until manually cleaned up)."""
    sent = mailer.get_new_sent_message(lead["email_thread_id"], known_message_ids=set())
    if not sent:
        return

    if dry_run:
        print("    [DRY RUN] draft has been sent — would start the reply-tracking clock")
        return

    internal_date_ms = sent.get("internal_date")
    sent_at = (
        datetime.fromtimestamp(int(internal_date_ms) / 1000, tz=timezone.utc)
        if internal_date_ms else datetime.now(timezone.utc)
    )

    db.update_lead(lead["place_id"], {
        "status": "emailed",
        "email_message_id": sent["message_id"],
        "email_sent_at": sent_at.isoformat(),
        "expire_at": (sent_at + timedelta(days=EXPIRE_AFTER_DAYS)).isoformat(),
    })
    db.commit_and_push(f"replies: {lead['name']} ({lead['place_id']}) draft was sent, clock started")
    print(f"    -> draft sent at {sent_at.isoformat()}, day-{EXPIRE_AFTER_DAYS} clock started")


def check_followup_draft_sent(lead: dict, dry_run: bool) -> None:
    """Same idea as check_draft_sent, but for the day-3 nudge draft — and
    the thread already contains one SENT message (the original outreach
    email) by this point, so the original's message_id has to be excluded
    or it'd look like the nudge was "sent" the instant the draft exists."""
    known = {lead["email_message_id"]} if lead.get("email_message_id") else set()
    sent = mailer.get_new_sent_message(lead["email_thread_id"], known_message_ids=known)
    if not sent:
        return

    if dry_run:
        print("    [DRY RUN] follow-up draft has been sent")
        return

    internal_date_ms = sent.get("internal_date")
    sent_at = (
        datetime.fromtimestamp(int(internal_date_ms) / 1000, tz=timezone.utc)
        if internal_date_ms else datetime.now(timezone.utc)
    )

    db.update_lead(lead["place_id"], {
        "status": "followed_up",
        "followup_sent_at": sent_at.isoformat(),
    })
    db.commit_and_push(f"replies: {lead['name']} ({lead['place_id']}) follow-up draft was sent")
    print(f"    -> follow-up sent at {sent_at.isoformat()}")


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


def draft_followup(lead: dict, dry_run: bool) -> None:
    if dry_run:
        print(f"    [DRY RUN] would draft day-{FOLLOWUP_AFTER_DAYS} nudge to {lead.get('contact_email')}")
        return

    body = (
        f"Hey — just following up in case my last email got buried. "
        f"Here's the mockup again: {lead['site_url']}\n\nNo pressure either way, "
        f"just didn't want it to get lost.\n\n- Steve"
    )
    draft = mailer.create_draft_reply(
        lead["contact_email"], f"Mockup for {lead['name']}", body, lead["email_thread_id"]
    )
    db.update_lead(lead["place_id"], {
        "status": "followup_draft_created",
        "followup_draft_id": draft["draft_id"],
    })
    # Per-draft commit, same reasoning as outreach.py — don't risk creating
    # a duplicate nudge draft if the process dies before the end-of-run commit.
    db.commit_and_push(f"replies: drafted follow-up for {lead['name']} ({lead['place_id']})")
    print(f"    -> follow-up draft created, {draft['draft_id']} — review and send from Gmail")


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
    print("    -> expired, mockup removed")


def process_lead(lead: dict, now: datetime, dry_run: bool) -> None:
    if check_for_reply(lead, dry_run):
        return

    sent_at = lead.get("email_sent_at")
    if not sent_at:
        return
    sent_dt = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
    days_since_send = (now - sent_dt).days

    # Timer is always relative to the ORIGINAL send, regardless of whether
    # the day-3 nudge draft has been sent yet — expiry doesn't wait on you
    # remembering to send the nudge.
    if days_since_send >= EXPIRE_AFTER_DAYS:
        expire_lead(lead, dry_run)
        return

    if lead["status"] == "emailed" and days_since_send >= FOLLOWUP_AFTER_DAYS:
        draft_followup(lead, dry_run)


def main():
    # Safe-by-default — see outreach.py for why this isn't "== 'true'".
    dry_run = os.environ.get("DRY_RUN", "true").lower() != "false"
    now = datetime.now(timezone.utc)

    initial_drafts = db.get_leads("draft_created", limit=50)
    print(f"Checking {len(initial_drafts)} pending initial draft(s){' [DRY RUN]' if dry_run else ''}...")
    for lead in initial_drafts:
        print(f"  {lead['name']} ({lead['place_id']})")
        try:
            check_draft_sent(lead, dry_run)
        except Exception as e:
            print(f"    [ERROR] {e}")

    followup_drafts = db.get_leads("followup_draft_created", limit=50)
    print(f"Checking {len(followup_drafts)} pending follow-up draft(s){' [DRY RUN]' if dry_run else ''}...")
    for lead in followup_drafts:
        print(f"  {lead['name']} ({lead['place_id']})")
        try:
            check_followup_draft_sent(lead, dry_run)
        except Exception as e:
            print(f"    [ERROR] {e}")

    # Re-fetch: some leads above may have just been promoted this run
    # (draft_created->emailed, followup_draft_created->followed_up) — pull
    # fresh so reply/expiry checks see their current status, not stale.
    leads = (
        db.get_leads("emailed", limit=50)
        + db.get_leads("followup_draft_created", limit=50)
        + db.get_leads("followed_up", limit=50)
    )
    print(f"Checking {len(leads)} outstanding lead(s){' [DRY RUN]' if dry_run else ''}...")

    for lead in leads:
        print(f"  {lead['name']} ({lead['place_id']}) - {lead['status']}")
        try:
            process_lead(lead, now, dry_run)
        except Exception as e:
            print(f"    [ERROR] {e}")

    total = len(initial_drafts) + len(followup_drafts) + len(leads)
    db.commit_and_push(f"replies: {total} lead(s) checked")
    print("Done.")


if __name__ == "__main__":
    main()
