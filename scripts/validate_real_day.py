"""
Run the JFK simulation against a real-day BTS CSV and compare taxi statistics.

Usage (from project root):
    PYTHONPATH=. python scripts/validate_real_day.py data/real_days/2024-06-03_jfk.csv
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from jfk_sim.simulation import AirportSimulation
from jfk_sim.config import load_config, load_ground_programs

CONFIG_PATH = str(Path(__file__).parent.parent / "data" / "jfk_config.json")

W = 74  # output width


def _pct(vals, p):
    if not vals:
        return float("nan")
    return float(np.percentile(vals, p))


def run_validation(csv_path: str, gdp_path: str = None) -> dict:
    p = Path(csv_path)
    if not p.exists():
        print(f"Missing: {csv_path}")
        sys.exit(1)

    programs = load_ground_programs(gdp_path) if gdp_path else []
    if programs:
        print(f"Ground programs: {gdp_path} ({len(programs)} program(s))")
    print(f"Running sim for {p.name}...")
    cfg = load_config(CONFIG_PATH)
    sim = AirportSimulation(config=cfg, seed=42, ground_programs=programs)
    sim.load_schedule(csv_path)
    metrics = sim.run()

    # --- Real data ---
    df = pd.read_csv(csv_path)
    arr_real = df[df["operation"] == "ARR"]["actual_taxi_min"].dropna().tolist()
    dep_real = df[df["operation"] == "DEP"]["actual_taxi_min"].dropna().tolist()

    # --- Sim data ---
    arr_sim = [r.taxi_min for r in metrics.records if r.operation == "ARR" and r.taxi_min]
    dep_sim = [r.taxi_min for r in metrics.records if r.operation == "DEP" and r.taxi_min]
    crossing_waits = [r.crossing_wait_min for r in metrics.records
                      if r.operation == "DEP" and r.crossing_wait_min > 0]

    # --- A0 / D0 rates ---
    arr_recs = [r for r in metrics.records if r.operation == "ARR"]
    dep_recs = [r for r in metrics.records if r.operation == "DEP"]
    arr_ot = df[(df["operation"] == "ARR") & df["actual_min"].notna() & df["scheduled_min"].notna()]
    dep_ot = df[(df["operation"] == "DEP") & df["actual_min"].notna() & df["scheduled_min"].notna()]
    real_a0 = float((arr_ot["actual_min"] <= arr_ot["scheduled_min"]).mean()) if len(arr_ot) else 0.0
    real_d0 = float((dep_ot["actual_min"] <= dep_ot["scheduled_min"]).mean()) if len(dep_ot) else 0.0
    sim_a0 = sum(1 for r in arr_recs if r.gate_in_min is not None and r.gate_in_min <= r.scheduled_min) / len(arr_recs) if arr_recs else 0.0
    sim_d0 = sum(1 for r in dep_recs if r.pushback_min is not None and r.pushback_min <= r.scheduled_min) / len(dep_recs) if dep_recs else 0.0

    def _fmt_delta(v):
        return f"{v:+.2f}" if abs(v) >= 0.01 else " 0.00"

    def _flag(delta, threshold=3.0):
        return " !" if abs(delta) >= threshold else ""

    date_str = p.name.replace("_jfk.csv", "")
    sim_count = len(metrics.records)
    total_in_schedule = len(df)

    print()
    print("=" * W)
    print(f"  {date_str}  |  {sim_count}/{total_in_schedule} flights simulated")
    print("=" * W)
    print()

    rows = [
        ("taxi-in mean (min)",  np.mean(arr_real) if arr_real else 0, np.mean(arr_sim) if arr_sim else 0),
        ("taxi-in std (min)",   np.std(arr_real) if arr_real else 0,  np.std(arr_sim) if arr_sim else 0),
        ("taxi-in p50 (min)",   _pct(arr_real, 50), _pct(arr_sim, 50)),
        ("taxi-in p95 (min)",   _pct(arr_real, 95), _pct(arr_sim, 95)),
    ]
    print(f"  {'Metric':<32} {'Actual':>8}  {'Simulated':>10}  {'Delta':>8}")
    print("-" * W)
    for label, actual, sim_val in rows:
        delta = sim_val - actual
        flag = _flag(delta)
        print(f"  {label:<32} {actual:>8.2f}  {sim_val:>10.2f}  {_fmt_delta(delta):>8}{flag}")

    print("-" * W)
    dep_rows = [
        ("taxi-out mean (min)", np.mean(dep_real) if dep_real else 0, np.mean(dep_sim) if dep_sim else 0),
        ("taxi-out std (min)",  np.std(dep_real) if dep_real else 0,  np.std(dep_sim) if dep_sim else 0),
        ("taxi-out p50 (min)",  _pct(dep_real, 50), _pct(dep_sim, 50)),
        ("taxi-out p95 (min)",  _pct(dep_real, 95), _pct(dep_sim, 95)),
    ]
    for label, actual, sim_val in dep_rows:
        delta = sim_val - actual
        flag = _flag(delta)
        print(f"  {label:<32} {actual:>8.2f}  {sim_val:>10.2f}  {_fmt_delta(delta):>8}{flag}")

    print("-" * W)
    summary = metrics.summary()
    arr_rwy = summary.get("arrival_runway_wait", {})
    dep_rwy = summary.get("departure_runway_wait", {})
    cross = summary.get("crossing_wait", {})
    print(f"  {'arr runway wait mean':<32} {'(sim only)':>8}  {arr_rwy.get('mean', 0):>10.2f}")
    print(f"  {'arr runway wait p95':<32} {'(sim only)':>8}  {arr_rwy.get('p95', 0):>10.2f}")
    print(f"  {'dep runway wait mean':<32} {'(sim only)':>8}  {dep_rwy.get('mean', 0):>10.2f}")
    print(f"  {'dep runway wait p95':<32} {'(sim only)':>8}  {dep_rwy.get('p95', 0):>10.2f}")
    if cross.get("mean", 0) > 0:
        print(f"  {'crossing wait mean':<32} {'(sim only)':>8}  {cross.get('mean', 0):>10.2f}")
        print(f"  {'crossing wait p95':<32} {'(sim only)':>8}  {cross.get('p95', 0):>10.2f}")
    print("-" * W)
    print(f"  {'A0 rate (gate arr on time)':<32} {real_a0:>8.1%}  {sim_a0:>10.1%}  {sim_a0 - real_a0:>+7.1%}")
    print(f"  {'D0 rate (gate dep on time)':<32} {real_d0:>8.1%}  {sim_d0:>10.1%}  {sim_d0 - real_d0:>+7.1%}")

    # --- Per-hour breakdown ---
    by_hour = metrics.by_hour()
    real_arr_by_hour: dict = {}
    real_dep_by_hour: dict = {}
    df["sched_hour"] = df["scheduled_min"].apply(
        lambda x: int(float(x)) // 60 % 24 if str(x) not in ("nan", "") else -1
    )
    for h, grp in df.groupby("sched_hour"):
        if h < 0:
            continue
        ai = grp[grp["operation"] == "ARR"]["actual_taxi_min"].dropna()
        di = grp[grp["operation"] == "DEP"]["actual_taxi_min"].dropna()
        if len(ai):
            real_arr_by_hour[h] = float(ai.mean())
        if len(di):
            real_dep_by_hour[h] = float(di.mean())

    # Determine peak hours (top 25% of flight counts)
    total_dep_sched = df[df["operation"] == "DEP"]["sched_hour"].value_counts()
    if not total_dep_sched.empty:
        peak_threshold = total_dep_sched.quantile(0.75)
        peak_hours = set(total_dep_sched[total_dep_sched >= peak_threshold].index.tolist())
    else:
        peak_hours = set()

    print()
    print(f"{'':24}--- Time-of-Day Breakdown{'':25}")
    header = f"   {'Hr':>4}   {'Act TI':>6}  {'Sim TI':>6}  {'ΔTI':>5}   {'Act TO':>6}  {'Sim TO':>6}  {'ΔTO':>5}  {'XWt':>5}  {'RwyWt':>5}  Peak"
    print(header)
    print("  " + "-" * (W - 2))

    all_hours = sorted(set(list(real_arr_by_hour) + list(real_dep_by_hour) + list(by_hour)))
    for h in all_hours:
        sim_h = by_hour.get(h, {})
        ati = real_arr_by_hour.get(h, float("nan"))
        sto = real_dep_by_hour.get(h, float("nan"))
        sim_ti = sim_h.get("arr_taxi", {}) or {}
        sim_to = sim_h.get("dep_taxi", {}) or {}
        sim_rwy = sim_h.get("dep_rwy_wait", {}) or {}
        sim_cross = sim_h.get("crossing_wait", {}) or {}

        sti = sim_ti.get("mean", float("nan"))
        ssto = sim_to.get("mean", float("nan"))
        rwy = sim_rwy.get("mean", 0.0) or 0.0
        xwt = sim_cross.get("mean", 0.0) or 0.0

        dti = sti - ati if not (np.isnan(sti) or np.isnan(ati)) else float("nan")
        dto = ssto - sto if not (np.isnan(ssto) or np.isnan(sto)) else float("nan")

        peak_marker = " ◀" if h in peak_hours else "  "

        def _fv(v):
            return f"{v:6.1f}" if not np.isnan(v) else "   ---"

        def _fdelta(v):
            return f"{v:+5.1f}" if not np.isnan(v) else "  ---"

        print(
            f"  {h:02d}:xx  {_fv(ati)}  {_fv(sti)}  {_fdelta(dti)}  "
            f" {_fv(sto)}  {_fv(ssto)}  {_fdelta(dto)}  "
            f"{xwt:5.1f}  {rwy:5.1f} {peak_marker}"
        )

    print("=" * W)
    return summary


if __name__ == "__main__":
    import argparse as _ap
    parser = _ap.ArgumentParser()
    parser.add_argument("csv", help="Path to real-day CSV file")
    parser.add_argument("--gdp", metavar="FILE",
                        help="Ground programs JSON (e.g. data/ground_programs/2024-07-16_jfk.json)")
    args = parser.parse_args()
    run_validation(args.csv, gdp_path=args.gdp)
