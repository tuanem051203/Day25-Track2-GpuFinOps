"""M1 — Efficiency Audit: MFU/MBU, the GPU-Util lie, and idle waste (deck §5).

Run: python missions/m1_efficiency_audit.py
"""
from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from collections import defaultdict
from missions._common import load_csv, num, catalog_by_type
from finops import metrics

# Memory bandwidth targets — GPUs sorted by peak_bw_tbs (ascending).
# For memory-bound workloads (decode), we need enough bandwidth.
# $/GB-VRAM measures cost efficiency for memory-capacity-bound workloads.

def _rightsize_suggestions(summary: list, cat: dict) -> tuple[list, float]:
    """Right-size memory-bound GPUs: suggest cheaper alternatives based on MBU and $/GB-VRAM.

    For each GPU that is memory-bound (MBU < 0.5), find the cheapest GPU type
    that offers similar or better memory bandwidth.
    Returns (suggestions_list, monthly_savings).
    """
    # Build $/GB-VRAM table
    gpu_vram = {}
    for gtype, row in cat.items():
        hbm = num(row["hbm_gb"])
        od_hr = num(row["on_demand_hr"])
        vram_cost = od_hr / hbm if hbm > 0 else 999
        gpu_vram[gtype] = {
            "on_demand_hr": od_hr,
            "hbm_gb": hbm,
            "dollar_per_gb_vram": round(vram_cost, 4),
            "peak_bw_tbs": num(row["peak_bw_tbs"]),
        }

    DAYS = 30
    suggestions = []
    total_savings = 0.0

    for s in summary:
        if s["mbu"] >= 0.5:
            continue  # Not memory-bound
        cur_type = s["gpu_type"]
        cur = gpu_vram.get(cur_type)
        if not cur:
            continue

        # Find cheapest alternative with >= same bandwidth or better $/GB-VRAM
        best = None
        best_savings = 0.0
        for alt_type, alt in gpu_vram.items():
            if alt_type == cur_type:
                continue
            # Must have enough bandwidth for the workload
            if alt["peak_bw_tbs"] < cur["peak_bw_tbs"] * 0.7:
                continue  # Too slow
            # Calculate savings
            hourly_saving = cur["on_demand_hr"] - alt["on_demand_hr"]
            if hourly_saving > best_savings:
                best_savings = hourly_saving
                best = alt_type

        if best and best_savings > 0:
            monthly = best_savings * 24 * DAYS
            total_savings += monthly
            suggestions.append({
                "gpu_id": s["gpu_id"],
                "current_type": cur_type,
                "current_mbu": s["mbu"],
                "current_bw_tbs": cur["peak_bw_tbs"],
                "suggested_type": best,
                "suggested_bw_tbs": gpu_vram[best]["peak_bw_tbs"],
                "savings_per_hour": round(best_savings, 2),
                "monthly_savings": round(monthly),
            })

    return suggestions, round(total_savings)


def run(verbose: bool = True) -> dict:
    tel = load_csv("gpu_telemetry.csv")
    cat = catalog_by_type()

    # per-row MFU/MBU, then aggregate per GPU
    agg = defaultdict(lambda: {"util": [], "mfu": [], "mbu": [], "type": None, "idle_hours": 0})
    for r in tel:
        gtype = r["gpu_type"]
        peak_fp16 = num(cat[gtype]["peak_tflops_fp16"])
        peak_bw = num(cat[gtype]["peak_bw_tbs"])
        mfu = metrics.compute_mfu(num(r["achieved_tflops"]), peak_fp16)
        mbu = metrics.compute_mbu(num(r["achieved_bw_tbs"]), peak_bw)
        a = agg[r["gpu_id"]]
        a["type"] = gtype
        a["util"].append(num(r["gpu_util_pct"]))
        a["mfu"].append(mfu)
        a["mbu"].append(mbu)
        if num(r["gpu_util_pct"]) < 10:  # effectively idle this interval (1h)
            a["idle_hours"] += 1

    summary = []
    for gid, a in agg.items():
        summary.append({
            "gpu_id": gid, "gpu_type": a["type"],
            "gpu_util_pct": round(sum(a["util"]) / len(a["util"]), 1),
            "mfu": round(sum(a["mfu"]) / len(a["mfu"]), 3),
            "mbu": round(sum(a["mbu"]) / len(a["mbu"]), 3),
            "idle_hours": a["idle_hours"],
        })

    lies = metrics.flag_util_lies(summary)
    idle_waste = 0.0
    for s in summary:
        on_demand = num(catalog_by_type()[s["gpu_type"]]["on_demand_hr"])
        idle_waste += metrics.idle_waste_usd(s["idle_hours"], on_demand)

    rightsize_suggestions, rightsize_monthly = _rightsize_suggestions(summary, cat)

    if verbose:
        print("== M1 Efficiency Audit ==")
        print(f"{'GPU':14}{'type':7}{'util%':>7}{'MFU':>7}{'MBU':>7}{'idle_h':>8}")
        for s in sorted(summary, key=lambda x: x["mfu"]):
            print(f"{s['gpu_id']:14}{s['gpu_type']:7}{s['gpu_util_pct']:>7}{s['mfu']:>7}{s['mbu']:>7}{s['idle_hours']:>8}")
        print(f"\nGPU-Util LIES (util>=90% but MFU<30%): {[l['gpu_id'] for l in lies]}")
        print(f"Idle waste (1 day): ${idle_waste:,.2f}  ->  ${idle_waste*30:,.0f}/month")
        # Extension 2: Right-sizing by MBU
        if rightsize_suggestions:
            print("\n--- Right-sizing by MBU (Extension 2) ---")
            print(f"{'GPU':14}{'from':7}{'to':7}{'curr_bw':>8}{'new_bw':>8}{'$/hr diff':>9}{'$/mo':>8}")
            for sug in rightsize_suggestions:
                print(f"{sug['gpu_id']:14}{sug['current_type']:7}{sug['suggested_type']:7}"
                      f"{sug['current_bw_tbs']:>8.2f}{sug['suggested_bw_tbs']:>8.2f}"
                      f"${sug['savings_per_hour']:>7.2f}{sug['monthly_savings']:>8,}")
            print(f"Total MBU right-size savings: ${rightsize_monthly:,}/month")

    return {"summary": summary, "lies": lies, "idle_waste_daily": round(idle_waste, 2),
            "rightsize_suggestions": rightsize_suggestions,
            "rightsize_monthly": rightsize_monthly}


if __name__ == "__main__":
    run()
