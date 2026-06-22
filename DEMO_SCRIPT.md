# Demo script: narrating the universal intake agent

A tight 6 to 8 minute walkthrough for a live screen share. Three scenarios show three different routes into three real tools, each mirrored into one Google Sheet, plus an off-script curveball to prove the agent is not canned.

## Setup before you start

The bot runs on Fly.io, so nothing needs to be running on your laptop. Have these open:

- Slack, on a DM with the Intake bot.
- Four browser tabs: the Airtable base (Legal), the Jira "IT Service Desk" project, the Asana "Marketing Intake" project, and the Google Sheet (the unified record).
- Optional, for narration: a terminal running `fly logs`, which streams each routing decision and status sync live.
- One sentence to open: "Every team here has a different intake path. The idea is one front door: you tell a bot what you need in plain language, and it routes the work into whatever system that team already uses, while keeping one master record of everything."

## Scenario 1: laptop request, routes to IT / Jira

**You type in Slack:** `/problem`

The bot opens a DM and greets you.

**You type:** `My laptop won't hold a charge anymore, I think the battery is dead.`

> Narrate: notice it never asked "which team?" It inferred IT from the problem itself.

**Bot asks** something like: is this a replacement or a new device, and when do you need it by.

**You type:** `Replacement, and I have a customer demo Thursday so ideally before then.`

> Narrate: it picked up the deadline on its own and set priority from a real signal, not by asking me to rate urgency 1 to 5.

**Bot confirms and files.** It returns a ticket id like `IT-20260622-0001`.

> Switch to the Jira tab: the request is a real issue in the IT Service Desk project, fully specified. Then switch to the Google Sheet: the same ticket is there too. "It lands in the tool IT actually works in, and in the master record at the same time."

## Scenario 2: NDA, routes to Legal / Airtable

**You type:** `I need an NDA reviewed before I can share specs with a vendor.`

> Narrate: different problem, different team, and watch the questions change. It is going to ask Legal-specific things.

**Bot asks:** who is the counterparty, one-way or mutual, any deadline.

**You answer:** `Counterparty is Acme Robotics, mutual, and they want it signed by Friday.`

**Bot files** `LGL-20260622-0001`.

> Show the Airtable base: the request is a row in the Legal Requests table. Then the Google Sheet: same ticket, same master record. "Same front door, same structured payload, routed to Legal's tool instead of IT's. The only thing that changed is one line of config."

## Scenario 3: marketing campaign, routes to Marketing / Asana

**You type:** `We're launching the spring product update and I need a campaign to support it.`

**Bot asks:** goal and audience, channels, launch date.

**You answer:** `Drive signups from existing free users, email plus LinkedIn, launching April 2.`

**Bot files** `MKT-20260622-0001`.

> Show the Asana "Marketing Intake" project: the request is a task there, and again in the Google Sheet. "Three teams, three different tools, one experience for the employee and one master record. Adding HR or Finance tomorrow is a routing rule and an adapter."

## The status loop (the part employees never get today)

Go into one of the tools, not the Sheet, and move the work. For example, in Jira drag the laptop issue to **In Progress** and assign it to someone. (Or change the **Status** in Airtable for the NDA.)

Within about 8 seconds, two things happen: the bot DMs the requester in Slack ("Update on IT-20260622-0001: now In Progress, assigned to ..."), and the Google Sheet row updates to match.

> Narrate: "The team works in their own tool. The requester gets a plain-language update in Slack without asking, and the master record stays in sync automatically. Nobody is copy-pasting between systems."

You can also type `/ticket-status IT-20260622-0001` in Slack to pull status on demand.

## The curveball (let the interviewer drive)

Invite them: "Type anything an employee might actually ask."

Because the brain is live Claude, it will interpret novel requests, pick a team or ask one clarifying question, and gather the right details. If they type something genuinely outside the four teams, the agent says so and suggests who might own it instead of forcing a bad route.

> Closing line: "Each team keeps working in the tool they already use, the employee never learns any of them, and there's one Google Sheet that holds every request across every team. The connective tissue and the intake intelligence are the hard part, and that is what you just saw, running live."

## If the wifi dies

The agent silently falls back to a scripted flow. The conversation is slightly more linear but still classifies, qualifies, files into the right tool, and reports status. You can also run the whole thing in `cli.py` with no network at all.
