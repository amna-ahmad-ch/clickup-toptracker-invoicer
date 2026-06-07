# ClickUp → TopTracker Invoice Automation

Pull time entries from ClickUp and create **draft invoices** in TopTracker automatically. The bot saves invoices as drafts — it never clicks Send.

## What it does

1. Fetches time entries from the ClickUp API for a date range you choose
2. Adds each entry as a **Manual Time Entry** in TopTracker (My Activities)
3. Opens **Create Invoice**, sets the date range, and clicks **Save Draft & Preview**

Activity descriptions match your Google Sheets format:

```
ProjectName
	TaskName
	(description)
```

## Requirements

- Python 3.10+
- ClickUp API token
- TopTracker account (https://tracker.toptal.com)

## Quick start

```bash
pip install playwright python-dotenv requests
playwright install chromium
```

Copy the example env file and add your credentials:

```bash
cp .env.example .env.local
```

Edit `.env.local` with your ClickUp token, TopTracker login, hourly rate, and project name.

Preview this week's entries (no browser, no changes):

```bash
python main.py --dry-run --current-week
```

Run the full flow:

```bash
python main.py --current-week -y
```

On Windows, if `python` is not found, use `py` instead (e.g. `py main.py --dry-run --current-week`).

## Project files

| File | Purpose |
|------|---------|
| `main.py` | CLI entry point — ClickUp sync + invoice creation |
| `clickup_client.py` | ClickUp API client and line-item builder |
| `toptracker_bot.py` | Playwright bot for TopTracker login, entries, and drafts |
| `COMMANDS.md` | Full command reference and troubleshooting |
| `.env.example` | Template for required environment variables |
| `.env.local` | Your secrets (gitignored — do not commit) |

## Common commands

```bash
# Last week (Mon–Sun)
python main.py --last-week -y

# Single day
python main.py --start 2026-06-01 --end 2026-06-01 -y

# Draft invoice only (entries already in TopTracker)
python main.py --current-week --invoice-only -y

# Test TopTracker bot with one dummy entry
python toptracker_bot.py --entry-only
```

See **[COMMANDS.md](COMMANDS.md)** for the complete list of flags and examples.

## Notes

- Invoices are always saved as **drafts**. Review them in TopTracker before sending manually.
- Debug screenshots (`probe*.png`, `error_state.png`, etc.) are generated at runtime and are gitignored.
- Never commit `.env.local` — it contains API tokens and passwords.
