"""
Supabase wrapper for the outreach.leads table.

Uses the service_role key (server-side only, bypasses RLS) — never use the
publishable/anon key here. Same Supabase project as Card-Shout
("US Address Database" / mijynfwlftqyloykvsco), separate `outreach` schema so
this never touches `public.addresses`.
"""

import os
from supabase import create_client, Client, ClientOptions

SCHEMA = "outreach"
TABLE = "leads"

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        _client = create_client(url, key, options=ClientOptions(schema=SCHEMA))
    return _client


def upsert_lead(row: dict) -> None:
    """Insert a new lead or no-op on conflict (place_id). Used by sourcing —
    never overwrites an existing lead's pipeline state."""
    get_client().table(TABLE).upsert(row, on_conflict="place_id", ignore_duplicates=True).execute()


def update_lead(place_id: str, fields: dict) -> None:
    get_client().table(TABLE).update(fields).eq("place_id", place_id).execute()


def get_leads(status: str, limit: int = 25, **filters) -> list[dict]:
    q = get_client().table(TABLE).select("*").eq("status", status)
    for k, v in filters.items():
        q = q.eq(k, v)
    resp = q.limit(limit).execute()
    return resp.data


def get_lead(place_id: str) -> dict | None:
    resp = get_client().table(TABLE).select("*").eq("place_id", place_id).limit(1).execute()
    return resp.data[0] if resp.data else None


def get_leads_where_lt(column: str, value: str, status_in: list[str], limit: int = 50) -> list[dict]:
    """e.g. get_leads_where_lt('expire_at', now_iso, ['emailed', 'followed_up'])"""
    q = (
        get_client()
        .table(TABLE)
        .select("*")
        .lt(column, value)
        .in_("status", status_in)
        .limit(limit)
    )
    return q.execute().data
