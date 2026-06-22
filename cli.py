"""
Terminal simulator for the intake agent. No Slack required.

Use it to develop and rehearse the conversation, and to prove the routing/status
logic end to end. By default it runs fully in-memory (no credentials needed):

    python cli.py                 # in-memory stub destination, scripted or live
    python cli.py --live          # force live Claude (needs ANTHROPIC_API_KEY)
    python cli.py --real          # use the real routes from your .env (Sheet, etc.)

Commands inside the chat:
    /status TICKET-ID             look up a ticket
    /advance TICKET-ID Status [assignee]   (stub only) move a ticket to test breadcrumbs
    /quit
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
load_dotenv()

from backends import build_router, STATUSES
from agent import IntakeAgent

ME = os.environ.get("DEMO_USER_NAME", "Chris")
SOURCE = "cli:session"


def main():
    force_stub = "--real" not in sys.argv
    if "--live" in sys.argv:
        os.environ.setdefault("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", ""))
    router = build_router(force_stub=force_stub)
    agent = IntakeAgent(router)

    mode = "LIVE Claude" if agent._use_live else "SCRIPTED fallback"
    dest = ", ".join(f"{t}->{router.destination_label(t)}" for t in router.teams)
    print(f"\nIntake CLI | brain: {mode} | routes: {dest}")
    print("Type a request like 'I need a new laptop'. /quit to exit.\n")

    history: list[dict] = []
    while True:
        try:
            text = input(f"{ME}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text == "/quit":
            break
        if text.startswith("/advance"):
            _advance(router, text)
            continue

        history.append({"role": "user", "content": text})
        reply = agent.reply(history, requester=ME, requester_id="cli-user", source=SOURCE)
        print(f"\nIntake> {reply}\n")


def _advance(router, text):
    """Stub-only helper: simulate the destination team moving a ticket."""
    parts = text.split()
    if len(parts) < 3:
        print(f"  usage: /advance TICKET-ID <Status> [assignee]   "
              f"(statuses: {', '.join(STATUSES)})\n")
        return
    ticket_id = parts[1]
    rest = text.split(parts[1], 1)[1].strip()
    # match the longest known status that the remainder starts with (e.g. "In Progress")
    status = next((s for s in sorted(STATUSES, key=len, reverse=True)
                   if rest.lower().startswith(s.lower())), parts[2])
    assignee = rest[len(status):].strip()
    t = router.get_status(ticket_id)
    if not t:
        print(f"  no such ticket {ticket_id}\n")
        return
    t.status = status
    if assignee:
        t.assignee = assignee
    print(f"  {ticket_id} -> {status}" + (f" ({assignee})" if assignee else "") + "\n")


if __name__ == "__main__":
    main()
