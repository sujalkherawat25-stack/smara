"""Sign a built release artifact with an Ed25519 key kept outside Git."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--key", type=Path, default=Path("release/keys/smara-release-ed25519.private"))
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    artifact = args.artifact.resolve()
    key_path = args.key.resolve()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        private = Ed25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(key_path.read_text(encoding="utf-8").strip() + "=" * (-len(key_path.read_text(encoding="utf-8").strip()) % 4)))
    else:
        private = Ed25519PrivateKey.generate()
        key_path.write_text(_b64(private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())) + "\n", encoding="utf-8")
    public = _b64(private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    content = artifact.read_bytes()
    signature = _b64(private.sign(content))
    record = {"schema": "smara.release.signature.v1", "artifact": artifact.name, "sha256": hashlib.sha256(content).hexdigest(), "algorithm": "Ed25519", "public_key": public, "signature": signature}
    output = (args.output or artifact.with_suffix(artifact.suffix + ".sig.json")).resolve()
    output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"artifact": str(artifact), "signature": str(output), "public_key": public, "sha256": record["sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

