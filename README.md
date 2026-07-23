# Hosted Site Outreach

Automated cold-outreach pipeline: finds local businesses with dated/broken
websites, generates a free redesigned landing-page mockup grounded in their
real Google Places data, publishes it, emails them, and tracks replies.

This is a **different strategy** from `lead-gen-pipeline` (which builds brand
new sites from scratch to rank organically for keywords nobody owns yet).
This pipeline never buys a domain or targets SEO — it pitches an existing
business a redesign of what they already have, using the business's own real
name/phone/address/reviews, and a mockup hosted as a static page.

## How it works

```
01_source_leads        (weekly)   Google Places Text Search per config/targets.yml
                                   -> upserts into Supabase outreach.leads (status=new)

02_qualify_screenshot   (nightly)  No website -> auto-qualify.
                                   Site unreachable -> auto-qualify (broken site = best target).
                                   Site loads -> Playwright screenshots (desktop+mobile)
                                   + Claude vision scoring -> status=qualified/disqualified.
                                   Also best-effort scrapes a contact email off the site.

   <<< GATE 1 — human: review qualify_score/notes/screenshots in Supabase,
       flip approved_for_site=true on leads worth generating a mockup for >>>

03_generate_publish_site (nightly) Claude generates ONE self-contained landing
                                   page grounded in the lead's real Places data
                                   + their existing site's actual copy (never
                                   fabricated). Committed as a flat HTML file to
                                   vet-demo-sites' main branch (same convention
                                   as asapdraincleaners.com.html already there)
                                   -> live instantly via GitHub Pages.
                                   status=site_generated

   <<< GATE 2 — human: open the live preview URL, flip
       approved_for_outreach=true if it's good enough to send >>>

04_send_outreach        (weekdays) Claude drafts a short personal email,
                                   sent via Gmail API (real inbox, threaded).
                                   status=emailed, 7-day clock starts.
                                   Skips leads with no contact_email on file.

05_poll_replies_followup (4hrly)  Polls the Gmail thread. Reply arrives ->
                                   Claude classifies interested/not/needs_info,
                                   status=replied_*. No reply by day 3 -> one
                                   automated nudge (status=followed_up). No
                                   reply by day 7 -> status=expired, mockup
                                   file removed from vet-demo-sites.
```

Everything downstream of the two human gates can run unattended once you
trust it. Nothing before either gate touches a real business's inbox or
spends Opus tokens on a lead nobody's reviewed.

## Setup

### 1. GitHub Secrets (Settings → Secrets and variables → Actions → Secrets)

| Secret | Used by | Notes |
|---|---|---|
| `GOOGLE_API_KEY` | 01 | Places API — can reuse Card-Shout's key |
| `ANTHROPIC_API_KEY` | 02, 03, 04, 05 | |
| `SUPABASE_URL` | all | Card-Shout's Supabase project (`US Address Database`) — `https://mijynfwlftqyloykvsco.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | all | **service_role**, not anon/publishable — this pipeline needs to bypass RLS. Get it from Supabase → Project Settings → API. |
| `GH_TOKEN_SITES` | 03, 05 | Fine-grained PAT with **Contents: read/write** on `stevendelarwelle/vet-demo-sites` specifically |
| `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` / `GMAIL_REFRESH_TOKEN` | 04, 05 | See Gmail OAuth setup below |
| `GMAIL_SENDER` | 04, 05 | The Gmail address sending/receiving outreach, e.g. `stevendelarwelle@gmail.com` |

### 2. GitHub Variables (same page, "Variables" tab)

| Variable | Default | Notes |
|---|---|---|
| `DRY_RUN` | `true` | While `true`: 03 logs what it would publish without committing, 04 logs the drafted email without sending, 05 logs follow-up/expire actions without sending or deleting. **Keep this `true` until you've watched a manual dry run of the full pipeline and are comfortable with the output.** Flip to `false` only when ready to actually publish sites / send email. |

### 3. Gmail OAuth (one-time, manual — required before 04/05 can run)

1. In Google Cloud Console, create a project (or reuse one), enable the
   **Gmail API**, and create an OAuth 2.0 Client ID of type **Desktop app**.
2. Note the Client ID and Client Secret — these become `GMAIL_CLIENT_ID` /
   `GMAIL_CLIENT_SECRET`.
3. Run a local OAuth consent flow once (using `google-auth-oauthlib`'s
   `InstalledAppFlow`, scope `https://www.googleapis.com/auth/gmail.modify`)
   signed in as the Gmail account you want sending/receiving outreach. This
   opens a browser, you approve access, and it prints a refresh token —
   that becomes `GMAIL_REFRESH_TOKEN`. This step has to be done by a human
   with browser access; it can't run inside GitHub Actions.

### 4. Supabase

The `outreach` schema and `outreach.leads` table already exist in the same
Supabase project Card-Shout uses (kept in its own schema — this never touches
`public.addresses`). A public storage bucket `outreach-screenshots` holds the
qualify-step screenshots for gate-1 review. Nothing else to provision.

### First run (do this before enabling any cron)

1. Add the secrets above, leave `DRY_RUN=true`.
2. `workflow_dispatch` → **01 Source Leads** manually — check `outreach.leads`
   in Supabase for new rows.
3. `workflow_dispatch` → **02 Qualify + Screenshot** — check `qualify_score`,
   `qualify_notes`, and the screenshot URLs for a handful of leads. Tune
   `prompts/qualify_prompt.md` if the scoring doesn't match your own judgment
   on ~15-20 known sites before trusting it further.
4. Manually flip `approved_for_site=true` on 2-3 leads in Supabase.
5. `workflow_dispatch` → **03 Generate + Publish Site** with `DRY_RUN=true`
   first, read the logged HTML output. When happy, flip `DRY_RUN=false` for
   one real run and open the live preview URL(s).
6. Manually fill `contact_email` for any lead where the qualify step didn't
   find one, and flip `approved_for_outreach=true`.
7. `workflow_dispatch` → **04 Send Outreach** with `DRY_RUN=true`, read the
   drafted email. When ready, flip `DRY_RUN=false` and send a small first
   batch (~5 leads) — not the full cron.
8. Watch **05 Poll Replies + Follow-up** across a full 7-day cycle for that
   batch before enabling the schedules on 03/04/05 for real.
9. Only once all of the above has been exercised successfully, remove the
   `workflow_dispatch`-only caution and let the cron schedules run unattended.

## Known gap: Google Places has no email field

Places API returns phone/website/rating, never a business email. `02` makes
a best-effort attempt to scrape one off the lead's existing site (mailto:
links first, then a generic email regex) and stores it as `contact_email`.
Leads with no website, or a site with no discoverable email, need
`contact_email` filled in by hand during gate 1/2 review — `04` skips any
lead with no `contact_email` rather than guessing one.

## Booking (step 8, not yet wired up)

Once a lead replies `interested`, point them to a Google Calendar
appointment-scheduling page link by hand for now (Calendar → Appointment
schedules, share the booking link in your reply). Not worth automating until
there's real reply volume to justify it.
