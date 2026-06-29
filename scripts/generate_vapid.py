#!/usr/bin/env python3
"""Generate VAPID keys for Web Push."""

from __future__ import annotations

from vapid import Vapid01


def main() -> None:
    keys = Vapid01().generate_keys()
    print("Add these values to config/secrets.yaml under vapid:")
    print(f"  subject: mailto:you@yourdomain.com")
    print(f"  public_key: {keys['public_key']}")
    print(f"  private_key: {keys['private_key']}")


if __name__ == "__main__":
    main()
