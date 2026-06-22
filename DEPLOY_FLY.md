# Deploy the intake bot to Fly.io (always-on)

This runs the same `app.py` on a small Fly machine over Socket Mode, so the bot
stays live even when your laptop is closed. No public URL, no webhooks.

You do NOT need Docker installed locally. Fly builds the image on its own
remote builder.

## One-time: install and sign in

```bash
brew install flyctl          # or: curl -L https://fly.io/install.sh | sh
fly auth signup              # opens a browser; use this the first time
# (later, on the same machine, use:  fly auth login )
```

Signup asks for a card to prevent abuse. A single always-on shared-cpu-1x / 512MB
machine like this sits in Fly's low-cost tier; a short demo runs to roughly a few
dollars. Set a spend alert in the Fly dashboard if you want a hard guardrail.

## Deploy

From the project folder (`~/Documents/intake-bot`):

```bash
# 1. Create the app. The name in fly.toml is "intake-demo-coyoli".
#    If Fly says that name is taken, pick another and update the `app = ` line
#    in fly.toml to match, then re-run.
fly apps create intake-demo-coyoli

# 2. Push your secrets (reads them straight from your local .env; the local-only
#    file path line is skipped).
grep -vE '^\s*#|^\s*$|GOOGLE_SERVICE_ACCOUNT_FILE' .env | fly secrets import

# 3. Push the Google service-account key as a secret (not baked into the image).
fly secrets set GOOGLE_SERVICE_ACCOUNT_JSON="$(cat service_account.json)"

# 4. Deploy.
fly deploy

# 5. Pin to exactly ONE machine. Socket Mode duplicates replies if more than one
#    instance is connected, so never scale this above 1.
fly scale count 1
```

## Verify

```bash
fly logs
```

You want to see the routing map line and "Intake bot running (Socket Mode)."
Then test in Slack exactly as before (`/problem` or DM the bot). You can now
close your laptop and it stays up.

## Everyday commands

```bash
fly logs                 # live logs
fly status               # is the machine running?
fly secrets list         # names only (values are hidden)
fly deploy               # redeploy after code changes
fly apps destroy intake-demo-coyoli   # tear it all down when the eval is over
```

## Notes and limitations (by design for the demo)

- One machine only. Do not `fly scale count` above 1 (Socket Mode would double-reply).
- State (in-progress chats, the status-watcher registry, the ticket-ID counter)
  lives in memory, so a redeploy or restart resets it. Filed rows in the Google
  Sheet persist. Fine for a short evaluation; for production this state would move
  into the Sheet or a database.
- Secrets live in Fly's secret store and are injected as env vars at runtime.
  They are not in the image or in git (see .dockerignore / .gitignore).
- When the evaluation is over, run `fly apps destroy` to stop all charges, and
  regenerate the Slack, Anthropic, and Google keys.
