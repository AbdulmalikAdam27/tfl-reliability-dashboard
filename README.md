## TfL Network Reliability Dashboard

A live data pipeline and 5-page Power BI dashboard measuring London Underground service reliability, wait times and disruption patterns using the TfL Unified API.

## What the Project Does

A Python pipeline pulls data from the TfL Unified API, capturing live status updates and arrival data for 10 Underground lines. A GitHub Actions workflow runs it in the cloud and commits the CSVs to this repo, which Power BI reads directly. The workflow is scheduled every 5 minutes, but GitHub runs it roughly every 4 hours in practice (about 5–6 snapshots a day — see Limitations). The data is modelled in Power BI and surfaced across 5 dashboard pages covering network reliability, geographic wait times, time of day patterns, disruption causes and a methodology overview.

**Stack:** TfL Unified API, Python (pandas + requests), GitHub Actions, Power BI (DAX + Power Query)

## Dashboard Pages

1. Network Reliability Overview
<img width="968" height="500" alt="image" src="https://github.com/user-attachments/assets/80f44699-43a2-4083-a486-543241a69082" />


2. Geographic Wait Time Map
<img width="952" height="500" alt="image" src="https://github.com/user-attachments/assets/ea5bbf40-367d-4b2e-98d8-961e397081e6" />


3. Reliability by Time of Day
<img width="939" height="500" alt="image" src="https://github.com/user-attachments/assets/8459159c-e108-4e0f-980e-c82d891566e1" />


4. Disruption Overview
<img width="948" height="500" alt="image" src="https://github.com/user-attachments/assets/b8c6c019-9f76-4780-aed1-020bce30e35e" />


5. Methodology and Limitations
<img width="969" height="500" alt="image" src="https://github.com/user-attachments/assets/f5cde370-45aa-4f3c-b3f9-757b9efb7a5f" />



## Methodology

Reliability is measured with TfL's own status-severity assessment rather than a derived calculation. The reliability charts (pages 1 and 3) show the average severity per line and hour, where 10 means Good Service throughout. Overnight and planned closures (severity 20 and 4) are excluded as they are not service failures.

### Glossary

- **Status Severity:** TfL's operational score (10 = Good Service, 9 = Minor Delays, 6 = Severe Delays, lower = worse).
- **Average Severity:** mean status severity across operational snapshots; the reliability figure on pages 1 and 3.
- **% Good Service:** share of operational snapshots at severity 10. Computed in `reliability_summary.csv` (`pct_good_service`) but not currently charted.
- **Average Wait:** average time to the next train across snapshots; not a modelled passenger-wait figure.
- **Disruption Minutes:** disrupted snapshot count × 5. Snapshots are hours apart, so read it as a relative comparison of lines and causes, not actual minutes of disruption.

## Limitations

- GitHub runs scheduled workflows on a best-effort basis and drops runs under load. The workflow asks for every 5 minutes, but snapshots actually arrive irregularly, roughly every 4 hours (between about 2 and 9 hours apart). Figures reflect sampled moments, not continuous monitoring.
- Average-based metrics (average severity, % Good Service, average wait) are unaffected by uneven spacing. Disruption Minutes assumes 5-minute spacing, so it understates real disruption time and is only meaningful as a relative indicator.
- Hourly figures rest on few snapshots — often only a handful per line per hour — so they will shift as data accumulates.
- `line_status.csv` also contains earlier snapshots collected locally (25 Jun – 5 Oct 2026, roughly 35 minutes apart) before collection moved to GitHub Actions.
- Wait time is a next-train proxy, not a true passenger-wait model.
- An earlier delay metric was retired after validation showed it measured script processing-noise, not schedule deviation — the dashboard deliberately uses status-severity reliability instead.

## Data Model

A star schema with two dimensions and three fact tables, all loaded from the CSVs in `data/`:

- **Dimensions:** `lines` (the 10 lines and their colours), `stations` (names and coordinates)
- **Facts:** `line_status`, `reliability_summary`, `arrivals_summary`
- **Derived:** `station_wait` — built in Power Query from `arrivals_summary` and `stations`; drives the wait-time bands on the map (page 2)

All facts relate many-to-one into the dimensions.

## Running the Pipeline

### In the cloud (GitHub Actions)

`.github/workflows/collect.yml` runs `pipeline_1.py` on a 5-minute schedule (in practice about every 4 hours — see Limitations) and commits the updated CSVs in `data/`:

| File | Contents | Updated |
|---|---|---|
| `line_status.csv` | Every line-status snapshot (the reliability signal) | Appended each run |
| `reliability_summary.csv` | Average severity, % Good Service and snapshot count per line per hour | Recomputed each run |
| `arrivals_summary.csv` | Running next-train wait and arrival totals, one row per line + station | On runs at least 60 minutes apart (in practice, every run) |
| `stations.csv` | Station names and coordinates | Fetched once |

Raw arrivals (`arrivals_raw.csv`) are not committed: a single run produces 4,000–25,000 rows, which would exceed GitHub's 100 MB file limit within days. Their wait-time and volume figures are carried in `arrivals_summary.csv`.

Setup:
1. Get a free API key from api.tfl.gov.uk (optional — the pipeline also works anonymously, at a lower rate limit).
2. In this repo on GitHub, go to **Settings → Secrets and variables → Actions → New repository secret**, name it `TFL_APP_KEY` and paste the key. Never commit the key to the code.
3. The workflow starts on its own; to run it immediately, open **Actions → Collect TfL data → Run workflow**.

### Locally

```bash
pip install -r requirements.txt
python pipeline_1.py              # run once
python pipeline_1.py --schedule   # run every 5 minutes
```

Set `TFL_APP_KEY` as an environment variable. Output goes to `data/` (override with `OUTPUT_DIR`). Run `git pull` first if the workflow is also active, so local runs build on the latest data.

## Connecting Power BI to the GitHub data

Power BI reads the committed CSVs from their raw GitHub URLs, so no local files or gateway are needed:

```
https://raw.githubusercontent.com/AbdulmalikAdam27/tfl-reliability-dashboard/main/data/line_status.csv
https://raw.githubusercontent.com/AbdulmalikAdam27/tfl-reliability-dashboard/main/data/reliability_summary.csv
https://raw.githubusercontent.com/AbdulmalikAdam27/tfl-reliability-dashboard/main/data/arrivals_summary.csv
https://raw.githubusercontent.com/AbdulmalikAdam27/tfl-reliability-dashboard/main/data/stations.csv
```

The report's queries already use these URLs. Each one is written with a fixed base URL plus `RelativePath`:

```
Web.Contents(
    "https://raw.githubusercontent.com/AbdulmalikAdam27/tfl-reliability-dashboard/main/data/",
    [RelativePath = "line_status.csv"]
)
```

To point a new query at the data, use the same pattern and change the file name. When asked for credentials, choose **Anonymous**, then **Close & Apply** and **Refresh**. GitHub caches raw files for a few minutes, so a refresh straight after a workflow run may still show the previous data.

For scheduled refresh after publishing to the Power BI Service: dataset **Settings → Data source credentials → Anonymous**, then turn on **Scheduled refresh**. Keeping the fixed base URL with `RelativePath` (as above) is what lets the Service refresh without a gateway.

## Author

Abdul-Malik Adam
