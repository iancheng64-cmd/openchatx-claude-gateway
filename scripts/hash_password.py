#!/usr/bin/env python3
"""
Generate a spec-compliant bcrypt password hash for OpenChatX Claude Gateway.
Usage:
  python3 scripts/hash_password.py [password]
"""

import sys
import getpass
import bcrypt

def generate_hash(password: str) -> str:
    salt = bcrypt.gensalt(rounds=12)
    pw_hash = bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")
    assert bcrypt.checkpw(password.encode("utf-8"), pw_hash.encode("utf-8")), "Verification check failed"
    return pw_hash

def main():
    if len(sys.argv) > 1:
        password = sys.argv[1]
    else:
        password = getpass.getpass("Enter password to hash: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Error: Passwords do not match!", file=sys.stderr)
            sys.exit(1)

    if not password:
        print("Error: Password cannot be empty!", file=sys.stderr)
        sys.exit(1)

    hashed = generate_hash(password)
    print("\n--- Bcrypt Password Hash ---")
    print(hashed)
    print("\nPaste this string into your config.yaml under auth.users -> password_hash:\n")
    print(f'      password_hash: "{hashed}"\n')

if __name__ == "__main__":
    main()
