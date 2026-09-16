#!/usr/bin/env python3
"""Gera o hash de senha para LEAN4NET_ADMIN_PASSWORD_HASH.

Uso:
    python3 backend/webapp/hash_password.py 'minha-senha-forte'
"""
from __future__ import annotations

import sys

from werkzeug.security import generate_password_hash


def main() -> int:
    if len(sys.argv) != 2:
        print("uso: python3 backend/webapp/hash_password.py '<senha>'", file=sys.stderr)
        return 2
    print(generate_password_hash(sys.argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
