"""
TfL Delay Analyzer - Data Pipeline (v2)
=========================================
Fetches live arrivals (volume/wait-time only) and line status data,
exports to CSV for Power BI / Tableau.

IMPORTANT - v2 change from v1:
v1 attempted to compute "delay_minutes" by comparing expectedArrival
against timeToStation. This was found to be mathematically invalid:
both values are derived from the same live countdown, so subtracting
them mostly measures script processing-time noise (a near-constant
offset), not real schedule deviation. TfL's live Arrivals endpoint
does not expose a scheduled time to compare against.

v2 instead treats line_status (status_severity) as the primary
reliability signal, since it reflects TfL's own operational
assessment rather than a derived calculation. arrivals_raw is kept
for legitimate uses only: arrival volume and live wait-time
distributions - NOT "delay".

Usage:
    python pipeline.py                  # run once
    python pipeline.py --schedule       # run every 5 mins (keeps CSV updated)

Requirements:
    pip install requests pandas schedule
"""

import requests
import pandas as pd
import time
import schedule
import argparse
from datetime import datetime, timezone
import os

# ─── CONFIG ──────────────────────────────────────────────────────────────────

TFL_APP_KEY = "c0e7f72204e347d59f2fd37a60fdcb28"   # Replace with your key from api.tfl.gov.uk
OUTPUT_DIR  = "output"
LINES       = ["central", "jubilee", "northern", "victoria", "bakerloo",
               "circle", "district", "metropolitan", "piccadilly", "elizabeth"]

# ─── API HELPERS ─────────────────────────────────────────────────────────────

BASE_URL = "https://api.tfl.gov.uk"

def get(endpoint: str, params: dict = {}) -> dict | list | None:
    """Generic TfL API GET with error handling."""
    params["app_key"] = TFL_APP_KEY
    try:
        r = requests.get(f"{BASE_URL}{endpoint}", params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        print(f"  [!] API error on {endpoint}: {e}")
        return None


def get_line_status() -> pd.DataFrame:
    """
    Fetch current status for all lines (Good Service / Delays / Suspended etc).
    This is the PRIMARY reliability signal - it's TfL's own operational
    assessment, not something we derive ourselves.
    """
    data = get(f"/line/{','.join(LINES)}/status")
    if not data:
        return pd.DataFrame()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = []
    for line in data:
        for status in line.get("lineStatuses", []):
            rows.append({
                "timestamp":        now.strftime("%Y-%m-%d %H:%M:%S"),
                "hour":             now.hour,
                "line_id":          line["id"],
                "line_name":        line["name"],
                "status_severity":  status.get("statusSeverity"),
                "status_desc":      status.get("statusSeverityDescription"),
                "reason":           status.get("reason", "") or "",
                "disruption_category": (
                    status.get("disruption", {}) or {}
                ).get("category", "None"),
            })
    return pd.DataFrame(rows)


def get_arrivals_for_line(line_id: str) -> pd.DataFrame:
    """
    Fetch live arrival predictions for all stops on a line.
    Kept for volume and live wait-time distribution only.
    Does NOT attempt to compute a "delay" - see module docstring.
    """
    data = get(f"/line/{line_id}/arrivals")
    if not data:
        return pd.DataFrame()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = []
    for arr in data:
        rows.append({
            "timestamp":         now.strftime("%Y-%m-%d %H:%M:%S"),
            "hour":              now.hour,
            "line_id":           line_id,
            "line_name":         arr.get("lineName", line_id) or line_id,
            "station_name":      arr.get("stationName", "") or "",
            "naptan_id":         arr.get("naptanId", "") or "",
            "platform_name":     arr.get("platformName", "") or "",
            "direction":         arr.get("direction", "") or "",
            "destination_name":  arr.get("destinationName", "") or "",
            "vehicle_id":        str(arr.get("vehicleId", "") or ""),
            "time_to_station_s": arr.get("timeToStation", 0) or 0,
            "expected_arrival":  arr.get("expectedArrival", "") or "",
        })
    return pd.DataFrame(rows)


def get_all_arrivals() -> pd.DataFrame:
    """Loop over all configured lines and combine arrivals into one DataFrame."""
    frames = []
    for line in LINES:
        print(f"  → Fetching arrivals: {line}")
        df = get_arrivals_for_line(line)
        if not df.empty:
            frames.append(df)
        time.sleep(0.2)   # be polite to the API
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def get_stop_points() -> pd.DataFrame:
    """
    Fetch all stop points (stations) for the configured lines.
    Useful for geo mapping in Tableau/Power BI.
    Only needs to run once - saved separately as stations.csv
    """
    rows = []
    for line in LINES:
        print(f"  → Fetching stop points: {line}")
        data = get(f"/line/{line}/stoppoints")
        if not data:
            continue
        for stop in data:
            rows.append({
                "line_id":       line,
                "station_name":  stop.get("commonName", "") or "",
                "naptan_id":     stop.get("naptanId", "") or "",
                "lat":           stop.get("lat"),
                "lon":           stop.get("lon"),
                "zone":          stop.get("zone", "") or "",
            })
        time.sleep(0.2)
    return pd.DataFrame(rows)


# ─── SUMMARY / AGGREGATION ───────────────────────────────────────────────────

def build_arrivals_summary(arrivals_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate raw arrivals into a station-level VOLUME summary.
    No delay claim here - just how many trains and how long the wait is.
    """
    if arrivals_df.empty:
        return pd.DataFrame()

    summary = (
        arrivals_df
        .groupby(["line_id", "line_name", "station_name", "naptan_id"])
        .agg(
            avg_wait_seconds  = ("time_to_station_s", "mean"),
            min_wait_seconds  = ("time_to_station_s", "min"),
            max_wait_seconds  = ("time_to_station_s", "max"),
            total_arrivals    = ("vehicle_id",         "count"),
        )
        .reset_index()
    )
    summary["avg_wait_minutes"] = (summary["avg_wait_seconds"] / 60).round(2)
    summary["snapshot_time"]    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return summary


def build_reliability_summary(status_df: pd.DataFrame) -> pd.DataFrame:
    """
    The REAL reliability metric: per line, per hour, what fraction of
    status snapshots were Good Service (severity 10) vs degraded,
    and the average severity score.

    Severity scale (TfL): 10=Good Service, 9=Minor Delays,
    6=Severe Delays, 5=Part Closure, etc. Lower = worse.
    """
    if status_df.empty:
        return pd.DataFrame()

    summary = (
        status_df
        .groupby(["line_id", "line_name", "hour"])
        .agg(
            avg_severity      = ("status_severity", "mean"),
            min_severity      = ("status_severity", "min"),
            snapshot_count    = ("status_severity", "count"),
            good_service_count = ("status_severity", lambda s: (s == 10).sum()),
        )
        .reset_index()
    )
    summary["pct_good_service"] = (
        summary["good_service_count"] / summary["snapshot_count"] * 100
    ).round(1)
    summary["avg_severity"] = summary["avg_severity"].round(2)
    return summary


# ─── EXPORT ──────────────────────────────────────────────────────────────────

def export(df: pd.DataFrame, filename: str, append: bool = True):
    """Save DataFrame to CSV, appending if file exists (builds history)."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, filename)
    if append and os.path.exists(path):
        df.to_csv(path, mode="a", header=False, index=False)
        print(f"  ✓ Appended {len(df)} rows → {path}")
    else:
        df.to_csv(path, index=False)
        print(f"  ✓ Saved {len(df)} rows → {path}")


# ─── MAIN JOB ────────────────────────────────────────────────────────────────

def run_pipeline():
    print(f"\n{'='*55}")
    print(f"  TfL Reliability Pipeline (v2) | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*55}")

    # 1. Line status - the PRIMARY signal
    print("\n[1/3] Line status...")
    status_df = get_line_status()
    if not status_df.empty:
        export(status_df, "line_status.csv")
        reliability_df = build_reliability_summary(status_df)
        export(reliability_df, "reliability_summary.csv")

    # 2. Live arrivals - volume/wait-time only, no delay claim
    print("\n[2/3] Live arrivals (volume & wait time only)...")
    arrivals_df = get_all_arrivals()
    if not arrivals_df.empty:
        export(arrivals_df, "arrivals_raw.csv")
        arrivals_summary_df = build_arrivals_summary(arrivals_df)
        export(arrivals_summary_df, "arrivals_summary.csv")

    # 3. Station geo data (only write once)
    stations_path = os.path.join(OUTPUT_DIR, "stations.csv")
    if not os.path.exists(stations_path):
        print("\n[3/3] Fetching station geo data (one-time)...")
        stations_df = get_stop_points()
        if not stations_df.empty:
            export(stations_df, "stations.csv", append=False)
    else:
        print("\n[3/3] stations.csv already exists - skipping.")

    print(f"\n  ✅ Pipeline complete.\n")


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TfL Reliability Pipeline (v2)")
    parser.add_argument("--schedule", action="store_true",
                        help="Run every 5 minutes continuously")
    args = parser.parse_args()

    if args.schedule:
        print("Scheduling pipeline every 5 minutes. Press Ctrl+C to stop.")
        run_pipeline()
        schedule.every(5).minutes.do(run_pipeline)
        while True:
            schedule.run_pending()
            time.sleep(1)
    else:
        run_pipeline()
