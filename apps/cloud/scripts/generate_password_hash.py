#!/usr/bin/env python3
from __future__ import annotations

import getpass
import sys

from argon2 import PasswordHasher


def main() -> int:
    password = getpass.getpass("Choose the Pirouette Cloud administrator password: ")
    confirmation = getpass.getpass("Confirm the password: ")
    if password != confirmation:
        print("Passwords did not match.", file=sys.stderr)
        return 1
    if len(password) < 14:
        print("Use at least 14 characters.", file=sys.stderr)
        return 1
    print(PasswordHasher().hash(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
