"""SimPy resource models for JFK: runways, terminals, runway crossings, aircraft."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import simpy

from .config import AirportConfig

WEIGHT_CLASS_BY_TYPE = {"narrow": "Large", "wide": "Heavy", "heavy": "Heavy"}


@dataclass
class Flight:
    flight_id: str
    airline: str
    flight_number: str
    aircraft_type: str   # narrow | wide | heavy
    tail_number: str
    operation: str       # ARR | DEP
    scheduled_min: float
    origin: str
    destination: str
    gate_assigned: str
    terminal: str
    weight_class: str = "Large"

    actual_off_block_min: Optional[float] = None
    actual_runway_min: Optional[float] = None
    taxi_out_min: Optional[float] = None
    taxi_in_min: Optional[float] = None
    gate_delay_min: Optional[float] = None
    edct_delay_min: float = 0.0

    @property
    def is_heavy(self) -> bool:
        return self.weight_class in ("Heavy", "Super")


class ArrivalMeter:
    """
    Token-bucket rate limiter enforcing the FAA nominal arrival acceptance rate.

    Arrivals request a slot before competing for the runway resource.  The
    meter releases one slot every (60 / rate_per_hour) minutes, so the total
    landing rate cannot exceed the FAA-rated throughput even on a fully-loaded
    schedule.  When arrivals arrive faster than the rate, they queue here and
    the delay adds to their runway-wait time.
    """

    def __init__(self, env: simpy.Environment, rate_per_hour: float):
        self.env = env
        self._interval_min = 60.0 / max(1.0, rate_per_hour)
        self._mutex = simpy.Resource(env, capacity=1)
        self._next_slot_min: float = 0.0

    def request_slot(self):
        """SimPy process: wait until a metered arrival slot is available."""
        with self._mutex.request() as req:
            yield req
            wait = max(0.0, self._next_slot_min - self.env.now)
            if wait > 0.0:
                yield self.env.timeout(wait)
            self._next_slot_min = self.env.now + self._interval_min


class DepartureMeter:
    """
    Token-bucket rate limiter enforcing the FAA nominal departure rate.

    Departures request a slot before competing for a runway.  The meter
    releases one slot every (60 / rate_per_hour) minutes, capping total
    departure throughput at the FAA-rated runway capacity.  When more
    departures are ready than the runway system can accept, they queue here
    and the delay accumulates in their runway-wait time.
    """

    def __init__(self, env: simpy.Environment, rate_per_hour: float):
        self.env = env
        self._interval_min = 60.0 / max(1.0, rate_per_hour)
        self._mutex = simpy.Resource(env, capacity=1)
        self._next_slot_min: float = 0.0

    def request_slot(self):
        """SimPy process: wait until a metered departure slot is available."""
        with self._mutex.request() as req:
            yield req
            wait = max(0.0, self._next_slot_min - self.env.now)
            if wait > 0.0:
                yield self.env.timeout(wait)
            self._next_slot_min = self.env.now + self._interval_min


class DepartureRunway:
    """One physical departure runway with its own queue and separation enforcement."""

    def __init__(
        self,
        env: simpy.Environment,
        runway_id: str,
        default_sep_sec: int = 90,
        heavy_sep_sec: int = 90,
        takeoff_roll_min: float = 0.75,
        min_inter_dep_min: float = 0.0,
    ):
        self.env = env
        self.runway_id = runway_id
        self.resource = simpy.Resource(env, capacity=1)
        self.default_sep_sec = default_sep_sec
        self.heavy_sep_sec = heavy_sep_sec
        self.takeoff_roll_min = takeoff_roll_min
        self.min_inter_dep_min = min_inter_dep_min
        self._last_was_heavy = False

    @property
    def queue_depth(self) -> int:
        return len(self.resource.queue) + self.resource.count

    def process(self, is_heavy: bool):
        sep_min = (
            self.heavy_sep_sec if self._last_was_heavy else self.default_sep_sec
        ) / 60.0
        sep_min = max(sep_min, self.min_inter_dep_min)
        with self.resource.request() as req:
            yield req
            yield self.env.timeout(sep_min + self.takeoff_roll_min)
            self._last_was_heavy = is_heavy


class RunwayCrossing:
    """
    Models the clearance window to cross an active arrival runway mid-taxi.

    At JFK, departures from several terminals must cross an active arrival
    runway (e.g., 31L or 22R) to reach their departure runway.  Clearance
    is granted in gaps between arrivals; the capacity represents how many
    aircraft can be waved across in one such gap.  When the arrival runway
    is busy the queue builds organically, adding to taxi-out time without
    any lookup table.
    """

    def __init__(
        self,
        env: simpy.Environment,
        crossing_id: str,
        capacity: int = 2,
        occupancy_min: float = 1.5,
    ):
        self.env = env
        self.crossing_id = crossing_id
        self.resource = simpy.Resource(env, capacity=capacity)
        self.occupancy_min = occupancy_min

    def cross(self):
        """SimPy generator: hold until clearance, then cross."""
        with self.resource.request() as req:
            yield req
            yield self.env.timeout(self.occupancy_min)


class RunwayPool:
    """Arrival pool + independent per-runway departure queues + taxi permits."""

    def __init__(self, env: simpy.Environment, config: AirportConfig):
        self.env = env
        self.config = config

        arr, dep = [], []
        for name, rw in config.runways.items():
            if rw.primary_use == "arrival":
                arr.append(name)
            elif rw.primary_use == "departure":
                dep.append(name)
            else:
                arr.append(name)
                dep.append(name)

        self.arrival = simpy.Resource(env, capacity=max(1, len(arr)))
        self.arrival_names = arr
        self.arrival_meter = ArrivalMeter(env, config.nominal_arrival_rate_per_hour)
        self.departure_meter = DepartureMeter(env, config.nominal_departure_rate_per_hour)

        num_dep = max(1, len(dep))
        min_inter_dep_min = (
            60.0 * num_dep / config.nominal_departure_rate_per_hour
            if config.nominal_departure_rate_per_hour > 0
            else 0.0
        )

        sep = config.separation
        self.dep_runways: List[DepartureRunway] = [
            DepartureRunway(
                env,
                runway_id=name,
                default_sep_sec=sep.default_separation_sec,
                heavy_sep_sec=sep.heavy_behind_heavy_sec,
                min_inter_dep_min=min_inter_dep_min,
            )
            for name in dep
        ]

        max_q = getattr(config, "departure_max_taxi_queue", 18)
        self.dep_taxi_permits = simpy.Resource(env, capacity=max(1, max_q))

    def least_loaded_runway(self) -> DepartureRunway:
        return min(self.dep_runways, key=lambda r: r.queue_depth)

    def total_dep_queue_depth(self) -> int:
        return sum(r.queue_depth for r in self.dep_runways)


class CrossingPool:
    """Registry of all runway crossing points, keyed by crossing_id."""

    def __init__(self, env: simpy.Environment, config: AirportConfig):
        self.crossings: Dict[str, RunwayCrossing] = {
            cid: RunwayCrossing(
                env,
                crossing_id=cid,
                capacity=c.capacity,
                occupancy_min=c.occupancy_min,
            )
            for cid, c in config.crossings.items()
        }

    def get(self, crossing_id: str) -> Optional[RunwayCrossing]:
        return self.crossings.get(crossing_id)


class GatePool:
    """Per-terminal gate pools modeled as SimPy resources."""

    def __init__(self, env: simpy.Environment, config: AirportConfig):
        self.env = env
        self.config = config
        self.pools: Dict[str, simpy.Resource] = {
            name: simpy.Resource(env, capacity=t.gate_count)
            for name, t in config.terminals.items()
        }

    def pool_for(self, terminal: str) -> simpy.Resource:
        return self.pools.get(terminal) or next(iter(self.pools.values()))
