"""加密 API key 私钥为 .enc 文件。

用法：
  GRID_PASSPHRASE=你的口令 python scripts/encrypt_key.py
  然后交互粘贴私钥 hex（含或不含 0x 均可）。

密文写到 secrets/api-key.enc（不进 git）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.keystore import encrypt


def main() -> None:
    pk = input("粘贴 API key 私钥（hex）：").strip()
    if not pk:
        print("私钥为空，退出。")
        return
    # 规范化：保留 0x 前缀与否均可，交给 SDK 处理
    data = encrypt(pk)
    out = Path("secrets/api-key.enc")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    out.chmod(0o600)
    print(f"已加密写入 {out}（权限 600）")
    print("明文私钥已丢弃，请勿再在任何地方保留。")


if __name__ == "__main__":
    main()
