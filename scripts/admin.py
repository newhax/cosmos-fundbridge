"""Create or re-scope a regional admin (the only way to make an admin; there is no admin signup).

  python scripts/admin.py create EMAIL [--states "Rajasthan,Gujarat"] [--languages hi,mr]
  python scripts/admin.py scope  EMAIL [--states ...] [--languages ...]
  python scripts/admin.py list

No --states means all states; no --languages means all languages. The password is read from the
ADMIN_NEW_PASSWORD environment variable (or asked for) and never printed.
"""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fundbridge.config import PLATFORM_DB_PATH, settings  # noqa: E402
from fundbridge.platform.service import Platform  # noqa: E402
from fundbridge.stores import build_backends  # noqa: E402


def _opt(name: str) -> list[str]:
    if name not in sys.argv:
        return []
    return [x.strip() for x in sys.argv[sys.argv.index(name) + 1].split(",") if x.strip()]


def main() -> None:
    p = Platform(PLATFORM_DB_PATH, build_backends().memory, settings)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        for u in p.db.all("SELECT id, email FROM users WHERE role = 'admin' ORDER BY email"):
            sc = p.admin.scope(u)
            print(f"{u['email']:<40} states: {', '.join(sc['state_names']) or 'all'} | languages: {', '.join(sc['languages'] or []) or 'all'}")
        return
    email = sys.argv[2]
    states = []
    for name in _opt("--states"):
        m = p.geo.resolve_state(name)
        if m.level != "state":
            sys.exit(f"Unknown state {name!r} (use the LGD name)")
        states.append(m.code)
    languages = _opt("--languages")
    if cmd == "create":
        pw = os.getenv("ADMIN_NEW_PASSWORD") or getpass.getpass("Password (min 8 characters): ")
        p.admin.create(email, pw, states, languages)
        print(f"Created admin {email}")
    elif cmd == "scope":
        u = p.db.one("SELECT id FROM users WHERE email = ? AND role = 'admin'", (email.lower(),))
        if not u:
            sys.exit("No such admin")
        p.admin.set_scope(u["id"], states, languages)
        print(f"Updated scope for {email}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
