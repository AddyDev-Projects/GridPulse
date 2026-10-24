"""Generate GRIDPULSE_SECRET_KEY in the repository-root .env if it is missing or still a placeholder.

Cross-platform (used by scripts/setup.ps1 and scripts/setup.sh). Never prints the value and never
replaces an existing real secret.
"""

import re
import secrets
import sys
from pathlib import Path

ENV = Path(__file__).resolve().parents[2] / ".env"


def main() -> int:
    if not ENV.exists():
        print(".env not found; copy .env.example to .env first.", file=sys.stderr)
        return 1
    text = ENV.read_text(encoding="utf-8")
    line = re.search(r"(?m)^GRIDPULSE_SECRET_KEY=(.*)$", text)
    current = line.group(1).strip() if line else ""
    if current and current != "CHANGE_ME" and len(current) >= 32:
        print("GRIDPULSE_SECRET_KEY already set; left unchanged.")
        return 0
    new = f"GRIDPULSE_SECRET_KEY={secrets.token_urlsafe(48)}"
    text = re.sub(r"(?m)^GRIDPULSE_SECRET_KEY=.*$", new, text) if line else text.rstrip("\n") + "\n" + new + "\n"
    ENV.write_text(text, encoding="utf-8")
    print("Generated a new GRIDPULSE_SECRET_KEY in .env.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
