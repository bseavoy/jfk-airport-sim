"""Load a flight schedule CSV into Flight objects."""

from __future__ import annotations

import pandas as pd

from .resources import Flight

WEIGHT_CLASS_MAP = {
    "narrow": "Large",
    "wide": "Heavy",
    "heavy": "Heavy",
}


def _parse_hhmm(value: str) -> float:
    s = str(value).strip()
    if ":" in s:
        h, m = s.split(":")
        return int(h) * 60 + int(m)
    return float(s)


def load_schedule(csv_path: str) -> list[Flight]:
    df = pd.read_csv(csv_path, dtype=str)
    flights: list[Flight] = []
    for _, row in df.iterrows():
        aircraft_type = row.get("aircraft_type", "narrow")
        if aircraft_type and str(aircraft_type) not in ("nan", ""):
            aircraft_type = str(aircraft_type).strip().lower()
        else:
            aircraft_type = "narrow"
        weight_class = row.get("aircraft_weight_class", "")
        if not weight_class or str(weight_class) == "nan":
            weight_class = WEIGHT_CLASS_MAP.get(aircraft_type, "Large")
        terminal = row.get("terminal", "")
        if not terminal or str(terminal) == "nan":
            terminal = "T4"
        flights.append(
            Flight(
                flight_id=str(row["flight_id"]),
                airline=str(row["airline"]),
                flight_number=str(row["flight_number"]),
                aircraft_type=aircraft_type,
                tail_number=str(row.get("tail_number", "")),
                operation=str(row["operation"]).strip().upper(),
                scheduled_min=_parse_hhmm(row["scheduled_time"]),
                origin=str(row.get("origin", "")),
                destination=str(row.get("destination", "")),
                gate_assigned=str(row.get("gate_assigned", "")),
                terminal=terminal,
                weight_class=weight_class,
            )
        )
    flights.sort(key=lambda f: f.scheduled_min)
    return flights
