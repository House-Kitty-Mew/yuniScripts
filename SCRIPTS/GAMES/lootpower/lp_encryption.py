"""
LootPower Encryption — legacy AES-CTR compatibility.

Preserves the original pyaes/pyscrypt encryption scheme for
backwards compatibility with existing Minecraft clients.
"""
import base64
from typing import Union

import lp_config

try:
    import pyaes
    import pyscrypt
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


def encrypt(key: str, data: Union[str, bytes]) -> bytes:
    """
    Encrypt data using AES-CTR (original LootPower scheme).

    Args:
        key: Password string for key derivation.
        data: Plaintext data.

    Returns:
        Base64-encoded ciphertext bytes.
    """
    if not HAS_CRYPTO:
        # Fallback: simple XOR (INSECURE, for dev only)
        if isinstance(data, str):
            data = data.encode("utf-8")
        result = bytes(a ^ ord(k) for a, k in
                       zip(data, key * (len(data) // len(key) + 1)))
        return base64.b64encode(result)

    if isinstance(data, str):
        data = data.encode("utf-8")

    password_key = pyscrypt.hash(
        key.encode("utf-8"),
        salt=lp_config.ENCRYPTION_SALT,
        N=lp_config.ENCRYPTION_N,
        r=lp_config.ENCRYPTION_R,
        p=lp_config.ENCRYPTION_P,
        dkLen=lp_config.ENCRYPTION_DKLEN,
    )
    aes = pyaes.AESModeOfOperationCTR(password_key)
    ciphertext = aes.encrypt(data)
    return base64.b64encode(ciphertext)


def decrypt(key: str, data: Union[str, bytes]) -> str:
    """
    Decrypt base64 ciphertext using AES-CTR (original scheme).

    Args:
        key: Password string for key derivation.
        data: Base64-encoded ciphertext.

    Returns:
        Decrypted plaintext string.
    """
    if not HAS_CRYPTO:
        ciphertext = base64.b64decode(data)
        result = bytes(a ^ ord(k) for a, k in
                       zip(ciphertext, key * (len(ciphertext) // len(key) + 1)))
        return result.decode("utf-8", errors="replace")

    if isinstance(data, str):
        data = data.encode("utf-8")

    ciphertext = base64.b64decode(data)
    password_key = pyscrypt.hash(
        key.encode("utf-8"),
        salt=lp_config.ENCRYPTION_SALT,
        N=lp_config.ENCRYPTION_N,
        r=lp_config.ENCRYPTION_R,
        p=lp_config.ENCRYPTION_P,
        dkLen=lp_config.ENCRYPTION_DKLEN,
    )
    aes = pyaes.AESModeOfOperationCTR(password_key)
    decrypted = aes.decrypt(ciphertext)
    return decrypted.decode("utf-8")