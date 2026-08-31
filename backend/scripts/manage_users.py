"""Create, list, and set passwords for console accounts.

Run from the ``backend/`` directory::

    python scripts/manage_users.py list
    python scripts/manage_users.py create --email ops@example.com --role admin
    python scripts/manage_users.py set-password --email ops@example.com
    python scripts/manage_users.py set-role --email ops@example.com --role admin
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
    print(f"Password updated for {user.email} in {_target_database()}.")
    return 0


def cmd_set_role(session: Session, args: argparse.Namespace) -> int:
    """Change an existing account's role.

    The gap this fills: ``POST /api/auth/signup`` always produces a ``viewer``
    and deliberately has no role field, so the only way an account created
    through the console could ever hold ``simulator:control`` was to be created
    here in the first place. Someone who has already signed up had no path at
    all - the operator's only option was to delete the row by hand, which is
    exactly the manual database editing this project is meant not to require.

    Takes effect on the account's next request, not on their next login: the
    role in an already-issued token is never trusted, because ``deps`` re-reads
    the user row and ``permissions_for`` is applied to what the database says.
    """
    user = find_user_by_email(session, args.email)
    if user is None:
        raise SystemExit(f"No account for {args.email}.")

    previous = user.role
    target = UserRole(args.role)
    if previous == target:
        print(f"{user.email} already has role {target}. Nothing to do.")
        return 0

    user.role = target
    session.commit()
    print(f"{user.email}: {previous} -> {target}. Effective on their next request.")
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

    role = sub.add_parser("set-role", help="Change an existing account's role")
    role.add_argument("--email", required=True)
    role.add_argument(
        "--role",
        required=True,
        choices=[value.value for value in UserRole],
        help="admin, risk_analyst (the analyst role), viewer, or merchant",
    )

    disable = sub.add_parser("deactivate", help="Disable an account immediately")
    disable.add_argument("--email", required=True)

    return parser


#: Commands that write. Read-only `list` is exempt from the confirmation below.
_WRITE_COMMANDS = frozenset({"create", "set-password", "set-role", "deactivate"})


def _target_database() -> str:
    """``user@host:port/database`` for the configured connection. Never the password."""
    from urllib.parse import urlparse

    from app.core.config import get_settings

    parsed = urlparse(get_settings().database_url)
    host = parsed.hostname or "?"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.username or '?'}@{host}{port}/{(parsed.path or '/?').lstrip('/')}"


def _confirm_target(command: str) -> None:
    """Name the database before writing to it, and stop if it is not local.

    This script has no database of its own: it resolves ``DATABASE_URL`` exactly
    as the API does, so the same command edits local PostgreSQL or the
    production database depending only on an environment variable that is not
    visible in the command being typed. That is the intended way to administer
    production - and it is also how someone changes a password in the wrong
    environment and concludes the change "did not work".

    So a write against anything other than a local host prints the target and
    requires it to be confirmed. ``RAZORSHIELD_CONFIRM_TARGET`` accepts the
    hostname up front for a non-interactive run.
    """
    if command not in _WRITE_COMMANDS:
        return

    from urllib.parse import urlparse

    from app.core.config import get_settings

    settings = get_settings()
    host = (urlparse(settings.database_url).hostname or "").lower()

    # `postgres` is the Docker Compose service name; inside the compose network
    # that is still the local stack.
    if host in {"localhost", "127.0.0.1", "::1", "postgres", "razorshield-postgres", ""}:
        return

    target = _target_database()
    print(f"Target database : {target}")
    print(f"Environment     : {settings.environment}")
    print("This is not a local database. The change will apply there, not to local PostgreSQL.")

    preapproved = os.environ.get("RAZORSHIELD_CONFIRM_TARGET")
    if preapproved:
        if preapproved == host:
            print(f"Confirmed by RAZORSHIELD_CONFIRM_TARGET={host}.")
            return
        raise SystemExit(
            f"RAZORSHIELD_CONFIRM_TARGET={preapproved!r} does not match the target host {host!r}."
        )

    refusal = (
        "Refusing to write to a non-local database without confirmation. "
        f"Re-run with RAZORSHIELD_CONFIRM_TARGET={host}"
    )
    if not sys.stdin.isatty():
        raise SystemExit(refusal)

    try:
        answer = input(f"Type the host to continue ({host}): ").strip()
    except EOFError:
        # `isatty()` is not reliable everywhere - notably a Windows shell with
        # stdin redirected reports a terminal and then has nothing to read.
        # Reaching EOF here means nobody is present to confirm, which is the
        # same situation the check above is for.
        raise SystemExit(refusal) from None

    if answer != host:
        raise SystemExit("Not confirmed; nothing was changed.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "list": cmd_list,
        "create": cmd_create,
        "set-password": cmd_set_password,
        "set-role": cmd_set_role,
        "deactivate": cmd_deactivate,
    }
    _confirm_target(args.command)
    with SessionLocal() as session:
        return handlers[args.command](session, args)


if __name__ == "__main__":
    raise SystemExit(main())
