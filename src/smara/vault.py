"""Small envelope boundary for integration secrets.

The database receives ciphertext only. Production injects the Fernet key from a
secret manager; development may generate one explicitly for local testing.
"""
from cryptography.fernet import Fernet, InvalidToken


class SecretVault:
    def __init__(self, keys: str):
        """Create a key ring where the first key encrypts and all keys decrypt.

        Rotate by prepending a new key to ``SMARA_INTEGRATION_MASTER_KEYS``.
        Keep the old key until every stored credential has been re-encrypted.
        """
        raw_keys = [item.strip() for item in keys.split(",") if item.strip()]
        if not raw_keys:
            raise RuntimeError("SMARA_INTEGRATION_MASTER_KEY(S) must be configured before storing integration credentials.")
        try:
            self._fernets = [Fernet(key.encode()) for key in raw_keys]
        except (ValueError, TypeError) as exc:
            raise RuntimeError("SMARA_INTEGRATION_MASTER_KEY(S) contains an invalid Fernet key.") from exc

    def encrypt(self, plaintext: str) -> str:
        return self._fernets[0].encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        for fernet in self._fernets:
            try:
                return fernet.decrypt(ciphertext.encode()).decode()
            except InvalidToken:
                pass
        raise RuntimeError("Stored integration credential cannot be decrypted with the configured master key ring.")
