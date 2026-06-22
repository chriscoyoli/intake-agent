"""
Proactive status breadcrumbs.

This is stage 3 of the demo: when a ticket's status changes in the destination
system, the requester gets a plain-language DM ("your laptop ticket moved to In
Progress, assigned to Dan May") without having to ask.

How it works: tickets are registered as they're filed. A background thread polls
each ticket's destination every POLL_SECONDS, and when status or assignee changes
it DMs the requester via Slack. Polling keeps the demo dependency-free (no
webhooks to configure); for the live demo you just edit the Status cell in the
Google Sheet and the bot announces it a few seconds later.
"""

from __future__ import annotations

import threading
import time

POLL_SECONDS = 8


class StatusWatcher:
    def __init__(self, router, slack_client, poll_seconds: int = POLL_SECONDS):
        self.router = router
        self.slack = slack_client
        self.poll_seconds = poll_seconds
        # ticket_id -> {"requester_id", "channel", "title", "status", "assignee"}
        self._watched: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._thread = None

    def register(self, ticket):
        if not ticket.requester_id:
            return
        with self._lock:
            self._watched[ticket.ticket_id] = {
                "requester_id": ticket.requester_id,
                "channel": ticket.requester_id,  # DM channel = user id works with chat_postMessage
                "title": ticket.title,
                "status": ticket.status,
                "assignee": ticket.assignee,
            }

    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print(f"[watcher] polling every {self.poll_seconds}s for status changes.")

    def _loop(self):
        while True:
            time.sleep(self.poll_seconds)
            try:
                self._check_all()
            except Exception as e:
                print(f"[watcher] poll error: {e}")

    def _check_all(self):
        with self._lock:
            items = list(self._watched.items())
        for ticket_id, prev in items:
            current = self.router.get_status(ticket_id)
            if not current:
                continue
            changed = (current.status != prev["status"]
                       or (current.assignee and current.assignee != prev["assignee"]))
            if not changed:
                continue
            with self._lock:
                self._watched[ticket_id]["status"] = current.status
                self._watched[ticket_id]["assignee"] = current.assignee
            # mirror the change into the unified Google Sheet, then tell the user
            self.router.sync_sheet(current)
            self._notify(prev, ticket_id, current)

    def _notify(self, prev, ticket_id, current):
        msg = f":bell: Update on *{ticket_id}* ({prev['title']}): now *{current.status}*"
        if current.assignee:
            msg += f", assigned to {current.assignee}"
        try:
            self.slack.chat_postMessage(channel=prev["requester_id"], text=msg)
            print(f"[watcher] notified {prev['requester_id']} about {ticket_id}")
        except Exception as e:
            print(f"[watcher] could not DM about {ticket_id}: {e}")
