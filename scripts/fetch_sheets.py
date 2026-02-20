"""
Fetch Google Sheets initiatives data.
Outputs sheets_data.json with engineering readiness info.
Requires: GOOGLE_API_KEY + GOOGLE_SHEET_ID env vars
          OR service account JSON at GOOGLE_SA_JSON env var (path to file)
"""

import json
import os
import sys
import time
import base64
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

GOOGLE_API_KEY  = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
GOOGLE_SA_JSON  = os.environ.get("GOOGLE_SA_JSON", "")  # path to service account json

# Column mapping for "R1 2026 Initiatives" tab
# Adjust based on actual sheet structure
COL_MAP = {
    "initiative":     "A",   # Initiative name
    "pillar":         "B",   # Product pillar / pod
    "status":         "C",   # Overall status
    "owner":          "D",   # PM owner
    "eng_readiness":  "N",   # Column N: Pillar Weekly Update / engineering readiness
    "description":    "E",
    "priority":       "F",
    "target_date":    "G",
}

# Readiness labels from col N and their stage categorization
READINESS_STAGES = {
    "TODO":              "Ready for Engineering (Not Yet Picked Up)",
    "Waiting More Info": "Waiting More Info (Additional Work Needed)",
    "Business Intent":   "Business Intent Not Defined",
    "PRD":               "PRD In Discussion",
    "Design":            "Design Pending",
    "Vendor":            "Vendor Selection In Progress",
    "Legal":             "Legal / Compliance Review",
    "In Progress":       "In Progress",
    "Done":              "Completed",
}


def get_access_token_from_sa(sa_path: str) -> str:
    """Generate OAuth2 access token from a service account JSON file."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.backends import default_backend
    except ImportError:
        print("ERROR: 'cryptography' package required. Run: pip install cryptography", file=sys.stderr)
        sys.exit(1)

    with open(sa_path) as f:
        sa = json.load(f)

    header_b64 = base64.urlsafe_b64encode(
        json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
    ).rstrip(b"=")

    now = int(time.time())
    payload = {
        "iss":   sa["client_email"],
        "scope": "https://www.googleapis.com/auth/spreadsheets.readonly",
        "aud":   "https://oauth2.googleapis.com/token",
        "exp":   now + 3600,
        "iat":   now,
    }
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    signing_input = header_b64 + b"." + payload_b64

    private_key = serialization.load_pem_private_key(
        sa["private_key"].encode(), password=None, backend=default_backend()
    )
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=")
    jwt = (signing_input + b"." + sig_b64).decode()

    data = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion":  jwt,
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        token_data = json.loads(resp.read())
    return token_data["access_token"]


def fetch_sheet_range(range_str: str, access_token: str = None) -> list:
    """Fetch a range from the Google Sheet. Returns list of rows (list of lists)."""
    encoded_range = urllib.parse.quote(range_str)
    base_url = f"https://sheets.googleapis.com/v4/spreadsheets/{GOOGLE_SHEET_ID}/values/{encoded_range}"

    if access_token:
        url = base_url
        headers = {"Authorization": f"Bearer {access_token}"}
    elif GOOGLE_API_KEY:
        url = f"{base_url}?key={GOOGLE_API_KEY}"
        headers = {}
    else:
        raise ValueError("No Google credentials available")

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return data.get("values", [])


def categorize_readiness(raw_value: str) -> str:
    """Map a raw column-N value to a readiness stage label."""
    if not raw_value:
        return "Unknown / Not Set"
    val = raw_value.strip()
    for key, label in READINESS_STAGES.items():
        if key.lower() in val.lower():
            return label
    return val  # Return as-is if no known mapping


def parse_initiatives(rows: list) -> list:
    """Parse initiative rows from the sheet."""
    if not rows:
        return []

    # First row is header
    headers = [h.strip() for h in rows[0]] if rows else []

    def col_idx(letter: str) -> int:
        return ord(letter.upper()) - ord("A")

    initiatives = []
    for row_num, row in enumerate(rows[1:], start=2):
        def get(col_letter: str, default="") -> str:
            idx = col_idx(col_letter)
            if idx < len(row):
                return str(row[idx]).strip()
            return default

        name = get("A")
        if not name:
            continue  # Skip empty rows

        eng_readiness_raw = get("N")
        stage = categorize_readiness(eng_readiness_raw)

        initiatives.append({
            "row":           row_num,
            "name":          name,
            "pillar":        get("B"),
            "status":        get("C"),
            "owner":         get("D"),
            "description":   get("E"),
            "priority":      get("F"),
            "target_date":   get("G"),
            "eng_readiness_raw": eng_readiness_raw,
            "eng_readiness_stage": stage,
            "is_ready_for_eng": "TODO" in eng_readiness_raw.upper() if eng_readiness_raw else False,
            "is_waiting_info": "waiting" in eng_readiness_raw.lower() if eng_readiness_raw else False,
        })

    return initiatives


def main():
    if not GOOGLE_SHEET_ID:
        print("ERROR: GOOGLE_SHEET_ID not set", file=sys.stderr)
        sys.exit(1)

    access_token = None

    # Try service account first
    sa_path = GOOGLE_SA_JSON or os.path.join(
        os.path.dirname(__file__), "..",
        "product-ops-agent-4240807ba556.json"
    )
    if os.path.exists(sa_path):
        print(f"Using service account: {sa_path}", flush=True)
        try:
            access_token = get_access_token_from_sa(sa_path)
            print("Service account auth successful", flush=True)
        except Exception as e:
            print(f"Service account auth failed: {e}", file=sys.stderr)

    # Fetch the initiatives sheet
    tab_name = "R1 2026 Initiatives"
    range_str = f"'{tab_name}'!A:Z"

    print(f"Fetching sheet: {tab_name}", flush=True)
    try:
        rows = fetch_sheet_range(range_str, access_token)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"Sheet fetch error {e.code}: {body[:300]}", file=sys.stderr)
        # Write empty stub so report can still render
        rows = []

    initiatives = parse_initiatives(rows)
    print(f"Parsed {len(initiatives)} initiatives", flush=True)

    # Group by readiness stage
    by_stage = {}
    for init in initiatives:
        stage = init["eng_readiness_stage"]
        if stage not in by_stage:
            by_stage[stage] = []
        by_stage[stage].append(init)

    # Also fetch Pillar Weekly Update tab if available
    pillar_tab = "Pillar Weekly Update"
    pillar_range = f"'{pillar_tab}'!A:Z"
    pillar_rows = []
    try:
        pillar_rows = fetch_sheet_range(pillar_range, access_token)
        print(f"Pillar Weekly Update: {len(pillar_rows)} rows", flush=True)
    except Exception as e:
        print(f"Could not fetch Pillar Weekly Update: {e}", file=sys.stderr)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sheet_id": GOOGLE_SHEET_ID,
        "initiatives": initiatives,
        "by_stage": by_stage,
        "total_count": len(initiatives),
        "ready_for_eng": [i for i in initiatives if i["is_ready_for_eng"]],
        "waiting_info":  [i for i in initiatives if i["is_waiting_info"]],
        "pillar_weekly_rows": pillar_rows[:5] if pillar_rows else [],  # preview only
        "access_error": len(rows) == 0,
    }

    out_path = os.path.join(
        os.path.dirname(__file__), "..", "public", "data", "sheets_data.json"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Saved to {out_path}", flush=True)


if __name__ == "__main__":
    main()
