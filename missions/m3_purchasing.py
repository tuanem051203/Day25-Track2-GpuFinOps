"""M3 — Purchasing Strategy: break-even, tier choice, spot-checkpoint sim (deck §4).

Run: python missions/m3_purchasing.py
"""
from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from missions._common import load_csv, num, catalog_by_type
from finops import pricing, sustainability

DAYS = 30

# Region for carbon comparison
DIRTIEST_REGION = "us-east-1"
CLEANEST_REGION = min(sustainability.REGION_CARBON, key=sustainability.REGION_CARBON.get)  # europe-north1


def run(verbose: bool = True) -> dict:
    jobs = load_csv("workloads.csv")
    cat = catalog_by_type()
    on_demand_monthly = optimized_monthly = 0.0
    recs = []

    # Extension 5: Carbon-aware scheduling
    carbon_now = 0.0
    carbon_clean = 0.0
    carbon_jobs = []

    for j in jobs:
        gtype = j["gpu_type"]
        ngpu = int(num(j["num_gpus"]))
        hpd = num(j["hours_per_day"])
        days = int(num(j["days"]))
        interruptible = bool(int(num(j["interruptible"])))
        c = cat[gtype]
        gpu_hours = hpd * DAYS * ngpu
        od = num(c["on_demand_hr"])
        on_demand_cost = gpu_hours * od

        tier = pricing.recommend_tier(hpd, interruptible, gpu_type=gtype, job_days=days)
        if tier == "spot":
            sim = pricing.spot_checkpoint_cost(gpu_hours, num(c["spot_hr"]), od)
            opt_cost = sim["spot_cost"]
        elif tier == "reserved_3yr":
            opt_cost = gpu_hours * num(c["reserved_3yr_hr"])
        elif tier == "reserved_1yr":
            opt_cost = gpu_hours * num(c["reserved_1yr_hr"])
        else:
            opt_cost = on_demand_cost

        on_demand_monthly += on_demand_cost
        optimized_monthly += opt_cost
        # Map extended tier names back for backward compatibility with verify.py checks
        display_tier = tier
        if display_tier in ("reserved_1yr", "reserved_3yr"):
            display_tier = "reserved"
        recs.append({"job_id": j["job_id"], "gpu_type": gtype, "tier": display_tier,
                     "on_demand": round(on_demand_cost), "optimized": round(opt_cost)})

        # Extension 5: Carbon calculation for interruptible jobs
        if interruptible:
            watts = num(c.get("watts", 0))
            kwh = (gpu_hours * watts / 1000.0)  # Total kWh
            gco2_current = kwh * sustainability.REGION_CARBON.get(DIRTIEST_REGION, 400) / 1000.0
            gco2_clean = kwh * sustainability.REGION_CARBON.get(CLEANEST_REGION, 30) / 1000.0
            carbon_now += gco2_current
            carbon_clean += gco2_clean
            carbon_jobs.append({
                "job_id": j["job_id"],
                "gpu_type": gtype,
                "gpu_hours": gpu_hours,
                "kwh": round(kwh, 0),
                "gco2_current": round(gco2_current, 0),
                "gco2_clean": round(gco2_clean, 0),
                "gco2_saved": round(gco2_current - gco2_clean, 0),
            })

    savings = on_demand_monthly - optimized_monthly
    savings_pct = savings / on_demand_monthly * 100 if on_demand_monthly else 0.0

    if verbose:
        print("== M3 Purchasing Strategy ==")
        print(f"break-even utilization @ 45% reserved discount = {pricing.break_even_utilization(0.45):.0%}")
        print(f"{'job':18}{'gpu':7}{'tier':11}{'on-demand':>12}{'optimized':>12}")
        for r in recs:
            print(f"{r['job_id']:18}{r['gpu_type']:7}{r['tier']:11}${r['on_demand']:>11,}${r['optimized']:>11,}")
        print(f"\nmonthly: on-demand ${on_demand_monthly:,.0f} -> optimized ${optimized_monthly:,.0f}  ({savings_pct:.1f}% saved)")

        # Extension 5: Carbon-aware scheduling
        if carbon_jobs:
            total_gco2_saved = carbon_now - carbon_clean
            print(f"\n--- Carbon-aware Scheduling (Extension 5) ---")
            print(f"Scheduling interruptible jobs in {CLEANEST_REGION} instead of {DIRTIEST_REGION}:")
            print(f"{'job':18}{'gpu':7}{'kWh':>8}{'gCO2 (now)':>11}{'gCO2 (clean)':>12}{'saved':>10}")
            for cj in carbon_jobs:
                print(f"{cj['job_id']:18}{cj['gpu_type']:7}{cj['kwh']:>8,}{cj['gco2_current']:>12,}{cj['gco2_clean']:>12,}{cj['gco2_saved']:>10,}")
            print(f"\nTotal carbon: {carbon_now:,.0f} gCO2 (now) -> {carbon_clean:,.0f} gCO2 (clean)")
            print(f"Carbon savings: {total_gco2_saved:,.0f} gCO2 ({total_gco2_saved/carbon_now*100:.0f}%)")

    return {"recommendations": recs, "on_demand_monthly": round(on_demand_monthly),
            "optimized_monthly": round(optimized_monthly), "savings_pct": round(savings_pct, 1),
            # Extension 5 carbon data
            "carbon_now_gco2": round(carbon_now, 0), "carbon_clean_gco2": round(carbon_clean, 0)}


if __name__ == "__main__":
    run()
