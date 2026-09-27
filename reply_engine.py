"""
Vera Bot — Reply Engine
========================
Handles merchant/customer replies to Vera's messages.
Detects auto-replies, hostile messages, intent transitions,
off-topic asks, and continues conversations intelligently.
"""

from __future__ import annotations
import re
from typing import Optional

from context_store import ContextStore


# ═════════════════════════════════════════════════════════════════════════
# AUTO-REPLY DETECTION
# ═════════════════════════════════════════════════════════════════════════

AUTO_REPLY_PATTERNS = [
    r"thank\s*you\s+for\s+contacting",
    r"our\s+team\s+will\s+respond\s+shortly",
    r"we\s+will\s+get\s+back\s+to\s+you",
    r"your\s+(message|query)\s+(has\s+been|is)\s+received",
    r"auto[- ]?reply",
    r"out\s+of\s+office",
    r"currently\s+unavailable",
    r"we\s+are\s+closed",
    r"business\s+hours",
    r"will\s+revert\s+back",
    r"noted.*will.*respond",
]

def _is_auto_reply(message: str) -> bool:
    """Detect WhatsApp Business auto-replies."""
    msg_lower = message.lower().strip()
    for pattern in AUTO_REPLY_PATTERNS:
        if re.search(pattern, msg_lower):
            return True
    # Also detect if message is very short and generic
    if len(msg_lower) < 15 and msg_lower in ("ok", "okay", "👍", "thanks", "thank you", "noted"):
        return False  # These are real short replies, not auto-replies
    return False


# ═════════════════════════════════════════════════════════════════════════
# HOSTILE / OPT-OUT DETECTION
# ═════════════════════════════════════════════════════════════════════════

HOSTILE_PATTERNS = [
    r"stop\s+messag",
    r"stop\s+sending",
    r"don'?t\s+message",
    r"don'?t\s+send",
    r"don'?t\s+contact",
    r"not\s+interested",
    r"leave\s+me\s+alone",
    r"useless\s+spam",
    r"block",
    r"unsubscribe",
    r"remove\s+me",
    r"get\s+lost",
    r"shut\s+up",
    r"stop\s+it",
    r"enough",
    r"bakwas",
    r"band\s+karo",
    r"mat\s+bhejo",
]

def _is_hostile(message: str) -> bool:
    msg_lower = message.lower().strip()
    for pattern in HOSTILE_PATTERNS:
        if re.search(pattern, msg_lower):
            return True
    return False


# ═════════════════════════════════════════════════════════════════════════
# INTENT TRANSITION DETECTION
# ═════════════════════════════════════════════════════════════════════════

COMMITMENT_PATTERNS = [
    r"let'?s?\s+do\s+it",
    r"ok\s+let'?s\s+(go|do|start)",
    r"yes\s+let'?s",
    r"go\s+ahead",
    r"proceed",
    r"do\s+it",
    r"start\s+it",
    r"yes\s+(please|sure|ok|go)",
    r"haan\s+kar\s+do",
    r"kar\s+do",
    r"chalo",
    r"what'?s?\s+next",
    r"sounds?\s+good",
    r"i'?m?\s+in",
    r"sign\s+me\s+up",
    r"book\s+it",
    r"confirm",
    r"approved",
]

def _is_commitment(message: str) -> bool:
    msg_lower = message.lower().strip()
    for pattern in COMMITMENT_PATTERNS:
        if re.search(pattern, msg_lower):
            return True
    return False


# ═════════════════════════════════════════════════════════════════════════
# OFF-TOPIC DETECTION
# ═════════════════════════════════════════════════════════════════════════

OFF_TOPIC_KEYWORDS = [
    "gst", "tax", "income tax", "itr", "ca ", "chartered accountant",
    "loan", "emi", "insurance", "mutual fund",
    "personal", "family problem", "relationship",
    "astrology", "kundli", "vastu",
]

def _is_off_topic(message: str) -> bool:
    msg_lower = message.lower()
    return any(kw in msg_lower for kw in OFF_TOPIC_KEYWORDS)


# ═════════════════════════════════════════════════════════════════════════
# CONVERSATION TRACKER
# ═════════════════════════════════════════════════════════════════════════

class ConversationTracker:
    """Tracks per-conversation state for multi-turn handling."""

    def __init__(self):
        # conversation_id -> list of {from, message, turn}
        self._history: dict[str, list[dict]] = {}
        # conversation_id -> auto-reply count
        self._auto_reply_counts: dict[str, int] = {}

    def add_turn(self, conv_id: str, from_role: str, message: str, turn: int):
        self._history.setdefault(conv_id, []).append({
            "from": from_role,
            "message": message,
            "turn": turn,
        })

    def get_history(self, conv_id: str) -> list[dict]:
        return self._history.get(conv_id, [])

    def get_auto_reply_count(self, conv_id: str) -> int:
        return self._auto_reply_counts.get(conv_id, 0)

    def increment_auto_reply(self, conv_id: str) -> int:
        self._auto_reply_counts[conv_id] = self._auto_reply_counts.get(conv_id, 0) + 1
        return self._auto_reply_counts[conv_id]

    def get_last_bot_message(self, conv_id: str) -> Optional[str]:
        history = self._history.get(conv_id, [])
        for turn in reversed(history):
            if turn["from"] in ("vera", "bot"):
                return turn["message"]
        return None


# ═════════════════════════════════════════════════════════════════════════
# REPLY ENGINE
# ═════════════════════════════════════════════════════════════════════════

def handle_reply(
    conversation_id: str,
    merchant_id: str,
    customer_id: Optional[str],
    from_role: str,
    message: str,
    turn_number: int,
    store: ContextStore,
    tracker: ConversationTracker,
) -> dict:
    """
    Process an incoming reply and return the bot's response.

    Returns one of:
        {"action": "send", "body": ..., "cta": ..., "rationale": ...}
        {"action": "wait", "wait_seconds": ..., "rationale": ...}
        {"action": "end", "rationale": ...}
    """
    # Record the turn
    tracker.add_turn(conversation_id, from_role, message, turn_number)

    # ── 1. Auto-reply detection ──────────────────────────────────────
    if _is_auto_reply(message):
        count = tracker.increment_auto_reply(conversation_id)

        if count >= 3:
            store.suppressed_conversations.add(conversation_id)
            return {
                "action": "end",
                "rationale": f"Auto-reply {count}x in a row, no real reply. Conversation has zero engagement signal; closing.",
            }
        elif count >= 2:
            return {
                "action": "wait",
                "wait_seconds": 86400,
                "rationale": f"Same auto-reply {count}x in a row — owner not at phone. Wait 24h before retry.",
            }
        else:
            return {
                "action": "send",
                "body": "Looks like an auto-reply 😊 When the owner sees this, just reply 'Yes' to continue.",
                "cta": "binary_yes_no",
                "rationale": "Detected auto-reply; one explicit prompt to flag it for the owner.",
            }

    # ── 2. Hostile / opt-out detection ───────────────────────────────
    if _is_hostile(message):
        store.suppressed_conversations.add(conversation_id)
        store.suppressed_merchants.add(merchant_id)
        return {
            "action": "send",
            "body": "Apologies — I won't message again. If anything changes, you can always restart with 'Hi Vera'. 🙏",
            "cta": "none",
            "rationale": "Merchant frustration explicit. One-line acknowledgment + opt-out path; conversation will close after this send.",
        }

    # ── 3. Commitment / intent transition ────────────────────────────
    if _is_commitment(message):
        # Switch to ACTION mode — no more qualifying questions
        merchant = store.get_merchant(merchant_id) or {}
        owner = merchant.get("identity", {}).get("owner_first_name", "")
        active_offers = [o for o in merchant.get("offers", []) if o.get("status") == "active"]
        cust_agg = merchant.get("customer_aggregate", {})

        # Build action response based on available context
        body = "Done — setting it up now. "

        if active_offers:
            body += f"I'll activate your {active_offers[0].get('title', 'offer')} and draft the customer outreach. "

        members = cust_agg.get("total_active_members", cust_agg.get("total_unique_ytd"))
        if members:
            body += f"Targeting your {members} customers. "

        body += "Reply CONFIRM to send, or EDIT if you want changes first."

        return {
            "action": "send",
            "body": body,
            "cta": "binary_confirm_cancel",
            "rationale": "Merchant explicitly committed — switching from qualifying to action-execution. Concrete next step with measurable scope.",
        }

    # ── 4. Off-topic detection ───────────────────────────────────────
    if _is_off_topic(message):
        # Politely redirect
        last_bot = tracker.get_last_bot_message(conversation_id)
        redirect = ""
        if last_bot and len(last_bot) > 30:
            redirect = " Coming back to what we were discussing — "

        return {
            "action": "send",
            "body": f"I'll have to pass on that one — it's outside what I can help with directly.{redirect}anything else I can do for your business on magicpin?",
            "cta": "open_ended",
            "rationale": "Out-of-scope ask politely declined; redirecting back to business context.",
        }

    # ── 5. Positive engagement — continue conversation ───────────────
    msg_lower = message.lower().strip()

    # Short affirmative
    if msg_lower in ("yes", "yes please", "sure", "ok", "okay", "haan", "ha", "ji", "👍", "yes!"):
        merchant = store.get_merchant(merchant_id) or {}
        owner = merchant.get("identity", {}).get("owner_first_name", "")

        return {
            "action": "send",
            "body": f"Great — sending it over now. I'll also prepare a follow-up you can review. Give me 2 minutes.",
            "cta": "open_ended",
            "rationale": "Merchant said yes; delivering on the promise immediately + teasing follow-up value.",
        }

    # Question from merchant
    if "?" in message or any(w in msg_lower for w in ["how", "what", "when", "why", "which", "kaise", "kab", "kya"]):
        merchant = store.get_merchant(merchant_id) or {}
        cat_slug = merchant.get("category_slug", "")
        category = store.get_category(cat_slug) or {}

        return {
            "action": "send",
            "body": f"Good question — let me pull the relevant details for you. One moment.",
            "cta": "open_ended",
            "rationale": "Merchant asked a question; acknowledging and promising specific follow-up.",
        }

    # General engaged response
    return {
        "action": "send",
        "body": "Got it, noted. What would you like me to do next — draft something, check your metrics, or set up a campaign?",
        "cta": "open_ended",
        "rationale": "Acknowledged merchant's response; offering three clear next-action options.",
    }
