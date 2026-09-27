"""
Vera Bot — Main FastAPI Application
====================================
Exposes all 5 endpoints required by the magicpin Judge Harness:
1. GET  /v1/healthz  — liveness probe
2. GET  /v1/metadata — bot identity
3. POST /v1/context  — context push (idempotent)
4. POST /v1/tick     — tick wake-up & proactive composition
5. POST /v1/reply    — conversation turn reply
"""

from __future__ import annotations
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional, List, Dict

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

from context_store import ContextStore
from composer import compose
from reply_engine import ConversationTracker, handle_reply

START_TIME = time.time()

app = FastAPI(title="Vera Bot", version="1.0.0")

store = ContextStore()
tracker = ConversationTracker()


# ═════════════════════════════════════════════════════════════════════════
# MODELS
# ═════════════════════════════════════════════════════════════════════════

class ContextPushRequest(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: Dict[str, Any]
    delivered_at: Optional[str] = None


class TickRequest(BaseModel):
    now: str
    available_triggers: List[str] = Field(default_factory=list)


class ReplyRequest(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: Optional[str] = None
    turn_number: int = 1


# ═════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "Vera AI Merchant Growth Engine",
        "version": "1.0.0",
        "endpoints": {
            "health": "/v1/healthz",
            "metadata": "/v1/metadata",
            "context_push": "/v1/context (POST)",
            "tick": "/v1/tick (POST)",
            "reply": "/v1/reply (POST)",
        }
    }


@app.get("/v1/healthz")
async def healthz():
    uptime = int(time.time() - START_TIME)
    return {
        "status": "ok",
        "uptime_seconds": uptime,
        "contexts_loaded": store.counts(),
    }


@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": "Team Vera AI",
        "team_members": ["Vera Engineer"],
        "model": "deterministic-context-composer",
        "approach": "rule-grounded 4-context engine with zero hallucination & category voice profiles",
        "contact_email": "vera-team@magicpin.in",
        "version": "1.0.0",
        "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


@app.post("/v1/context")
async def push_context(req: ContextPushRequest):
    if req.scope not in ("category", "merchant", "customer", "trigger"):
        return {
            "accepted": False,
            "reason": "invalid_scope",
            "details": f"Scope '{req.scope}' is not valid",
        }

    accepted, current_ver, stored_at = store.upsert(
        req.scope, req.context_id, req.version, req.payload
    )

    if not accepted:
        return {
            "accepted": False,
            "reason": "stale_version",
            "current_version": current_ver,
        }

    return {
        "accepted": True,
        "ack_id": f"ack_{req.context_id}_v{req.version}",
        "stored_at": stored_at,
    }


@app.post("/v1/tick")
async def tick(req: TickRequest):
    actions = []

    for trg_id in req.available_triggers:
        # Avoid repeat processing of trigger in same session if already actioned
        if trg_id in store.actioned_triggers:
            continue

        trg = store.get_trigger(trg_id)
        if not trg:
            continue

        merchant_id = trg.get("merchant_id")
        if not merchant_id or merchant_id in store.suppressed_merchants:
            continue

        merchant = store.get_merchant(merchant_id)
        if not merchant:
            continue

        cat_slug = merchant.get("category_slug", "")
        category = store.get_category(cat_slug) or {"slug": cat_slug, "voice": {}}

        customer_id = trg.get("customer_id")
        customer = store.get_customer(customer_id) if customer_id else None

        # Compose message
        action = compose(category, merchant, trg, customer)

        # Mark trigger as actioned
        store.actioned_triggers.add(trg_id)
        actions.append(action)

        # Rate-cap at 20 per tick per protocol
        if len(actions) >= 20:
            break

    return {"actions": actions}


@app.post("/v1/reply")
async def reply(req: ReplyRequest):
    merchant_id = req.merchant_id or ""
    customer_id = req.customer_id

    res = handle_reply(
        conversation_id=req.conversation_id,
        merchant_id=merchant_id,
        customer_id=customer_id,
        from_role=req.from_role,
        message=req.message,
        turn_number=req.turn_number,
        store=store,
        tracker=tracker,
    )

    return res


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bot:app", host="0.0.0.0", port=8080, reload=True)
