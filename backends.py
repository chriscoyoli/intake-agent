"""
Destination backends + router for the universal intake agent.

The whole point of the demo is the connective tissue: the agent produces ONE
structured request (a Ticket), and the router drops it into whatever system the
destination team already uses. Every tool is hidden behind the same tiny
`Backend` interface (create / get), so:

  - adding a team  = one line in the routing map
  - swapping a team's tool = point that team at a different backend

Backends included:
  - GoogleSheetBackend : real, default destination for every team in the demo.
  - AirtableBackend    : real, enable with a token.
  - AsanaBackend       : real, enable with a token.
  - JiraBackend        : real, enable with a token.
  - StubBackend        : in-memory, used for CLI testing with zero credentials.

Routing is configured by env var INTAKE_ROUTES, e.g.:
    INTAKE_ROUTES="legal=sheet,it=jira,marketing=asana,product=sheet"
If unset, every team routes to the Google Sheet (or to the stub if no Sheet is
configured), so the demo works the moment you have one destination wired up.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Callable


# --- Teams + the atomic unit of work ----------------------------------------

# Team -> ticket id prefix. Add a team by adding a line here.
TEAM_PREFIX = {
    "legal": "LGL",
    "it": "IT",
    "marketing": "MKT",
    "product": "PRD",
}

# Friendly name of where each team's work is tracked (for messages to the user).
TEAM_TOOL_LABEL = {
    "legal": "Legal queue",
    "it": "IT service desk",
    "marketing": "Marketing board",
    "product": "Product intake",
}

STATUSES = ["Submitted", "Triaged", "In Progress", "Blocked", "Completed", "Rejected"]


@dataclass
class Ticket:
    ticket_id: str
    team: str
    request_type: str
    title: str
    description: str
    priority: str            # Low | Medium | High | Urgent
    requester: str           # human-readable name / slack handle
    requester_id: str = ""   # slack user id, for proactive status DMs
    status: str = "Submitted"
    assignee: str = ""
    created_at: str = ""
    source: str = ""         # slack channel/thread, for traceability
    extra: dict = field(default_factory=dict)  # team-specific fields
    record_id: str = ""      # backend's own row/issue id, if any

    def to_public(self) -> dict:
        d = asdict(self)
        d.pop("record_id", None)
        return d


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# Column order used by the spreadsheet-style backends.
COLUMNS = [
    "Ticket ID", "Team", "Request Type", "Title", "Description",
    "Priority", "Requester", "Requester ID", "Status", "Assignee",
    "Created", "Source", "Details",
]


def _ticket_to_row(t: Ticket) -> list:
    import json
    return [
        t.ticket_id, t.team, t.request_type, t.title, t.description,
        t.priority, t.requester, t.requester_id, t.status, t.assignee,
        t.created_at, t.source, json.dumps(t.extra) if t.extra else "",
    ]


def _row_to_ticket(row: dict) -> Ticket:
    import json
    raw = row.get("Details") or ""
    try:
        extra = json.loads(raw) if raw else {}
    except Exception:
        extra = {}
    return Ticket(
        ticket_id=row.get("Ticket ID", ""),
        team=row.get("Team", ""),
        request_type=row.get("Request Type", ""),
        title=row.get("Title", ""),
        description=row.get("Description", ""),
        priority=row.get("Priority", ""),
        requester=row.get("Requester", ""),
        requester_id=row.get("Requester ID", ""),
        status=row.get("Status", "Submitted") or "Submitted",
        assignee=row.get("Assignee", ""),
        created_at=row.get("Created", ""),
        source=row.get("Source", ""),
        extra=extra,
    )


# --- Backend interface -------------------------------------------------------

class Backend:
    name = "base"

    def create(self, ticket: Ticket) -> Ticket:
        raise NotImplementedError

    def get(self, ticket_id: str) -> Optional[Ticket]:
        raise NotImplementedError

    def list_open(self) -> list[Ticket]:
        """Optional: used by the status watcher to detect changes. Default none."""
        return []


# --- Google Sheets (real, default destination) -------------------------------

class GoogleSheetBackend(Backend):
    """
    Reads/writes a Google Sheet via a service account (gspread).

    Env:
      GOOGLE_SERVICE_ACCOUNT_FILE  path to the service-account JSON key
      GOOGLE_SHEET_ID              the spreadsheet id (from its URL)
      GOOGLE_WORKSHEET             worksheet/tab name (default "Requests")

    Setup (see README): create a service account, enable the Sheets API,
    download the JSON key, and SHARE the sheet with the service account's
    client_email as an Editor.
    """

    name = "sheet"

    def __init__(self):
        import gspread  # lazy import

        sheet_id = os.environ["GOOGLE_SHEET_ID"]
        tab = os.environ.get("GOOGLE_WORKSHEET", "Requests")
        # Prefer credentials from an env var (used in cloud/Fly, where we don't
        # ship the key file); fall back to a local file path for dev.
        sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        if sa_json:
            import json as _json
            gc = gspread.service_account_from_dict(_json.loads(sa_json))
        else:
            gc = gspread.service_account(
                filename=os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"])
        sh = gc.open_by_key(sheet_id)
        try:
            self.ws = sh.worksheet(tab)
        except Exception:
            self.ws = sh.add_worksheet(title=tab, rows=200, cols=len(COLUMNS))
        self._ensure_header()

    def _ensure_header(self):
        first = self.ws.row_values(1)
        if first[: len(COLUMNS)] != COLUMNS:
            self.ws.update("A1", [COLUMNS])

    def create(self, ticket: Ticket) -> Ticket:
        self.ws.append_row(_ticket_to_row(ticket), value_input_option="USER_ENTERED")
        return ticket

    def get(self, ticket_id: str) -> Optional[Ticket]:
        for row in self.ws.get_all_records():
            if str(row.get("Ticket ID", "")).strip().upper() == ticket_id.upper():
                return _row_to_ticket(row)
        return None

    def list_open(self) -> list[Ticket]:
        return [_row_to_ticket(r) for r in self.ws.get_all_records()
                if str(r.get("Ticket ID", "")).strip()]

    # -- mirror helpers (used when the Sheet aggregates other tools) ----------

    def _row_index(self, ticket_id: str) -> int:
        """1-based sheet row number for a ticket id, or -1 if not found."""
        for i, val in enumerate(self.ws.col_values(1), start=1):  # Ticket ID = col A
            if str(val).strip().upper() == ticket_id.upper():
                return i
        return -1

    def upsert(self, ticket: Ticket) -> None:
        """Append the ticket, or overwrite its whole row if the id exists."""
        idx = self._row_index(ticket.ticket_id)
        row = _ticket_to_row(ticket)
        if idx == -1:
            self.ws.append_row(row, value_input_option="USER_ENTERED")
        else:
            self.ws.update(f"A{idx}", [row], value_input_option="USER_ENTERED")

    def update_status(self, ticket_id: str, status: str, assignee: str = "") -> bool:
        """Update only the Status (and Assignee) cells for a ticket id."""
        idx = self._row_index(ticket_id)
        if idx == -1:
            return False
        self.ws.update_cell(idx, COLUMNS.index("Status") + 1, status)
        if assignee:
            self.ws.update_cell(idx, COLUMNS.index("Assignee") + 1, assignee)
        return True


# --- Airtable (real) ---------------------------------------------------------

class AirtableBackend(Backend):
    """Env: AIRTABLE_TOKEN, AIRTABLE_BASE_ID, AIRTABLE_TABLE (default 'Requests')."""

    name = "airtable"

    def __init__(self):
        from pyairtable import Api
        self._table = Api(os.environ["AIRTABLE_TOKEN"]).table(
            os.environ["AIRTABLE_BASE_ID"],
            os.environ.get("AIRTABLE_TABLE", "Requests"),
        )

    def _fields(self, t: Ticket) -> dict:
        import json
        return {
            "Ticket ID": t.ticket_id, "Team": t.team, "Request Type": t.request_type,
            "Title": t.title, "Description": t.description, "Priority": t.priority,
            "Requester": t.requester, "Requester ID": t.requester_id,
            "Status": t.status, "Assignee": t.assignee, "Created": t.created_at,
            "Source": t.source, "Details": json.dumps(t.extra) if t.extra else "",
        }

    def create(self, ticket: Ticket) -> Ticket:
        rec = self._table.create(self._fields(ticket))
        ticket.record_id = rec["id"]
        return ticket

    def get(self, ticket_id: str) -> Optional[Ticket]:
        safe = ticket_id.replace("'", "")
        rows = self._table.all(formula=f"{{Ticket ID}}='{safe}'", max_records=1)
        if not rows:
            return None
        t = _row_to_ticket(rows[0]["fields"])
        t.record_id = rows[0]["id"]
        return t


# --- Asana (real) ------------------------------------------------------------

class AsanaBackend(Backend):
    """
    Creates tasks in an Asana project via REST.
    Env: ASANA_TOKEN (personal access token), ASANA_PROJECT_ID.
    The ticket id and structured fields go into the task notes so nothing is lost
    even on Asana's free tier (which lacks custom fields).
    """

    name = "asana"
    BASE = "https://app.asana.com/api/1.0"

    def __init__(self):
        self.token = os.environ["ASANA_TOKEN"]
        self.project = os.environ["ASANA_PROJECT_ID"]

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def create(self, ticket: Ticket) -> Ticket:
        import requests
        notes = (
            f"Ticket: {ticket.ticket_id}\nTeam: {ticket.team}\n"
            f"Type: {ticket.request_type}\nPriority: {ticket.priority}\n"
            f"Requester: {ticket.requester}\nStatus: {ticket.status}\n\n"
            f"{ticket.description}"
        )
        r = requests.post(
            f"{self.BASE}/tasks", headers=self._headers(),
            json={"data": {"name": f"[{ticket.ticket_id}] {ticket.title}",
                           "notes": notes, "projects": [self.project]}},
            timeout=20,
        )
        r.raise_for_status()
        ticket.record_id = r.json()["data"]["gid"]
        return ticket

    def get(self, ticket_id: str) -> Optional[Ticket]:
        # Free-tier-friendly: scan project tasks for the ticket id in the name.
        import requests
        r = requests.get(
            f"{self.BASE}/projects/{self.project}/tasks",
            headers=self._headers(),
            params={"opt_fields": "name,completed,notes,assignee.name"},
            timeout=20,
        )
        r.raise_for_status()
        for task in r.json().get("data", []):
            if ticket_id.upper() in (task.get("name", "") or "").upper():
                t = Ticket(
                    ticket_id=ticket_id, team="", request_type="",
                    title=task.get("name", ""), description=task.get("notes", ""),
                    priority="", requester="",
                    status="Completed" if task.get("completed") else "In Progress",
                    assignee=(task.get("assignee") or {}).get("name", ""),
                )
                t.record_id = task.get("gid", "")
                return t
        return None


# --- Jira (real) -------------------------------------------------------------

class JiraBackend(Backend):
    """
    Creates issues in a Jira Cloud project via REST v3 (basic auth: email + API
    token).
    Env: JIRA_BASE_URL (e.g. https://yourco.atlassian.net), JIRA_EMAIL,
         JIRA_API_TOKEN, JIRA_PROJECT_KEY (e.g. IT), JIRA_ISSUE_TYPE (default 'Task').
    The intake ticket id is stored in the summary so status lookups work without
    custom fields.
    """

    name = "jira"

    def __init__(self):
        self.base = os.environ["JIRA_BASE_URL"].rstrip("/")
        self.auth = (os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"])
        self.project_key = os.environ["JIRA_PROJECT_KEY"]
        self.issue_type = os.environ.get("JIRA_ISSUE_TYPE", "Task")

    def create(self, ticket: Ticket) -> Ticket:
        import requests
        desc = (
            f"Intake ticket {ticket.ticket_id}\n"
            f"Requester: {ticket.requester} | Priority: {ticket.priority}\n\n"
            f"{ticket.description}"
        )
        payload = {"fields": {
            "project": {"key": self.project_key},
            "summary": f"[{ticket.ticket_id}] {ticket.title}",
            "description": {"type": "doc", "version": 1, "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": desc}]}]},
            "issuetype": {"name": self.issue_type},
        }}
        r = requests.post(f"{self.base}/rest/api/3/issue",
                          json=payload, auth=self.auth, timeout=20)
        r.raise_for_status()
        ticket.record_id = r.json()["key"]
        return ticket

    def get(self, ticket_id: str) -> Optional[Ticket]:
        import requests
        jql = f'summary ~ "{ticket_id}" ORDER BY created DESC'
        # /search was removed in 2025; the enhanced /search/jql replaces it.
        r = requests.get(f"{self.base}/rest/api/3/search/jql",
                         params={"jql": jql, "maxResults": 1,
                                 "fields": "summary,status,assignee"},
                         auth=self.auth, timeout=20)
        r.raise_for_status()
        issues = r.json().get("issues", [])
        if not issues:
            return None
        f = issues[0]["fields"]
        t = Ticket(
            ticket_id=ticket_id, team="", request_type="",
            title=f.get("summary", ""), description="", priority="", requester="",
            status=(f.get("status") or {}).get("name", "In Progress"),
            assignee=((f.get("assignee") or {}) or {}).get("displayName", ""),
        )
        t.record_id = issues[0]["key"]
        return t


# --- Stub (in-memory; CLI testing with no credentials) -----------------------

class StubBackend(Backend):
    def __init__(self, name: str = "stub"):
        self.name = name
        self._store: dict[str, Ticket] = {}
        self._lock = threading.Lock()

    def create(self, ticket: Ticket) -> Ticket:
        with self._lock:
            self._store[ticket.ticket_id] = ticket
        print(f"[{self.name}] filed {ticket.ticket_id}: {ticket.title!r} "
              f"-> {ticket.team}")
        return ticket

    def get(self, ticket_id: str) -> Optional[Ticket]:
        with self._lock:
            return self._store.get(ticket_id.upper())

    def list_open(self) -> list[Ticket]:
        with self._lock:
            return list(self._store.values())


# --- Mirror (team tool + Google Sheet as the unified record) -----------------

class MirroredBackend(Backend):
    """
    Wraps a team's primary tool (Airtable/Jira/Asana) so that every ticket is
    ALSO written to a shared Google Sheet, which becomes the unified record of
    all intake. The primary tool stays the place the team works; the Sheet
    always reflects what exists and (via the status watcher) the latest status.

    Reads come from the primary, so live status/assignee are sourced from the
    tool the team actually updates.
    """

    def __init__(self, primary: Backend, sheet: "GoogleSheetBackend"):
        self.primary = primary
        self.sheet = sheet
        self.name = f"{primary.name}+sheet"

    def create(self, ticket: Ticket) -> Ticket:
        ticket = self.primary.create(ticket)
        try:
            self.sheet.upsert(ticket)
        except Exception as e:
            print(f"[mirror] sheet write failed for {ticket.ticket_id}: {e}")
        return ticket

    def get(self, ticket_id: str) -> Optional[Ticket]:
        return self.primary.get(ticket_id)

    def list_open(self) -> list[Ticket]:
        return self.primary.list_open()

    def mirror_status(self, ticket: Ticket) -> None:
        """Push a status/assignee change from the primary into the Sheet."""
        try:
            self.sheet.update_status(ticket.ticket_id, ticket.status, ticket.assignee)
        except Exception as e:
            print(f"[mirror] sheet status sync failed for {ticket.ticket_id}: {e}")


# --- Backend registry --------------------------------------------------------

# Map a destination key -> factory. Lazily constructed so unused backends never
# need their credentials.
BACKEND_FACTORIES: dict[str, Callable[[], Backend]] = {
    "sheet": GoogleSheetBackend,
    "airtable": AirtableBackend,
    "asana": AsanaBackend,
    "jira": JiraBackend,
    "stub": lambda: StubBackend("stub"),
}


# --- Router ------------------------------------------------------------------

class Router:
    def __init__(self, team_backends: dict[str, Backend], mirror_sheet=None):
        self._backends = team_backends
        self._mirror_sheet = mirror_sheet   # unified Google Sheet record, or None
        self._counters: dict[str, int] = {}
        self._index: dict[str, str] = {}   # ticket_id -> team
        self._lock = threading.Lock()

    @property
    def teams(self) -> list[str]:
        return list(self._backends.keys())

    def destination_label(self, team: str) -> str:
        be = self._backends.get(team)
        return be.name if be else "unknown"

    def _next_id(self, team: str) -> str:
        prefix = TEAM_PREFIX.get(team, "REQ")
        with self._lock:
            self._counters[team] = self._counters.get(team, 0) + 1
            n = self._counters[team]
        return f"{prefix}-{datetime.now().strftime('%Y%m%d')}-{n:04d}"

    def create_ticket(self, team: str, request_type: str, title: str,
                      description: str, priority: str, requester: str,
                      requester_id: str = "", source: str = "",
                      extra: Optional[dict] = None) -> Ticket:
        if team not in self._backends:
            raise ValueError(f"No destination configured for team {team!r}. "
                             f"Known teams: {', '.join(self._backends)}")
        ticket = Ticket(
            ticket_id=self._next_id(team), team=team, request_type=request_type,
            title=title, description=description, priority=priority,
            requester=requester, requester_id=requester_id,
            status="Submitted", created_at=_now(), source=source,
            extra=extra or {},
        )
        ticket = self._backends[team].create(ticket)
        with self._lock:
            self._index[ticket.ticket_id] = team
        return ticket

    def get_status(self, ticket_id: str) -> Optional[Ticket]:
        ticket_id = ticket_id.strip().upper()
        team = self._index.get(ticket_id)
        if team:
            return self._backends[team].get(ticket_id)
        for be in dict.fromkeys(self._backends.values()):  # de-dup shared backends
            t = be.get(ticket_id)
            if t:
                return t
        return None

    def sync_sheet(self, ticket: Optional[Ticket]) -> None:
        """Mirror a ticket's current status/assignee into the unified Sheet."""
        if self._mirror_sheet is None or ticket is None:
            return
        try:
            self._mirror_sheet.update_status(
                ticket.ticket_id, ticket.status, ticket.assignee)
        except Exception as e:
            print(f"[router] sheet sync failed for "
                  f"{getattr(ticket, 'ticket_id', '?')}: {e}")

    def all_backends(self) -> list[Backend]:
        return list(dict.fromkeys(self._backends.values()))


def build_router(force_stub: bool = False) -> Router:
    """
    Build the team->backend map from env.

    INTAKE_ROUTES="legal=sheet,it=jira,marketing=asana,product=sheet"
    Any team omitted falls back to DEFAULT_DESTINATION (env, default "sheet").
    If a chosen backend can't initialize (missing creds), that team falls back
    to an in-memory stub so the demo still runs.
    """
    teams = list(TEAM_PREFIX.keys())
    if force_stub:
        shared = StubBackend("stub")
        return Router({t: shared for t in teams})

    default_dest = os.environ.get("DEFAULT_DESTINATION", "sheet")
    routes: dict[str, str] = {t: default_dest for t in teams}
    for pair in os.environ.get("INTAKE_ROUTES", "").split(","):
        pair = pair.strip()
        if "=" in pair:
            team, dest = (x.strip() for x in pair.split("=", 1))
            if team in routes:
                routes[team] = dest

    # Instantiate each distinct backend once, share across teams that use it.
    instances: dict[str, Backend] = {}

    def get_instance(dest: str) -> Backend:
        if dest not in instances:
            try:
                instances[dest] = BACKEND_FACTORIES[dest]()
            except Exception as e:
                print(f"[router] '{dest}' unavailable ({e}); using an in-memory stub.")
                instances[dest] = StubBackend(f"{dest}-stub")
        return instances[dest]

    primaries = {team: get_instance(dest) for team, dest in routes.items()}

    # Establish ONE Google Sheet as the unified record that mirrors every tool.
    # Disable with MIRROR_TO_SHEET=false.
    mirror_sheet = None
    if os.environ.get("MIRROR_TO_SHEET", "true").lower() != "false":
        mirror_sheet = next(
            (i for i in instances.values() if isinstance(i, GoogleSheetBackend)), None)
        if mirror_sheet is None and os.environ.get("GOOGLE_SHEET_ID"):
            try:
                mirror_sheet = GoogleSheetBackend()
            except Exception as e:
                print(f"[router] mirror sheet unavailable ({e}); mirroring off.")

    # Wrap non-sheet primaries so every ticket also lands in the unified Sheet.
    team_backends: dict[str, Backend] = {}
    for team, primary in primaries.items():
        if mirror_sheet is not None and not isinstance(primary, GoogleSheetBackend):
            team_backends[team] = MirroredBackend(primary, mirror_sheet)
        else:
            team_backends[team] = primary
    return Router(team_backends, mirror_sheet=mirror_sheet)
