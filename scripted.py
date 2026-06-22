"""
Deterministic fallback intake flow.

Used automatically when the live model is unavailable (no API key, or a network
failure mid-demo). It is intentionally simple: keyword intent detection, a short
fixed set of qualifying questions per team, then it files the ticket through the
same Router the live agent uses. The user experience degrades gracefully instead
of breaking on stage.

State is kept per conversation key (the Slack thread, or a fixed CLI session id),
so multiple threads don't collide.
"""

from __future__ import annotations

import re
from backends import Router, TEAM_TOOL_LABEL

# team -> ordered (slot_key, question) qualifying questions
QUESTIONS = {
    "it": [
        ("subtype", "Got it, that's an IT request. Is this a replacement, a new-hire setup, or a brand-new need?"),
        ("needed_by", "When do you need it by?"),
        ("specifics", "Any specifics I should pass along (model/spec preference, or the exact issue)?"),
    ],
    "legal": [
        ("counterparty", "Sounds like one for Legal. Who's the counterparty (the other company or person)?"),
        ("mutual", "Is it one-way or mutual?"),
        ("needed_by", "Any deadline for turnaround or signature?"),
    ],
    "marketing": [
        ("goal", "That's a Marketing request. What's the goal and who's the target audience?"),
        ("channels", "Which channels are in play (email, social, web, event)?"),
        ("launch", "When does it need to launch?"),
    ],
    "product": [
        ("problem", "That's one for Product. What problem are you trying to solve, and who's affected?"),
        ("urgency", "How urgent is it, and what's driving the timing?"),
    ],
}

KEYWORDS = {
    "it": ["laptop", "computer", "monitor", "mouse", "keyboard", "hardware",
           "software", "license", "access", "password", "vpn", "account",
           "login", "install", "wifi", "printer", "equipment", "device", "email setup"],
    "legal": ["nda", "contract", "agreement", "legal", "compliance", "terms",
              "vendor", "trademark", " ip ", "counsel", "sign", "msa", "dpa"],
    "marketing": ["campaign", "content", "blog", "social", "ad ", "ads",
                  "branding", "design", "event", "swag", "webinar", "landing",
                  "copy", "newsletter", "launch a"],
    "product": ["feature", "roadmap", "analytics", "data pull", "dashboard",
                "report", "integration", "product feedback", "metric"],
}

URGENT_WORDS = ["today", "tomorrow", "asap", "urgent", "blocked", "this week",
                "eod", "end of day", "immediately", "deadline"]
LOW_WORDS = ["no rush", "whenever", "someday", "nice to have", "eventually", "low priority"]

TICKET_RE = re.compile(r"\b([A-Z]{2,3}-\d{8}-\d{4})\b", re.I)


def _classify(text: str) -> str | None:
    t = f" {text.lower()} "
    scores = {team: sum(1 for kw in kws if kw in t) for team, kws in KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None


def _priority_from(text: str) -> str:
    t = text.lower()
    if any(w in t for w in URGENT_WORDS):
        return "High"
    if any(w in t for w in LOW_WORDS):
        return "Low"
    return "Medium"


class ScriptedAgent:
    def __init__(self, router: Router, on_ticket=None):
        self.router = router
        self.on_ticket = on_ticket
        self._sessions: dict[str, dict] = {}

    def _key(self, source, requester_id):
        return source or requester_id or "default"

    @staticmethod
    def _last_user_text(history) -> str:
        for msg in reversed(history):
            if msg.get("role") == "user":
                c = msg.get("content")
                if isinstance(c, str):
                    return c
        return ""

    def reply(self, history, requester, requester_id="", source="") -> str:
        key = self._key(source, requester_id)
        s = self._sessions.setdefault(
            key, {"stage": "start", "team": None, "qi": 0,
                  "first": "", "answers": {}})
        text = self._last_user_text(history).strip()

        # status lookup short-circuit, any time
        m = TICKET_RE.search(text)
        if m or "status" in text.lower():
            if m:
                t = self.router.get_status(m.group(1))
                if not t:
                    return f"I couldn't find a ticket with id {m.group(1)}."
                line = f"*{t.ticket_id}* | {t.title}\nStatus: *{t.status}*"
                if t.assignee:
                    line += f", assigned to {t.assignee}"
                return line
            return "Sure, what's the ticket id? It looks like e.g. IT-20260618-0001."

        if s["stage"] == "start":
            s["first"] = text
            team = _classify(text)
            if not team:
                s["stage"] = "disambiguate"
                return ("Happy to help route that. Which fits best: *IT*, "
                        "*Legal*, *Marketing*, or *Product*?")
            return self._begin_team(s, team)

        if s["stage"] == "disambiguate":
            team = _classify(text) or next(
                (t for t in QUESTIONS if t in text.lower()), None)
            if not team:
                return ("No problem, just say one of: IT, Legal, Marketing, "
                        "or Product.")
            return self._begin_team(s, team)

        if s["stage"] == "asking":
            qs = QUESTIONS[s["team"]]
            slot_key = qs[s["qi"]][0]
            s["answers"][slot_key] = text
            s["qi"] += 1
            if s["qi"] < len(qs):
                return qs[s["qi"]][1]
            return self._file(s, requester, requester_id, source)

        # after filing: if it's a new request, start fresh; else acknowledge
        team = _classify(text)
        if team:
            s["first"] = text
            s["answers"] = {}
            return self._begin_team(s, team)
        return ("That one's filed. Tell me another request anytime, or ask me for "
                "a status update with the ticket id.")

    def _begin_team(self, s, team):
        s["team"] = team
        s["stage"] = "asking"
        s["qi"] = 0
        return QUESTIONS[team][0][1]

    def _file(self, s, requester, requester_id, source):
        team = s["team"]
        ans = s["answers"]
        first = s["first"]
        blob = first + " " + " ".join(str(v) for v in ans.values())
        priority = _priority_from(blob)
        title = (first[:70] + "…") if len(first) > 70 else first
        if not title:
            title = f"{team} request"
        desc_lines = [f"Original request: {first}"]
        for k, v in ans.items():
            desc_lines.append(f"{k.replace('_', ' ').title()}: {v}")
        description = "\n".join(desc_lines)
        request_type = {"it": "IT request", "legal": "Legal review",
                        "marketing": "Marketing request",
                        "product": "Product request"}[team]
        ticket = self.router.create_ticket(
            team=team, request_type=request_type, title=title,
            description=description, priority=priority, requester=requester,
            requester_id=requester_id, source=source, extra=dict(ans))
        if self.on_ticket:
            try:
                self.on_ticket(ticket)
            except Exception:
                pass
        s["stage"] = "done"
        return (f"Here's what I'll file: *{title}* → {TEAM_TOOL_LABEL.get(team, team)}, "
                f"priority {priority}.\n"
                f"Done. Your ticket is *{ticket.ticket_id}*. "
                f"Ask me for a status update on it anytime.")
