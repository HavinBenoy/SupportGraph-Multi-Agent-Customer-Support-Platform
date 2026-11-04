"""Admin analytics: aggregates over the tables the system already keeps (chat_events, tickets, escalations, makeup_requests).
Portable SQL + Python (SQLite and Postgres). Tenant-scoped by construction: every query filters on tenant_id.
Measures what the SYSTEM did, not whether answers were right (that needs labelled evaluation: `make eval-routing`)."""

from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ChatEvent, Escalation, MakeupRequest, Ticket
from app.tenants.schema import TenantConfig

MAX_ROWS = 100_000  # safety cap on rows read per query


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _pct(sorted_vals: list[int], q: float) -> int | None:
    if not sorted_vals:
        return None
    return sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))]


def compute(db: Session, cfg: TenantConfig, *, days: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    tid = cfg.tenant_id

    events = [e for e in db.execute(select(ChatEvent).where(ChatEvent.tenant_id == tid).limit(MAX_ROWS)).scalars()
              if _aware(e.created_at) >= since]
    turns = len(events)
    routes = Counter(e.route for e in events)
    knowledge = [e for e in events if e.route == "knowledge"]
    lat = sorted(e.latency_ms for e in events)
    per_day: Counter[str] = Counter(_aware(e.created_at).date().isoformat() for e in events)
    day_series = [{"date": (since + timedelta(days=i + 1)).date().isoformat(),
                   "turns": per_day.get((since + timedelta(days=i + 1)).date().isoformat(), 0)} for i in range(days)]
    chat = {
        "turns": turns, "by_route": dict(sorted(routes.items())),
        "signed_in_share": round(sum(e.signed_in for e in events) / turns, 4) if turns else None,
        "abstain_rate": round(sum(e.abstained for e in knowledge) / len(knowledge), 4) if knowledge else None,
        "qa_blocked": sum(e.qa_blocked for e in events),
        # share of turns that did not need a person (no escalation, no staff approval waiting)
        "self_service_rate": round(sum(not e.needs_human and e.awaiting != "staff" for e in events) / turns, 4) if turns else None,
        "latency_ms": {"p50": _pct(lat, 0.5), "p95": _pct(lat, 0.95)},
        "per_day": day_series,
    }

    esc = [e for e in db.execute(select(Escalation).where(Escalation.tenant_id == tid).limit(MAX_ROWS)).scalars()]
    recent_esc = [e for e in esc if _aware(e.created_at) >= since]
    open_ = [e for e in esc if e.status != "resolved"]
    breaches = [e for e in open_ if now - _aware(e.created_at) > timedelta(hours=cfg.sla_hours.get(e.priority, 48))]
    resolved_hours = sorted(round((_aware(e.resolved_at) - _aware(e.created_at)).total_seconds() / 3600, 2)
                            for e in recent_esc if e.resolved_at)
    escalations = {
        "created": len(recent_esc), "open": sum(e.status == "open" for e in open_), "claimed": sum(e.status == "claimed" for e in open_),
        "resolved": len(resolved_hours), "by_team": dict(sorted(Counter(e.team for e in recent_esc).items())),
        "by_priority": dict(sorted(Counter(e.priority for e in recent_esc).items())),
        "by_reason": dict(sorted(Counter(e.reason for e in recent_esc).items())),
        "median_hours_to_resolve": resolved_hours[len(resolved_hours) // 2] if resolved_hours else None,
        "sla_breaches_open": len(breaches),
        "oldest_open_hours": round(max(((now - _aware(e.created_at)).total_seconds() / 3600 for e in open_), default=0), 1) or None,
    }

    mk = [m for m in db.execute(select(MakeupRequest).where(MakeupRequest.tenant_id == tid).limit(MAX_ROWS)).scalars()]
    decided_hours = sorted((_aware(m.decided_at) - _aware(m.created_at)).total_seconds() / 3600 for m in mk if m.decided_at)
    approvals = {
        "pending": sum(m.status == "pending" for m in mk), "approved": sum(m.status == "approved" for m in mk),
        "rejected": sum(m.status == "rejected" for m in mk),
        "median_hours_to_decide": round(decided_hours[len(decided_hours) // 2], 2) if decided_hours else None,
    }
    tickets = Counter(t.status for t in db.execute(select(Ticket).where(Ticket.tenant_id == tid).limit(MAX_ROWS)).scalars())
    return {"tenant_id": tid, "days": days, "generated_at": now.isoformat(), "chat": chat, "escalations": escalations,
            "approvals": approvals, "tickets_by_status": dict(sorted(tickets.items())),
            "notes": ["Counts what the system did; answer quality is measured by `make eval-routing`, not here.",
                      "Data comes from chat_events written by the API: turns made before step 5 are not included."]}
