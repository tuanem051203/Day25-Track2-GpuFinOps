"""M5 — Optimization Report: combine M1-M4 into baseline-vs-optimized (deck §1/§11).

Run: python missions/m5_report.py   ->  outputs/report.md + outputs/savings.png
"""
from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import os
from missions._common import num, catalog_by_type, ROOT
from finops import report, sustainability
from missions import m1_efficiency_audit, m2_inference_levers, m3_purchasing

DAYS = 30
# one tier down for over-provisioned ("util-lie") GPUs
RIGHTSIZE_MAP = {"H100": "A100", "H200": "H100", "A100": "A10G", "A10G": "L4", "L4": "L4"}

# Regions for carbon-aware comparison
REGIONS = ["us-east-1", "us-west-2", "europe-north1", "europe-central2", "us-east-wa"]


def run(verbose: bool = True) -> dict:
    r1 = m1_efficiency_audit.run(verbose=False)
    r2 = m2_inference_levers.run(verbose=False)
    r3 = m3_purchasing.run(verbose=False)
    cat = catalog_by_type()

    # --- buckets ---
    infer_savings = (r2["baseline_daily"] - r2["optimized_daily"]) * DAYS
    purchasing_savings = r3["on_demand_monthly"] - r3["optimized_monthly"]

    idle_savings = r1["idle_waste_daily"] * DAYS
    rightsize_savings = 0.0
    for lie in r1["lies"]:
        cur = lie["gpu_type"]
        tgt = RIGHTSIZE_MAP.get(cur, cur)
        delta = num(cat[cur]["on_demand_hr"]) - num(cat[tgt]["on_demand_hr"])
        rightsize_savings += max(0.0, delta) * 24 * DAYS

    # Also add MBU right-size savings from Extension 2
    mbu_savings = r1.get("rightsize_monthly", 0)
    # Avoid double-counting: MBU suggestions are separate from util-lie rightsizing

    levers = {
        "Inference (cascade/cache/batch)": round(infer_savings),
        "Purchasing (spot/reserved)": round(purchasing_savings),
        "Right-size util-lies": round(rightsize_savings),
        "Right-size by MBU": round(mbu_savings),
        "Kill idle GPUs": round(idle_savings),
    }
    baseline = r2["baseline_daily"] * DAYS + r3["on_demand_monthly"]
    optimized = baseline - sum(levers.values())
    total_pct = sum(levers.values()) / baseline * 100 if baseline else 0.0

    # --- sustainability snapshot ---
    median_tokens = 800
    wh = sustainability.wh_per_query(median_tokens)
    sust = {
        "wh_per_query": wh,
        "carbon_g": sustainability.carbon_g(wh, "us-east-1"),
        "best_region": min(sustainability.REGION_CARBON, key=sustainability.REGION_CARBON.get),
    }

    # Extension 4: Append reasoning analysis to report
    reasoning_lines = []
    if "reasoning_pct_traffic" in r2:
        reasoning_lines = [
            "",
            "## Reasoning Budget Analysis (Extension 4)",
            "",
            f"- Reasoning requests: {r2['reasoning_count']}/{r2['reasoning_count'] + r2['non_reasoning_count']} ({r2['reasoning_pct_traffic']}% of traffic)",
            f"- Reasoning cost: ${r2['reasoning_cost']:.2f}/day ({r2['reasoning_pct_cost']}% of optimized cost)",
            f"- Reasoning energy: {r2['reasoning_wh']:.1f} Wh/day",
            f"- Recommendation: Cap reasoning to 10% traffic to reduce cost by ~{r2['reasoning_pct_cost'] - 10:.0f}% points",
        ]

    # Extension 5: Carbon-aware region comparison
    carbon_lines = []
    cat_data = catalog_by_type()
    # Calculate GPU energy cost for comparison
    h100_watts = num(cat_data.get("H100", {}).get("watts", 700))
    gpu_kwh_per_hour = h100_watts / 1000.0  # kW per GPU-hour
    carbon_lines.append("")
    carbon_lines.append("## Carbon-Aware Region Comparison (Extension 5)")
    carbon_lines.append("")
    carbon_lines.append("| Region | gCO2/kWh | $/kWh | gCO2/GPU-hr | $/GPU-hr (energy) |")
    carbon_lines.append("|---|---|---|---|---|")
    for region in REGIONS:
        gco2 = sustainability.REGION_CARBON.get(region, 0)
        price = sustainability.REGION_PRICE_KWH.get(region, 0)
        gco2_per_gpu_hr = gco2 * gpu_kwh_per_hour
        energy_per_gpu_hr = price * gpu_kwh_per_hour
        carbon_lines.append(f"| {region} | {gco2} | ${price:.3f} | {gco2_per_gpu_hr:.0f} | ${energy_per_gpu_hr:.4f} |")

    # Build report with all extensions
    md = report.build_report(baseline, optimized, levers, sustainability=sust)
    # Append extension sections
    md += "\n" + "\n".join(reasoning_lines)
    md += "\n" + "\n".join(carbon_lines)

    out_md = os.path.join(ROOT, "outputs", "report.md")
    os.makedirs(os.path.dirname(out_md), exist_ok=True)
    with open(out_md, "w") as f:
        f.write(md)
    png = report.savings_waterfall(levers, os.path.join(ROOT, "outputs", "savings.png"))

    if verbose:
        print("== M5 Optimization Report ==")
        print(md)
        print(f"\nWritten: outputs/report.md" + (f" + outputs/savings.png" if png else " (matplotlib absent: PNG skipped)"))

    return {"baseline_monthly": round(baseline), "optimized_monthly": round(optimized),
            "levers": levers, "total_savings_pct": round(total_pct, 1)}


if __name__ == "__main__":
    run()
