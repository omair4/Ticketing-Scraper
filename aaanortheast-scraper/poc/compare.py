"""Run all three POC methods as subprocesses and print a comparison table.

Subprocess isolation matters: if camoufox fails to launch or a runner hangs,
the others still produce results. Each runner prints exactly one JSON line on
stdout; we take the last non-empty line to be robust against incidental prints.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

METHODS = ("try_camoufox", "try_curlcffi", "try_patchright")
TIMEOUT_S = 180  # camoufox + proxy can be slow on cold start


def run_one(module: str) -> dict:
    cmd = [sys.executable, "-m", f"poc.{module}"]
    project_root = Path(__file__).resolve().parent.parent
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            cwd=str(project_root),
        )
    except subprocess.TimeoutExpired:
        return {
            "method": module.replace("try_", ""),
            "error": f"timeout > {TIMEOUT_S}s",
        }

    last = ""
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            last = line
    if not last:
        stderr_tail = (proc.stderr or "").strip().splitlines()[-3:]
        return {
            "method": module.replace("try_", ""),
            "error": f"no JSON output (rc={proc.returncode}); stderr: {' | '.join(stderr_tail)[:300]}",
        }
    try:
        return json.loads(last)
    except json.JSONDecodeError:
        return {
            "method": module.replace("try_", ""),
            "error": f"unparseable output: {last[:200]}",
        }


def fmt_row(r: dict) -> str:
    method = r.get("method", "?")
    status = r.get("status")
    blocked = bool(r.get("blocked", False))
    reason = (r.get("block_reason") or r.get("error") or "").replace("\n", " ")
    events = int(r.get("event_count", 0))
    elapsed = float(r.get("elapsed_s", 0.0) or 0.0)
    err = r.get("error")

    if err:
        verdict = "ERROR"
    elif blocked or events == 0:
        verdict = "FAIL"
    else:
        verdict = "PASS"

    return (
        f"{method:<12} "
        f"{(str(status) if status is not None else '-'):<7} "
        f"{('yes' if blocked else 'no'):<8} "
        f"{reason[:38]:<40} "
        f"{events:<7} "
        f"{elapsed:>7.2f}s   "
        f"{verdict}"
    )


def main() -> int:
    header = (
        f"{'method':<12} {'status':<7} {'blocked':<8} {'reason':<40} "
        f"{'events':<7} {'elapsed':>9}   verdict"
    )
    print(header)
    print("-" * len(header))

    results: list[dict] = []
    for m in METHODS:
        r = run_one(m)
        results.append(r)
        print(fmt_row(r))

    print()
    for r in results:
        path = r.get("html_path")
        if path:
            print(f"  {r.get('method'):<12} html -> {path}")

    any_pass = any(
        not r.get("error")
        and not r.get("blocked")
        and r.get("event_count", 0) > 0
        for r in results
    )
    return 0 if any_pass else 2


if __name__ == "__main__":
    sys.exit(main())
