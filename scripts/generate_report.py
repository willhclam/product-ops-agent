"""
Generate the HTML report from Linear + Sheets data.
Reads: public/data/linear_data.json, public/data/sheets_data.json
Writes: public/index.html
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

SCRIPT_DIR  = os.path.dirname(__file__)
PUBLIC_DIR  = os.path.join(SCRIPT_DIR, "..", "public")
DATA_DIR    = os.path.join(PUBLIC_DIR, "data")
OUTPUT_FILE = os.path.join(PUBLIC_DIR, "index.html")

POD_COLORS = {
    "Megapod":    "#6366f1",
    "Accounting": "#f59e0b",
    "AP":         "#10b981",
    "Cards":      "#3b82f6",
    "Wallet":     "#ec4899",
}

COMPLETED_TYPES = {"completed", "done"}
INPROGRESS_TYPES = {"started"}
BLOCKED_LABEL   = "blocked"


def load_json(path: str) -> dict:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def is_stale(issue: dict, days: int = 3) -> bool:
    """True if In Progress but no update in `days` days."""
    state_type = issue.get("state", {}).get("type", "")
    if state_type not in INPROGRESS_TYPES:
        return False
    updated = issue.get("updatedAt", "")
    if not updated:
        return False
    try:
        dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days > days
    except Exception:
        return False


def is_blocked(issue: dict) -> bool:
    labels = [l["name"].lower() for l in issue.get("labels", {}).get("nodes", [])]
    return BLOCKED_LABEL in labels


def aggregate_pod_stats(pod_name: str, pod_data: dict) -> dict:
    current = pod_data.get("current") or {}
    issues = current.get("issues", [])

    total = len(issues)
    completed = sum(1 for i in issues if i["state"]["type"] in COMPLETED_TYPES)
    in_prog   = sum(1 for i in issues if i["state"]["type"] in INPROGRESS_TYPES)
    canceled  = sum(1 for i in issues if i["state"]["type"] == "cancelled")

    total_pts    = sum(i.get("estimate") or 0 for i in issues)
    done_pts     = sum((i.get("estimate") or 0) for i in issues if i["state"]["type"] in COMPLETED_TYPES)

    blocked_issues = [i for i in issues if is_blocked(i)]
    stale_issues   = [i for i in issues if is_stale(i)]

    # Per-engineer breakdown
    engineers = {}
    for i in issues:
        a = (i.get("assignee") or {}).get("name", "Unassigned")
        if a not in engineers:
            engineers[a] = {"total": 0, "completed": 0, "in_progress": 0, "points": 0, "done_points": 0}
        engineers[a]["total"] += 1
        engineers[a]["points"] += i.get("estimate") or 0
        if i["state"]["type"] in COMPLETED_TYPES:
            engineers[a]["completed"] += 1
            engineers[a]["done_points"] += i.get("estimate") or 0
        elif i["state"]["type"] in INPROGRESS_TYPES:
            engineers[a]["in_progress"] += 1

    cycle = current.get("cycle", {})
    burndown = current.get("burndown", [])

    return {
        "pod":           pod_name,
        "color":         POD_COLORS.get(pod_name, "#94a3b8"),
        "cycle_name":    cycle.get("name") or f'Cycle {cycle.get("number", "?")}',
        "total":         total,
        "completed":     completed,
        "in_progress":   in_prog,
        "canceled":      canceled,
        "total_pts":     total_pts,
        "done_pts":      done_pts,
        "pct_tickets":   round(100 * completed / total, 1) if total else 0,
        "pct_points":    round(100 * done_pts / total_pts, 1) if total_pts else 0,
        "engineers":     engineers,
        "blocked":       blocked_issues,
        "stale":         stale_issues,
        "burndown":      burndown,
        "issues":        issues,
    }


def aggregate_next_sprint(pod_name: str, pod_data: dict) -> dict:
    next_data = pod_data.get("next") or {}
    issues = next_data.get("issues", [])

    total_pts = sum(i.get("estimate") or 0 for i in issues)
    engineers = {}
    for i in issues:
        a = (i.get("assignee") or {}).get("name", "Unassigned")
        if a not in engineers:
            engineers[a] = {"total": 0, "points": 0}
        engineers[a]["total"] += 1
        engineers[a]["points"] += i.get("estimate") or 0

    # Sort by story points (Pareto)
    sorted_issues = sorted(issues, key=lambda x: x.get("estimate") or 0, reverse=True)

    return {
        "pod":         pod_name,
        "color":       POD_COLORS.get(pod_name, "#94a3b8"),
        "total":       len(issues),
        "total_pts":   total_pts,
        "engineers":   engineers,
        "issues":      sorted_issues,
        "cycle":       next_data.get("cycle", {}),
    }


def generate_html(linear: dict, sheets: dict) -> str:
    now_str = datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")
    sprint_label = linear.get("sprint", {}).get("label", "Feb 11 – Feb 24, 2026")

    pods_raw = linear.get("pods", {})

    # Current sprint stats
    current_stats = []
    for pod_name, pod_data in pods_raw.items():
        if pod_data.get("current"):
            current_stats.append(aggregate_pod_stats(pod_name, pod_data))

    # Next sprint stats
    next_stats = []
    for pod_name, pod_data in pods_raw.items():
        if pod_data.get("next") and pod_data["next"].get("issues"):
            next_stats.append(aggregate_next_sprint(pod_name, pod_data))

    # Overall totals
    overall_total   = sum(s["total"] for s in current_stats)
    overall_done    = sum(s["completed"] for s in current_stats)
    overall_pts     = sum(s["total_pts"] for s in current_stats)
    overall_done_pts = sum(s["done_pts"] for s in current_stats)
    overall_pct_t   = round(100 * overall_done / overall_total, 1) if overall_total else 0
    overall_pct_p   = round(100 * overall_done_pts / overall_pts, 1) if overall_pts else 0

    all_blocked = []
    all_stale   = []
    for s in current_stats:
        for b in s["blocked"]:
            all_blocked.append({**b, "_pod": s["pod"]})
        for st in s["stale"]:
            all_stale.append({**st, "_pod": s["pod"]})

    # Initiatives from sheets
    initiatives  = sheets.get("initiatives", [])
    ready_init   = sheets.get("ready_for_eng", [])
    waiting_init = sheets.get("waiting_info", [])
    by_stage     = sheets.get("by_stage", {})
    sheets_error = sheets.get("access_error", True)

    # Chart data serialization
    def jd(obj):
        return json.dumps(obj, ensure_ascii=False)

    # Build burndown data across all pods (combined)
    burndown_datasets = []
    for s in current_stats:
        bd = s.get("burndown", [])
        if bd:
            burndown_datasets.append({
                "label": s["pod"],
                "data":  [d["remaining"] for d in bd],
                "borderColor": s["color"],
                "backgroundColor": s["color"] + "22",
                "tension": 0.3,
                "fill": False,
            })

    burndown_labels = []
    if current_stats and current_stats[0].get("burndown"):
        burndown_labels = [d["day"] for d in current_stats[0]["burndown"]]

    # Per-pod engineer points (next sprint)
    next_eng_data = {}
    for ns in next_stats:
        for eng, info in ns["engineers"].items():
            if eng not in next_eng_data:
                next_eng_data[eng] = {}
            next_eng_data[eng][ns["pod"]] = info["points"]

    all_engineers = sorted(set(
        e for ns in next_stats for e in ns["engineers"].keys()
        if e != "Unassigned"
    ))

    # Pareto data: all next-sprint issues sorted by points
    pareto_issues = []
    for ns in next_stats:
        for i in ns["issues"]:
            pareto_issues.append({
                "label": i["identifier"],
                "title": i["title"][:50],
                "pts": i.get("estimate") or 0,
                "pod": ns["pod"],
                "color": ns["color"],
            })
    pareto_issues.sort(key=lambda x: x["pts"], reverse=True)
    pareto_top = pareto_issues[:25]  # Top 25 heaviest

    # Cumulative percentage for Pareto line
    total_pareto_pts = sum(p["pts"] for p in pareto_top) or 1
    cumulative = 0
    pareto_cumulative = []
    for p in pareto_top:
        cumulative += p["pts"]
        pareto_cumulative.append(round(100 * cumulative / total_pareto_pts, 1))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Product Ops Report — {sprint_label}</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js"></script>
  <style>
    :root {{
      --bg: #0f172a;
      --surface: #1e293b;
      --surface2: #273449;
      --border: #334155;
      --text: #e2e8f0;
      --muted: #94a3b8;
      --accent: #6366f1;
      --green: #10b981;
      --yellow: #f59e0b;
      --red: #ef4444;
      --blue: #3b82f6;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      padding: 0 0 60px 0;
    }}
    header {{
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 20px 32px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      position: sticky; top: 0; z-index: 100;
    }}
    header h1 {{ font-size: 1.25rem; font-weight: 700; color: var(--text); }}
    header .meta {{ font-size: 0.8rem; color: var(--muted); text-align: right; }}
    .badge {{
      display: inline-block;
      padding: 2px 10px;
      border-radius: 999px;
      font-size: 0.72rem;
      font-weight: 600;
      background: var(--accent);
      color: #fff;
      margin-left: 8px;
    }}
    .container {{ max-width: 1400px; margin: 0 auto; padding: 28px 24px; }}
    .section-header {{
      font-size: 1.4rem; font-weight: 700;
      border-left: 4px solid var(--accent);
      padding-left: 14px;
      margin: 36px 0 20px 0;
    }}
    .section-header small {{ font-size: 0.85rem; color: var(--muted); font-weight: 400; margin-left: 10px; }}
    .grid-2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 18px; }}
    .grid-3 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; }}
    .grid-5 {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; }}
    .card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 20px;
    }}
    .card-title {{
      font-size: 0.8rem; font-weight: 600; text-transform: uppercase;
      letter-spacing: 0.06em; color: var(--muted); margin-bottom: 10px;
    }}
    .card-value {{ font-size: 2rem; font-weight: 800; color: var(--text); }}
    .card-sub {{ font-size: 0.8rem; color: var(--muted); margin-top: 4px; }}
    .progress-bar {{
      height: 8px; background: var(--border); border-radius: 4px;
      margin-top: 10px; overflow: hidden;
    }}
    .progress-fill {{
      height: 100%; border-radius: 4px;
      background: linear-gradient(90deg, var(--accent), var(--blue));
      transition: width 0.6s ease;
    }}
    .chart-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 22px;
    }}
    .chart-card h3 {{
      font-size: 1rem; font-weight: 600; margin-bottom: 16px; color: var(--text);
    }}
    .chart-wrap {{ position: relative; height: 280px; }}
    .chart-wrap-tall {{ position: relative; height: 360px; }}
    .pod-grid {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 18px;
    }}
    .pod-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      overflow: hidden;
    }}
    .pod-header {{
      padding: 14px 18px;
      display: flex; align-items: center; justify-content: space-between;
      border-bottom: 1px solid var(--border);
    }}
    .pod-name {{
      font-weight: 700; font-size: 1rem;
      display: flex; align-items: center; gap: 10px;
    }}
    .pod-dot {{
      width: 12px; height: 12px; border-radius: 50%;
      flex-shrink: 0;
    }}
    .pod-pct {{ font-size: 1.4rem; font-weight: 800; }}
    .pod-body {{ padding: 16px 18px; }}
    .stat-row {{
      display: flex; justify-content: space-between;
      font-size: 0.82rem; color: var(--muted);
      margin-bottom: 6px;
    }}
    .stat-row strong {{ color: var(--text); font-weight: 600; }}
    .mini-progress {{ height: 5px; background: var(--border); border-radius: 3px; margin: 6px 0 12px; overflow: hidden; }}
    .mini-fill {{ height: 100%; border-radius: 3px; }}
    table {{
      width: 100%; border-collapse: collapse;
      font-size: 0.82rem;
    }}
    th {{
      text-align: left; padding: 8px 10px;
      background: var(--surface2);
      color: var(--muted);
      font-weight: 600; font-size: 0.75rem;
      text-transform: uppercase; letter-spacing: 0.05em;
      border-bottom: 1px solid var(--border);
    }}
    td {{
      padding: 9px 10px;
      border-bottom: 1px solid var(--border);
      color: var(--text);
      vertical-align: top;
    }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: var(--surface2); }}
    .tag {{
      display: inline-block;
      padding: 2px 8px; border-radius: 6px;
      font-size: 0.72rem; font-weight: 600;
    }}
    .tag-red    {{ background: #991b1b33; color: #fca5a5; }}
    .tag-yellow {{ background: #78350f33; color: #fcd34d; }}
    .tag-green  {{ background: #14532d33; color: #6ee7b7; }}
    .tag-blue   {{ background: #1e3a5f33; color: #93c5fd; }}
    .tag-gray   {{ background: #33415533; color: var(--muted); }}
    .tag-purple {{ background: #3730a333; color: #a5b4fc; }}
    .alert-block {{
      background: #991b1b22;
      border: 1px solid #991b1b66;
      border-radius: 8px; padding: 12px 16px;
      margin-top: 12px;
    }}
    .alert-block h4 {{ font-size: 0.85rem; color: #fca5a5; margin-bottom: 8px; }}
    .alert-item {{
      font-size: 0.8rem; color: var(--muted);
      padding: 4px 0;
      border-bottom: 1px solid #ffffff10;
    }}
    .alert-item:last-child {{ border-bottom: none; }}
    .alert-item strong {{ color: var(--text); }}
    .warn-block {{
      background: #78350f22;
      border: 1px solid #78350f66;
      border-radius: 8px; padding: 12px 16px;
      margin-top: 12px;
    }}
    .warn-block h4 {{ font-size: 0.85rem; color: #fcd34d; margin-bottom: 8px; }}
    .sheets-notice {{
      background: #78350f22; border: 1px solid #78350f66;
      border-radius: 10px; padding: 16px 20px; margin: 16px 0;
      font-size: 0.85rem; color: #fcd34d;
    }}
    .sheets-notice code {{
      background: #ffffff15; padding: 2px 6px; border-radius: 4px;
      font-family: monospace; font-size: 0.8rem;
    }}
    .initiative-stage {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      margin-bottom: 14px;
      overflow: hidden;
    }}
    .initiative-stage-header {{
      padding: 12px 16px;
      background: var(--surface2);
      display: flex; justify-content: space-between; align-items: center;
      cursor: pointer; user-select: none;
    }}
    .initiative-stage-header h4 {{ font-size: 0.88rem; font-weight: 600; }}
    .count-badge {{
      background: var(--accent);
      color: #fff;
      border-radius: 999px;
      padding: 2px 9px;
      font-size: 0.72rem;
      font-weight: 700;
    }}
    .tabs {{
      display: flex; gap: 6px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 24px;
    }}
    .tab-btn {{
      padding: 10px 20px;
      background: none; border: none; cursor: pointer;
      color: var(--muted); font-size: 0.88rem; font-weight: 600;
      border-bottom: 3px solid transparent;
      transition: all 0.15s;
    }}
    .tab-btn.active {{
      color: var(--accent);
      border-bottom-color: var(--accent);
    }}
    .tab-pane {{ display: none; }}
    .tab-pane.active {{ display: block; }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    @media (max-width: 768px) {{
      .grid-5 {{ grid-template-columns: repeat(2, 1fr); }}
      header {{ padding: 14px 16px; flex-direction: column; gap: 8px; align-items: flex-start; }}
    }}
  </style>
</head>
<body>
<header>
  <div>
    <h1>⚡ Product Ops Report
      <span class="badge">LIVE</span>
    </h1>
    <div style="font-size:0.82rem;color:var(--muted);margin-top:4px;">Sprint: {sprint_label}</div>
  </div>
  <div class="meta">
    <div>Updated: {now_str}</div>
    <div style="margin-top:3px;"><a href="#section1">Sprint Progress</a> · <a href="#section2">Sprint Planning</a></div>
  </div>
</header>

<div class="container">

<!-- ============================================================
     SECTION 1: SPRINT PROGRESS
     ============================================================ -->
<div id="section1" class="section-header">
  Section 1 — Sprint Progress
  <small>{sprint_label}</small>
</div>

<!-- Overall KPIs -->
<div class="grid-5" style="margin-bottom:20px;">
  <div class="card">
    <div class="card-title">Tickets Done</div>
    <div class="card-value">{overall_done}<span style="font-size:1rem;color:var(--muted);">/{overall_total}</span></div>
    <div class="progress-bar"><div class="progress-fill" style="width:{overall_pct_t}%"></div></div>
    <div class="card-sub">{overall_pct_t}% completion</div>
  </div>
  <div class="card">
    <div class="card-title">Points Burned</div>
    <div class="card-value">{overall_done_pts}<span style="font-size:1rem;color:var(--muted);">/{overall_pts}</span></div>
    <div class="progress-bar"><div class="progress-fill" style="width:{overall_pct_p}%"></div></div>
    <div class="card-sub">{overall_pct_p}% of story points</div>
  </div>
  <div class="card">
    <div class="card-title">Active Pods</div>
    <div class="card-value">{len(current_stats)}</div>
    <div class="card-sub">Accounting · AP · Cards · Megapod · Wallet</div>
  </div>
  <div class="card">
    <div class="card-title">Blocked Issues</div>
    <div class="card-value" style="color:var(--red);">{len(all_blocked)}</div>
    <div class="card-sub">Needs immediate attention</div>
  </div>
  <div class="card">
    <div class="card-title">Stale (3+ days)</div>
    <div class="card-value" style="color:var(--yellow);">{len(all_stale)}</div>
    <div class="card-sub">In-progress, no recent updates</div>
  </div>
</div>

<!-- Burndown Chart -->
<div class="chart-card" style="margin-bottom:20px;">
  <h3>📉 Burndown — Remaining Story Points by Day</h3>
  <div class="chart-wrap">
    <canvas id="burndownChart"></canvas>
  </div>
</div>

<!-- Per-Pod Progress -->
<div class="section-header" style="font-size:1.1rem;margin-top:28px;">Per-Pod Progress</div>
<div class="pod-grid">
"""

    for s in current_stats:
        color = s["color"]
        pct_t = s["pct_tickets"]
        pct_p = s["pct_points"]
        html += f"""
  <div class="pod-card">
    <div class="pod-header">
      <div class="pod-name">
        <div class="pod-dot" style="background:{color};"></div>
        {s["pod"]}
        <span style="font-size:0.75rem;color:var(--muted);font-weight:400;">{s["cycle_name"]}</span>
      </div>
      <div class="pod-pct" style="color:{color};">{pct_t}%</div>
    </div>
    <div class="pod-body">
      <div class="stat-row">
        <span>Tickets</span>
        <strong>{s["completed"]}/{s["total"]} done &nbsp;·&nbsp; {s["in_progress"]} in-progress</strong>
      </div>
      <div class="mini-progress">
        <div class="mini-fill" style="width:{pct_t}%;background:{color};"></div>
      </div>
      <div class="stat-row">
        <span>Story Points</span>
        <strong>{s["done_pts"]}/{s["total_pts"]} burned ({pct_p}%)</strong>
      </div>
      <div class="mini-progress">
        <div class="mini-fill" style="width:{pct_p}%;background:{color}88;"></div>
      </div>
"""
        if s["blocked"]:
            html += f"""
      <div class="alert-block">
        <h4>🚫 {len(s["blocked"])} Blocked</h4>
"""
            for b in s["blocked"][:3]:
                html += f"""        <div class="alert-item"><strong>{b["identifier"]}</strong> — {b["title"][:60]}</div>\n"""
            if len(s["blocked"]) > 3:
                html += f"""        <div class="alert-item" style="color:var(--muted);">+{len(s["blocked"])-3} more...</div>\n"""
            html += "      </div>\n"

        if s["stale"]:
            html += f"""
      <div class="warn-block">
        <h4>⏳ {len(s["stale"])} Stale (3+ days no update)</h4>
"""
            for st in s["stale"][:3]:
                assignee = (st.get("assignee") or {}).get("name", "Unassigned")
                html += f"""        <div class="alert-item"><strong>{st["identifier"]}</strong> — {st["title"][:50]} <span style="color:var(--muted);">({assignee})</span></div>\n"""
            if len(s["stale"]) > 3:
                html += f"""        <div class="alert-item" style="color:var(--muted);">+{len(s["stale"])-3} more...</div>\n"""
            html += "      </div>\n"

        html += "    </div>\n  </div>\n"

    html += "</div>\n"

    # Per-Pod completion chart
    html += """
<div class="grid-2" style="margin-top:20px;">
  <div class="chart-card">
    <h3>📊 Tickets: Completed vs. Total per Pod</h3>
    <div class="chart-wrap">
      <canvas id="podTicketsChart"></canvas>
    </div>
  </div>
  <div class="chart-card">
    <h3>🔥 Story Points: Burned vs. Total per Pod</h3>
    <div class="chart-wrap">
      <canvas id="podPointsChart"></canvas>
    </div>
  </div>
</div>
"""

    # Blocked / Stale detail tables
    if all_blocked or all_stale:
        html += """
<div class="section-header" style="font-size:1.05rem;margin-top:28px;">⚠️ Attention Required</div>
<div class="grid-2">
"""
        if all_blocked:
            html += """
  <div class="chart-card">
    <h3>🚫 Blocked Issues</h3>
    <table>
      <thead><tr><th>ID</th><th>Title</th><th>Pod</th><th>Assignee</th></tr></thead>
      <tbody>
"""
            for b in all_blocked:
                assignee = (b.get("assignee") or {}).get("name", "—")
                url = b.get("url", "#")
                html += f"""        <tr>
          <td><a href="{url}" target="_blank">{b["identifier"]}</a></td>
          <td>{b["title"][:60]}</td>
          <td><span class="tag tag-blue">{b["_pod"]}</span></td>
          <td>{assignee}</td>
        </tr>\n"""
            html += "      </tbody></table></div>\n"

        if all_stale:
            html += """
  <div class="chart-card">
    <h3>⏳ Stale In-Progress (3+ days)</h3>
    <table>
      <thead><tr><th>ID</th><th>Title</th><th>Pod</th><th>Last Update</th></tr></thead>
      <tbody>
"""
            for st in all_stale:
                upd = st.get("updatedAt", "")[:10]
                url = st.get("url", "#")
                html += f"""        <tr>
          <td><a href="{url}" target="_blank">{st["identifier"]}</a></td>
          <td>{st["title"][:60]}</td>
          <td><span class="tag tag-yellow">{st["_pod"]}</span></td>
          <td>{upd}</td>
        </tr>\n"""
            html += "      </tbody></table></div>\n"

        html += "</div>\n"

    # ============================================================
    # SECTION 2: SPRINT PLANNING
    # ============================================================
    html += f"""
<div id="section2" class="section-header" style="margin-top:44px;">
  Section 2 — Sprint Planning (Upcoming Sprint)
  <small>Feb 25 – Mar 10, 2026</small>
</div>
"""

    if not next_stats:
        html += """<div class="card" style="color:var(--muted);text-align:center;padding:40px;">
  No upcoming sprint data available yet. Tickets may not have been added to the next cycle.
</div>"""
    else:
        next_total   = sum(ns["total"] for ns in next_stats)
        next_total_p = sum(ns["total_pts"] for ns in next_stats)

        html += f"""
<div class="grid-3" style="margin-bottom:20px;">
  <div class="card">
    <div class="card-title">Next Sprint Tickets</div>
    <div class="card-value">{next_total}</div>
    <div class="card-sub">Across {len(next_stats)} pods</div>
  </div>
  <div class="card">
    <div class="card-title">Next Sprint Story Points</div>
    <div class="card-value">{next_total_p}</div>
    <div class="card-sub">Total planned capacity</div>
  </div>
  <div class="card">
    <div class="card-title">Heaviest Ticket</div>
    <div class="card-value">{pareto_top[0]["pts"] if pareto_top else 0} pts</div>
    <div class="card-sub">{pareto_top[0]["title"] if pareto_top else "—"}</div>
  </div>
</div>

<div class="grid-2">
  <div class="chart-card">
    <h3>📋 Ticket Count per Pod (Next Sprint)</h3>
    <div class="chart-wrap"><canvas id="nextPodChart"></canvas></div>
  </div>
  <div class="chart-card">
    <h3>👤 Story Points per Engineer (Next Sprint)</h3>
    <div class="chart-wrap-tall"><canvas id="engPointsChart"></canvas></div>
  </div>
</div>

<div class="chart-card" style="margin-top:18px;">
  <h3>📈 Pareto Chart — Tickets Ranked by Story Points (Top 25)</h3>
  <div class="chart-wrap-tall"><canvas id="paretoChart"></canvas></div>
</div>
"""

    # Initiatives Section
    html += """
<div class="section-header" style="font-size:1.05rem;margin-top:36px;">
  🗂️ Initiatives Not Yet Ready for Engineering
</div>
"""

    if sheets_error:
        html += """
<div class="sheets-notice">
  <strong>⚠️ Google Sheet Access Required</strong><br/>
  The Google Sheet is not yet accessible. To fix this:<br/>
  1. Share the Google Sheet with the service account: <code>product-ops-agent@product-ops-agent.iam.gserviceaccount.com</code> (Viewer access)<br/>
  2. Or set the sheet to "Anyone with the link can view" and ensure GOOGLE_API_KEY has access<br/>
  3. Re-run the daily pipeline after granting access.<br/><br/>
  The rest of the report (Linear data) is fully functional.
</div>
"""
    else:
        if not by_stage:
            html += "<div class='card' style='color:var(--muted);'>No initiative data found in sheet.</div>"
        else:
            readiness_order = [
                "Ready for Engineering (Not Yet Picked Up)",
                "Waiting More Info (Additional Work Needed)",
                "Business Intent Not Defined",
                "PRD In Discussion",
                "Design Pending",
                "Vendor Selection In Progress",
                "Legal / Compliance Review",
                "Unknown / Not Set",
            ]
            tag_map = {
                "Ready for Engineering (Not Yet Picked Up)": "tag-green",
                "Waiting More Info (Additional Work Needed)": "tag-yellow",
                "Business Intent Not Defined": "tag-red",
                "PRD In Discussion": "tag-blue",
                "Design Pending": "tag-purple",
                "Vendor Selection In Progress": "tag-yellow",
            }

            for stage in readiness_order:
                items = by_stage.get(stage, [])
                if not items:
                    continue
                tag_cls = tag_map.get(stage, "tag-gray")
                html += f"""
<div class="initiative-stage">
  <div class="initiative-stage-header">
    <h4><span class="tag {tag_cls}">{stage}</span></h4>
    <span class="count-badge">{len(items)}</span>
  </div>
  <table>
    <thead><tr><th>Initiative</th><th>Pillar</th><th>Owner</th><th>Target Date</th><th>Readiness</th></tr></thead>
    <tbody>
"""
                for init in items:
                    html += f"""      <tr>
        <td><strong>{init["name"]}</strong>{f'<br/><span style="font-size:0.75rem;color:var(--muted);">{init["description"][:80]}</span>' if init.get("description") else ""}</td>
        <td>{init["pillar"]}</td>
        <td>{init["owner"]}</td>
        <td>{init["target_date"]}</td>
        <td><span class="tag {tag_cls}" style="font-size:0.7rem;">{init["eng_readiness_raw"] or "—"}</span></td>
      </tr>\n"""
                html += "    </tbody></table></div>\n"

            # Remaining stages not in order
            for stage, items in by_stage.items():
                if stage not in readiness_order and items:
                    html += f"""
<div class="initiative-stage">
  <div class="initiative-stage-header">
    <h4><span class="tag tag-gray">{stage}</span></h4>
    <span class="count-badge">{len(items)}</span>
  </div>
  <table>
    <thead><tr><th>Initiative</th><th>Pillar</th><th>Owner</th><th>Status</th></tr></thead>
    <tbody>
"""
                    for init in items:
                        html += f"""      <tr>
          <td>{init["name"]}</td>
          <td>{init["pillar"]}</td>
          <td>{init["owner"]}</td>
          <td>{init["status"]}</td>
        </tr>\n"""
                    html += "    </tbody></table></div>\n"

    # ============================================================
    # JAVASCRIPT / CHARTS
    # ============================================================
    pod_labels    = [s["pod"] for s in current_stats]
    pod_colors    = [s["color"] for s in current_stats]
    pod_done      = [s["completed"] for s in current_stats]
    pod_total     = [s["total"] for s in current_stats]
    pod_done_pts  = [s["done_pts"] for s in current_stats]
    pod_total_pts = [s["total_pts"] for s in current_stats]

    next_pod_labels = [ns["pod"] for ns in next_stats]
    next_pod_colors = [ns["color"] for ns in next_stats]
    next_pod_counts = [ns["total"] for ns in next_stats]
    next_pod_pts    = [ns["total_pts"] for ns in next_stats]

    # Engineer stacked bar data for next sprint
    eng_datasets = []
    for ns in next_stats:
        eng_points = [next_eng_data.get(e, {}).get(ns["pod"], 0) for e in all_engineers]
        eng_datasets.append({
            "label": ns["pod"],
            "data": eng_points,
            "backgroundColor": ns["color"],
        })

    pareto_labels = [p["label"] for p in pareto_top]
    pareto_pts    = [p["pts"] for p in pareto_top]
    pareto_colors = [p["color"] for p in pareto_top]

    html += f"""
</div><!-- .container -->

<script>
Chart.defaults.color = '#94a3b8';
Chart.defaults.borderColor = '#334155';

// ── Burndown Chart ──────────────────────────────
const burndownCtx = document.getElementById('burndownChart');
if (burndownCtx) {{
  new Chart(burndownCtx, {{
    type: 'line',
    data: {{
      labels: {jd(burndown_labels)},
      datasets: {jd(burndown_datasets)}
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ position: 'top' }} }},
      scales: {{
        x: {{ grid: {{ color: '#334155' }} }},
        y: {{ grid: {{ color: '#334155' }}, title: {{ display: true, text: 'Remaining Points' }} }}
      }}
    }}
  }});
}}

// ── Pod Tickets Chart ───────────────────────────
const podTicketsCtx = document.getElementById('podTicketsChart');
if (podTicketsCtx) {{
  new Chart(podTicketsCtx, {{
    type: 'bar',
    data: {{
      labels: {jd(pod_labels)},
      datasets: [
        {{
          label: 'Completed',
          data: {jd(pod_done)},
          backgroundColor: {jd(pod_colors)},
          borderRadius: 6,
        }},
        {{
          label: 'Remaining',
          data: {jd([t - d for t, d in zip(pod_total, pod_done)])},
          backgroundColor: {jd([c + '44' for c in pod_colors])},
          borderRadius: 6,
        }}
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ position: 'top' }} }},
      scales: {{
        x: {{ stacked: true, grid: {{ color: '#334155' }} }},
        y: {{ stacked: true, grid: {{ color: '#334155' }}, title: {{ display: true, text: 'Tickets' }} }}
      }}
    }}
  }});
}}

// ── Pod Points Chart ────────────────────────────
const podPointsCtx = document.getElementById('podPointsChart');
if (podPointsCtx) {{
  new Chart(podPointsCtx, {{
    type: 'bar',
    data: {{
      labels: {jd(pod_labels)},
      datasets: [
        {{
          label: 'Burned',
          data: {jd(pod_done_pts)},
          backgroundColor: {jd(pod_colors)},
          borderRadius: 6,
        }},
        {{
          label: 'Remaining',
          data: {jd([t - d for t, d in zip(pod_total_pts, pod_done_pts)])},
          backgroundColor: {jd([c + '44' for c in pod_colors])},
          borderRadius: 6,
        }}
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ position: 'top' }} }},
      scales: {{
        x: {{ stacked: true, grid: {{ color: '#334155' }} }},
        y: {{ stacked: true, grid: {{ color: '#334155' }}, title: {{ display: true, text: 'Story Points' }} }}
      }}
    }}
  }});
}}

// ── Next Sprint Pod Chart ───────────────────────
const nextPodCtx = document.getElementById('nextPodChart');
if (nextPodCtx) {{
  new Chart(nextPodCtx, {{
    type: 'bar',
    data: {{
      labels: {jd(next_pod_labels)},
      datasets: [
        {{
          label: 'Tickets',
          data: {jd(next_pod_counts)},
          backgroundColor: {jd(next_pod_colors)},
          borderRadius: 6,
          yAxisID: 'y',
        }},
        {{
          label: 'Story Points',
          data: {jd(next_pod_pts)},
          backgroundColor: {jd([c + '66' for c in next_pod_colors])},
          borderRadius: 6,
          yAxisID: 'y2',
          type: 'line',
          tension: 0.3,
        }}
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ position: 'top' }} }},
      scales: {{
        x: {{ grid: {{ color: '#334155' }} }},
        y: {{ grid: {{ color: '#334155' }}, title: {{ display: true, text: 'Tickets' }} }},
        y2: {{ position: 'right', grid: {{ drawOnChartArea: false }}, title: {{ display: true, text: 'Points' }} }}
      }}
    }}
  }});
}}

// ── Engineer Points Chart ───────────────────────
const engPointsCtx = document.getElementById('engPointsChart');
if (engPointsCtx) {{
  new Chart(engPointsCtx, {{
    type: 'bar',
    data: {{
      labels: {jd(all_engineers)},
      datasets: {jd(eng_datasets)}
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      indexAxis: 'y',
      plugins: {{ legend: {{ position: 'top' }} }},
      scales: {{
        x: {{ stacked: true, grid: {{ color: '#334155' }}, title: {{ display: true, text: 'Story Points' }} }},
        y: {{ stacked: true, grid: {{ color: '#33415577' }}, ticks: {{ font: {{ size: 11 }} }} }}
      }}
    }}
  }});
}}

// ── Pareto Chart ────────────────────────────────
const paretoCtx = document.getElementById('paretoChart');
if (paretoCtx) {{
  new Chart(paretoCtx, {{
    type: 'bar',
    data: {{
      labels: {jd(pareto_labels)},
      datasets: [
        {{
          label: 'Story Points',
          data: {jd(pareto_pts)},
          backgroundColor: {jd(pareto_colors)},
          borderRadius: 4,
          yAxisID: 'y',
        }},
        {{
          label: 'Cumulative %',
          data: {jd(pareto_cumulative)},
          type: 'line',
          borderColor: '#f59e0b',
          backgroundColor: '#f59e0b33',
          tension: 0.3,
          yAxisID: 'y2',
          pointRadius: 3,
          fill: true,
        }}
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{ position: 'top' }},
        tooltip: {{
          callbacks: {{
            afterBody: function(items) {{
              const i = items[0].dataIndex;
              const titles = {jd([p["title"] for p in pareto_top])};
              return 'Title: ' + titles[i];
            }}
          }}
        }}
      }},
      scales: {{
        x: {{ grid: {{ color: '#334155' }}, ticks: {{ font: {{ size: 10 }} }} }},
        y: {{ grid: {{ color: '#334155' }}, title: {{ display: true, text: 'Story Points' }} }},
        y2: {{
          position: 'right',
          min: 0, max: 100,
          grid: {{ drawOnChartArea: false }},
          title: {{ display: true, text: 'Cumulative %' }},
          ticks: {{ callback: v => v + '%' }}
        }}
      }}
    }}
  }});
}}
</script>
</body>
</html>
"""
    return html


def main():
    linear_path = os.path.join(DATA_DIR, "linear_data.json")
    sheets_path = os.path.join(DATA_DIR, "sheets_data.json")

    linear = load_json(linear_path)
    sheets = load_json(sheets_path)

    if not linear:
        print("WARNING: linear_data.json not found or empty", file=sys.stderr)

    html = generate_html(linear, sheets)

    os.makedirs(PUBLIC_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Report written to {OUTPUT_FILE}", flush=True)


if __name__ == "__main__":
    main()
