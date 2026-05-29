from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

from analysis.simple_yaml import load_yaml


REJECTED = "REJECTED_BY_RISK"
WATCH_ONLY = "WATCH_ONLY"
APPROVED = "APPROVED_FOR_MANUAL_REVIEW"
NEED_MORE_DATA = "NEED_MORE_DATA"


@dataclass(frozen=True)
class RiskInput:
    symbol: str
    action: str
    entry_price: float
    atr: Optional[float]
    trend_confirmed: bool
    technical_status: str = "ok"
    derivatives_status: str = "ok"
    sentiment_status: str = "ok"
    daily_loss_pct: float = 0.0
    consecutive_losses: int = 0
    open_positions: int = 0
    stop_price: Optional[float] = None
    account_equity: Optional[float] = None


def validate_risk(
    risk_input: RiskInput,
    risk_rules_path: Union[str, Path] = "config/risk_rules.yaml",
    contract_specs_path: Union[str, Path] = "config/contract_specs.yaml",
) -> dict[str, Any]:
    rules = load_yaml(risk_rules_path)
    specs = load_yaml(contract_specs_path)
    limits = rules["risk_limits"]
    assumptions = rules["execution_assumptions"]
    account_equity = float(risk_input.account_equity or rules["account"]["paper_equity_usdt"])
    max_loss_pct = float(limits["max_loss_pct_per_trade"])
    max_loss_amount = account_equity * max_loss_pct / 100.0
    leverage_cap = (
        float(limits["default_leverage_cap"])
        if risk_input.trend_confirmed
        else float(limits["unconfirmed_trend_leverage_cap"])
    )

    reasons: list[str] = []
    verdict = APPROVED
    if risk_input.technical_status != "ok":
        verdict = REJECTED
        reasons.append("Technical failed; ATR/structure unavailable.")
    elif risk_input.derivatives_status != "ok":
        verdict = WATCH_ONLY
        reasons.append("Derivatives failed; full trade proposal is disabled.")
    elif risk_input.action in {"NO_TRADE", "WATCH"}:
        verdict = WATCH_ONLY
        reasons.append(f"Action is {risk_input.action}.")

    if risk_input.daily_loss_pct >= float(limits["max_daily_loss_pct"]):
        verdict = REJECTED
        reasons.append("Daily loss limit reached.")
    if risk_input.consecutive_losses >= int(limits["max_consecutive_losses"]):
        verdict = REJECTED
        reasons.append("Consecutive loss cooldown reached.")
    if risk_input.open_positions >= int(limits["max_open_positions"]):
        verdict = REJECTED
        reasons.append("Open position limit reached.")
    if risk_input.atr is None or risk_input.atr <= 0:
        verdict = REJECTED
        reasons.append("ATR is missing or non-positive.")

    stop_distance = _stop_distance(risk_input, float(assumptions["stop_distance_atr"]))
    suggested_position_size = 0.0
    if verdict != REJECTED and stop_distance > 0:
        suggested_position_size = max_loss_amount / stop_distance

    symbol_specs = specs["symbols"].get(risk_input.symbol)
    if symbol_specs:
        suggested_position_size = _round_down_to_step(
            suggested_position_size,
            float(symbol_specs["size_increment"]),
        )
        if suggested_position_size < float(symbol_specs["min_size"]):
            suggested_position_size = 0.0
            if verdict == APPROVED:
                verdict = WATCH_ONLY
            reasons.append("Calculated position is below minimum size.")
    else:
        if verdict == APPROVED:
            verdict = NEED_MORE_DATA
        reasons.append("Missing contract specs for symbol.")

    liq_safety_margin = _liq_safety_margin(
        risk_input=risk_input,
        leverage_cap=leverage_cap,
        stop_distance=stop_distance,
        min_gap_atr=float(limits["min_liq_stop_gap_atr"]),
    )
    if liq_safety_margin["status"] == "FAIL":
        verdict = REJECTED
        reasons.append("Liquidation estimate is too close to stop.")
    elif liq_safety_margin["status"] == "UNKNOWN" and verdict == APPROVED:
        verdict = WATCH_ONLY
        reasons.append("Liquidation safety margin is unknown.")

    output = {
        "risk_rules_version": rules["version"],
        "account_equity": account_equity,
        "max_loss_pct": max_loss_pct,
        "max_loss_amount": round(max_loss_amount, 8),
        "leverage_cap": leverage_cap,
        "stop_distance": round(stop_distance, 8),
        "suggested_position_size": suggested_position_size,
        "margin_mode": assumptions["margin_mode"],
        "liq_safety_margin": liq_safety_margin,
        "daily_loss_state": {
            "daily_loss_pct": risk_input.daily_loss_pct,
            "limit_pct": float(limits["max_daily_loss_pct"]),
        },
        "consecutive_loss_state": {
            "consecutive_losses": risk_input.consecutive_losses,
            "limit": int(limits["max_consecutive_losses"]),
        },
        "verdict": verdict,
        "reasons": reasons,
        "input_json": json.loads(json.dumps(risk_input.__dict__, sort_keys=True)),
    }
    return output


def _stop_distance(risk_input: RiskInput, stop_distance_atr: float) -> float:
    if risk_input.stop_price is not None:
        return abs(float(risk_input.entry_price) - float(risk_input.stop_price))
    if risk_input.atr is None:
        return 0.0
    return float(risk_input.atr) * stop_distance_atr


def _liq_safety_margin(
    risk_input: RiskInput,
    leverage_cap: float,
    stop_distance: float,
    min_gap_atr: float,
) -> dict[str, Any]:
    if risk_input.atr is None or risk_input.atr <= 0 or risk_input.action not in {"CONSIDER_LONG", "CONSIDER_SHORT"}:
        return {"status": "UNKNOWN", "gap": None}
    rough_liq_distance = risk_input.entry_price / max(leverage_cap, 1.0) * 0.85
    gap = rough_liq_distance - stop_distance
    status = "PASS" if gap >= float(risk_input.atr) * min_gap_atr else "FAIL"
    return {"status": status, "gap": round(gap, 8)}


def _round_down_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return int(value / step) * step
