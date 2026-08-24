"""Generate a Web Push VAPID pair without printing private material."""
from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    private_path = output / "vapid_private.pem"
    public_path = output / "vapid_public.txt"
    output.mkdir(parents=True, exist_ok=True)
    os.chmod(output, 0o700)
    if private_path.exists() or public_path.exists():
        raise SystemExit("VAPID_KEYS=EXISTING (refusing to overwrite)")

    vapid = Vapid()
    vapid.generate_keys()
    vapid.save_key(str(private_path))
    public_raw = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    public_key = base64.urlsafe_b64encode(public_raw).decode("ascii").rstrip("=")
    public_path.write_text(public_key + "\n", encoding="ascii")
    os.chmod(private_path, 0o600)
    os.chmod(public_path, 0o644)
    print("VAPID_KEYS=CREATED")


if __name__ == "__main__":
    main()
