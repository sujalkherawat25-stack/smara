"""Small envelope boundary for integration secrets.

The database receives ciphertext only. Production injects the Fernet key from a
secret manager; development may generate one explicitly for local testing.
"""
from cryptography.fernet import Fernet, InvalidToken


class SecretVault:
    def __init__(self, key: str):
        if not key:
            raise RuntimeError("SMARA_INTEGRATION_MASTER_KEY must be configured before storing integration credentials.")
        try:
            self._fernet = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise RuntimeError("SMARA_INTEGRATION_MASTER_KEY is not a valid Fernet key.") from exc

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError("Stored integration credential cannot be decrypted with the configured master key.") from exc
