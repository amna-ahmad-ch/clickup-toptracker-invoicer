"""
main.py — ClickUp → TopTracker Invoice Automation

Flow:
  1. Pull time entries from ClickUp for selected date range
  2. Add each as a Manual Time Entry in TopTracker Activities
  3. Create Invoice from those activities → Save as Draft

Usage:
    python main.py                         # Interactive menu (default)
    python main.py --current-week          # This Mon–today automatically
    python main.py --last-week             # Last Mon–Sun automatically
    python main.py --start 2026-05-01 --end 2026-05-31
    python main.py --dry-run               # Preview only, don't touch TopTracker
    python main.py --invoice-only -y       # Draft invoice from existing TopTracker activities
    python main.py --current-week -y       # Sync ClickUp → TopTracker → save draft
    python main.py --headless              # No browser window (for cron)
"""

import asyncio
import argparse
import os
import sys
from datetime import datetime, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from dotenv import load_dotenv

from clickup_client import ClickUpClient
from toptracker_bot import TopTrackerConfig, run_toptracker_automation

load_dotenv(".env.local")


# ── CLI args ──────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="ClickUp → TopTracker invoice automation")
    parser.add_argument("--current-week", action="store_true", help="Bill this Mon–today automatically")
    parser.add_argument("--last-week", action="store_true", help="Bill last Mon–Sun automatically")
    parser.add_argument("--start", type=str, help="Start date YYYY-MM-DD")
    parser.add_argument("--end",   type=str, help="End date YYYY-MM-DD")
    parser.add_argument("--dry-run",  action="store_true", help="Print entries only, skip TopTracker")
    parser.add_argument("--invoice-only", action="store_true", help="Create draft invoice from existing TopTracker activities")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--headless", action="store_true", help="Run browser headless (no window)")
    return parser.parse_args()


# ── Date range helpers ────────────────────────────────────────────────────────

def current_week_range() -> tuple[datetime, datetime]:
    """This Monday 00:00 → today 23:59."""
    today = datetime.now()
    this_monday = today - timedelta(days=today.weekday())
    start = this_monday.replace(hour=0, minute=0, second=0, microsecond=0)
    end = today.replace(hour=23, minute=59, second=59, microsecond=0)
    return start, end


def last_week_range() -> tuple[datetime, datetime]:
    """Last Monday 00:00 → last Sunday 23:59."""
    today = datetime.now()
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday  = last_monday + timedelta(days=6)
    start = last_monday.replace(hour=0,  minute=0,  second=0,  microsecond=0)
    end   = last_sunday.replace( hour=23, minute=59, second=59, microsecond=0)
    return start, end


def prompt_date_range() -> tuple[datetime, datetime]:
    print("\n┌─────────────────────────────────────┐")
    print("│        Select invoice period        │")
    print("├─────────────────────────────────────┤")
    print("│  1. Current week (Mon–today)        │")
    print("│  2. Last week (Mon–Sun)             │")
    print("│  3. Last 30 days                   │")
    print("│  4. Custom dates                   │")
    print("└─────────────────────────────────────┘")

    choice = input("\nEnter choice [1/2/3/4]: ").strip()

    if choice == "1":
        return current_week_range()
    elif choice == "2":
        return last_week_range()
    elif choice == "3":
        end = datetime.now()
        return end - timedelta(days=30), end
    elif choice == "4":
        while True:
            try:
                start_str = input("Start date (YYYY-MM-DD): ").strip()
                end_str   = input("End date   (YYYY-MM-DD): ").strip()
                start = datetime.strptime(start_str, "%Y-%m-%d")
                end   = datetime.strptime(end_str,   "%Y-%m-%d")
                if end < start:
                    print("⚠ End date must be after start date.")
                    continue
                return start, end
            except ValueError:
                print("⚠ Use format YYYY-MM-DD e.g. 2026-05-01")
    else:
        print("Defaulting to current week.")
        return current_week_range()


def resolve_date_range(args) -> tuple[datetime, datetime]:
    if args.current_week:
        return current_week_range()
    elif args.last_week:
        return last_week_range()
    elif args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        if args.end:
            end = datetime.strptime(args.end, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, microsecond=0,
            )
        else:
            end = datetime.now()
        return start, end
    else:
        return prompt_date_range()


# ── Format entries for TopTracker ─────────────────────────────────────────────

def prepare_entries(line_items: list[dict]) -> list[dict]:
    """
    Convert ClickUp line items to the format toptracker_bot expects.
    Calculates worked_from / worked_to based on hours (starting 09:00).
    """
    entries = []
    for item in line_items:
        # Parse date from "06 Jun 2026" → "Jun 6, 2026" (TopTracker format)
        try:
            dt = datetime.strptime(item["date"], "%d %b %Y")
            tt_date = f"{dt.strftime('%b')} {dt.day}, {dt.year}"
        except Exception:
            tt_date = item["date"]

        # Simple time: start at 09:00, end at 09:00 + duration
        total_minutes = int(round(item["hours"] * 60))
        hh = total_minutes // 60
        mm = total_minutes % 60
        worked_to = f"{9 + hh:02d}:{mm:02d}"

        entries.append({
            "date":        tt_date,
            "task":        item["task"],
            "description": item.get("description", ""),
            "details":     item["details"],
            "hours":       item["hours"],
            "worked_from": "09:00",
            "worked_to":   worked_to,
        })
    return entries


# ── Summary printer ───────────────────────────────────────────────────────────

def print_summary(line_items: list[dict], start: datetime, end: datetime):
    total_hours  = sum(i["hours"]  for i in line_items)
    total_amount = sum(i["amount"] for i in line_items)

    print(f"\n{'='*60}")
    print(f"  Invoice Period: {start.strftime('%b %d')} – {end.strftime('%b %d, %Y')}")
    print(f"{'='*60}")
    for item in line_items:
        label = f"[{item['date']}] {item['project']} → {item['task']}"
        print(f"  {label[:52]:<52} {item['hours']:>5}h  ${item['amount']:>8.2f}")
        if item.get("description"):
            print(f"    Note: {item['description']}")
    print(f"{'─'*60}")
    print(f"  {'TOTAL':<52} {total_hours:>5}h  ${total_amount:>8.2f}")
    print(f"{'='*60}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    args = parse_args()
    start, end = resolve_date_range(args)

    # 1. Pull from ClickUp
    print(f"\nFetching ClickUp entries: {start.strftime('%b %d')} – {end.strftime('%b %d, %Y')}...")
    client = ClickUpClient(os.environ["CLICKUP_API_TOKEN"])

    workspaces = client.get_workspaces()
    team_id = os.environ.get("CLICKUP_TEAM_ID") or workspaces[0]["id"]

    line_items = client.build_invoice_line_items(
        team_id=team_id,
        start_date=start,
        end_date=end,
        hourly_rate=float(os.environ["HOURLY_RATE"]),
        assignee_id=os.environ.get("CLICKUP_ASSIGNEE_ID"),
    )

    if not line_items:
        print("⚠ No time entries found for this period. Exiting.")
        return

    print_summary(line_items, start, end)

    if args.dry_run:
        print("Dry run — not touching TopTracker. Remove --dry-run to run for real.")
        return

    if args.invoice_only:
        action = f"Create draft invoice for {start.strftime('%b %d')} – {end.strftime('%b %d, %Y')} from existing TopTracker activities"
    else:
        action = f"Add {len(line_items)} entries to TopTracker and create draft invoice"

    if not args.yes:
        confirm = input(f"{action}? [y/N]: ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return

    config = TopTrackerConfig(
        email=os.environ["TOPTRACKER_EMAIL"],
        password=os.environ["TOPTRACKER_PASSWORD"],
        project_name=os.environ["TOPTRACKER_PROJECT"],
        headless=args.headless,
    )

    entries = [] if args.invoice_only else prepare_entries(line_items)
    await run_toptracker_automation(
        config, entries, start, end, invoice_only=args.invoice_only,
    )


if __name__ == "__main__":
    asyncio.run(main())
