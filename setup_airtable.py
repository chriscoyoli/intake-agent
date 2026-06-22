"""
One-time setup: build the Airtable "Requests" table so its columns match what
the bot writes (see COLUMNS in backends.py). Idempotent: if the table already
exists, it does nothing.

Run once after setting AIRTABLE_TOKEN and AIRTABLE_BASE_ID in .env:

    python3 setup_airtable.py
"""

from __future__ import annotations

import os
from dotenv import load_dotenv
from pyairtable import Api

load_dotenv()

TOKEN = os.environ["AIRTABLE_TOKEN"]
BASE_ID = os.environ["AIRTABLE_BASE_ID"]
TABLE = os.environ.get("AIRTABLE_TABLE", "Requests")

PRIORITY = ["Low", "Medium", "High", "Urgent"]
STATUS = ["Submitted", "Triaged", "In Progress", "Blocked", "Completed", "Rejected"]
TEAMS = ["legal", "it", "marketing", "product"]


def _select(choices):
    return {"choices": [{"name": c} for c in choices]}


# First field becomes the table's primary field; it must be a text type.
FIELDS = [
    {"name": "Ticket ID", "type": "singleLineText"},
    {"name": "Team", "type": "singleSelect", "options": _select(TEAMS)},
    {"name": "Request Type", "type": "singleLineText"},
    {"name": "Title", "type": "singleLineText"},
    {"name": "Description", "type": "multilineText"},
    {"name": "Priority", "type": "singleSelect", "options": _select(PRIORITY)},
    {"name": "Requester", "type": "singleLineText"},
    {"name": "Requester ID", "type": "singleLineText"},
    {"name": "Status", "type": "singleSelect", "options": _select(STATUS)},
    {"name": "Assignee", "type": "singleLineText"},
    {"name": "Created", "type": "singleLineText"},
    {"name": "Source", "type": "singleLineText"},
    {"name": "Details", "type": "multilineText"},
]


def main():
    base = Api(TOKEN).base(BASE_ID)
    existing = {t.name for t in base.tables()}
    if TABLE in existing:
        print(f"Table '{TABLE}' already exists in base {BASE_ID}. Nothing to do.")
        return
    base.create_table(TABLE, fields=FIELDS)
    print(f"Created table '{TABLE}' in base {BASE_ID} with {len(FIELDS)} fields.")


if __name__ == "__main__":
    main()
