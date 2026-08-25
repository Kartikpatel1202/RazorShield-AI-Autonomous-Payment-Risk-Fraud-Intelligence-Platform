"""Create, list, and set passwords for console accounts.

Run from the ``backend/`` directory::

    python scripts/manage_users.py list
    python scripts/manage_users.py create --email ops@example.com --role admin
    python scripts/manage_users.py set-password --email ops@example.com
    python scripts/manage_users.py deactivate --email ops@example.com

WHY A SCRIPT AND NOT AN ENDPOINT

There is no ``POST /api/users``. An HTTP endpoint that creates administrators is
a privilege-escalation primitive: it has to exist before the first administrator
does, which means it has to be reachable by someone who is not yet one, and
every design that squares that circle - a bootstrap token, an "only if no admins
exist" check, a setup wizard - is a new thing to get wrong. Shell access to the
container is already full control, so putting account creation there adds no
privilege that an attacker who can run this did not already have.

HOW THE PASSWORD IS SUPPLIED

Never on the command line. Argv is visible in ``ps`` output, shell history and
container inspection, so this reads from the ``RAZORSHIELD_PASSWORD``
environment variable or, when a terminal is attached, prompts for it without
echo. If neither is available the command fails rather than inventing a default.

The stored value is a bcrypt hash produced by ``app.core.security``. The
plaintext is never written anywhere: not to the database, not to the log, and
not to this script's output.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

# Allow `python scripts/manage_users.py` from the backend directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.security import MAX_PASSWORD_BYTES, PasswordTooLongError, hash_password  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.services.auth import find_user_by_email  # noqa: E402

#: Refuse anything shorter. Not a substitute for a password policy - it is the
#: floor below which bcrypt's cost stops mattering because the search space is
#: small enough to enumerate.
MIN_PASSWORD_LENGTH = 12

PASSWORD_ENV = "RAZORSHIELD_PASSWORD"


def _read_password(confirm: bool = True) -> str:
    """Get a password from the environment or an interactive prompt."""
    from_env = os.environ.get(PASSWORD_ENV)
    if from_env:
        return from_env

    if not sys.stdin.isatty():
        raise SystemExit(f"No password supplied. Set {PASSWORD_ENV} or run this from a terminal.")

    password = getpass.getpass("Password: ")
    if confirm and password != getpass.getpass("Confirm: "):
        raise SystemExit("Passwords did not match.")
    return password


def _validate(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise SystemExit(
            f"Password must be at most {MAX_PASSWORD_BYTES} bytes; bcrypt cannot hash more."
        )


def cmd_list(session: Session, _args: argparse.Namespace) -> int:
    users = list(session.scalars(select(User).order_by(User.role, User.email)))
    if not users:
        print("No accounts.")
        return 0

    print(f"{'id':>4}  {'role':<13} {'active':<7} {'password':<9} email")
    for user in users:
        # Whether a credential *exists*, never the hash itself.
        credential = "set" if user.password_hash else "unset"
        print(
            f"{user.id:>4}  {str(user.role):<13} "
            f"{'yes' if user.is_active else 'no':<7} {credential:<9} {user.email}"
        )
    return 0


def cmd_create(session: Session, args: argparse.Namespace) -> int:
    email = args.email.strip().lower()
    if find_user_by_email(session, email) is not None:
        raise SystemExit(f"An account already exists for {email}. Use set-password instead.")

    password = _read_password()
    _validate(password)

    try:
        hashed = hash_password(password)
    except PasswordTooLongError as exc:
        raise SystemExit(str(exc)) from exc

    user = User(
        email=email,
        full_name=args.name,
        role=UserRole(args.role),
        is_active=True,
        password_hash=hashed,
    )
    session.add(user)
    session.commit()
    print(f"Created {email} with role {args.role} (id {user.id}).")
    return 0


def cmd_set_password(session: Session, args: argparse.Namespace) -> int:
    user = find_user_by_email(session, args.email)
    if user is None:
        raise SystemExit(f"No account for {args.email}.")

    password = _read_password()
    _validate(password)
    user.password_hash = hash_password(password)
    session.commit()
    print(f"Password updated for {user.email}.")
    return 0


def cmd_deactivate(session: Session, args: argparse.Namespace) -> int:
    """Disable an account.

    Takes effect immediately, including for tokens already issued: every request
    re-reads the account, so a deactivated user is refused on their next call
    rather than when their token happens to expire.
    """
    user = find_user_by_email(session, args.email)
    if user is None:
        raise SystemExit(f"No account for {args.email}.")
    user.is_active = False
    session.commit()
    print(f"Deactivated {user.email}. Existing tokens for it are refused from now on.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="Show every account and whether it has a password set")

    create = sub.add_parser("create", help="Create an account")
    create.add_argument("--email", required=True)
    create.add_argument("--name", default=None)
    create.add_argument(
        "--role",
        required=True,
        choices=[role.value for role in UserRole],
        help="admin, risk_analyst (the analyst role), viewer, or merchant",
    )

    reset = sub.add_parser("set-password", help="Set or replace an account's password")
    reset.add_argument("--email", required=True)

    disable = sub.add_parser("deactivate", help="Disable an account immediately")
    disable.add_argument("--email", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "list": cmd_list,
        "create": cmd_create,
        "set-password": cmd_set_password,
        "deactivate": cmd_deactivate,
    }
    with SessionLocal() as session:
        return handlers[args.command](session, args)


if __name__ == "__main__":
    raise SystemExit(main())
