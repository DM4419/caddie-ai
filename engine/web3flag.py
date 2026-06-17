"""Detect crypto / blockchain / Web3 roles, so the Web3-flavoured application CV
is auto-selected for them. Mirrors voiceai.py."""
from __future__ import annotations

import re

WEB3_RE = re.compile(
    r"\b(web3|web 3|crypto|cryptocurrenc|blockchain|on-?chain|defi\b|de-fi|"
    r"tokenomics|\btoken\b|\bdao\b|smart contract|ethereum|\beth\b|solana|\bsol\b|"
    r"\bton\b|polkadot|cosmos|avalanche|layer ?[12]\b|\bl1\b|\bl2\b|rollup|zk-?|"
    r"validator|staking|stablecoin|\bnft\b|\bdapp\b|wallet\b|consensus|"
    r"data availability|modular blockchain|protocol team|onchain)\b",
    re.I)


def detect(title: str, description: str) -> bool:
    text = f"{title or ''}\n{description or ''}"
    return bool(WEB3_RE.search(text))
