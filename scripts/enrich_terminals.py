"""
Add terminal assignments to real-day BTS files (which have no gate column).
Uses known JFK terminal assignments by IATA carrier code (2024 operations).

Usage:
    python scripts/enrich_terminals.py            # enriches all real_days/*.csv
    python scripts/enrich_terminals.py <file.csv> # single file
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pandas as pd

# JFK terminal assignments by IATA carrier code, 2024 ops.
# Sources: JFK terminal maps, airline press releases, OAG schedule data.
TERMINAL_BY_AIRLINE: dict[str, list[str]] = {
    # Delta (mainline + Delta Connection regional)
    "DL": ["T4"],
    "9E": ["T4"],        # Endeavor Air (Delta Connection)
    "OO": ["T4"],        # SkyWest (Delta Connection at JFK)
    "MQ": ["T8"],        # Envoy Air (American Eagle)
    "YX": ["T8"],        # Republic / Midwest (American connection)
    # JetBlue (hub)
    "B6": ["T5"],
    # American Airlines
    "AA": ["T8"],
    # British Airways
    "BA": ["T7"],
    # International Terminal 1 carriers
    "AF": ["T1"],        # Air France
    "LH": ["T1"],        # Lufthansa
    "KE": ["T1"],        # Korean Air
    "JL": ["T1"],        # Japan Airlines
    "NH": ["T1"],        # ANA
    "SQ": ["T1"],        # Singapore Airlines
    "EK": ["T4"],        # Emirates (moved to T4)
    "QR": ["T1"],        # Qatar Airways
    "EY": ["T1"],        # Etihad
    "TK": ["T1"],        # Turkish Airlines
    "CX": ["T1"],        # Cathay Pacific
    "VS": ["T4"],        # Virgin Atlantic (T4 with Delta)
    "KL": ["T4"],        # KLM (SkyTeam, T4 with Delta)
    "AZ": ["T1"],        # ITA Airways (Alitalia successor)
    "IB": ["T7"],        # Iberia (T7 with BA)
    # Domestic/LCC
    "UA": ["T7"],        # United (uses T7 at JFK)
    "WN": ["T4"],        # Southwest (limited JFK, T4)
    "NK": ["T5"],        # Spirit (uses T5)
    "F9": ["T4"],        # Frontier
    "G4": ["T4"],        # Allegiant
    "AS": ["T7"],        # Alaska Airlines
    # Canadian carriers
    "AC": ["T1"],        # Air Canada
    "WS": ["T4"],        # WestJet
    # Latin American
    "LA": ["T4"],        # LATAM
    "AM": ["T4"],        # Aeromexico
    # Cargo / charter — assign to T4 cargo apron
    "FX": ["T4"],
    "UPS": ["T4"],
}

# International airports (outside US/Canada/Mexico) — not used for terminal
# routing at JFK since all terminals handle some international, but useful
# for widebody type inference.
INTL_AIRPORTS = {
    "CDG", "LHR", "FRA", "AMS", "NRT", "HND", "ICN", "PEK", "PVG", "SIN",
    "BKK", "DXB", "DOH", "GRU", "GIG", "EZE", "BOG", "LIM", "SCL", "MEX",
    "CUN", "MBJ", "SJO", "GUA", "HAV", "BCN", "MAD", "FCO", "MUC", "ZRH",
    "BRU", "VIE", "DUB", "CPH", "ARN", "OSL", "HEL", "WAW", "PRG", "BUD",
    "IST", "CAI", "JNB", "NBO", "ADD", "SYD", "MEL", "AKL", "YYZ", "YUL",
    "YVR", "YYC",
}

DEFAULT_TERMINALS = ["T4", "T5", "T8"]


def assign_terminal(airline: str, origin: str, destination: str, rng: random.Random) -> str:
    options = TERMINAL_BY_AIRLINE.get(airline, DEFAULT_TERMINALS)
    return rng.choice(options)


def infer_aircraft_type(airline: str, origin: str, destination: str) -> str:
    """Widen to 'wide' for known international routes and widebody carriers."""
    other = origin if destination == "JFK" else destination
    if other in INTL_AIRPORTS:
        return "wide"
    widebody_airlines = {"DL", "AA", "UA", "BA", "AF", "LH", "KE", "JL", "NH",
                         "SQ", "EK", "QR", "EY", "TK", "CX", "VS", "KL", "AC"}
    if airline in widebody_airlines and other in INTL_AIRPORTS:
        return "wide"
    return "narrow"


def enrich(path: Path, seed: int = 42) -> None:
    rng = random.Random(seed)
    df = pd.read_csv(path)

    needs_terminal = "terminal" not in df.columns or df["terminal"].isna().any()
    needs_aircraft = "aircraft_type" not in df.columns or df["aircraft_type"].isna().any()

    if not needs_terminal and not needs_aircraft:
        non_default = (df["terminal"] != "T4").sum()
        if non_default > 0:
            print(f"  {path.name}: already enriched, skipping")
            return

    if needs_terminal:
        df["terminal"] = df.apply(
            lambda r: assign_terminal(
                str(r.get("airline", "")),
                str(r.get("origin", "")),
                str(r.get("destination", "")),
                rng,
            ),
            axis=1,
        )

    if needs_aircraft:
        df["aircraft_type"] = df.apply(
            lambda r: infer_aircraft_type(
                str(r.get("airline", "")),
                str(r.get("origin", "")),
                str(r.get("destination", "")),
            ),
            axis=1,
        )

    term_dist = df["terminal"].value_counts().to_dict()
    type_dist = df["aircraft_type"].value_counts().to_dict()
    df.to_csv(path, index=False)
    print(f"  {path.name}: enriched {len(df)} flights — terminals={term_dist} types={type_dist}")


def main():
    base = Path("data/real_days")
    if len(sys.argv) > 1:
        paths = [Path(a) for a in sys.argv[1:]]
    else:
        paths = sorted(base.glob("*_jfk.csv"))

    print(f"Enriching {len(paths)} file(s)...")
    for p in paths:
        enrich(p)
    print("Done.")


if __name__ == "__main__":
    main()
