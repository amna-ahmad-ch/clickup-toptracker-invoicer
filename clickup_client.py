"""
ClickUp API Client
Pulls tasks and time entries for invoice generation.
Matches exact data structure from your Google Sheets script.
"""

import os
import requests
from datetime import datetime, timedelta
from typing import Optional


CLICKUP_API_BASE = "https://api.clickup.com/api/v2"


def build_activity_details(project_name: str, task_name: str, description: str = "") -> str:
    """Match Sheets script: Project\\n\\tTask\\n\\t(description)"""
    details = f"{project_name}\n\t{task_name}"
    if description:
        details += f"\n\t({description})"
    return details


class ClickUpClient:
    def __init__(self, api_token: str):
        self.headers = {"Authorization": api_token, "Content-Type": "application/json"}

    def get_workspaces(self) -> list:
        """Get all teams/workspaces the user belongs to."""
        r = requests.get(f"{CLICKUP_API_BASE}/team", headers=self.headers)
        r.raise_for_status()
        return r.json().get("teams", [])

    def get_time_entries(
        self,
        team_id: str,
        start_date: datetime,
        end_date: datetime,
        assignee_id: Optional[str] = None,
    ) -> list:
        """
        Pull time entries for a date range.
        Matches your Sheets script exactly:
          - include_location_names=true  → gives folder_name / list_name
          - timestamps in milliseconds
        """
        params = {
            "start_date": int(start_date.timestamp() * 1000),
            "end_date":   int(end_date.timestamp() * 1000),
            "include_location_names": "true",
        }
        if assignee_id:
            params["assignee"] = assignee_id

        r = requests.get(
            f"{CLICKUP_API_BASE}/team/{team_id}/time_entries",
            headers=self.headers,
            params=params,
        )
        r.raise_for_status()
        return r.json().get("data", [])

    def build_invoice_line_items(
        self,
        team_id: str,
        start_date: datetime,
        end_date: datetime,
        hourly_rate: float,
        assignee_id: Optional[str] = None,
    ) -> list[dict]:
        """
        Transform ClickUp time entries into invoice line items.

        Mirrors your Sheets script logic:
          - project  = task_location.folder_name  OR  task_location.list_name
          - task     = task.name
          - details  = "ProjectName\\n\\tTaskName\\n\\t(description)"
          - hours    = duration ms → hours, rounded to 2dp

        Each entry becomes its own line item (no grouping),
        matching how your Sheets script fills rows.

        Returns list of dicts:
            { date, project, task, description, details, hours, rate, amount }
        """
        entries = self.get_time_entries(team_id, start_date, end_date, assignee_id)

        line_items = []
        for entry in entries:
            # ── Mirrors your Sheets script exactly ──────────────────────────
            duration_ms  = int(entry.get("duration", 0))
            hours        = round(duration_ms / (1000 * 60 * 60), 2)

            date         = datetime.fromtimestamp(int(entry.get("start", 0)) / 1000)

            task_name    = entry.get("task", {}).get("name", "No task")

            location     = entry.get("task_location", {})
            project_name = (
                location.get("folder_name")
                or location.get("list_name")
                or "No Project"
            )

            description  = entry.get("description", "")

            details = build_activity_details(project_name, task_name, description)

            line_items.append({
                "date":        date.strftime("%d %b %Y"),
                "project":     project_name,
                "task":        task_name,
                "description": description,
                "details":     details,       # full formatted string for TopTracker
                "hours":       hours,
                "rate":        hourly_rate,
                "amount":      round(hours * hourly_rate, 2),
            })

        return line_items


# ── Quick test ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(".env.local")

    client = ClickUpClient(os.environ["CLICKUP_API_TOKEN"])
    workspaces = client.get_workspaces()
    print("Workspaces:", [w["name"] for w in workspaces])

    end   = datetime.now()
    start = end - timedelta(days=7)
    team_id = os.environ.get("CLICKUP_TEAM_ID") or workspaces[0]["id"]

    items = client.build_invoice_line_items(
        team_id=team_id,
        start_date=start,
        end_date=end,
        hourly_rate=float(os.environ.get("HOURLY_RATE", 50)),
    )

    print(f"\n{len(items)} entries found:\n")
    for item in items:
        print(f"  [{item['date']}] {item['project']} → {item['task']}")
        print(f"           {item['hours']}h × ${item['rate']} = ${item['amount']}")
        if item['description']:
            print(f"           Note: {item['description']}")
        print()
