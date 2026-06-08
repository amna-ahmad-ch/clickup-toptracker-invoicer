"""
TopTracker Invoice Bot
Exact selectors based on real TopTracker UI screenshots.

Flow:
  1. Log in
  2. For each ClickUp entry → Add Manual Time Entry in My Activities
  3. Go to Invoices → Create Invoice
  4. Set date range → Save Draft & Preview
"""

import asyncio
import os
import sys
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

BASE_URL = "https://tracker.toptal.com"
ACTIVITIES_URL = f"{BASE_URL}/app/my-activities"


@dataclass
class TopTrackerConfig:
    email: str
    password: str
    project_name: str        # e.g. "Development Tasks"
    headless: bool = False   # False = watch it run; True = background/cron


# ── Helpers ──────────────────────────────────────────────────────────────────

async def new_browser_context(playwright, headless: bool):
    browser = await playwright.chromium.launch(headless=headless, slow_mo=200)
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    )
    return browser, context


async def screenshot(page: Page, name: str):
    path = f"{name}.png"
    await page.screenshot(path=path)
    print(f"  📸 Screenshot: {path}")


async def dismiss_overlays(page: Page):
    """Close cookie banners and one-time popups that block interaction."""
    for label in ("Accept Cookies", "Got it", "Continue"):
        button = page.get_by_role("button", name=label)
        if await button.count():
            try:
                await button.first.click(timeout=2_000)
                await page.wait_for_timeout(300)
            except Exception:
                pass


async def wait_for_app(page: Page):
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(800)


def format_date_range(start: datetime, end: datetime) -> str:
    return (
        f"{start.strftime('%b')} {start.day}, {start.year} - "
        f"{end.strftime('%b')} {end.day}, {end.year}"
    )


def parse_toptracker_date(date_str: str) -> datetime:
    """Parse TopTracker date string, e.g. 'Jun 1, 2026'."""
    return datetime.strptime(date_str, "%b %d, %Y")


def _date_matches(actual: str, expected: datetime) -> bool:
    actual = actual.strip()
    if not actual:
        return False
    for fmt in ("%b %d, %Y", "%b %d, %Y"):
        try:
            if datetime.strptime(actual, fmt).date() == expected.date():
                return True
        except ValueError:
            pass
    for candidate in (
        f"{expected.strftime('%b')} {expected.day}, {expected.year}",
        f"{expected.strftime('%b')} {expected.day:02d}, {expected.year}",
    ):
        if actual == candidate:
            return True
    return False


async def _read_start_date(form) -> str:
    return await form.locator('input[type="text"]').nth(1).input_value()


async def set_start_date(form, page: Page, date_str: str):
    """
    Set the Start Date on the manual time entry form.
    TopTracker defaults to today — React inputs need explicit change events.
    """
    expected = parse_toptracker_date(date_str)
    date_input = form.locator('input[type="text"]').nth(1)

    await date_input.click(click_count=3)
    await date_input.evaluate(
        """(el, value) => {
            el.value = value;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true }));
        }""",
        date_str,
    )
    await page.wait_for_timeout(400)

    if _date_matches(await _read_start_date(form), expected):
        return

    await date_input.click(click_count=3)
    await date_input.fill(date_str)
    await date_input.press("Enter")
    await page.wait_for_timeout(400)

    if _date_matches(await _read_start_date(form), expected):
        return

    # Fallback: pick the day from the calendar widget
    await date_input.click()
    await page.wait_for_timeout(500)

    aria_day = expected.strftime("%B %d, %Y").replace(" 0", " ")  # "June 2, 2026"
    day_pick = page.locator(f'[aria-label*="{aria_day}"], [aria-label*="{expected.strftime("%B")} {expected.day},"]')
    if await day_pick.count():
        await day_pick.first.click()
    else:
        calendar = page.locator('[class*="calendar"], [class*="DayPicker"], [role="dialog"]')
        await calendar.get_by_role("button", name=str(expected.day), exact=True).first.click()

    await page.wait_for_timeout(400)
    if not _date_matches(await _read_start_date(form), expected):
        actual = await _read_start_date(form)
        raise RuntimeError(f"Could not set start date to {date_str} (field shows {actual!r})")


async def open_create_invoice_form(page: Page):
    await navigate_to_invoices(page)
    await page.get_by_role("button", name="Create Invoice").click()
    await page.wait_for_url("**/create-invoice**", timeout=15_000)
    await page.get_by_role("heading", name="Create Invoice").wait_for(timeout=10_000)


async def set_invoice_date_range(page: Page, start_date: datetime, end_date: datetime):
    """Reload the create-invoice form with the desired start/end query params."""
    parsed = urlparse(page.url)
    params = parse_qs(parsed.query)
    params["start"] = [start_date.strftime("%Y-%m-%d")]
    params["end"] = [end_date.strftime("%Y-%m-%d")]
    new_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{urlencode(params, doseq=True)}"
    print(f"→ Setting date range: {format_date_range(start_date, end_date)}")
    await page.goto(new_url, wait_until="domcontentloaded", timeout=30_000)
    await wait_for_app(page)


async def wait_for_save_enabled(page: Page, timeout_ms: int = 20_000) -> bool:
    save_btn = page.get_by_role("button", name="Save Draft & Preview")
    for _ in range(timeout_ms // 500):
        if await save_btn.is_enabled():
            return True
        await page.wait_for_timeout(500)
    return await save_btn.is_enabled()


async def navigate_to_invoices(page: Page):
    await page.goto(ACTIVITIES_URL, wait_until="domcontentloaded", timeout=30_000)
    await wait_for_app(page)
    await dismiss_overlays(page)
    await page.get_by_text("Invoices", exact=True).locator("visible=true").last.click()
    await page.wait_for_url("**/payments/invoices**", timeout=15_000)
    await wait_for_app(page)
    await dismiss_overlays(page)


# ── Step 1: Login ─────────────────────────────────────────────────────────────

async def login(page: Page, email: str, password: str):
    print("\n→ Logging in...")
    await page.goto(f"{BASE_URL}/signin", wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_selector('input[type="email"]', timeout=30_000)

    await page.fill('input[type="email"]', email)
    await page.fill('input[type="password"]', password)
    await page.click('button[type="submit"]')

    await page.wait_for_url("**/app/**", timeout=30_000)
    await wait_for_app(page)
    await dismiss_overlays(page)
    print(f"✓ Logged in ({page.url})")


# ── Step 2: Add Manual Time Entries ──────────────────────────────────────────

async def add_manual_entry(page: Page, entry: dict, project_name: str):
    """
    Fills the 'Add Manual Time Entry' modal for one ClickUp entry.

    entry keys: date, task, description, details, hours, worked_from, worked_to

    details format (matches Sheets script):
        ProjectName
        \\tTaskName
        \\t(description)   # if present
    """
    print(f"  → Adding: {entry['task']} ({entry['hours']}h on {entry['date']})")

    add_btn = page.get_by_role("button", name="Add manual time")
    await add_btn.scroll_into_view_if_needed()
    await add_btn.click()
    await page.locator("#description").wait_for(state="visible", timeout=10_000)

    form = page.locator("form").filter(has=page.locator("#description")).first

    description_text = entry.get("details") or entry["task"]

    try:
        await form.locator("select").last.select_option(label=project_name)
    except Exception:
        pass  # project is often pre-selected to the only project

    await set_start_date(form, page, entry["date"])
    await form.locator("#description").fill(description_text)

    total_minutes = int(round(entry["hours"] * 60))
    duration_str = f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"
    await form.locator("#duration").fill(duration_str, force=True)
    await form.locator("#from").fill(entry.get("worked_from", "09:00"), force=True)
    await form.locator("#to").fill(entry.get("worked_to", "10:00"), force=True)
    await page.wait_for_timeout(300)

    await form.get_by_role("button", name="Submit").click()
    await page.locator("#description").wait_for(state="hidden", timeout=10_000)
    print("  ✓ Entry added")


async def add_all_entries(page: Page, line_items: list[dict], project_name: str):
    """Navigate to My Activities and add all entries."""
    print("\n→ Navigating to My Activities...")
    await page.goto(ACTIVITIES_URL, wait_until="domcontentloaded", timeout=30_000)
    await wait_for_app(page)
    await dismiss_overlays(page)
    await screenshot(page, "activities_page")

    print(f"→ Adding {len(line_items)} time entries...")
    for entry in line_items:
        await add_manual_entry(page, entry, project_name)

    print(f"✓ All {len(line_items)} entries added to Activities")


# ── Step 3: Create Invoice as Draft ──────────────────────────────────────────

async def create_draft_invoice(page: Page, project_name: str, start_date: datetime, end_date: datetime):
    """
    Invoices → Create Invoice → set date range → Save Draft & Preview.
    Does NOT click Send.
    """
    print("\n→ Opening Create Invoice...")
    await open_create_invoice_form(page)
    print("✓ Create Invoice form opened")

    try:
        await page.locator("select").first.select_option(label=project_name)
    except Exception:
        pass

    await set_invoice_date_range(page, start_date, end_date)
    await dismiss_overlays(page)

    if not await wait_for_save_enabled(page):
        await screenshot(page, "invoice_preview")
        raise RuntimeError(
            "No billable hours found for this period. "
            "Add time entries to TopTracker first, then retry."
        )

    await screenshot(page, "invoice_preview")

    save_btn = page.get_by_role("button", name="Save Draft & Preview")
    await save_btn.scroll_into_view_if_needed()
    print("→ Saving as draft (not sending)...")
    await save_btn.click()
    await page.wait_for_url("**/payments/invoices/**", timeout=20_000)
    await page.wait_for_timeout(1_500)

    await screenshot(page, "invoice_draft_saved")
    print(f"✓ Invoice saved as Draft at {page.url}")


# ── Main entry point ──────────────────────────────────────────────────────────

async def run_toptracker_automation(
    config: TopTrackerConfig,
    line_items: list[dict],
    start_date: datetime,
    end_date: datetime,
    *,
    invoice_only: bool = False,
):
    async with async_playwright() as p:
        browser, context = await new_browser_context(p, config.headless)
        page = await context.new_page()

        try:
            await login(page, config.email, config.password)
            if not invoice_only and line_items:
                await add_all_entries(page, line_items, config.project_name)
            await create_draft_invoice(page, config.project_name, start_date, end_date)
            print("\n✅ Done! Invoice saved as Draft in TopTracker (not sent).")

        except PWTimeout as e:
            print(f"\n✗ Timeout — something didn't load in time.")
            await screenshot(page, "error_state")
            raise
        except Exception as e:
            print(f"\n✗ Error: {e}")
            await screenshot(page, "error_state")
            raise
        finally:
            await browser.close()


# ── Quick test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    from dotenv import load_dotenv
    from datetime import timedelta

    load_dotenv(".env.local")

    parser = argparse.ArgumentParser(description="Test TopTracker bot with a dummy entry")
    parser.add_argument("--entry-only", action="store_true", help="Add dummy entry only, skip invoice")
    parser.add_argument("--invoice-only", action="store_true", help="Create draft invoice from existing activities")
    parser.add_argument("--headless", action="store_true", help="Run browser without a window")
    args = parser.parse_args()

    cfg = TopTrackerConfig(
        email=os.environ["TOPTRACKER_EMAIL"],
        password=os.environ["TOPTRACKER_PASSWORD"],
        project_name=os.environ["TOPTRACKER_PROJECT"],
        headless=args.headless,
    )

    today = datetime.now()
    test_items = [
        {
            "date": f"{today.strftime('%b')} {today.day}, {today.year}",
            "task": "Test Task (bot check)",
            "description": "Safe to delete",
            "hours": 0.25,
            "worked_from": "09:00",
            "worked_to": "09:15",
        }
    ]

    async def run_test():
        async with async_playwright() as p:
            browser, context = await new_browser_context(p, cfg.headless)
            page = await context.new_page()
            try:
                end = datetime.now()
                start = end - timedelta(days=7)
                await login(page, cfg.email, cfg.password)
                if args.invoice_only:
                    await create_draft_invoice(page, cfg.project_name, start, end)
                    print("\n✅ Done! Draft invoice saved (not sent).")
                elif args.entry_only:
                    await add_all_entries(page, test_items, cfg.project_name)
                    print("\n✅ Done! Dummy entry added to My Activities (no invoice created).")
                else:
                    await add_all_entries(page, test_items, cfg.project_name)
                    await create_draft_invoice(page, cfg.project_name, start, end)
                    print("\n✅ Done! Dummy entry added and draft invoice saved (not sent).")
            except Exception:
                await screenshot(page, "error_state")
                raise
            finally:
                await browser.close()

    asyncio.run(run_test())
