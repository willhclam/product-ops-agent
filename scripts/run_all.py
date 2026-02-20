"""
Master runner: fetch Linear data → fetch Sheets data → generate HTML report.
Run this script daily via cron or GitHub Actions.
"""

import os
import sys
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def run_script(name: str, env: dict = None):
    path = os.path.join(SCRIPT_DIR, name)
    print(f"\n{'='*60}")
    print(f"Running: {name}")
    print('='*60)
    result = subprocess.run(
        [sys.executable, path],
        env={**os.environ, **(env or {})},
        capture_output=False,
    )
    if result.returncode != 0:
        print(f"WARNING: {name} exited with code {result.returncode}", file=sys.stderr)
    return result.returncode


def main():
    # These must be set as environment variables (from .env or GitHub Secrets)
    required = ["LINEAR_API_KEY"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"ERROR: Missing required env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    rc1 = run_script("fetch_linear.py")
    rc2 = run_script("fetch_sheets.py")
    rc3 = run_script("generate_report.py")

    if rc3 != 0:
        print("ERROR: Report generation failed", file=sys.stderr)
        sys.exit(1)

    print(f"\n✓ Report generated successfully")
    print(f"  Linear fetch: {'OK' if rc1==0 else 'WARN (check logs)'}")
    print(f"  Sheets fetch: {'OK' if rc2==0 else 'WARN (sheet may need access)'}")


if __name__ == "__main__":
    main()
