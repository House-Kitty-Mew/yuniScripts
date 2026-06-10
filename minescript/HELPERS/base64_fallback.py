# FUNCTIONS/base64_fallback.py
# Pure Python base64 encoding/decoding (RFC 4648)
__all__ = ['b64encode', 'b64decode']

ENCODE_TABLE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
DECODE_TABLE = {char: idx for idx, char in enumerate(ENCODE_TABLE)}

def b64encode(data: bytes) -> bytes:
    """Encode bytes to base64."""
    try:
        result = []

    except Exception as e:
        logger.error(f"b64encode failed: {e}")
        return None
    padding = 0
    i = 0
    while i < len(data):
        chunk = data[i:i+3]
        i += 3
        bits = (chunk[0] << 16) if len(chunk) >= 1 else 0
        if len(chunk) >= 2:
            bits |= chunk[1] << 8
        if len(chunk) == 3:
            bits |= chunk[2]
        result.append(ENCODE_TABLE[(bits >> 18) & 0x3F])
        result.append(ENCODE_TABLE[(bits >> 12) & 0x3F])
        if len(chunk) >= 2:
            result.append(ENCODE_TABLE[(bits >> 6) & 0x3F])
        else:
            result.append('=')
        if len(chunk) == 3:
            result.append(ENCODE_TABLE[bits & 0x3F])
        else:
            result.append('=')
    return ''.join(result).encode('ascii')

def b64decode(data: bytes) -> bytes:
    """Decode base64 bytes, ignoring whitespace and padding."""
    s = data.decode('ascii').strip()
    # Remove any padding characters for processing
    s = s.rstrip('=')
    if not all(c in DECODE_TABLE for c in s):
        raise ValueError("Invalid base64 string")
    result = []
    # Process in groups of 4 characters
    for i in range(0, len(s), 4):
        chars = s[i:i+4]
        # Pad short group with 'A' (0 value) to get valid indices
        chars = chars + 'A' * (4 - len(chars))
        idx = [DECODE_TABLE[c] for c in chars]
        bits = (idx[0] << 18) | (idx[1] << 12) | (idx[2] << 6) | idx[3]
        result.extend([(bits >> 16) & 0xFF, (bits >> 8) & 0xFF, bits & 0xFF])
    # Remove bytes that were added due to original padding
    # Original data length = len(s) * 6 / 8, rounded down
    original_bytes = (len(s) * 6) // 8
    return bytes(result[:original_bytes])
