#!/usr/bin/env python3
"""Hash a plaintext password for config/secrets.yaml."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import hash_password


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/hash_password.py <password>")
        raise SystemExit(1)
    print(hash_password(sys.argv[1]))


if __name__ == "__main__":
    main()
