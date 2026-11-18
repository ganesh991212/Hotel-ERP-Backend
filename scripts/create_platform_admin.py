"""Create (or promote) a super admin. There is no API for this, by design.

    PLATFORM_ADMIN_PASSWORD='...' .venv/bin/python scripts/create_platform_admin.py \
        --name "Ganesh" --login you@example.com

The password is read from the environment (or prompted), never from argv, so it does not
land in shell history or the process list.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere

from sqlalchemy import text

from app.core.identifiers import parse_login
from app.core.security import hash_password_async
from app.db.session import dispose_engine, system_session


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--login", required=True, help="email or phone")
    args = parser.parse_args()

    login = parse_login(args.login)
    if login is None:
        print("invalid email or phone", file=sys.stderr)
        return 2
    password = os.environ.get("PLATFORM_ADMIN_PASSWORD") or getpass.getpass("Password: ")
    if len(password) < 12:
        print("super admin passwords must be at least 12 characters", file=sys.stderr)
        return 2
    column = "email" if login.kind == "email" else "phone"
    password_hash = await hash_password_async(password)
    async with system_session() as db:
        await db.execute(
            text(
                f"INSERT INTO app_user (full_name, {column}, password_hash, password_updated_at, "  # noqa: S608
                "is_platform_admin) VALUES (:n, :v, :p, now(), true) "
                f"ON CONFLICT ({column}) DO UPDATE SET is_platform_admin = true, "
                "password_hash = EXCLUDED.password_hash, password_updated_at = now()"
            ),
            {"n": args.name, "v": login.value, "p": password_hash},
        )
    await dispose_engine()
    print(f"super admin ready: {login.value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
