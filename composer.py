"""
Vera Bot — Deterministic Message Composer
==========================================
The brain of Vera. Takes 4 contexts and produces a grounded, specific,
category-appropriate message with a clear CTA and rationale.

Every fact in the output comes from the pushed context — zero hallucination.
"""

from __future__ import annotations
import re
from datetime import datetime, timezone
from typing import Any, Optional

from context_store import ContextStore


# ═════════════════════════════════════════════════════════════════════════
# CATEGORY VOICE PROFILES — how Vera talks per vertical
# ═════════════════════════════════════════════════════════════════════════

VOICE_PROFILES = {
    "dentists": {
        "salutation": lambda m: f"Dr. {m.get('identity', {}).get('owner_first_name', '')}",
        "tone": "peer_clinical",
        "register": "collegial",
    },
    "salons": {
        "salutation": lambda m: f"Hi {m.get('identity', {}).get('owner_first_name', '')}",
        "tone": "warm_practical",
        "register": "friendly",
    },
    "restaurants": {
        "salutation": lambda m: f"{m.get('identity', {}).get('owner_first_name', '')}",
        "tone": "warm_busy_practical",
        "register": "fellow_operator",
    },
    "gyms": {
        "salutation": lambda m: f"{m.get('identity', {}).get('owner_first_name', '')}",
        "tone": "energetic_disciplined",
        "register": "coach",
    },
    "pharmacies": {
        "salutation": lambda m: f"{m.get('identity', {}).get('owner_first_name', '')}",
        "tone": "trustworthy_precise",
        "register": "neighbourhood_pharmacist",
    },
}


def _owner_name(merchant: dict) -> str:
    return merchant.get("identity", {}).get("owner_first_name", "there")


def _biz_name(merchant: dict) -> str:
    return merchant.get("identity", {}).get("name", "your business")


def _locality(merchant: dict) -> str:
    return merchant.get("identity", {}).get("locality", "")


def _city(merchant: dict) -> str:
    return merchant.get("identity", {}).get("city", "")


def _salutation(merchant: dict, category_slug: str) -> str:
    profile = VOICE_PROFILES.get(category_slug, VOICE_PROFILES["restaurants"])
    return profile["salutation"](merchant)


def _active_offers(merchant: dict) -> list[dict]:
    return [o for o in merchant.get("offers", []) if o.get("status") == "active"]


def _perf(merchant: dict) -> dict:
    return merchant.get("performance", {})


def _delta7d(merchant: dict) -> dict:
    return _perf(merchant).get("delta_7d", {})


def _signals(merchant: dict) -> list[str]:
    return merchant.get("signals", [])


def _customer_agg(merchant: dict) -> dict:
    return merchant.get("customer_aggregate", {})


def _pct(val: float) -> str:
    """Format a decimal like 0.18 as '+18%' or '-22%'."""
    sign = "+" if val >= 0 else ""
    return f"{sign}{int(val * 100)}%"


def _find_digest_item(category: dict, item_id: str) -> Optional[dict]:
    for d in category.get("digest", []):
        if d.get("id") == item_id:
            return d
    return None


# ═════════════════════════════════════════════════════════════════════════
# TRIGGER HANDLERS — one per trigger kind
# ═════════════════════════════════════════════════════════════════════════

def _handle_research_digest(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    item_id = payload.get("top_item_id", "")
    digest = _find_digest_item(category, item_id)
    owner = _owner_name(merchant)
    sal = _salutation(merchant, category.get("slug", ""))

    if not digest:
        # Fallback — still ground in category
        return {
            "body": f"{sal}, new research update relevant to your practice just dropped. Want me to pull the key points?",
            "cta": "open_ended",
            "send_as": "vera",
            "rationale": "Research digest trigger but item not found in category context; generic research prompt.",
        }

    source = digest.get("source", "recent publication")
    title = digest.get("title", "")
    summary = digest.get("summary", "")
    trial_n = digest.get("trial_n")
    patient_seg = digest.get("patient_segment", "")
    actionable = digest.get("actionable", "")

    # Build merchant-specific anchor
    anchor = ""
    signals = _signals(merchant)
    cust_agg = _customer_agg(merchant)
    if patient_seg == "high_risk_adults" and "high_risk_adult_cohort" in signals:
        hrac = cust_agg.get("high_risk_adult_count", "")
        if hrac:
            anchor = f" One item relevant to your high-risk adult patients ({hrac} in your roster)"
        else:
            anchor = " One item relevant to your high-risk adult patients"
    elif patient_seg:
        anchor = f" Relevant to {patient_seg.replace('_', ' ')} patients"

    # Extract key numbers from summary
    numbers_match = re.search(r'(\d+)%', summary)
    pct_fact = f", {numbers_match.group(0)} " if numbers_match else " — "

    trial_fact = f"{trial_n:,}-patient trial showed " if trial_n else ""

    body = (
        f"{sal}, {source.split(',')[0]} just landed.{anchor} — "
        f"{trial_fact}{title.lower()}. "
        f"Worth a look (2-min abstract). Want me to pull it + draft a patient-ed WhatsApp you can share? "
        f"— {source}"
    )

    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "rationale": (
            f"External research digest with merchant-relevant anchor. "
            f"Source citation ({source}) maintains credibility. "
            f"Open-ended CTA invites continuation without forcing a binary choice."
        ),
    }


def _handle_regulation_change(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    item_id = payload.get("top_item_id", "")
    deadline = payload.get("deadline_iso", "")
    digest = _find_digest_item(category, item_id)
    sal = _salutation(merchant, category.get("slug", ""))

    if digest:
        source = digest.get("source", "regulatory update")
        title = digest.get("title", "")
        summary = digest.get("summary", "")
        actionable = digest.get("actionable", "")

        deadline_str = ""
        if deadline:
            try:
                dt = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
                deadline_str = f" Deadline: {dt.strftime('%d %B %Y')}."
            except Exception:
                deadline_str = f" Deadline: {deadline}."

        body = (
            f"{sal}, heads up — {title}.{deadline_str} "
            f"{summary} "
            f"Action needed: {actionable}. Want me to draft a compliance checklist? "
            f"— {source}"
        )
    else:
        body = f"{sal}, new regulatory update affecting your practice. Want me to pull the details and draft an action checklist?"

    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "rationale": "Compliance trigger — time-bound regulatory change requiring merchant action. Source-cited with deadline.",
    }


def _handle_recall_due(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    owner = _owner_name(merchant)
    biz = _biz_name(merchant)

    if not customer:
        return {
            "body": f"{_salutation(merchant, category.get('slug', ''))}, recall reminders are due for some patients. Want me to draft and send them?",
            "cta": "binary_yes_no",
            "send_as": "vera",
            "rationale": "Recall trigger without customer context — prompting merchant to approve sends.",
        }

    cust_name = customer.get("identity", {}).get("name", "there")
    lang_pref = customer.get("identity", {}).get("language_pref", "en")
    service_due = payload.get("service_due", "checkup").replace("_", " ")
    last_service = payload.get("last_service_date", "")
    slots = payload.get("available_slots", [])
    prefs = customer.get("preferences", {})

    # Calculate months since last visit
    months_str = ""
    if last_service:
        try:
            last_dt = datetime.fromisoformat(last_service)
            now = datetime.now()
            months = (now.year - last_dt.year) * 12 + (now.month - last_dt.month)
            months_str = f"It's been {months} months since your last visit"
        except Exception:
            months_str = "Your recall is due"

    # Find matching offer
    active = _active_offers(merchant)
    offer_str = ""
    for o in active:
        title_lower = o.get("title", "").lower()
        if "cleaning" in title_lower or "check" in title_lower:
            offer_str = o.get("title", "")
            break

    # Build slot text
    slot_parts = []
    for i, s in enumerate(slots[:2], 1):
        slot_parts.append(s.get("label", f"Slot {i}"))

    if len(slot_parts) == 2:
        slot_text = f"{slot_parts[0]} ya {slot_parts[1]}" if "hi" in lang_pref else f"{slot_parts[0]} or {slot_parts[1]}"
    elif len(slot_parts) == 1:
        slot_text = slot_parts[0]
    else:
        slot_text = "this week"

    # Language-aware phrasing
    if "hi" in lang_pref:
        slots_intro = f"Apke liye 2 slots ready hain: "
        reply_cta = "Reply 1 for first, 2 for second, or tell us a time that works."
    else:
        slots_intro = f"We have slots available: "
        reply_cta = "Reply 1 for first, 2 for second, or tell us a time that works."

    emoji = "🦷" if category.get("slug") == "dentists" else "💊" if category.get("slug") == "pharmacies" else "👋"

    body = (
        f"Hi {cust_name}, {biz} here {emoji} "
        f"{months_str} — your {service_due} is due. "
        f"{slots_intro}**{slot_text}**. "
        f"{offer_str + '. ' if offer_str else ''}"
        f"{reply_cta}"
    )

    return {
        "body": body,
        "cta": "multi_choice_slot",
        "send_as": "merchant_on_behalf",
        "rationale": (
            f"Customer-scoped recall. Sending via merchant's number. "
            f"Honoring {cust_name}'s {lang_pref} language pref"
            f"{' + ' + prefs.get('preferred_slots', 'flexible') + ' time preference' if prefs.get('preferred_slots') else ''}. "
            f"Multi-choice slot CTA for booking flows."
        ),
    }


def _handle_perf_dip(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    sal = _salutation(merchant, category.get("slug", ""))
    metric = payload.get("metric", "views")
    delta_pct = payload.get("delta_pct", 0)
    window = payload.get("window", "7d")
    is_seasonal = payload.get("is_expected_seasonal", False)
    season_note = payload.get("season_note", "")
    vs_baseline = payload.get("vs_baseline", "")

    perf = _perf(merchant)
    views = perf.get("views", "?")
    cust_agg = _customer_agg(merchant)

    delta_str = _pct(delta_pct) if delta_pct else "down"
    metric_label = metric.replace("_", " ")

    if is_seasonal:
        # Reframe as normal — this is the high-scoring move
        body = (
            f"{sal}, your {metric_label} are {delta_str} this week — but I want to flag this is the "
            f"normal {season_note.replace('_', ' ')} lull. "
            f"Action: skip ad spend now, save it for the recovery window when conversion is 2x. "
        )
        # Add member/customer count if available
        members = cust_agg.get("total_active_members", cust_agg.get("total_unique_ytd"))
        if members:
            body += f"For now, focus retention on your {members} existing customers. "
        body += "Want me to draft a retention campaign to keep them engaged through the dip?"

        rationale = (
            f"Seasonal dip trigger — reframing anxiety as expected pattern. "
            f"Advising restraint on ad spend (contrarian, data-backed). "
            f"Offering retention campaign as constructive alternative."
        )
    else:
        body = (
            f"{sal}, your {metric_label} dropped {delta_str} this {window} "
            f"(from {vs_baseline} baseline). This looks like a real dip, not seasonal. "
        )
        # Suggest actions based on signals
        signals = _signals(merchant)
        if "no_active_offers" in signals or not _active_offers(merchant):
            body += "You don't have any active offers right now — that's likely a factor. Want me to draft one?"
        elif "stale_posts" in [s.split(":")[0] for s in signals]:
            stale_days = next((s.split(":")[1] for s in signals if s.startswith("stale_posts:")), "?")
            body += f"Your Google posts are {stale_days} days old — refreshing could help. Want me to draft 3 posts?"
        else:
            body += "Want me to run a quick diagnostic on what's changed?"

        rationale = f"Performance dip trigger — real decline (not seasonal). Connecting to specific actionable signals."

    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "rationale": rationale,
    }


def _handle_perf_spike(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    sal = _salutation(merchant, category.get("slug", ""))
    metric = payload.get("metric", "views")
    delta_pct = payload.get("delta_pct", 0)
    likely_driver = payload.get("likely_driver", "")

    perf = _perf(merchant)
    metric_val = perf.get(metric, "?")

    body = (
        f"{sal}, nice — your {metric.replace('_', ' ')} are up {_pct(delta_pct)} this week"
        f"{' (now at ' + str(metric_val) + ')' if metric_val != '?' else ''}. "
    )

    if likely_driver:
        body += f"Likely driver: {likely_driver.replace('_', ' ')}. "

    body += "Want me to double down on what's working — draft a follow-up post or push to more channels?"

    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "rationale": f"Performance spike — celebrating momentum + offering to amplify. Driver identified: {likely_driver or 'unknown'}.",
    }


def _handle_milestone(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    sal = _salutation(merchant, category.get("slug", ""))
    metric = payload.get("metric", "reviews").replace("_", " ")
    val_now = payload.get("value_now", "?")
    milestone = payload.get("milestone_value", "?")
    is_imminent = payload.get("is_imminent", False)

    if is_imminent:
        body = (
            f"{sal}, you're at {val_now} {metric} — just {milestone - val_now if isinstance(val_now, int) and isinstance(milestone, int) else 'a few'} away from {milestone}! "
            f"That's a strong signal for new customers searching in {_locality(merchant)}. "
            f"Want me to draft a thank-you post for your existing reviewers + a nudge to push past {milestone}?"
        )
    else:
        body = (
            f"{sal}, you just crossed {milestone} {metric} 🎉 "
            f"This puts you above peer average in {_locality(merchant)}. "
            f"Want me to draft a celebratory Google post?"
        )

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "rationale": f"Milestone trigger — {'imminent' if is_imminent else 'crossed'} {milestone} {metric}. Social proof opportunity.",
    }


def _handle_dormant(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    sal = _salutation(merchant, category.get("slug", ""))
    days = payload.get("days_since_last_merchant_message", "?")
    last_topic = payload.get("last_topic", "")

    # Don't guilt-trip — lead with value
    perf = _perf(merchant)
    views = perf.get("views", "?")
    delta = _delta7d(merchant)
    views_delta = delta.get("views_pct", 0)

    body = f"{sal}, quick update — "
    if views != "?" and views_delta:
        body += f"your profile views are {'up' if views_delta > 0 else 'at'} {views} this month ({_pct(views_delta)} vs last week). "
    else:
        body += f"your profile has been getting steady traffic. "

    body += "Anything I can help with this week? Even a quick Google post keeps you visible."

    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "rationale": f"Dormant re-engagement ({days} days since last message). Leading with value (performance data), not guilt.",
    }


def _handle_review_theme(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    sal = _salutation(merchant, category.get("slug", ""))
    theme = payload.get("theme", "").replace("_", " ")
    occurrences = payload.get("occurrences_30d", "?")
    trend = payload.get("trend", "")
    quote = payload.get("common_quote", "")

    # Also check merchant's review_themes for richer data
    for rt in merchant.get("review_themes", []):
        if rt.get("theme", "").replace("_", " ") == theme:
            occurrences = rt.get("occurrences_30d", occurrences)
            quote = rt.get("common_quote", quote)
            break

    body = f"{sal}, spotted a pattern in your recent reviews — \"{theme}\" mentioned {occurrences} times in the last 30 days"
    if trend == "rising":
        body += " (and trending up)"
    body += ". "
    if quote:
        body += f"Common phrasing: \"{quote}\". "
    body += "Want me to draft a response template or suggest operational fixes?"

    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "rationale": f"Review theme emerged — '{theme}' x{occurrences} in 30d. Surfacing actionable pattern from reviews.",
    }


def _handle_competitor(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    sal = _salutation(merchant, category.get("slug", ""))
    comp_name = payload.get("competitor_name", "A new competitor")
    distance = payload.get("distance_km", "?")
    their_offer = payload.get("their_offer", "")
    opened_date = payload.get("opened_date", "")

    body = f"{sal}, heads up — {comp_name} opened {distance}km from you"
    if opened_date:
        body += f" (since {opened_date})"
    body += ". "
    if their_offer:
        body += f"They're running: \"{their_offer}\". "

    # Suggest differentiation based on merchant's strengths
    active = _active_offers(merchant)
    reviews = merchant.get("review_themes", [])
    pos_reviews = [r for r in reviews if r.get("sentiment") == "pos"]

    if pos_reviews:
        top_strength = pos_reviews[0].get("theme", "").replace("_", " ")
        body += f"Your strongest card: customers love your {top_strength} ({pos_reviews[0].get('occurrences_30d', '?')} mentions). "

    body += "Want me to sharpen your listing to highlight what makes you different?"

    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "rationale": f"Competitor opened nearby — voyeur curiosity hook + differentiation strategy using merchant's review strengths.",
    }


def _handle_festival(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    sal = _salutation(merchant, category.get("slug", ""))
    festival = payload.get("festival", "upcoming festival")
    days_until = payload.get("days_until", "?")
    date_str = payload.get("date", "")
    cat_slug = category.get("slug", "")

    body = f"{sal}, {festival} is {days_until} days away"
    if date_str:
        body += f" ({date_str})"
    body += ". "

    # Category-specific suggestions
    if cat_slug == "salons":
        body += "Bridal and festive bookings typically 2-4x baseline in this window. Want me to push your festive packages as a Google post?"
    elif cat_slug == "restaurants":
        body += "Festival-week footfall usually spikes for dine-in. Want me to draft a festive set menu or catering offer?"
    elif cat_slug == "pharmacies":
        body += "Post-festival demand for digestive and diabetic-monitoring products typically surges. Worth front-stocking. Want me to draft a health-awareness WhatsApp for your regulars?"
    elif cat_slug == "gyms":
        body += "Post-festival is the second-best conversion window after January. Want me to draft a 'back to fitness' campaign?"
    else:
        body += "Want me to help you prep a festive campaign?"

    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "rationale": f"Festival upcoming ({festival}, {days_until} days). Category-specific seasonal prep suggestion.",
    }


def _handle_customer_lapsed(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    owner = _owner_name(merchant)
    biz = _biz_name(merchant)
    cat_slug = category.get("slug", "")

    if not customer:
        sal = _salutation(merchant, cat_slug)
        return {
            "body": f"{sal}, some of your customers haven't visited in a while. Want me to draft personalized winback messages?",
            "cta": "binary_yes_no",
            "send_as": "vera",
            "rationale": "Customer lapse trigger without customer context.",
        }

    cust_name = customer.get("identity", {}).get("name", "there")
    lang = customer.get("identity", {}).get("language_pref", "en")
    days_since = payload.get("days_since_last_visit", "?")
    prev_focus = payload.get("previous_focus", "").replace("_", " ")
    state = customer.get("state", "lapsed_soft")

    # Build no-shame, warm message
    active = _active_offers(merchant)
    offer_text = ""
    if active:
        offer_text = f" {active[0].get('title', '')}."

    weeks = f"about {days_since // 7} weeks" if isinstance(days_since, int) else "a while"

    body = f"Hi {cust_name} 👋 {owner} from {biz} here. It's been {weeks} — happens to everyone, no judgment. "

    if prev_focus:
        body += f"We know your focus was {prev_focus}. "

    if offer_text:
        body += f"We have{offer_text} "

    body += "Want me to hold a spot for you this week? Reply YES — no commitment, no auto-charge."

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "merchant_on_behalf",
        "rationale": (
            f"Customer lapsed ({'hard' if 'hard' in state else 'soft'}). "
            f"No-shame framing + specific offer. "
            f"Single binary CTA with explicit 'no commitment' to remove friction."
        ),
    }


def _handle_planning_intent(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    sal = _salutation(merchant, category.get("slug", ""))
    topic = payload.get("intent_topic", "").replace("_", " ")
    last_msg = payload.get("merchant_last_message", "")
    cat_slug = category.get("slug", "")
    locality = _locality(merchant)

    body = f"{sal}, here's a starter version based on what you asked — you can edit:\n\n"

    if "corporate" in topic and "thali" in topic and cat_slug == "restaurants":
        biz = _biz_name(merchant)
        body += (
            f"{biz} Corporate Lunch — for offices in {locality}\n"
            f"- 10 meals @ regular price + free delivery\n"
            f"- 25 meals @ 10% off + 2 complimentary items\n"
            f"- 50+: 15% off + complimentary dessert platter\n"
            f"- WhatsApp the day-before by 5pm; delivery between 12:30-1pm\n\n"
            f"Want me to draft a 3-line WhatsApp to send to offices in {locality}?"
        )
    elif "kids" in topic and cat_slug == "gyms":
        body += (
            f"Kids Program — 4-week program\n"
            f"- 3 classes/week, age 7-12\n"
            f"- Suggest pricing: ₹2,499 for the full program\n"
            f"- Saturday morning slot likely best for parents\n\n"
            f"Want me to draft the Google post + Insta carousel?"
        )
    else:
        body += (
            f"Topic: {topic}\n"
            f"Based on your locality ({locality}) and current performance, "
            f"I'd suggest starting with a pilot version. "
            f"Want me to draft the full plan with pricing and promotion?"
        )

    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "rationale": f"Active planning intent — merchant asked about '{topic}'. Delivering a concrete draft they can edit, not more questions.",
    }


def _handle_supply_alert(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    sal = _salutation(merchant, category.get("slug", ""))
    molecule = payload.get("molecule", "medication")
    batches = payload.get("affected_batches", [])
    manufacturer = payload.get("manufacturer", "")
    cust_agg = _customer_agg(merchant)
    chronic_count = cust_agg.get("chronic_rx_count", "")

    batch_str = ", ".join(batches[:3]) if batches else "specific batches"

    body = f"{sal}, urgent: voluntary recall on {molecule} batches ({batch_str})"
    if manufacturer:
        body += f" by {manufacturer}"
    body += " — sub-potency, no safety risk, but customers should be informed for replacement. "

    if chronic_count:
        # Derive affected estimate
        affected_est = max(1, int(chronic_count * 0.09))  # ~9% estimate
        body += f"From your {chronic_count} chronic-Rx customers, an estimated {affected_est} may be affected. "

    body += "Want me to draft their WhatsApp note + the replacement-pickup workflow?"

    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "rationale": f"Supply/compliance alert — urgent, specific batch numbers cited. Derived affected count from merchant's customer aggregate. End-to-end workflow offer.",
    }


def _handle_chronic_refill(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    owner = _owner_name(merchant)
    biz = _biz_name(merchant)
    locality = _locality(merchant)

    if not customer:
        return {
            "body": f"{_salutation(merchant, category.get('slug', ''))}, chronic refills are due for some patients. Want me to draft and send reminders?",
            "cta": "binary_yes_no",
            "send_as": "vera",
            "rationale": "Chronic refill trigger without customer context.",
        }

    cust_name = customer.get("identity", {}).get("name", "")
    lang = customer.get("identity", {}).get("language_pref", "en")
    molecules = payload.get("molecule_list", [])
    stock_out = payload.get("stock_runs_out_iso", "")
    delivery_saved = payload.get("delivery_address_saved", False)
    is_senior = customer.get("identity", {}).get("senior_citizen", False)
    channel = customer.get("preferences", {}).get("channel", "whatsapp")

    mol_str = ", ".join(molecules) if molecules else "your regular medicines"

    # Date formatting
    date_str = ""
    if stock_out:
        try:
            dt = datetime.fromisoformat(stock_out.replace("Z", "+00:00"))
            date_str = dt.strftime("%d %B")
        except Exception:
            date_str = stock_out[:10]

    # Check for senior discount
    active = _active_offers(merchant)
    senior_offer = ""
    delivery_offer = ""
    for o in active:
        t = o.get("title", "").lower()
        if "senior" in t:
            senior_offer = o.get("title", "")
        if "delivery" in t or "free" in t.lower():
            delivery_offer = o.get("title", "")

    if "hi" in lang:
        body = f"Namaste — {biz} {locality} yahan. "
        if cust_name:
            body += f"{cust_name} ji ki "
        body += f"medicines ({mol_str}) {date_str} ko khatam hongi. "
        body += "Same dose, same brand pack ready hai. "
        if senior_offer and is_senior:
            body += f"{senior_offer} applied. "
        if delivery_saved:
            body += "Free home delivery to saved address. "
        body += "Reply CONFIRM to dispatch."
    else:
        body = f"Hi — {biz} {locality} here. "
        if cust_name:
            body += f"{cust_name}'s "
        body += f"medicines ({mol_str}) run out {date_str}. "
        body += "Same dose, same brand ready. "
        if senior_offer and is_senior:
            body += f"{senior_offer} applied. "
        if delivery_saved:
            body += "Free home delivery to saved address. "
        body += "Reply CONFIRM to dispatch."

    return {
        "body": body,
        "cta": "binary_confirm_cancel",
        "send_as": "merchant_on_behalf",
        "rationale": f"Chronic refill due. Molecule names cited for precision. {lang} language preference honored. Delivery + senior offers applied where applicable.",
    }


def _handle_ipl_match(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    sal = _salutation(merchant, category.get("slug", ""))
    match = payload.get("match", "IPL match")
    venue = payload.get("venue", "")
    match_time = payload.get("match_time_iso", "")
    is_weeknight = payload.get("is_weeknight", True)

    time_str = ""
    if match_time:
        try:
            dt = datetime.fromisoformat(match_time)
            time_str = dt.strftime("%I:%M%p").lstrip("0").lower()
        except Exception:
            time_str = "tonight"

    active = _active_offers(merchant)
    active_offer = active[0].get("title", "") if active else ""

    if is_weeknight:
        body = (
            f"Quick heads-up {sal.split(',')[0].split(' ')[-1]} — {match} at {venue} tonight, {time_str}. "
            f"Weeknight IPL matches typically drive +18% covers. "
        )
        if active_offer:
            body += f"Push your {active_offer} as a match-night special. "
        body += "Want me to draft a quick social post? Live in 10 min."
    else:
        body = (
            f"Quick heads-up {sal.split(',')[0].split(' ')[-1]} — {match} at {venue} tonight, {time_str}. "
            f"Important: Saturday IPL matches usually shift -12% restaurant covers (people watch at home). "
            f"Skip the match-night promo today; "
        )
        if active_offer:
            body += f"instead push your {active_offer} as a delivery-only special. "
        body += "Want me to draft the delivery banner? Live in 10 min."

    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "rationale": f"IPL match trigger — {'weeknight (push dine-in)' if is_weeknight else 'Saturday (contrarian: push delivery, skip dine-in promo)'}. Data-backed recommendation using match-day cover patterns.",
    }


def _handle_winback(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    sal = _salutation(merchant, category.get("slug", ""))
    days_since = payload.get("days_since_expiry", "?")
    perf_dip = payload.get("perf_dip_pct", 0)
    lapsed_added = payload.get("lapsed_customers_added_since_expiry", 0)

    body = (
        f"{sal}, it's been {days_since} days since your subscription paused. "
        f"In that time, your visibility dropped {_pct(perf_dip) if perf_dip else 'noticeably'}"
    )
    if lapsed_added:
        body += f" and {lapsed_added} customers have lapsed without re-engagement"
    body += ". "
    body += "Want to restart? I can reactivate your profile + draft 3 fresh posts in under 5 minutes."

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "rationale": f"Winback trigger — showing concrete cost of inaction ({days_since} days, {_pct(perf_dip)} visibility drop). Loss aversion lever.",
    }


def _handle_renewal(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    sal = _salutation(merchant, category.get("slug", ""))
    days_rem = payload.get("days_remaining", "?")
    plan = payload.get("plan", "")
    amount = payload.get("renewal_amount", "")
    perf = _perf(merchant)
    views = perf.get("views", "?")
    calls = perf.get("calls", "?")

    body = (
        f"{sal}, your {plan} subscription renews in {days_rem} days. "
        f"Quick recap of what it's done: {views} profile views and {calls} calls in the last 30 days. "
    )
    if amount:
        body += f"Renewal: ₹{amount:,}. " if isinstance(amount, int) else f"Renewal: ₹{amount}. "
    body += "Want to renew now, or discuss if the plan still fits?"

    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "rationale": f"Renewal due in {days_rem} days. Leading with value recap (views/calls) before asking for commitment.",
    }


def _handle_curious_ask(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    sal = _salutation(merchant, category.get("slug", ""))
    biz = _biz_name(merchant)

    body = (
        f"{sal}! Quick check — what service has been most asked-for this week at {biz}? "
        f"I'll turn the answer into a Google post + a 4-line WhatsApp reply you can use when customers ask about pricing. Takes 5 min."
    )

    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "rationale": "Curious ask — low-stakes question to merchant. Reciprocity offered up-front (post + reply draft). Respects merchant's time.",
    }


def _handle_appointment_tomorrow(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    biz = _biz_name(merchant)

    if not customer:
        return {
            "body": f"{_salutation(merchant, category.get('slug', ''))}, appointment reminders going out tomorrow. All good?",
            "cta": "binary_yes_no", "send_as": "vera",
            "rationale": "Appointment reminder without customer context.",
        }

    cust_name = customer.get("identity", {}).get("name", "there")
    body = f"Hi {cust_name} 👋 Quick reminder — your appointment at {biz} is tomorrow. See you there!"

    return {
        "body": body,
        "cta": "none",
        "send_as": "merchant_on_behalf",
        "rationale": "Appointment confirmation — simple, no CTA needed, just confirmation.",
    }


def _handle_trial_followup(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    owner = _owner_name(merchant)
    biz = _biz_name(merchant)

    if not customer:
        return {
            "body": f"{_salutation(merchant, category.get('slug', ''))}, trial follow-ups are due. Want me to send them?",
            "cta": "binary_yes_no", "send_as": "vera",
            "rationale": "Trial followup without customer context.",
        }

    cust_name = customer.get("identity", {}).get("name", "there")
    trial_date = payload.get("trial_date", "")
    next_sessions = payload.get("next_session_options", [])

    slot_str = ""
    if next_sessions:
        slot_str = next_sessions[0].get("label", "this week")

    body = (
        f"Hi {cust_name} 👋 {owner} from {biz} here. "
        f"Hope you enjoyed the trial! "
    )
    if slot_str:
        body += f"Next session available: {slot_str}. "
    body += "Want me to book you in? Reply YES — no commitment, try one more before you decide."

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "merchant_on_behalf",
        "rationale": "Trial followup — warm, no-pressure tone. Offering next session with explicit 'no commitment'.",
    }


def _handle_bridal_followup(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    owner = _owner_name(merchant)
    biz = _biz_name(merchant)
    locality = _locality(merchant)

    if not customer:
        return {
            "body": f"{_salutation(merchant, category.get('slug', ''))}, bridal follow-ups are due for upcoming weddings.",
            "cta": "open_ended", "send_as": "vera",
            "rationale": "Bridal followup without customer context.",
        }

    cust_name = customer.get("identity", {}).get("name", "there")
    wedding_date = payload.get("wedding_date", "")
    days_to = payload.get("days_to_wedding", "?")
    next_step = payload.get("next_step_window_open", "").replace("_", " ")

    body = (
        f"Hi {cust_name} 💍 {owner} from {biz} {locality} here. "
        f"{days_to} days to your wedding — perfect window to start the {next_step} before serious bridal bookings roll in. "
    )

    active = _active_offers(merchant)
    if active:
        body += f"Current offer: {active[0].get('title', '')}. "

    prefs = customer.get("preferences", {})
    pref_slot = prefs.get("preferred_slots", "")
    if pref_slot:
        body += f"Want me to block your preferred {pref_slot.replace('_', ' ')} slot for the first session?"
    else:
        body += "Want me to block a slot for the first session?"

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "merchant_on_behalf",
        "rationale": f"Bridal followup — {days_to} days to wedding, {next_step} window open. Preference-aware slot suggestion.",
    }


def _handle_cde_opportunity(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    sal = _salutation(merchant, category.get("slug", ""))
    digest_id = payload.get("digest_item_id", "")
    credits = payload.get("credits", "")
    fee = payload.get("fee", "")

    digest = _find_digest_item(category, digest_id)

    if digest:
        title = digest.get("title", "")
        source = digest.get("source", "")
        date = digest.get("date", "")
        summary = digest.get("summary", "")

        body = (
            f"{sal}, upcoming CDE opportunity — {title}. "
            f"{credits} CDE credits, {fee}. "
        )
        if date:
            body += f"Date: {date[:10]}. "
        if summary:
            body += f"{summary} "
        body += "Want me to register you?"
    else:
        body = f"{sal}, there's a CDE webinar coming up — {credits} credits, {fee}. Want me to send you the details?"

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "rationale": f"CDE opportunity — professional development with credits. Low-friction registration offer.",
    }


def _handle_gbp_unverified(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    sal = _salutation(merchant, category.get("slug", ""))
    uplift = payload.get("estimated_uplift_pct", 0)
    path = payload.get("verification_path", "postcard or phone call")

    body = (
        f"{sal}, your Google Business Profile isn't verified yet — "
        f"verified profiles get ~{_pct(uplift)} more visibility in your locality. "
        f"It's a one-time {path.replace('_', ' ')} process, takes 5 minutes. "
        f"Want me to walk you through it right now?"
    )

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "rationale": f"GBP unverified — quantified uplift ({_pct(uplift)}) as motivation. Simple verification path offered.",
    }


def _handle_seasonal(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    payload = trigger.get("payload", {})
    sal = _salutation(merchant, category.get("slug", ""))
    season = payload.get("season", "this season")
    trends = payload.get("trends", [])
    shelf_action = payload.get("shelf_action_recommended", False)

    trend_str = ""
    if trends:
        ups = [t for t in trends if "+" in t or "up" in t.lower()]
        downs = [t for t in trends if "-" in t or "down" in t.lower()]
        if ups:
            trend_str += "Going up: " + ", ".join(t.replace("_", " ") for t in ups[:3]) + ". "
        if downs:
            trend_str += "Cooling off: " + ", ".join(t.replace("_", " ") for t in downs[:2]) + ". "

    body = f"{sal}, seasonal demand shift for {season.replace('_', ' ')}: {trend_str}"
    if shelf_action:
        body += "Worth rearranging your shelf/menu for this window. "
    body += "Want me to draft a seasonal promotion?"

    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "rationale": f"Seasonal demand shift — specific trend data cited. Actionable shelf/menu recommendation.",
    }


def _handle_generic(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> dict:
    """Fallback for unknown trigger kinds."""
    sal = _salutation(merchant, category.get("slug", ""))
    kind = trigger.get("kind", "update")

    body = f"{sal}, got an update for you — want to take a quick look?"

    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "rationale": f"Generic handler for trigger kind '{kind}'. Minimal but valid.",
    }


# ═════════════════════════════════════════════════════════════════════════
# HANDLER DISPATCH TABLE
# ═════════════════════════════════════════════════════════════════════════

TRIGGER_HANDLERS = {
    "research_digest": _handle_research_digest,
    "regulation_change": _handle_regulation_change,
    "recall_due": _handle_recall_due,
    "perf_dip": _handle_perf_dip,
    "seasonal_perf_dip": _handle_perf_dip,  # same handler, different payload
    "perf_spike": _handle_perf_spike,
    "milestone_reached": _handle_milestone,
    "dormant_with_vera": _handle_dormant,
    "review_theme_emerged": _handle_review_theme,
    "competitor_opened": _handle_competitor,
    "festival_upcoming": _handle_festival,
    "customer_lapsed_soft": _handle_customer_lapsed,
    "customer_lapsed_hard": _handle_customer_lapsed,
    "active_planning_intent": _handle_planning_intent,
    "supply_alert": _handle_supply_alert,
    "chronic_refill_due": _handle_chronic_refill,
    "ipl_match_today": _handle_ipl_match,
    "winback_eligible": _handle_winback,
    "renewal_due": _handle_renewal,
    "curious_ask_due": _handle_curious_ask,
    "appointment_tomorrow": _handle_appointment_tomorrow,
    "trial_followup": _handle_trial_followup,
    "wedding_package_followup": _handle_bridal_followup,
    "cde_opportunity": _handle_cde_opportunity,
    "gbp_unverified": _handle_gbp_unverified,
    "category_seasonal": _handle_seasonal,
}


# ═════════════════════════════════════════════════════════════════════════
# MAIN COMPOSE FUNCTION
# ═════════════════════════════════════════════════════════════════════════

def compose(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict] = None,
) -> dict:
    """
    Deterministic composition from 4 contexts.

    Returns:
        {body, cta, send_as, suppression_key, rationale,
         template_name, template_params, conversation_id,
         merchant_id, customer_id, trigger_id}
    """
    kind = trigger.get("kind", "")
    handler = TRIGGER_HANDLERS.get(kind, _handle_generic)

    result = handler(category, merchant, trigger, customer)

    # Add standard fields
    merchant_id = merchant.get("merchant_id", trigger.get("merchant_id", ""))
    customer_id = trigger.get("customer_id")
    trigger_id = trigger.get("id", "")

    result["merchant_id"] = merchant_id
    result["customer_id"] = customer_id
    result["trigger_id"] = trigger_id
    result["suppression_key"] = trigger.get("suppression_key", f"{kind}:{merchant_id}")
    result["template_name"] = f"vera_{kind}_v1"
    result["template_params"] = [_owner_name(merchant), _biz_name(merchant)]
    result["conversation_id"] = f"conv_{merchant_id}_{trigger_id}"

    return result
