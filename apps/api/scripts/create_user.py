"""Creates (or resets the password of) an admin-provisioned login account —
there is no signup endpoint, this script is the only way in. Run from
apps/api/:

    uv run python scripts/create_user.py someone@example.com
"""

from __future__ import annotations

import asyncio
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy.ext.asyncio import async_sessionmaker

from forge_api.db import Base, make_engine  # noqa: E402
from forge_api.models_orm import UserORM  # noqa: E402
from forge_api.security import hash_password  # noqa: E402


async def main(email: str, password: str) -> None:
    engine = make_engine()  # reads FORGE_DATABASE_URL itself when no arg is given
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        email = email.strip().lower()
        user = await session.get(UserORM, email)
        if user is None:
            user = UserORM(email=email, password_hash=hash_password(password))
            session.add(user)
            print(f"Created user {email}")
        else:
            user.password_hash = hash_password(password)
            print(f"Updated password for {email}")
        await session.commit()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: create_user.py <email>", file=sys.stderr)
        raise SystemExit(1)
    pw = getpass.getpass("Password: ")
    if pw != getpass.getpass("Confirm password: "):
        print("Passwords did not match.", file=sys.stderr)
        raise SystemExit(1)
    asyncio.run(main(sys.argv[1], pw))
