"""
Download BTS On-Time Performance data for JFK, extract summer 2024 weekdays.
Terminal assignments are inferred from airline + destination (no gate data in BTS).

Usage:
    python scripts/fetch_bts_data.py [--months 6 7 8] [--year 2024] [--days-per-month 2]

Output:
    data/real_days/YYYY-MM-DD_jfk.csv   — one file per selected day
    data/real_days/manifest.json        — metadata for each day file
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent))
from enrich_terminals import assign_terminal, infer_aircraft_type

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

BTS_ZIP_URL = (
    "https://transtats.bts.gov/PREZIP/"
    "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip"
)

KEEP_FIELDS = [
    "FlightDate",
    "Reporting_Airline",
    "Tail_Number",
    "Flight_Number_Reporting_Airline",
    "Origin",
    "Dest",
    "CRSDepTime",
    "DepTime",
    "DepDelay",
    "TaxiOut",
    "WheelsOff",
    "WheelsOn",
    "TaxiIn",
    "CRSArrTime",
    "ArrTime",
    "ArrDelay",
    "Cancelled",
    "Diverted",
    "Distance",
]

WEEKDAY_CODES = {1, 2, 3, 4, 5}  # Mon-Fri

OUT_DIR = Path(__file__).parent.parent / "data" / "real_days"


def download_month(year: int, month: int, tmp_dir: Path) -> pd.DataFrame:
    url = BTS_ZIP_URL.format(year=year, month=month)
    zip_path = tmp_dir / f"bts_{year}_{month:02d}.zip"

    if not zip_path.exists():
        log.info(f"Downloading {url} ...")
        resp = requests.get(url, stream=True, timeout=120)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with open(zip_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    print(f"\r  {pct:.1f}%  ({downloaded >> 20} MB)", end="", flush=True)
        print()
        log.info(f"Saved {zip_path} ({zip_path.stat().st_size >> 20} MB)")
    else:
        log.info(f"Using cached {zip_path}")

    log.info("Extracting and filtering for JFK weekday flights...")
    with zipfile.ZipFile(zip_path) as zf:
        csv_name = next(n for n in zf.namelist() if n.endswith(".csv"))
        with zf.open(csv_name) as fh:
            df = pd.read_csv(
                fh,
                usecols=lambda c: c in KEEP_FIELDS + ["DayOfWeek"],
                dtype=str,
                low_memory=False,
            )

    df = df[df["DayOfWeek"].isin([str(d) for d in WEEKDAY_CODES])]
    jfk_mask = (df["Origin"] == "JFK") | (df["Dest"] == "JFK")
    df = df[jfk_mask].copy()
    df["FlightDate"] = pd.to_datetime(df["FlightDate"])
    log.info(f"  {len(df):,} JFK weekday rows for {year}-{month:02d}")
    return df


def pick_days(df: pd.DataFrame, n: int, rng: random.Random) -> list[date]:
    dates = sorted(df["FlightDate"].dt.date.unique())
    if len(dates) <= n:
        return dates
    step = len(dates) // n
    return [dates[i * step] for i in range(n)]


def build_day_file(df: pd.DataFrame, day: date, rng: random.Random | None = None) -> pd.DataFrame:
    if rng is None:
        rng = random.Random(42)
    day_df = df[df["FlightDate"].dt.date == day].copy()
    rows = []

    for _, r in day_df.iterrows():
        cancelled = str(r.get("Cancelled", "0")).strip() == "1"
        diverted = str(r.get("Diverted", "0")).strip() == "1"
        if cancelled or diverted:
            continue

        airline = str(r.get("Reporting_Airline", "")).strip()
        fn = str(r.get("Flight_Number_Reporting_Airline", "")).strip()
        tail = str(r.get("Tail_Number", "")).strip()
        origin = str(r.get("Origin", "")).strip()
        dest = str(r.get("Dest", "")).strip()
        terminal = assign_terminal(airline, origin, dest, rng)
        aircraft_type = infer_aircraft_type(airline, origin, dest)

        def _hhmm_to_min(val):
            s = str(val).strip().replace(".0", "")
            if not s or s in ("nan", ""):
                return None
            s = s.zfill(4)
            try:
                return int(s[:2]) * 60 + int(s[2:])
            except ValueError:
                return None

        if origin == "JFK":
            sched = _hhmm_to_min(r.get("CRSDepTime"))
            actual = _hhmm_to_min(r.get("DepTime"))
            taxi = r.get("TaxiOut")
            wheels = _hhmm_to_min(r.get("WheelsOff"))
            if sched is None:
                continue
            rows.append({
                "flight_date": day.isoformat(),
                "flight_id": f"{airline}{fn}",
                "airline": airline,
                "flight_number": fn,
                "tail_number": tail,
                "operation": "DEP",
                "origin": origin,
                "destination": dest,
                "terminal": terminal,
                "aircraft_type": aircraft_type,
                "scheduled_time": f"{int(sched)//60:02d}:{int(sched)%60:02d}",
                "scheduled_min": sched,
                "actual_time": f"{int(actual)//60:02d}:{int(actual)%60:02d}" if actual is not None else "",
                "actual_min": actual,
                "actual_taxi_min": float(taxi) if taxi and str(taxi) not in ("nan", "") else None,
                "wheels_time_min": wheels,
                "delay_min": float(r.get("DepDelay", 0) or 0),
                "distance_mi": float(r.get("Distance", 0) or 0),
            })

        if dest == "JFK":
            sched = _hhmm_to_min(r.get("CRSArrTime"))
            actual = _hhmm_to_min(r.get("ArrTime"))
            taxi = r.get("TaxiIn")
            wheels = _hhmm_to_min(r.get("WheelsOn"))
            if sched is None:
                continue
            rows.append({
                "flight_date": day.isoformat(),
                "flight_id": f"{airline}{fn}",
                "airline": airline,
                "flight_number": fn,
                "tail_number": tail,
                "operation": "ARR",
                "origin": origin,
                "destination": dest,
                "terminal": terminal,
                "aircraft_type": aircraft_type,
                "scheduled_time": f"{int(sched)//60:02d}:{int(sched)%60:02d}",
                "scheduled_min": sched,
                "actual_min": actual,
                "actual_taxi_min": float(taxi) if taxi and str(taxi) not in ("nan", "") else None,
                "wheels_time_min": wheels,
                "delay_min": float(r.get("ArrDelay", 0) or 0),
                "distance_mi": float(r.get("Distance", 0) or 0),
            })

    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values("scheduled_min").reset_index(drop=True)
        result["flight_id"] = [f"F{i+1:04d}" for i in range(len(result))]
    return result


def main():
    p = argparse.ArgumentParser(description="Fetch JFK BTS data for summer 2024 weekdays")
    p.add_argument("--year", type=int, default=2024)
    p.add_argument("--months", nargs="+", type=int, default=[6, 7, 8])
    p.add_argument("--days-per-month", type=int, default=2)
    p.add_argument("--keep-zips", action="store_true")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp_dir = OUT_DIR / "_tmp"
    tmp_dir.mkdir(exist_ok=True)

    rng = random.Random(args.seed)
    manifest = []

    for month in args.months:
        df_month = download_month(args.year, month, tmp_dir)
        selected_days = pick_days(df_month, args.days_per_month, rng)
        log.info(f"Selected days for {args.year}-{month:02d}: {[str(d) for d in selected_days]}")

        for day in selected_days:
            day_df = build_day_file(df_month, day, rng)
            if day_df.empty:
                log.warning(f"  No usable flights for {day}, skipping")
                continue

            out_path = OUT_DIR / f"{day}_jfk.csv"
            day_df.to_csv(out_path, index=False)

            arr = day_df[day_df["operation"] == "ARR"]
            dep = day_df[day_df["operation"] == "DEP"]
            arr_taxi = arr["actual_taxi_min"].dropna()
            dep_taxi = dep["actual_taxi_min"].dropna()

            entry = {
                "date": day.isoformat(),
                "file": out_path.name,
                "total_flights": len(day_df),
                "arrivals": len(arr),
                "departures": len(dep),
                "actual_taxi_in_mean": round(arr_taxi.mean(), 2) if len(arr_taxi) else None,
                "actual_taxi_in_std": round(arr_taxi.std(), 2) if len(arr_taxi) else None,
                "actual_taxi_out_mean": round(dep_taxi.mean(), 2) if len(dep_taxi) else None,
                "actual_taxi_out_std": round(dep_taxi.std(), 2) if len(dep_taxi) else None,
            }
            manifest.append(entry)
            log.info(
                f"  {day}: {len(day_df)} flights | "
                f"taxi-in {entry['actual_taxi_in_mean']:.1f}±{entry['actual_taxi_in_std']:.1f} | "
                f"taxi-out {entry['actual_taxi_out_mean']:.1f}±{entry['actual_taxi_out_std']:.1f}"
            )

    manifest_path = OUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    log.info(f"Manifest written to {manifest_path}")

    if not args.keep_zips:
        for zp in tmp_dir.glob("*.zip"):
            zp.unlink()
            log.info(f"Removed {zp.name}")

    print("\n=== Summary ===")
    print(f"Days extracted: {len(manifest)}")
    for m in manifest:
        print(
            f"  {m['date']}: {m['total_flights']} flights | "
            f"taxi-in {m['actual_taxi_in_mean']:.1f} min | "
            f"taxi-out {m['actual_taxi_out_mean']:.1f} min"
        )


if __name__ == "__main__":
    main()
