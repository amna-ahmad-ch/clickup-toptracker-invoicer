# ClickUp → TopTracker — Command Reference

## Setup (one time)

```bash
pip install playwright python-dotenv requests
playwright install chromium
```

Copy `.env.example` → `.env.local` and fill in your credentials.

If `python` is not found on Windows, use `py` instead:

```bash
py main.py --help
```

---

## Main workflow (`main.py`)

Full flow: **ClickUp → add manual time entries → save invoice as draft** (never sends).

### Preview only (no TopTracker changes)

```bash
python main.py --dry-run --current-week
python main.py --dry-run --last-week
python main.py --dry-run --start 2026-06-01 --end 2026-06-06
python main.py --dry-run --start 2026-06-01 --end 2026-06-01   # single day
```

### Run for real (asks for confirmation)

```bash
python main.py --current-week
python main.py --last-week
python main.py --start 2026-06-01 --end 2026-06-06
```

### Run without confirmation prompt

```bash
python main.py --current-week -y
python main.py --last-week -y
python main.py --start 2026-06-01 --end 2026-06-01 -y
```

### Draft invoice only (skip adding entries)

Use when activities are already in TopTracker:

```bash
python main.py --current-week --invoice-only -y
python main.py --start 2026-06-01 --end 2026-06-06 --invoice-only -y
```

### Interactive date picker (no flags)

```bash
python main.py
```



---

## TopTracker bot tests (`toptracker_bot.py`)

### Test login + one dummy entry (safe to delete)

```bash
python toptracker_bot.py --entry-only
```

### Test dummy entry + draft invoice

```bash
python toptracker_bot.py
```

### Draft invoice from existing activities (no dummy entry)

```bash
python toptracker_bot.py --invoice-only
```

---

## ClickUp client test

```bash
python clickup_client.py
```

Lists workspaces and prints line items for the last 7 days.

---

## Date range options

| Flag | Period |
|------|--------|
| `--current-week` | This Monday → today |
| `--last-week` | Last Monday → last Sunday |
| `--start YYYY-MM-DD --end YYYY-MM-DD` | Custom range (end date is inclusive, full day) |
| No flags | Interactive menu |

---

## Environment variables (`.env.local`)

| Variable | Required | Description |
|----------|----------|-------------|
| `CLICKUP_API_TOKEN` | Yes | From [ClickUp Settings → Apps](https://app.clickup.com/settings/apps) |
| `CLICKUP_TEAM_ID` | No | Workspace ID from URL; auto-uses first if blank |
| `CLICKUP_ASSIGNEE_ID` | No | Filter entries to one user |
| `HOURLY_RATE` | Yes | Billing rate (e.g. `25`) |
| `TOPTRACKER_EMAIL` | Yes | TopTracker login email |
| `TOPTRACKER_PASSWORD` | Yes | TopTracker login password |
| `TOPTRACKER_PROJECT` | Yes | Exact project name (e.g. `Development Tasks`) |

---

## Activity description format

Each TopTracker entry uses the same format as the Sheets script:

```
ProjectName
	TaskName
	(description)
```

---

## Troubleshooting

| Problem | What to check |
|---------|----------------|
| `Python was not found` | Use `py` instead of `python`, or restart terminal after PATH fix |
| Login works but nothing happens | Run `python toptracker_bot.py --entry-only` to test the bot alone |
| `No billable hours found` | Entries may not be in the invoice date range — re-run without `--invoice-only` |
| Script fails mid-run | Open `error_state.png` in the project folder |
| Invoice saved but not sent | Expected — bot only clicks **Save Draft & Preview**, never **Send** |

---

## Common examples

```bash
# Monday routine: bill last week
python main.py --last-week -y

# Bill today only
python main.py --start 2026-06-06 --end 2026-06-06 -y

# Check this week before submitting
python main.py --dry-run --current-week

# Re-create draft from activities already in TopTracker
python main.py --current-week --invoice-only -y

### Headless (no browser window)

```bash
python main.py --last-week -y --headless
```
```
