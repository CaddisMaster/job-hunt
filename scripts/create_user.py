"""Create (or reset the password of) an account from the command line.

    docker compose exec web python -m scripts.create_user <email> [--admin]

For bootstrapping the first admin; everyone else is invited from /admin (#3).
Prompts for the password so it never lands in shell history. Resetting rotates
the session token, which logs out every existing session. --admin makes the
account an admin; without it, an existing account keeps its setting.
"""

import argparse
import getpass
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models import User, new_session_token
from app.security import PASSWORD_MIN_LENGTH, hash_password, normalize_email


def main(argv: list[str] | None = None, read_password=getpass.getpass) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("email")
    parser.add_argument("--admin", action="store_true", help="make the account an admin")
    args = parser.parse_args(argv)
    email = normalize_email(args.email)
    if "@" not in email:
        print("That doesn't look like an email address.", file=sys.stderr)
        return 1

    password = read_password("Password: ")
    if len(password) < PASSWORD_MIN_LENGTH:
        print(f"Password must be at least {PASSWORD_MIN_LENGTH} characters.", file=sys.stderr)
        return 1
    if read_password("Repeat: ") != password:
        print("Passwords do not match.", file=sys.stderr)
        return 1

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            db.add(User(email=email, password_hash=hash_password(password), is_admin=args.admin))
            action = "Created"
        else:
            user.password_hash = hash_password(password)
            user.session_token = new_session_token()
            action = "Reset password for"
            if args.admin:
                user.is_admin = True
        db.commit()
    print(f"{action} {email}{' (admin)' if args.admin else ''}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
