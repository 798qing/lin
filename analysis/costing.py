from __future__ import annotations

from pathlib import Path
from typing import Any

from analysis.simple_yaml import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def estimate_ticket_cost(config_path: Path = ROOT / "config" / "cost_budget.yaml") -> dict[str, Any]:
    config = load_yaml(config_path)
    chars_per_token = int(config["token_estimation"]["chars_per_token"])
    event_input_tokens = int(config["token_estimation"]["event_input_tokens"])
    max_ticket_cost = float(config["max_ticket_cost_usd"])
    agents = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost = 0.0

    for name in sorted(config["agents"]):
        agent = config["agents"][name]
        prompt_tokens = _estimate_prompt_tokens(ROOT / agent["prompt_path"], chars_per_token)
        input_tokens = prompt_tokens + event_input_tokens + int(agent["input_tokens"])
        output_tokens = int(agent["output_tokens"])
        input_cost = input_tokens * float(agent["usd_per_1m_input_tokens"]) / 1_000_000
        output_cost = output_tokens * float(agent["usd_per_1m_output_tokens"]) / 1_000_000
        cost = round(input_cost + output_cost, 8)
        agents.append(
            {
                "agent": name,
                "prompt_path": agent["prompt_path"],
                "prompt_tokens_estimate": prompt_tokens,
                "input_tokens_estimate": input_tokens,
                "output_tokens_estimate": output_tokens,
                "cost_usd_estimate": cost,
            }
        )
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens
        total_cost += cost

    total_cost = round(total_cost, 8)
    return {
        "version": config["version"],
        "status": "PASS" if total_cost <= max_ticket_cost else "REDUCE_AGENT_ROUNDS",
        "max_ticket_cost_usd": max_ticket_cost,
        "total_input_tokens_estimate": total_input_tokens,
        "total_output_tokens_estimate": total_output_tokens,
        "total_cost_usd_estimate": total_cost,
        "agents": agents,
        "private_api": "not_used",
    }


def _estimate_prompt_tokens(path: Path, chars_per_token: int) -> int:
    text = path.read_text(encoding="utf-8")
    return max(1, (len(text) + chars_per_token - 1) // chars_per_token)
