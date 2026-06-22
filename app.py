"""
Slack front end (Socket Mode) for the universal intake agent.

Run:  python app.py

Socket Mode opens a websocket from this process to Slack, so there is NO public
URL, ngrok, or deployed server. Keep this running and the bot is live.

Entry points for the employee:
  - /problem <optional text>  -> opens an intake DM thread (the "one front door")
  - DM the bot directly       -> same intake conversation
  - @mention in a channel      -> intake in a thread
  - /status <TICKET-ID>        -> quick status lookup

Required env (see .env.example):
  SLACK_BOT_TOKEN   xoxb-...
  SLACK_APP_TOKEN   xapp-...     (App-Level Token, scope connections:write)
  ANTHROPIC_API_KEY sk-ant-...   (omit to run the scripted fallback)
  Destination creds: GOOGLE_* / AIRTABLE_* / ASANA_* / JIRA_* as configured.
"""

from __future__ import annotations

import os
import re

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from agent import IntakeAgent
from backends import build_router
from status_watcher import StatusWatcher

load_dotenv()

app = App(token=os.environ["SLACK_BOT_TOKEN"])
router = build_router()
watcher = StatusWatcher(router, app.client)
agent = IntakeAgent(router, on_ticket=watcher.register)

CONVERSATIONS: dict[str, list[dict]] = {}   # thread_key -> message history
USER_NAMES: dict[str, str] = {}


def display_name(user_id: str) -> str:
    if user_id not in USER_NAMES:
        try:
            p = app.client.users_info(user=user_id)["user"]["profile"]
            USER_NAMES[user_id] = p.get("display_name") or p.get("real_name") or user_id
        except Exception:
            USER_NAMES[user_id] = user_id
    return USER_NAMES[user_id]


def strip_mention(text: str) -> str:
    return re.sub(r"<@[\w]+>", "", text or "").strip()


def run_turn(channel, thread_ts, user_id, text, say):
    key = f"{channel}:{thread_ts}"
    history = CONVERSATIONS.setdefault(key, [])
    history.append({"role": "user", "content": text or "Hi"})
    try:
        reply = agent.reply(history, requester=display_name(user_id),
                            requester_id=user_id, source=f"slack:{channel}/{thread_ts}")
    except Exception as e:
        reply = f":warning: Something went wrong: `{e}`"
    say(text=reply or "…", thread_ts=thread_ts)


@app.command("/problem")
def on_problem(ack, body, client):
    ack()
    user_id = body["user_id"]
    # Open a DM with the user and start the intake there (the "front door").
    dm = client.conversations_open(users=user_id)["channel"]["id"]
    opening = (body.get("text") or "").strip()
    greeting = ("Hi! I'm Intake, your one front door for any request: IT, Legal, "
                "Marketing, or Product. Tell me what you need in plain language and "
                "I'll take it from there.")
    client.chat_postMessage(channel=dm, text=greeting)
    if opening:
        # treat the slash-command text as their first message
        run_turn(dm, _ts(client, dm), user_id, opening,
                 lambda text, thread_ts=None: client.chat_postMessage(
                     channel=dm, text=text, thread_ts=thread_ts))


def _ts(client, channel):
    # anchor a thread on a fresh placeholder message so the convo stays grouped
    r = client.chat_postMessage(channel=channel, text="…")
    return r["ts"]


@app.event("app_mention")
def on_mention(event, say):
    thread_ts = event.get("thread_ts") or event["ts"]
    run_turn(event["channel"], thread_ts, event["user"],
             strip_mention(event.get("text", "")), say)


@app.event("message")
def on_message(event, say):
    if event.get("channel_type") != "im" or event.get("bot_id") or event.get("subtype"):
        return
    thread_ts = event.get("thread_ts") or event["ts"]
    run_turn(event["channel"], thread_ts, event["user"], event.get("text", ""), say)


@app.command("/ticket-status")
def on_status(ack, respond, command):
    ack()
    ticket_id = (command.get("text") or "").strip()
    if not ticket_id:
        respond("Usage: `/ticket-status TICKET-ID`  e.g. `/ticket-status IT-20260618-0001`")
        return
    t = router.get_status(ticket_id)
    if not t:
        respond(f"No ticket found with id `{ticket_id}`.")
        return
    line = (f"*{t.ticket_id}* | {t.title}\nTeam: {t.team}   Priority: {t.priority}\n"
            f"Status: *{t.status}*")
    if t.assignee:
        line += f"   Assignee: {t.assignee}"
    respond(line)


if __name__ == "__main__":
    watcher.start()
    print(f"Teams routed to: " +
          ", ".join(f"{t}->{router.destination_label(t)}" for t in router.teams))
    print("Intake bot running (Socket Mode). Ctrl+C to stop.")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
