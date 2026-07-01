"""M2 — Inference Cost Levers: $/1M-token, batch x cache x cascade (deck §7).

Run: python missions/m2_inference_levers.py
"""
from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from missions._common import load_csv, num
from finops import pricing
from finops.sustainability import wh_per_query

# $/1M tokens (input, output) — illustrative 2026.
MODEL_PRICES = {"small": (0.20, 0.40), "large": (3.00, 15.00)}
# Cache write cost per million tokens (Gemini charges ~$1.00/1M to write cache).
CACHE_WRITE_COST_PER_M = 1.00


def run(verbose: bool = True) -> dict:
    rows = load_csv("token_usage.csv")
    base_cost = opt_cost = 0.0
    total_tokens = 0

    # Extension 4: Reasoning tracking
    reasoning_cost = 0.0
    non_reasoning_cost = 0.0
    reasoning_tokens = 0
    non_reasoning_tokens = 0
    reasoning_count = 0
    non_reasoning_count = 0
    reasoning_wh = 0.0
    non_reasoning_wh = 0.0

    # Extension 3: Two-pass approach for cache economics
    # First pass: compute averages
    total_cached = 0
    total_cacheable = 0
    cache_requests = 0
    for r in rows:
        cached = int(num(r["cached_input_tokens"]))
        inp = int(num(r["input_tokens"]))
        if cached > 0:
            total_cached += cached
            total_cacheable += inp
            cache_requests += 1

    # Determine if cache is worth it (one decision for all requests)
    cache_worth_it = False
    cache_break_even = float("inf")
    if cache_requests > 0:
        avg_cacheable = total_cacheable / cache_requests
        avg_reads = total_cached / (total_cacheable or 1)
        cache_worth_it, cache_break_even = pricing.cache_is_worth_it(
            avg_reads, write_cost_per_m=CACHE_WRITE_COST_PER_M,
            read_discount=0.10,
            avg_cached_tokens_per_request=avg_cacheable
        )

    # Second pass: compute costs
    for r in rows:
        inp, out = int(num(r["input_tokens"])), int(num(r["output_tokens"]))
        cached = int(num(r["cached_input_tokens"]))
        is_batch = bool(int(num(r["is_batch"])))
        is_reasoning = bool(int(num(r["is_reasoning"])))
        total_tokens += inp + out

        # BASELINE: naive deployment — everything on the large model, no cache, no batch
        lin, lout = MODEL_PRICES["large"]
        base_cost += pricing.request_cost(inp, out, lin, lout)

        # OPTIMIZED: cascade (route_tier), prompt caching, batch API
        pin, pout = MODEL_PRICES[r["route_tier"]]

        opt_cost += pricing.request_cost(
            inp, out, pin, pout,
            cached_in=(cached if cache_worth_it else 0),
            batch=is_batch
        )

        # Extension 4: Reasoning budget tracking
        wh = wh_per_query(inp + out, is_reasoning=is_reasoning)
        if is_reasoning:
            reasoning_cost += pricing.request_cost(
                inp, out, pin, pout,
                cached_in=(cached if cache_worth_it else 0),
                batch=is_batch
            )
            reasoning_tokens += inp + out
            reasoning_count += 1
            reasoning_wh += wh
        else:
            non_reasoning_cost += pricing.request_cost(
                inp, out, pin, pout,
                cached_in=(cached if cache_worth_it else 0),
                batch=is_batch
            )
            non_reasoning_tokens += inp + out
            non_reasoning_count += 1
            non_reasoning_wh += wh

    base_pm = pricing.dollars_per_million(base_cost, total_tokens)
    opt_pm = pricing.dollars_per_million(opt_cost, total_tokens)
    savings_pct = (1 - opt_cost / base_cost) * 100 if base_cost else 0.0

    # Reasoning analysis
    total_opt = reasoning_cost + non_reasoning_cost
    reasoning_pct_cost = (reasoning_cost / total_opt * 100) if total_opt > 0 else 0
    reasoning_pct_traffic = (reasoning_count / len(rows) * 100) if rows else 0
    reasoning_pct_tokens = (reasoning_tokens / total_tokens * 100) if total_tokens > 0 else 0

    if verbose:
        print("== M2 Inference Cost Levers ==")
        print(f"requests={len(rows)}  tokens={total_tokens:,}")
        print(f"baseline  : ${base_cost:,.2f}/day   ${base_pm:.3f}/1M-token")
        print(f"optimized : ${opt_cost:,.2f}/day   ${opt_pm:.3f}/1M-token")
        print(f"savings   : {savings_pct:.1f}%  (cascade + caching + batch)")
        print(f"discount stack (batch + 100% cache): {pricing.discount_stack(batch=True, cache_hit_frac=1.0):.3f} of naive")

        # Extension 3: Cache economics
        print(f"\n--- Cache Economics (Extension 3) ---")
        print(f"  Avg cache reads per request: {avg_reads:.2f}x")
        print(f"  Cache worth it? {cache_worth_it}  (break-even: {cache_break_even:.2f} reads)")

        # Extension 4: Reasoning budget
        print(f"\n--- Reasoning Budget (Extension 4) ---")
        print(f"  Reasoning requests: {reasoning_count}/{len(rows)} ({reasoning_pct_traffic:.1f}%)")
        print(f"  Reasoning tokens: {reasoning_tokens:,}/{total_tokens:,} ({reasoning_pct_tokens:.1f}%)")
        print(f"  Reasoning cost: ${reasoning_cost:.2f}/day ({reasoning_pct_cost:.1f}% of optimized)")
        print(f"  Reasoning energy: {reasoning_wh:.1f} Wh/day")
        print(f"  Non-reasoning energy: {non_reasoning_wh:.1f} Wh/day")
        # Suggestion: cap reasoning to 10% traffic
        if reasoning_pct_traffic > 10:
            cap_frac = 0.10
            # Savings if we cap reasoning at 10% of requests
            # Assume reasoning requests cost on average reasoning_cost / reasoning_count
            avg_reasoning_cost_per_req = reasoning_cost / (reasoning_count or 1)
            excess_reasoning = reasoning_count - int(len(rows) * cap_frac)
            if excess_reasoning > 0:
                reasoning_saving = excess_reasoning * avg_reasoning_cost_per_req
                print(f"  >> PROPOSAL: Cap reasoning to 10% traffic, save ~${reasoning_saving:.2f}/day")

    return {
        "baseline_daily": round(base_cost, 2), "optimized_daily": round(opt_cost, 2),
        "baseline_per_m": round(base_pm, 3), "optimized_per_m": round(opt_pm, 3),
        "savings_pct": round(savings_pct, 1), "total_tokens": total_tokens,
        # Extension 4 reasoning data
        "reasoning_cost": round(reasoning_cost, 2),
        "non_reasoning_cost": round(non_reasoning_cost, 2),
        "reasoning_count": reasoning_count,
        "non_reasoning_count": non_reasoning_count,
        "reasoning_tokens": reasoning_tokens,
        "reasoning_pct_traffic": round(reasoning_pct_traffic, 1),
        "reasoning_pct_cost": round(reasoning_pct_cost, 1),
        "reasoning_wh": round(reasoning_wh, 1),
    }


if __name__ == "__main__":
    run()
