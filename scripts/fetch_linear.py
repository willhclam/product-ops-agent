"""
Fetch Linear sprint data for all pods (Accounting, AP, Cards, Megapod, Wallet).
Outputs linear_data.json with current + next sprint issues.
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

LINEAR_API_KEY = os.environ.get("LINEAR_API_KEY", "")

# Pod name -> team key mapping
POD_TEAMS = {
    "Megapod":    {"id": "3a1e40bd-511d-4a1f-8a43-ccb18c206aa6", "key": "MP"},
    "Accounting": {"id": "42b3b13f-0924-478e-99f3-74eb2d542c67", "key": "ACC"},
    "AP":         {"id": "a83ddef5-7178-4670-bf25-f75b5a6deceb", "key": "PAY"},
    "Cards":      {"id": "048e3e5a-5e37-4740-b78f-9acd81bc06f7", "key": "CARDS"},
    "Wallet":     {"id": "79a3bb37-a198-4434-b679-68af0907b353", "key": "WAL"},
}

SPRINT_START = datetime(2026, 2, 11, tzinfo=timezone.utc)
SPRINT_END   = datetime(2026, 2, 24, tzinfo=timezone.utc)


def query_linear(query: str) -> dict:
    data = json.dumps({"query": query}).encode()
    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=data,
        headers={
            "Authorization": LINEAR_API_KEY,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def find_sprint_cycles(team_id: str, sprint_start: datetime, sprint_end: datetime):
    """Find current and next sprint cycles for a team."""
    gql = """
    {{
      cycles(filter: {{
        team: {{ id: {{ eq: "{tid}" }} }}
      }}, first: 20) {{
        nodes {{
          id name number startsAt endsAt progress
          completedScopeHistory scopeHistory
        }}
      }}
    }}
    """.format(tid=team_id)
    result = query_linear(gql)
    cycles = result["data"]["cycles"]["nodes"]

    current = None
    next_sprint = None

    # Sort by start date ascending so we can find ordered pairs
    sorted_cycles = sorted(cycles, key=lambda x: x["startsAt"])

    for i, c in enumerate(sorted_cycles):
        start = datetime.fromisoformat(c["startsAt"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(c["endsAt"].replace("Z", "+00:00"))

        # Match the configured sprint window (allow ±2 day tolerance)
        if abs((start - sprint_start).days) <= 2 and abs((end - sprint_end).days) <= 2:
            current = c
            # Next sprint is the one immediately after
            if i + 1 < len(sorted_cycles):
                next_sprint = sorted_cycles[i + 1]
            break

    # Fallback: pick most recently started cycle if no match
    if current is None:
        now = datetime.now(timezone.utc)
        active = [
            c for c in sorted_cycles
            if datetime.fromisoformat(c["startsAt"].replace("Z", "+00:00")) <= now
            <= datetime.fromisoformat(c["endsAt"].replace("Z", "+00:00"))
        ]
        if active:
            current = active[-1]  # latest active
            idx = sorted_cycles.index(current)
            if idx + 1 < len(sorted_cycles):
                next_sprint = sorted_cycles[idx + 1]

    return current, next_sprint


def fetch_cycle_issues(cycle_id: str) -> list:
    """Fetch all issues in a cycle with full details."""
    gql = """
    {{
      cycle(id: "{cid}") {{
        issues(first: 250) {{
          nodes {{
            id title identifier
            state {{ name type }}
            assignee {{ name email }}
            estimate
            priority
            updatedAt startedAt completedAt createdAt
            labels {{ nodes {{ name }} }}
            url
          }}
        }}
      }}
    }}
    """.format(cid=cycle_id)
    result = query_linear(gql)
    return result["data"]["cycle"]["issues"]["nodes"]


def fetch_team_backlog(team_id: str) -> list:
    """Fetch unstarted/unassigned backlog issues for sprint planning."""
    gql = """
    {{
      issues(
        filter: {{
          team: {{ id: {{ eq: "{tid}" }} }}
          state: {{ type: {{ in: ["backlog", "unstarted"] }} }}
          cycle: {{ null: true }}
        }}
        first: 100
        orderBy: priority
      ) {{
        nodes {{
          id title identifier
          state {{ name type }}
          assignee {{ name }}
          estimate
          priority
          updatedAt
          labels {{ nodes {{ name }} }}
          url
        }}
      }}
    }}
    """.format(tid=team_id)
    result = query_linear(gql)
    return result["data"]["issues"]["nodes"]


def calculate_burndown(cycle: dict) -> list:
    """Generate burndown data from scope/completed history.

    The ideal line is projected over the full sprint duration (start → end),
    not just the data points recorded so far.
    """
    scope_hist = cycle.get("scopeHistory", []) or []
    done_hist  = cycle.get("completedScopeHistory", []) or []

    if not scope_hist:
        return []

    sprint_start = datetime.fromisoformat(cycle["startsAt"].replace("Z", "+00:00"))
    sprint_end   = datetime.fromisoformat(cycle["endsAt"].replace("Z", "+00:00"))
    sprint_days  = max((sprint_end - sprint_start).days, 1)

    initial_scope = scope_hist[0]
    burndown = []

    for i, (scope, done) in enumerate(zip(scope_hist, done_hist)):
        day = sprint_start + timedelta(days=i)
        remaining = scope - done
        ideal = max(0.0, initial_scope - (initial_scope / sprint_days) * i)
        burndown.append({
            "day": day.strftime("%b %d"),
            "remaining": max(0, remaining),
            "ideal": round(ideal, 1),
        })
    return burndown


def main():
    if not LINEAR_API_KEY:
        print("ERROR: LINEAR_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc)
    output = {
        "generated_at": now.isoformat(),
        "sprint": {
            "start": SPRINT_START.strftime("%Y-%m-%d"),
            "end":   SPRINT_END.strftime("%Y-%m-%d"),
            "label": "Feb 11 – Feb 24, 2026",
        },
        "pods": {},
    }

    for pod_name, team_info in POD_TEAMS.items():
        print(f"Fetching {pod_name}...", flush=True)
        team_id = team_info["id"]

        current_cycle, next_cycle = find_sprint_cycles(team_id, SPRINT_START, SPRINT_END)

        pod_data = {"current": None, "next": None, "backlog": []}

        if current_cycle:
            issues = fetch_cycle_issues(current_cycle["id"])
            pod_data["current"] = {
                "cycle": current_cycle,
                "issues": issues,
                "burndown": calculate_burndown(current_cycle),
            }
            print(f"  Current: {len(issues)} issues", flush=True)
        else:
            print(f"  WARNING: No current cycle found for {pod_name}", flush=True)

        if next_cycle:
            issues = fetch_cycle_issues(next_cycle["id"])
            pod_data["next"] = {
                "cycle": next_cycle,
                "issues": issues,
            }
            print(f"  Next: {len(issues)} issues", flush=True)
        else:
            print(f"  No next cycle found for {pod_name}", flush=True)

        output["pods"][pod_name] = pod_data

    out_path = os.path.join(os.path.dirname(__file__), "..", "public", "data", "linear_data.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nSaved to {out_path}", flush=True)


if __name__ == "__main__":
    main()
