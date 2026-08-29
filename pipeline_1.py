"""
TfL Delay Analyzer - Data Pipeline (v2)

Collects live arrivals (volume/wait-time only) and line status data,
exports to CSV for Power BI.

IMPORTANT - v2 change from v1:
v1 attempted to compute a "delay_minutes" minutes metric by comparing 
expectedArrival against timeToStation. This was found to be an invalid 
metric as both values are derived from the same live countdown and
subtracting them measures script processing-time noise (a near-constant
offset of 0.27 minutes), not real schedule deviation. TfL's live Arrivals 
endpoint does not share a scheduled time to compare against timeToStation.

v2 instead treats line_status (status_severity) as the primary
reliability signal, since it reflects TfL's own operational
assessment rather than a derived calculation. arrivals_raw is kept
for legitimate uses only: arrival volume and live wait-time
distributions, NOT "delay".

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

TFL_APP_KEY = "API Key"   # Replace with API key from api.tfl.gov.uk
OUTPUT_DIR  = "output - 2"
LINES       = ["central", "jubilee", "northern", "victoria", "bakerloo",
               "circle", "district", "metropolitan", "piccadilly", "elizabeth"]

# ─── API HELPERS ─────────────────────────────────────────────────────────────

BASE_URL = "https://api.tfl.gov.uk"

def get(endpoint: str, params: dict = {}) -> dict | list | None:
    """Generic API GET with error handling."""
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
    Get current status for all lines (Good Service / Delays / Suspended etc).
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
    From (v1) - Get live arrival predictions for all stops on a line.
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
        time.sleep(0.2)   
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def get_stop_points() -> pd.DataFrame:
    """
    Fetch all stop points (stations) for the configured lines.
    Useful for geo mapping in Power BI.
    Saved separately as stations.csv
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
    Collect raw arrivals into a wait-time summary for stations.

    IMPORTANT - 'next train wait', not "average across all tracked trains":
    TfL's arrivals endpoint returns predictions for multiple upcoming trains
    at each station (e.g. next in 2 min, then 6, then 11...). Averaging all of
    them inflates the figure to 8-15 min, which fails a sanity check against
    real tube frequencies. The honest "wait" is the minimum time_to_station_s
    per station per snapshot. That per-snapshot
    minimum is taken, then average those minimums over time for a typical next-train wait.
    """
    if arrivals_df.empty:
        return pd.DataFrame()

    # Stage 1: per station, per snapshot (timestamp) -> the NEXT train's wait
    # (minimum time_to_station), plus how many trains were tracked that snapshot.
    per_snapshot = (
        arrivals_df
        .groupby(["line_id", "line_name", "station_name", "naptan_id", "timestamp"])
        .agg(
            next_train_s   = ("time_to_station_s", "min"),
            trains_tracked = ("vehicle_id",         "count"),
        )
        .reset_index()
    )

    # Stage 2: per station, average the per-snapshot next-train waits over time,
    # and sum the tracked trains for a volume measure.
    summary = (
        per_snapshot
        .groupby(["line_id", "line_name", "station_name", "naptan_id"])
        .agg(
            avg_wait_seconds = ("next_train_s",   "mean"),
            min_wait_seconds = ("next_train_s",   "min"),
            max_wait_seconds = ("next_train_s",   "max"),
            total_arrivals   = ("trains_tracked", "sum"),
        )
        .reset_index()
    )
    summary["avg_wait_minutes"] = (summary["avg_wait_seconds"] / 60).round(2)
    summary["snapshot_time"]    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return summary


def build_reliability_summary(status_df: pd.DataFrame) -> pd.DataFrame:
    """
    The new reliability metric: per line, per hour, what fraction of
    status snapshots were Good Service (severity 10) vs degraded,
    and the average severity score.

    TfL's Severity scale: 10 = Good Service, 9 = Minor Delays,
    6 = Severe Delays, 5 = Part Closure, etc. Lower = worse.
    """
    if status_df.empty:
        return pd.DataFrame()

    # Exclude routine non-operation from the reliability denominator:
    # severity 20 = Service Closed (overnight), 4 = Planned Closure (engineering).
    # These aren't service FAILURES, so counting them would unfairly deflate
    # the score. Matches how professional TfL reliability trackers compute
    # "percentage of operational time at Good Service".
    operational = status_df[~status_df["status_severity"].isin([20, 4])].copy()
    if operational.empty:
        return pd.DataFrame()

    summary = (
        operational
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
        # Append THIS run's raw snapshot to the accumulating history file
        export(status_df, "line_status.csv")

        # Recompute reliability from the FULL accumulated history, not just
        # this run's single snapshot. This is what makes pct_good_service a
        # genuine fractional percentage rather than a one-shot 0/100 value.
        history_path = os.path.join(OUTPUT_DIR, "line_status.csv")
        try:
            full_history = pd.read_csv(history_path)
        except Exception as e:
            print(f"  [!] Could not read line_status history: {e}")
            full_history = status_df  # fall back to current snapshot

        reliability_df = build_reliability_summary(full_history)
        # Overwrite (append=False): complete recomputation each run, NOT an
        # increment. Appending would pile up duplicate/stale summaries.
        export(reliability_df, "reliability_summary.csv", append=False)
   

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

    print("\n  ✅ Pipeline complete.\n")


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
