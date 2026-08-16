"""auth 冒烟测试：用 .enc 私钥探测正确的 API key index，并验证签名链路。

对 index 4..15 逐个尝试生成 auth token + 拉 account_active_orders，
找到能通过的那个 index。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lighter

from src.keystore import load_private_key


async def try_index(signer, account_index: int, api_key_index: int) -> str:
    """返回 None 表示成功，否则返回错误信息。"""
    try:
        tok, err = signer.create_auth_token_with_expiry(
            deadline=3600, api_key_index=api_key_index
        )
        if err:
            return f"auth token 生成失败: {err}"
        # 用 token 拉活跃订单验证
        api = lighter.ApiClient(lighter.Configuration(host="https://api.rh.lighter.xyz/"))
        oa = lighter.OrderApi(api)
        try:
            r = await oa.account_active_orders(
                authorization=tok, account_index=account_index, market_id=5
            )
            return ""  # 成功
        except Exception as e:
            return f"拉活跃订单失败: {e}"
        finally:
            await api.close()
    except Exception as e:
        return f"异常: {e}"


async def main() -> None:
    account_index = int(os.environ.get("LIGHTER_ACCOUNT_INDEX", "0"))
    if account_index <= 0:
        print("请先设置 LIGHTER_ACCOUNT_INDEX 环境变量")
        return
    enc_path = "secrets/api-key.enc"
    pk = load_private_key(enc_path)
    print(f"私钥已从 {enc_path} 解密（长度 {len(pk)}）")

    for idx in range(4, 16):
        signer = lighter.SignerClient(
            url="https://api.rh.lighter.xyz/",
            api_private_keys={idx: pk},
            account_index=account_index,
        )
        err = await try_index(signer, account_index, idx)
        if err == "":
            print(f"✓ API key index = {idx} 通过（auth token + 拉活跃订单成功）")
            return
        else:
            print(f"  index={idx} 失败: {err[:80]}")
    print("未找到有效 index（4-15 都失败）")


if __name__ == "__main__":
    asyncio.run(main())
