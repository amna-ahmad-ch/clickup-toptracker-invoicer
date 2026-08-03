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
import re
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
    for fmt in ("%b %d, %Y", "%m-%d-%Y", "%m/%d/%Y"):
        try:
            if datetime.strptime(actual, fmt).date() == expected.date():
                return True
        except ValueError:
            pass
    for candidate in (
        f"{expected.strftime('%b')} {expected.day}, {expected.year}",
        f"{expected.strftime('%b')} {expected.day:02d}, {expected.year}",
        expected.strftime("%m-%d-%Y"),
    ):
        if actual == candidate:
            return True
    return False


def _manual_entry_form(page: Page):
    return page.locator("form").filter(has=page.locator("#description")).first


def _date_input(page: Page):
    return _manual_entry_form(page).locator('input[type="text"]').nth(1)


async def _read_start_date(page: Page) -> str:
    if not await _modal_is_open(page):
        return ""
    form = _manual_entry_form(page)
    await form.get_by_text("Add Manual Time Entry").click()
    await page.wait_for_timeout(200)
    return await _date_input(page).input_value(timeout=5_000)


async def _modal_is_open(page: Page) -> bool:
    return await page.locator("#description").is_visible()


def _parse_month_year(text: str) -> tuple[int, int] | None:
    text = " ".join(text.split())
    match = re.search(r"([A-Za-z]+)\s+(\d{4})", text)
    if not match:
        return None
    month_str, year_str = match.group(1), match.group(2)
    for fmt in ("%B", "%b"):
        try:
            month = datetime.strptime(month_str, fmt).month
            return int(year_str), month
        except ValueError:
            continue
    return None


MONTH_YEAR_HEADER = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}$"
)


async def _calendar_popup(page: Page):
    """Find the open date picker popup inside the manual entry modal."""
    form = _manual_entry_form(page)
    mon = form.get_by_text("MON", exact=True)
    if await mon.count():
        return form.locator("div").filter(has=mon).filter(
            has=form.get_by_text("SUN", exact=True)
        ).last

    picker = form.locator("[class*='DayPicker'], [class*='datepicker'], [class*='DatePicker']")
    if await picker.count():
        return picker.last

    raise RuntimeError("Date picker popup not found — click Start Date first")


async def _read_calendar_month_year(page: Page) -> tuple[int, int] | None:
    form = _manual_entry_form(page)
    for scope in (form, page):
        header = scope.get_by_text(MONTH_YEAR_HEADER)
        if await header.count():
            parsed = _parse_month_year(await header.first.inner_text())
            if parsed:
                return parsed
    popup = await _calendar_popup(page)
    return _parse_month_year(await popup.inner_text())


async def _navigate_calendar_to(page: Page, expected: datetime) -> bool:
    target = (expected.year, expected.month)
    for _ in range(24):
        current = await _read_calendar_month_year(page)
        if current == target:
            return True
        if current is None:
            await page.wait_for_timeout(200)
            continue
        cur_idx = current[0] * 12 + current[1]
        tgt_idx = target[0] * 12 + target[1]
        if not await _calendar_nav(page, forward=cur_idx < tgt_idx):
            break
        await page.wait_for_timeout(250)
    return await _read_calendar_month_year(page) == target


async def _click_calendar_day(page: Page, expected: datetime):
    day_str = str(expected.day)
    labels = [
        expected.strftime("%B %d, %Y").replace(" 0", " "),
        f"{expected.strftime('%B')} {expected.day}, {expected.year}",
        expected.strftime("%A, %B %d, %Y").replace(" 0", " "),
        f"{expected.strftime('%b')} {expected.day}, {expected.year}",
        f"Choose {expected.strftime('%A, %B %d, %Y').replace(' 0', ' ')}",
    ]
    for label in labels:
        pick = page.locator(f'[aria-label*="{label}"]').locator("visible=true")
        if await pick.count():
            await pick.first.click()
            return

    popup = await _calendar_popup(page)
    for selector in (
        '[class*="day"]:not([class*="outside"]):not([class*="disabled"]):not([aria-disabled="true"])',
        '[role="gridcell"]:not([aria-disabled="true"])',
        "td:not(.disabled)",
        "button:not([disabled])",
    ):
        cell = popup.locator(selector).filter(has_text=re.compile(rf"^{day_str}$")).locator("visible=true")
        if await cell.count():
            await cell.first.click()
            return

    # TopTracker uses plain div text for day numbers (not buttons)
    cells = popup.get_by_text(day_str, exact=True)
    for i in range(await cells.count()):
        cell = cells.nth(i)
        outside = await cell.evaluate("""el => !!el.closest(
            '[class*="outside"], [class*="disabled"], [class*="other"], [aria-disabled="true"]'
        )""")
        if not outside:
            await cell.click()
            return

    raise RuntimeError(f"Could not click day {day_str} in calendar for {expected.strftime('%b %d, %Y')}")


async def _calendar_nav(page: Page, forward: bool):
    form = _manual_entry_form(page)
    if forward:
        sel = '[aria-label*="Next"], [aria-label*="next"], button:has-text("›"), button:has-text(">")'
    else:
        sel = '[aria-label*="Previous"], [aria-label*="previous"], button:has-text("‹"), button:has-text("<")'
    btn = form.locator(sel).locator("visible=true")
    if await btn.count():
        await btn.first.click()
        return True
    btn = page.locator(sel).locator("visible=true")
    if await btn.count():
        await btn.first.click()
        return True
    return False


async def _type_start_date(page: Page, date_input, expected: datetime) -> bool:
    """Set date via the text input — TopTracker uses MM-DD-YYYY."""
    iso_value = expected.strftime("%m-%d-%Y")

    await date_input.click(click_count=3)
    await date_input.evaluate(
        """(el, value) => {
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(el, value);
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true }));
        }""",
        iso_value,
    )
    await _dismiss_date_picker(page)
    if await _modal_is_open(page):
        actual = await _read_start_date(page)
        if actual and _date_matches(actual, expected):
            return True

    for fmt in (iso_value, expected.strftime("%b %d, %Y")):
        await date_input.click(click_count=3)
        await date_input.fill("")
        await page.keyboard.type(fmt, delay=30)
        await _dismiss_date_picker(page)
        if not await _modal_is_open(page):
            return False
        actual = await _read_start_date(page)
        if actual and _date_matches(actual, expected):
            return True
    return False


async def _dismiss_date_picker(page: Page):
    await _manual_entry_form(page).get_by_text("Add Manual Time Entry").click()
    await page.wait_for_timeout(300)


async def set_start_date(page: Page, date_str: str):
    """
    Set the Start Date on the manual time entry form.
    TopTracker input uses MM-DD-YYYY; calendar picker is the fallback.
    """
    if not await _modal_is_open(page):
        raise RuntimeError("Manual time entry modal closed unexpectedly")

    expected = parse_toptracker_date(date_str)
    date_input = _date_input(page)

    # Primary: type MM-DD-YYYY (matches the visible input format)
    if await _type_start_date(page, date_input, expected):
        return

    # Fallback: calendar picker
    try:
        await date_input.click()
        await page.wait_for_timeout(500)
        if await _navigate_calendar_to(page, expected):
            await _click_calendar_day(page, expected)
            await page.wait_for_timeout(400)
            await _dismiss_date_picker(page)
            actual = await _read_start_date(page)
            if actual and _date_matches(actual, expected):
                return
    except (RuntimeError, PWTimeout):
        if not await _modal_is_open(page):
            raise RuntimeError(
                f"Modal closed while setting date to {date_str} — entry may have been saved with wrong date"
            ) from None
        await _dismiss_date_picker(page)

    if not await _modal_is_open(page):
        raise RuntimeError(
            f"Modal closed while setting date to {date_str} — entry may have been saved with wrong date"
        )

    actual = await _read_start_date(page)
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

    try:
        await page.wait_for_url("**/app/**", timeout=60_000, wait_until="domcontentloaded")
    except PWTimeout:
        if "/app/" not in page.url:
            raise RuntimeError(
                "Login timed out — check TOPTRACKER_EMAIL and TOPTRACKER_PASSWORD in .env.local"
            ) from None
    await wait_for_app(page)
    await dismiss_overlays(page)
    print(f"✓ Logged in ({page.url})")


# ── Step 2: Add Manual Time Entries ──────────────────────────────────────────

def flatten_description(text: str) -> str:
    """TopTracker description is a single-line input — collapse Sheets-style newlines."""
    parts = [line.strip() for line in text.replace("\t", " ").split("\n") if line.strip()]
    result = " — ".join(parts)
    result = re.sub(r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF]", "", result)
    return re.sub(r"\s+", " ", result).strip()


async def _form_error_text(page: Page) -> str:
    form = _manual_entry_form(page)
    for sel in ('[class*="error"]', '[role="alert"]', '[class*="Error"]'):
        loc = form.locator(sel)
        if await loc.count():
            text = (await loc.first.inner_text()).strip()
            if text:
                return text
    return ""


async def _close_manual_entry_modal(page: Page):
    if not await _modal_is_open(page):
        return
    form = _manual_entry_form(page)
    for sel in ('button[aria-label="Close"]', 'button.close', '[class*="close"]'):
        btn = form.locator(sel)
        if await btn.count():
            await btn.first.click()
            await page.locator("#description").wait_for(state="hidden", timeout=5_000)
            return
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(500)


async def _submit_manual_entry(page: Page):
    form = _manual_entry_form(page)
    submit = form.get_by_role("button", name="Submit")
    await submit.scroll_into_view_if_needed()
    await submit.click()

    try:
        await page.locator("#description").wait_for(state="hidden", timeout=15_000)
        return
    except PWTimeout:
        pass

    if not await page.locator("#description").is_visible():
        return

    error = await _form_error_text(page)
    if error:
        raise RuntimeError(f"TopTracker rejected entry: {error}")

    raise RuntimeError(
        "Submit did not close the manual entry form — "
        "likely overlapping time on the same day (delete existing entries and re-run)"
    )


def _shift_time(time_str: str, offset_minutes: int) -> str:
    hh, mm = map(int, time_str.split(":"))
    total = hh * 60 + mm + offset_minutes
    return f"{total // 60:02d}:{total % 60:02d}"


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

    description_text = flatten_description(entry.get("details") or entry["task"])

    total_minutes = int(round(entry["hours"] * 60))
    duration_str = f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"
    worked_from = entry.get("worked_from", "09:00")
    worked_to = entry.get("worked_to", "10:00")

    last_error = None
    for offset in (0, total_minutes, total_minutes * 2, total_minutes * 3):
        try:
            await dismiss_overlays(page)
            add_btn = page.get_by_role("button", name="Add manual time")
            await add_btn.scroll_into_view_if_needed()
            await add_btn.click()
            await page.locator("#description").wait_for(state="visible", timeout=15_000)

            form = _manual_entry_form(page)

            try:
                await form.locator("select").last.select_option(label=project_name)
            except Exception:
                pass

            await form.locator("#description").fill(description_text)
            await form.locator("#duration").fill(duration_str, force=True)
            from_time = _shift_time(worked_from, offset)
            to_time = _shift_time(worked_to, offset)
            await form.locator("#from").fill(from_time, force=True)
            await form.locator("#to").fill(to_time, force=True)
            await page.wait_for_timeout(300)

            await set_start_date(page, entry["date"])
            confirmed = await _read_start_date(page)
            if offset:
                print(f"    date confirmed: {confirmed}, time {from_time}–{to_time} (offset +{offset}min)")
            else:
                print(f"    date confirmed: {confirmed}")

            await _submit_manual_entry(page)
            print("  ✓ Entry added")
            return
        except RuntimeError as e:
            last_error = e
            await _close_manual_entry_modal(page)
            if offset == total_minutes * 3:
                raise
            print("    ↻ Retrying with later time slot...")

    raise last_error or RuntimeError("Failed to add manual entry")


async def navigate_to_activities(page: Page):
    print("\n→ Navigating to My Activities...")
    await page.goto(ACTIVITIES_URL, wait_until="domcontentloaded", timeout=30_000)
    await wait_for_app(page)
    await dismiss_overlays(page)

    my_activities = page.get_by_text("My activities", exact=True)
    add_btn = page.get_by_role("button", name="Add manual time")
    for _ in range(3):
        if await my_activities.count() and await add_btn.count():
            break
        await page.wait_for_timeout(2_000)
        await dismiss_overlays(page)
        await page.goto(ACTIVITIES_URL, wait_until="domcontentloaded", timeout=30_000)
        await wait_for_app(page)

    if not await add_btn.count():
        await screenshot(page, "activities_page")
        raise RuntimeError(
            "Could not open My Activities page. "
            "Check you are logged in as a freelancer at tracker.toptal.com"
        )

    await add_btn.wait_for(state="visible", timeout=15_000)


async def add_all_entries(page: Page, line_items: list[dict], project_name: str):
    """Navigate to My Activities and add all entries."""
    await navigate_to_activities(page)
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

    test_items = [
        {
            "date": "Jul 28, 2026",
            "task": "Test Task (bot check)",
            "description": "Safe to delete",
            "details": "Test Project\n\tTest Task (bot check)\n\t(Safe to delete)",
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
