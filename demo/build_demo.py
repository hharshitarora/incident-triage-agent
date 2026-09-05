"""Build a throwaway git repo with a *planted* bug + realistic history.

The bug: a "simplify discount lookup" commit swaps a safe `DISCOUNTS.get(code, 0)`
for `DISCOUNTS[code]`, which KeyErrors on any unknown promo code. The triage
agent should trace the KeyError back to that specific commit and its author.

Run:
    python demo/build_demo.py
    python -m triage --repo demo/.sandbox --log demo/error.log
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SANDBOX = ROOT / ".sandbox"


def git(*args: str, **env: str) -> None:
    subprocess.run(
        ["git", "-C", str(SANDBOX), *args],
        check=True,
        env={**os.environ, **env},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def write(rel: str, content: str) -> None:
    path = SANDBOX / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


def commit(msg: str, name: str, email: str, date: str) -> None:
    git("add", "-A")
    git(
        "commit", "-m", msg,
        GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email, GIT_AUTHOR_DATE=date,
        GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email, GIT_COMMITTER_DATE=date,
    )


def force_rmtree(path: Path) -> None:
    """Delete the sandbox on Windows too.

    Git writes objects read-only and Windows refuses to unlink a read-only file,
    so a plain rmtree raises PermissionError on the *second* run of this script.
    Worse than the crash: it leaves a half-deleted .sandbox that is no longer a
    git repo, and because that directory sits inside this repo, the agent then
    silently investigates its parent instead and reports "Suspect: unclear".
    """
    if not path.exists():
        return

    def _retry(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_retry)
    else:
        shutil.rmtree(path, onerror=_retry)


def main() -> None:
    force_rmtree(SANDBOX)
    SANDBOX.mkdir(parents=True)
    git("init", "-q")
    git("config", "commit.gpgsign", "false")

    # --- commit 1: initial pricing module (safe lookup) ---
    write(
        "pricing.py",
        """
        DISCOUNTS = {"WELCOME10": 0.10, "LOYAL15": 0.15}


        def apply_discount(price, code):
            rate = DISCOUNTS.get(code, 0.0)
            return round(price * (1 - rate), 2)
        """,
    )
    commit("Add pricing module", "Alice Chen", "alice@shop.dev", "2026-07-02T10:00:00")

    # --- commit 2: order checkout helper (unrelated author) ---
    write(
        "orders.py",
        """
        from pricing import apply_discount

        # Minimal checkout path used by the storefront API.


        def checkout(subtotal, promo_code):
            \"\"\"Return the final total for an order after applying a promo code.\"\"\"
            if subtotal <= 0:
                raise ValueError("subtotal must be positive")

            total = apply_discount(subtotal, promo_code)
            return total
        """,
    )
    commit("Add checkout helper", "Sam Ortiz", "sam@shop.dev", "2026-08-05T14:30:00")

    # --- commit 3: the CULPRIT — "simplify" removes the safe default ---
    write(
        "pricing.py",
        """
        DISCOUNTS = {"WELCOME10": 0.10, "LOYAL15": 0.15}


        def apply_discount(price, code):
            # simplified lookup
            rate = DISCOUNTS[code]
            return round(price * (1 - rate), 2)
        """,
    )
    commit("Simplify discount lookup", "Dana Lee", "dana@shop.dev", "2026-08-24T09:15:00")

    sha = subprocess.run(
        ["git", "-C", str(SANDBOX), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()

    print(f"Demo repo built at {SANDBOX}")
    print(f"Planted culprit commit: {sha} 'Simplify discount lookup' by Dana Lee")
    print("\nRun the agent:")
    print("  python -m triage --repo demo/.sandbox --log demo/error.log")


if __name__ == "__main__":
    main()
