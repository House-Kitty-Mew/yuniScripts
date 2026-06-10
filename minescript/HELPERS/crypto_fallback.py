# HELPERS/crypto_fallback.py
# Pure Python AES‑256‑GCM (no external dependencies)
# Compatible with pycryptodome's AES.new() interface.
import os
import struct
import hashlib
import hmac

# ------------------------------------------------------------
#  AES block cipher (256‑bit key)
# ------------------------------------------------------------
# S‑box and inverse S‑box from FIPS 197
SBOX = [
    0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
    0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
    0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
    0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
    0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
    0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
    0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
    0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
    0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
    0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
    0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
    0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
    0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
    0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
    0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
    0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16
]

# Round constants
RCON = [0x01,0x02,0x04,0x08,0x10,0x20,0x40,0x80,0x1b,0x36]

def _key_expansion(key):
    """AES‑256 key schedule. Returns list of 15 round keys (each 16 bytes)."""
    Nk = 8   # 256‑bit key has 8 words
    Nr = 14  # rounds for AES‑256
    w = []
    for i in range(Nk):
        w.append(key[4*i:4*i+4])
    for i in range(Nk, 4*(Nr+1)):
        temp = w[i-1]
        if i % Nk == 0:
            temp = bytes([SBOX[b] for b in temp[1:]+temp[:1]])  # RotWord + SubWord
            temp = bytes([temp[0] ^ RCON[(i//Nk)-1]] + list(temp[1:]))
        elif Nk > 6 and i % Nk == 4:
            temp = bytes([SBOX[b] for b in temp])
        w.append(bytes([a^b for a,b in zip(w[i-Nk], temp)]))
    # Group into round keys (16 bytes each)
    round_keys = [b''.join(w[i:i+4]) for i in range(0, len(w), 4)]
    return round_keys

def _aes_encrypt_block(block, round_keys):
    """Encrypt a 16‑byte block using the expanded key."""
    state = list(block)
    # AddRoundKey
    state = [a^b for a,b in zip(state, round_keys[0])]
    # 13 rounds for AES‑256 (Nr-1 = 13)
    for r in range(1, 14):
        state = [SBOX[b] for b in state]          # SubBytes
        state = [state[0], state[5], state[10], state[15],
                 state[4], state[9], state[14], state[3],
                 state[8], state[13], state[2], state[7],
                 state[12], state[1], state[6], state[11]]  # ShiftRows
        # MixColumns (except last round)
        if r < 14:
            state = _mix_columns(state)
        state = [a^b for a,b in zip(state, round_keys[r])]  # AddRoundKey
    # Last round (no MixColumns)
    state = [SBOX[b] for b in state]
    state = [state[0], state[5], state[10], state[15],
             state[4], state[9], state[14], state[3],
             state[8], state[13], state[2], state[7],
             state[12], state[1], state[6], state[11]]
    state = [a^b for a,b in zip(state, round_keys[14])]
    return bytes(state)

def _mix_columns(state):
    """MixColumns step for 4x4 column‑major matrix."""
    def xtime(a):
        return ((a << 1) ^ 0x1b) & 0xff if (a & 0x80) else (a << 1) & 0xff
    result = []
    for c in range(4):
        s0, s1, s2, s3 = state[c*4], state[c*4+1], state[c*4+2], state[c*4+3]
        t = s0 ^ s1 ^ s2 ^ s3
        u = s0
        s0 ^= t ^ xtime(s0 ^ s1)
        s1 ^= t ^ xtime(s1 ^ s2)
        s2 ^= t ^ xtime(s2 ^ s3)
        s3 ^= t ^ xtime(s3 ^ u)
        result.extend([s0, s1, s2, s3])
    return result

# ------------------------------------------------------------
#  GHASH (used by GCM)
# ------------------------------------------------------------
def _ghash(h, aad, ciphertext):
    """Compute GHASH for GCM using the hash subkey h (16 bytes)."""
    def gf_mult(x, y):
        """Multiplication in GF(2^128) defined by polynomial x^128 + x^7 + x^2 + x + 1."""
        R = 0xe1000000000000000000000000000000  # upper bit mask
        z = 0
        v = y
        for i in range(128):
            if (x >> (127-i)) & 1:
                z ^= v
            if v & 1:
                v = (v >> 1) ^ R
            else:
                v >>= 1
        return z

    def bytes_to_int(b):
        return int.from_bytes(b, 'big')

    def int_to_bytes(i):
        return i.to_bytes(16, 'big')

    h_int = bytes_to_int(h)
    # Process AAD
    aad_len = len(aad)
    # Pad AAD to 16 bytes
    aad_padded = aad + b'\x00' * ((16 - (aad_len % 16)) % 16)
    blocks = [aad_padded[i:i+16] for i in range(0, len(aad_padded), 16)]
    y = 0
    for block in blocks:
        y = gf_mult(y ^ bytes_to_int(block), h_int)
    # Process ciphertext
    ct_len = len(ciphertext)
    ct_padded = ciphertext + b'\x00' * ((16 - (ct_len % 16)) % 16)
    blocks = [ct_padded[i:i+16] for i in range(0, len(ct_padded), 16)]
    for block in blocks:
        y = gf_mult(y ^ bytes_to_int(block), h_int)
    # Length block
    len_block = struct.pack('>QQ', aad_len * 8, ct_len * 8)
    y = gf_mult(y ^ bytes_to_int(len_block), h_int)
    return int_to_bytes(y)

# ------------------------------------------------------------
#  AES‑GCM class (mimics pycryptodome's interface)
# ------------------------------------------------------------
class AES:
    class _GcmMode:
        def __init__(self, key, nonce):
            self.key = key
            self.nonce = nonce
            # Compute hash subkey H = AES(0^128)
            self.round_keys = _key_expansion(key)
            self.H = _aes_encrypt_block(b'\x00'*16, self.round_keys)
            # Initial counter J0
            if len(nonce) == 12:
                self.J0 = nonce + b'\x00\x00\x00\x01'
            else:
                # For other nonce lengths, need GHASH; we only support 12-byte nonce like pycryptodome.
                raise ValueError("Only 12-byte nonce supported in fallback")
            # Encrypt J0 to get the initial counter block
            self.counter = int.from_bytes(self.J0, 'big')

        def _next_counter(self):
            ctr = self.counter.to_bytes(16, 'big')
            self.counter += 1
            return ctr

        def encrypt_and_digest(self, plaintext, associated_data=b''):
            """Returns (ciphertext, tag)."""
            # Increment counter for each block
            ct = b''
            for i in range(0, len(plaintext), 16):
                ctr_block = _aes_encrypt_block(self._next_counter(), self.round_keys)
                chunk = plaintext[i:i+16]
                ct += bytes([a^b for a,b in zip(chunk, ctr_block[:len(chunk)])])
            # Compute GHASH
            ghash_input = associated_data + ct
            S = _ghash(self.H, associated_data, ct)
            # Final tag = S XOR AES(J0)
            tag = _aes_encrypt_block(self.J0, self.round_keys)
            tag = bytes([a^b for a,b in zip(S, tag)])
            return ct, tag

        def decrypt_and_verify(self, ciphertext, tag, associated_data=b''):
            """Decrypts and verifies tag. Returns plaintext. Raises ValueError on failure."""
            # Compute expected tag
            S = _ghash(self.H, associated_data, ciphertext)
            expected_tag = _aes_encrypt_block(self.J0, self.round_keys)
            expected_tag = bytes([a^b for a,b in zip(S, expected_tag)])
            if not hmac.compare_digest(tag, expected_tag):
                # Use constant‑time comparison (not really necessary here, but good)
                raise ValueError("MAC check failed")
            pt = b''
            for i in range(0, len(ciphertext), 16):
                ctr_block = _aes_encrypt_block(self._next_counter(), self.round_keys)
                chunk = ciphertext[i:i+16]
                pt += bytes([a^b for a,b in zip(chunk, ctr_block[:len(chunk)])])
            return pt

    @staticmethod
    def new(key, mode, nonce=None):
        if mode == AES.MODE_GCM:
            return AES._GcmMode(key, nonce)
        else:
            raise ValueError("Only GCM mode supported")

    MODE_GCM = 0x0   # placeholder

# ------------------------------------------------------------
#  Convenience function that match pycryptodome's usage
# ------------------------------------------------------------
def encrypt(key, plaintext, nonce):
    """Encrypt with AES‑GCM. Returns (ciphertext, tag)."""
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ct, tag = cipher.encrypt_and_digest(plaintext)
    return ct, tag

def decrypt(key, nonce, ciphertext, tag):
    """Decrypt with AES‑GCM. Returns plaintext or raises ValueError."""
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    return cipher.decrypt_and_verify(ciphertext, tag)
