"""
CSV-backed lead store — data/leads.csv is the database, committed to the repo
like lead-gen-pipeline's approved.csv/sites.csv. No external service.

Write-through cache: every upsert_lead()/update_lead() call rewrites the
whole file immediately (cheap at this scale — tens to low hundreds of rows),
so callers never need to remember to flush. What still needs an explicit git
commit+push is making that local write durable to the remote repo — see
commit_and_push() below, called once at the end of most scripts' main(), and
after every send in outreach.py specifically (see its module docstring for
why that one script needs tighter durability).

Human approval gates (approved_for_site, approved_for_outreach) are just
cells in this CSV — edit directly, or through GitHub's web file editor.
"""

import csv
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

CSV_PATH = Path(__file__).parent.parent / "data" / "leads.csv"

FIELDS = [
    "place_id", "business_type", "location_query", "name", "address", "phone",
    "website", "rating", "review_count",
    "status",
    "qualify_score", "qualify_worth_pursuing", "qualify_notes",
    "screenshot_desktop_path", "screenshot_mobile_path",
    "approved_for_site", "approved_for_outreach",
    "site_filename", "site_url", "site_generated_at",
    "contact_email",
    "draft_id", "email_message_id", "email_thread_id", "email_sent_at",
    "followup_sent_at", "expire_at", "reply_classification",
    "last_reply_message_id", "claimed_at",
    "error", "created_at", "updated_at",
]

BOOL_FIELDS = {"qualify_worth_pursuing", "approved_for_site", "approved_for_outreach"}
INT_FIELDS = {"review_count", "qualify_score"}
FLOAT_FIELDS = {"rating"}

_leads: dict[str, dict] | None = None


def _coerce_in(row: dict) -> dict:
    """CSV round-trips everything as strings — cast back to the types the
    rest of the codebase expects when reading a row."""
    out = dict(row)
    for f in BOOL_FIELDS:
        out[f] = str(out.get(f, "")).strip().lower() == "true"
    for f in INT_FIELDS:
        out[f] = int(out[f]) if out.get(f, "").strip() else None
    for f in FLOAT_FIELDS:
        out[f] = float(out[f]) if out.get(f, "").strip() else None
    return out


def _coerce_out(row: dict) -> dict:
    """Flatten Python types back to strings for csv.DictWriter."""
    out = {}
    for f in FIELDS:
        v = row.get(f)
        out[f] = "" if v is None else str(v)
    return out


def _load() -> dict[str, dict]:
    global _leads
    if _leads is not None:
        return _leads

    _leads = {}
    if CSV_PATH.exists():
        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            for raw in csv.DictReader(f):
                row = _coerce_in(raw)
                _leads[row["place_id"]] = row
    return _leads


def _save() -> None:
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for row in sorted(_leads.values(), key=lambda r: r["place_id"]):
            writer.writerow(_coerce_out(row))


def upsert_lead(row: dict) -> None:
    """Insert a new lead. No-op if place_id already exists — sourcing never
    overwrites a lead already further along in the pipeline."""
    leads = _load()
    if row["place_id"] in leads:
        return
    now = _now()
    stored = {f: row.get(f) for f in FIELDS}
    stored["created_at"] = now
    stored["updated_at"] = now
    leads[row["place_id"]] = stored
    _save()


def update_lead(place_id: str, fields: dict) -> None:
    leads = _load()
    if place_id not in leads:
        raise KeyError(f"no lead with place_id={place_id!r} in {CSV_PATH}")
    leads[place_id].update(fields)
    leads[place_id]["updated_at"] = _now()
    _save()


def get_lead(place_id: str) -> dict | None:
    return _load().get(place_id)


def get_leads(status: str, limit: int = 25, **filters) -> list[dict]:
    rows = [r for r in _load().values() if r.get("status") == status]
    for k, v in filters.items():
        rows = [r for r in rows if r.get(k) == v]
    return rows[:limit]


def commit_and_push(message: str, extra_paths: list[str] | None = None) -> None:
    """Commits data/leads.csv (+ any extra_paths, e.g. screenshots/) and
    pushes. Safe to call with nothing staged — just no-ops."""
    paths = [str(CSV_PATH)] + (extra_paths or [])
    existing = [p for p in paths if Path(p).exists()]
    if not existing:
        return

    repo_root = Path(__file__).parent.parent
    subprocess.run(["git", "add", *existing], cwd=repo_root, check=True)

    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=repo_root
    )
    if diff.returncode == 0:
        return  # nothing staged, no-op

    subprocess.run(["git", "commit", "-m", message], cwd=repo_root, check=True)
    subprocess.run(["git", "push"], cwd=repo_root, check=True)
