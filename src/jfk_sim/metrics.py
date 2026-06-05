"""Collect and summarise simulation metrics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np


@dataclass
class FlightRecord:
    flight_id: str
    operation: str          # ARR | DEP
    scheduled_min: float
    actual_min: float       # runway time (arr) or wheels-off (dep)
    gate_in_min: float | None = None
    gate_out_min: float | None = None
    taxi_min: float | None = None
    gate_delay_min: float = 0.0
    runway_wait_min: float = 0.0
    gate_hold_min: float = 0.0
    crossing_wait_min: float = 0.0   # time held waiting for runway crossing clearance
    terminal: str = ""
    weight_class: str = ""

    @property
    def delay_min(self) -> float:
        return max(0.0, self.actual_min - self.scheduled_min)

    @property
    def hour_of_day(self) -> int:
        return int(self.scheduled_min) // 60 % 24


@dataclass
class SimMetrics:
    records: List[FlightRecord] = field(default_factory=list)
    runway_queue_samples: List[tuple] = field(default_factory=list)
    gate_utilisation_samples: List[tuple] = field(default_factory=list)

    def record_flight(self, rec: FlightRecord) -> None:
        self.records.append(rec)

    def sample_runway_queue(self, sim_min: float, queue_len: int) -> None:
        self.runway_queue_samples.append((sim_min, queue_len))

    def sample_gate_utilisation(self, sim_min: float, terminal: str, used: int, cap: int) -> None:
        self.gate_utilisation_samples.append((sim_min, terminal, used, cap))

    def summary(self) -> Dict:
        arrivals = [r for r in self.records if r.operation == "ARR"]
        departures = [r for r in self.records if r.operation == "DEP"]

        def _stats(vals):
            if not vals:
                return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
            a = np.array(vals)
            return {
                "mean": float(np.mean(a)),
                "p50": float(np.percentile(a, 50)),
                "p95": float(np.percentile(a, 95)),
                "max": float(np.max(a)),
            }

        gate_util: Dict[str, List[float]] = {}
        for _, term, used, cap in self.gate_utilisation_samples:
            gate_util.setdefault(term, []).append(used / cap if cap else 0.0)

        crossing_waits = [r.crossing_wait_min for r in departures if r.crossing_wait_min > 0]

        return {
            "flights_simulated": len(self.records),
            "arrivals": len(arrivals),
            "departures": len(departures),
            "arrival_delay": _stats([r.delay_min for r in arrivals]),
            "departure_delay": _stats([r.delay_min for r in departures]),
            "arrival_runway_wait": _stats([r.runway_wait_min for r in arrivals]),
            "departure_runway_wait": _stats([r.runway_wait_min for r in departures]),
            "crossing_wait": _stats(crossing_waits),
            "gate_utilisation": {
                k: round(float(np.mean(v)), 3) for k, v in gate_util.items()
            },
        }

    def by_hour(self) -> Dict[int, Dict]:
        from collections import defaultdict
        buckets: Dict[int, Dict[str, list]] = defaultdict(lambda: {
            "arr_taxi": [], "dep_taxi": [], "arr_rwy_wait": [], "dep_rwy_wait": [],
            "crossing_wait": [],
        })
        for r in self.records:
            h = r.hour_of_day
            if r.operation == "ARR":
                if r.taxi_min is not None:
                    buckets[h]["arr_taxi"].append(r.taxi_min)
                buckets[h]["arr_rwy_wait"].append(r.runway_wait_min)
            else:
                if r.taxi_min is not None:
                    buckets[h]["dep_taxi"].append(r.taxi_min)
                buckets[h]["dep_rwy_wait"].append(r.runway_wait_min)
                if r.crossing_wait_min > 0:
                    buckets[h]["crossing_wait"].append(r.crossing_wait_min)

        def _s(vals):
            if not vals:
                return None
            a = np.array(vals)
            return {"n": len(a), "mean": round(float(np.mean(a)), 2),
                    "p95": round(float(np.percentile(a, 95)), 2)}

        return {
            h: {
                "arr_taxi": _s(v["arr_taxi"]),
                "dep_taxi": _s(v["dep_taxi"]),
                "arr_rwy_wait": _s(v["arr_rwy_wait"]),
                "dep_rwy_wait": _s(v["dep_rwy_wait"]),
                "crossing_wait": _s(v["crossing_wait"]),
            }
            for h, v in sorted(buckets.items())
        }
