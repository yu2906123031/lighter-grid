"""私钥加密/解密工具：AES-256-GCM + PBKDF2，密文存 .enc 文件。

passphrase 从环境变量 GRID_PASSPHRASE 读取（不进代码、不进 git）。
"""
from __future__ import annotations

import base64
import os

from Crypto.Cipher import AES
from Crypto.Protocol.KDF import PBKDF2
from Crypto.Random import get_random_bytes

SALT_LEN = 16
NONCE_LEN = 12
TAG_LEN = 16
KDF_ITER = 200_000


def _passphrase() -> bytes:
    p = os.getenv("GRID_PASSPHRASE", "")
    if not p:
        raise RuntimeError("未设置 GRID_PASSPHRASE 环境变量")
    return p.encode("utf-8")


def _derive_key(salt: bytes) -> bytes:
    return PBKDF2(_passphrase(), salt, dkLen=32, count=KDF_ITER)


def encrypt(plaintext: str) -> bytes:
    """加密明文（私钥 hex），返回 .enc 文件字节。"""
    salt = get_random_bytes(SALT_LEN)
    nonce = get_random_bytes(NONCE_LEN)
    key = _derive_key(salt)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ct, tag = cipher.encrypt_and_digest(plaintext.encode("utf-8"))
    return base64.b64encode(salt + nonce + tag + ct)


def decrypt(data: bytes) -> str:
    """解密 .enc 文件字节，返回私钥 hex 字符串。"""
    raw = base64.b64decode(data)
    salt = raw[:SALT_LEN]
    nonce = raw[SALT_LEN:SALT_LEN + NONCE_LEN]
    tag = raw[SALT_LEN + NONCE_LEN:SALT_LEN + NONCE_LEN + TAG_LEN]
    ct = raw[SALT_LEN + NONCE_LEN + TAG_LEN:]
    key = _derive_key(salt)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    return cipher.decrypt_and_verify(ct, tag).decode("utf-8")


def load_private_key(enc_path: str) -> str:
    """从 .enc 文件读取并解密私钥。"""
    with open(enc_path, "rb") as f:
        return decrypt(f.read())
