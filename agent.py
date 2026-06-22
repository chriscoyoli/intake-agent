"""
The intake operator.

Wraps the Anthropic Messages API with a help-desk-operator persona that:
  - interprets a plain-language request ("I need a new laptop")
  - infers the destination team and pre-fills everything it can
  - asks only the missing qualifying questions, a couple at a time
  - assembles one complete payload and files it via the router (create_ticket)
  - reports status on request (get_ticket_status)

If the Anthropic API is unavailable (no key, network blip mid-demo), it falls
back to a deterministic scripted flow so the demo never dies on stage. See
scripted.py.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from backends import Router, STATUSES, TEAM_PREFIX, TEAM_TOOL_LABEL
from scripted import ScriptedAgent

MODEL = os.environ.get("INTAKE_MODEL", "claude-sonnet-4-6")

SYSTEM_PROMPT = f"""\
You are Intake, the single front door for employee requests at this company. \
You live in Slack. An employee describes a problem in plain language and you get \
it to the right team, fully specified, without making them learn that team's \
tool or fill out a form.

Teams you route to (pick exactly ONE):
- legal: contracts, NDAs, vendor agreements, reviews, compliance, IP/trademark.
- it: hardware (laptops, monitors), software/licenses, access & permissions, \
accounts, bugs in internal tools, anything break/fix. "I need a new laptop" -> it.
- marketing: campaigns, content, design, events, web copy, social, swag.
- product: feature requests, product feedback, roadmap asks, data/analytics pulls.

Operating principles:
1. INFER, don't interrogate. From the opening message, guess the team and \
pre-fill every field you reasonably can. Only ask about genuine gaps.
2. Ask at most one or two short questions per turn. Never dump a long form.
3. If the team is ambiguous, ask ONE clarifying question instead of guessing.
4. Match the qualifying questions to the request type. Examples:
   - laptop/hardware (it): replacement or new hire? any spec/budget tier? needed by when?
   - NDA/contract (legal): counterparty? one-way or mutual? any deadline?
   - campaign (marketing): goal/audience? channels? launch date? assets needed?
   - feature/data (product): what problem? who's affected? how urgent?

Required fields before you may file:
  team, request_type (short category), title (one line), description (enough for \
the receiving team to act with NO follow-up), priority (Low|Medium|High|Urgent).
Capture useful team-specific answers in the `extra` object (e.g. counterparty, \
needed_by, budget_tier). Do not ask the requester for their name; the system \
provides it.

Set priority from real signals (a hard deadline, a blocked employee, a legal \
risk). Do not invent urgency; if unclear, ask what's driving the timing.

When you have everything: echo a one-line confirmation ("Here's what I'll file: \
...") then call create_ticket. After it succeeds, give the ticket id and say \
they can ask you for a status update anytime.

For status questions, call get_ticket_status with the id and report it plainly. \
Valid statuses: {', '.join(STATUSES)}.

If a request fits none of the four teams, say so briefly and suggest who might \
own it rather than forcing a bad route.

Style: concise, warm, human. No corporate filler, no bullet dumps. One tight \
paragraph per turn.
"""

TOOLS = [
    {
        "name": "create_ticket",
        "description": ("File the request as a tracked ticket with the owning "
                        "team. Call only after all required fields are gathered "
                        "and you've confirmed a one-line summary."),
        "input_schema": {
            "type": "object",
            "properties": {
                "team": {"type": "string", "enum": list(TEAM_PREFIX.keys())},
                "request_type": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string",
                             "enum": ["Low", "Medium", "High", "Urgent"]},
                "extra": {"type": "object",
                          "description": "Team-specific fields, e.g. "
                          "{counterparty, needed_by, budget_tier}."},
            },
            "required": ["team", "request_type", "title", "description", "priority"],
        },
    },
    {
        "name": "get_ticket_status",
        "description": "Look up the current status of an existing ticket by id.",
        "input_schema": {
            "type": "object",
            "properties": {"ticket_id": {"type": "string"}},
            "required": ["ticket_id"],
        },
    },
]


class IntakeAgent:
    def __init__(self, router: Router, client=None, on_ticket=None):
        """
        on_ticket: optional callback(Ticket) fired right after a ticket is filed,
        used by the Slack app to register it with the status watcher.
        """
        self.router = router
        self.on_ticket = on_ticket
        self._scripted = ScriptedAgent(router, on_ticket=on_ticket)
        self._client = client
        self._use_live = True
        if client is None:
            try:
                import anthropic
                if os.environ.get("ANTHROPIC_API_KEY"):
                    self._client = anthropic.Anthropic()
                else:
                    self._use_live = False
            except Exception:
                self._use_live = False

    # -- tool execution -------------------------------------------------------

    def _run_tool(self, name, args, requester, requester_id, source):
        if name == "create_ticket":
            ticket = self.router.create_ticket(
                team=args["team"], request_type=args["request_type"],
                title=args["title"], description=args["description"],
                priority=args["priority"], requester=requester,
                requester_id=requester_id, source=source,
                extra=args.get("extra") or {},
            )
            if self.on_ticket:
                try:
                    self.on_ticket(ticket)
                except Exception:
                    pass
            return {"ok": True, "ticket_id": ticket.ticket_id, "team": ticket.team,
                    "status": ticket.status,
                    "tracked_in": TEAM_TOOL_LABEL.get(ticket.team, ticket.team)}
        if name == "get_ticket_status":
            t = self.router.get_status(args["ticket_id"])
            if not t:
                return {"ok": False, "error": "No ticket found with that id."}
            return {"ok": True, **t.to_public()}
        return {"ok": False, "error": f"Unknown tool {name}"}

    # -- main turn ------------------------------------------------------------

    def reply(self, history: list[dict], requester: str,
              requester_id: str = "", source: str = "") -> str:
        """
        Advance one user turn. `history` is the full Anthropic-format message list
        INCLUDING the new user message; it is mutated in place so the caller can
        persist it. Returns assistant text to show the user.
        """
        if not self._use_live:
            return self._scripted.reply(history, requester, requester_id, source)

        try:
            return self._live_reply(history, requester, requester_id, source)
        except Exception as e:
            # one-time, graceful downgrade so the demo keeps going
            self._use_live = False
            print(f"[agent] live model unavailable ({e}); using scripted fallback.")
            return self._scripted.reply(history, requester, requester_id, source)

    def _live_reply(self, history, requester, requester_id, source):
        while True:
            resp = self._client.messages.create(
                model=MODEL, max_tokens=1024, system=SYSTEM_PROMPT,
                tools=TOOLS, messages=history,
            )
            history.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason != "tool_use":
                return "".join(b.text for b in resp.content
                               if getattr(b, "type", "") == "text").strip()
            results = []
            for block in resp.content:
                if getattr(block, "type", "") != "tool_use":
                    continue
                out = self._run_tool(block.name, block.input,
                                     requester, requester_id, source)
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(out)})
            history.append({"role": "user", "content": results})
