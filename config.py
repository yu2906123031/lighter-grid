"""配置层：从环境变量加载并校验网格配置。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _f(name: str, default: float) -> float:
    v = os.getenv(name)
    return float(v) if v not in (None, "") else default


def _i(name: str, default: int) -> int:
    v = os.getenv(name)
    return int(v) if v not in (None, "") else default


def _s(name: str, default: str) -> str:
    v = os.getenv(name)
    return v if v not in (None, "") else default


def _b(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v in (None, ""):
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    base_url: str = field(default_factory=lambda: _s("LIGHTER_BASE_URL", "https://api.rh.lighter.xyz/"))
    account_index: int = field(default_factory=lambda: _i("LIGHTER_ACCOUNT_INDEX", 0))
    api_key_index: int = field(default_factory=lambda: _i("LIGHTER_API_KEY_INDEX", 2))
    api_private_key: str = field(default_factory=lambda: _s("LIGHTER_API_PRIVATE_KEY", ""))
    api_key_enc: str = field(default_factory=lambda: _s("LIGHTER_API_KEY_ENC", ""))

    market: int = field(default_factory=lambda: _i("LIGHTER_MARKET", 5))
    symbol: str = field(default_factory=lambda: _s("LIGHTER_SYMBOL", "LIT"))

    grid_count: int = field(default_factory=lambda: _i("GRID_COUNT", 9))
    grid_spacing_pct: float = field(default_factory=lambda: _f("GRID_SPACING_PCT", 0.5))
    leverage: int = field(default_factory=lambda: _i("LEVERAGE", 5))
    margin_frac: float = field(default_factory=lambda: _f("MARGIN_FRAC", 0.8))
    max_inventory: int = field(default_factory=lambda: _i("MAX_INVENTORY", 3))
    stop_loss_pct: float = field(default_factory=lambda: _f("STOP_LOSS_PCT", 0.0))
    max_spread_pct: float = field(default_factory=lambda: _f("MAX_SPREAD_PCT", 0.5))

    mode: str = field(default_factory=lambda: _s("MODE", "dryrun"))
    tick_ms: int = field(default_factory=lambda: _i("TICK_MS", 10))
    state_file: str = field(default_factory=lambda: _s("STATE_FILE", "data/state.json"))
    log_level: str = field(default_factory=lambda: _s("LOG_LEVEL", "INFO"))

    @property
    def is_live(self) -> bool:
        return self.mode.strip().lower() == "live"

    @property
    def state_path(self) -> Path:
        return Path(self.state_file)

    def resolve_private_key(self) -> str:
        """live 模式下解析 API key 私钥：优先 .enc 文件，其次环境变量。"""
        if self.api_key_enc:
            from src.keystore import load_private_key
            return load_private_key(self.api_key_enc)
        return self.api_private_key

    def validate(self) -> list[str]:
        errs: list[str] = []
        if self.grid_count < 3 or self.grid_count % 2 == 0:
            errs.append("GRID_COUNT 必须是 >=3 的奇数")
        if not (0 < self.grid_spacing_pct < 100):
            errs.append("GRID_SPACING_PCT 必须在 (0, 100) 之间")
        if self.leverage < 1:
            errs.append("LEVERAGE 必须 >= 1")
        if self.max_inventory < 1:
            errs.append("MAX_INVENTORY 必须 >= 1")
        if not (0 < self.margin_frac <= 1):
            errs.append("MARGIN_FRAC 必须在 (0, 1] 之间")
        if self.mode not in ("live", "dryrun"):
            errs.append("MODE 必须是 live 或 dryrun")
        if self.is_live and not (self.api_private_key or self.api_key_enc):
            errs.append("live 模式需要 LIGHTER_API_PRIVATE_KEY 或 LIGHTER_API_KEY_ENC")
        return errs
