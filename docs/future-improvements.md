# Future Simulation Improvements

## Approach 1 — GDP/Ground-Stop Injection from FAA Public Data

**Problem:** On days with afternoon convective weather (e.g. Jul 16, Aug 1), actual taxi-out spikes to 50-75 min from ~14:xx onward while the sim runs clean. The step-change is characteristic of a GDP or ground stop, not gradual queue growth.

**The mechanism already exists:** `GroundProgram` dataclass and `--gdp` flag on `validate_real_day.py` are fully wired. The only missing piece is a data feed.

**Data source:** FAA Aviation System Performance Metrics (ASPM) at aspm.faa.gov publishes historical GDP/GS event logs. For each event you get: airport, type (GDP/GS), start time UTC, end time UTC, and arrival acceptance rate (AAR). Download `OPSNET_traffic.csv` or use the ASPM API.

**Implementation steps:**
1. For a given validation date, query ASPM for KJFK events and convert to local minutes-from-midnight.
2. Map the AAR to `arr_rate_per_hour`.
3. For the departure side, set `dep_clearance_hold_mean_min` proportional to the severity: light GDP (~15 min mean), moderate (~25 min), severe/GS (~45 min). These can be read from EDCT compliance data if available, or estimated from the GDP severity tier.
4. Write a `scripts/fetch_gdp.py` that calls ASPM, outputs a GDP JSON file, and passes it to `validate_real_day.py` automatically.

**Expected impact:** On Jul 16 and Aug 1, taxi-out delta in the 14-22:xx window should collapse from -35 to -48 min down to ±5-10 min.

---

## Approach 3 — Weather-Conditioned Taxi-Out Sigma

**Problem:** On irregular days the actual taxi-out std is ~25 min vs the sim's ~8-9 min. The current lognormal sigma (`taxi_out_lognorm_sigma = 0.337`) is fixed and calibrated to VMC conditions. GDP/weather days have fat-tailed distributions that the model can't reproduce without a state variable.

**Data sources:**
- METAR archives at aviationweather.gov (free, per-airport, hourly)
- FAA ASPM VMC/IMC fraction by hour (same portal as above)

**Implementation:**
1. Add `weather_state: str = "VMC"` parameter to `AirportSimulation.__init__` and `validate_real_day.py --weather`.
2. Add a `weather_taxi_out_sigma_overrides: Dict[str, float]` to `AirportConfig`:
   ```json
   "weather_taxi_out_sigma_overrides": {
     "VMC": 0.337,
     "IMC": 0.480,
     "LIFR": 0.620
   }
   ```
3. In `_taxi_out()`, replace `cfg.taxi_out_lognorm_sigma` with a lookup into this dict keyed on current weather state.
4. Optionally make weather state time-varying: accept a list of `(start_min, end_min, state)` tuples mirroring the `GroundProgram` pattern, and switch sigma mid-simulation as conditions change.

**Calibration:** From the irregular-day validation data, a sigma of ~0.55-0.60 reproduces the observed p95 of 85-87 min given the base means in use.

**Expected impact:** p95 taxi-out on weather days improves from -40 min delta to ±15 min. Mean taxi-out delta improves modestly since the lognormal mean is unchanged; the main gain is in distributional accuracy (std, p95).

---

## Notes on Approach 2 (implemented)

Per-hour baseline scaling (`hourly_taxi_out_scale`) was added in `config.py` and `simulation.py`. Calibration values in `jfk_config.json` are derived from the FAA ASPM JFK unimpeded taxi-out time (~16.5 min) relative to the peak terminal base means (23.5-28 min). The JFK scale drops to 0.65 for hours 0-5, ramping up through 7-8 back to 1.0 for peak hours 9-21.
