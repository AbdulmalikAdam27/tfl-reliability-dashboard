## TfL Network Reliability Dashboard

A live data pipeline and 5-page Power BI dashboard measuring London Underground service reliability, wait times and disruption patterns using the TfL Unified API.

## What the Project Does

A Python pipeline pulls data from the TfL Unified API on a 5-minute schedule, capturing live status updates and arrival data for 10 Underground lines. A GitHub Actions workflow runs it in the cloud and commits the CSVs to this repo, which Power BI reads directly. The data is modelled in Power BI and surfaced across 5 dashboard pages covering network reliability, geographic wait times, time of day patterns, disruption causes and a methodology overview.

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

Reliability = the percentage of time a line is operating at Good Service based on TfL's own status-severity assessment rather than a derived calculation. Overnight and planned closures are excluded as they are not service failures.

### Glossary

- **Status Severity:** Use of TfL's operational score (10 = Good Service, 9 = Minor Delays, 6 = Severe Delays, lower = worse)
- **% Good Service:** share of operational snapshots at severity 10.
- **Average Wait:** average time to the next train across snapshots; not a modelled passenger-wait figure.
- **Disruption Minutes:** snapshot count × 5; estimate that assumes an even 5-minute sampling (see Limitations — cloud sampling is irregular).

## Limitations

- GitHub runs scheduled workflows on a best-effort basis and drops runs under load, so snapshots are irregularly spaced rather than exactly every 5 minutes. Percentage-based metrics (% Good Service, average severity) are unaffected by uneven spacing; Disruption Minutes, which assumes 5-minute spacing, should be read as a relative indicator rather than an absolute duration.
- Reliability percentages need sufficient sampling depth per period to stabilise.
- Wait time is a next-train proxy, not a true passenger-wait model.
- An earlier delay metric was retired after validation showed it measured script processing-noise, not schedule deviation — the dashboard deliberately uses status-severity reliability instead.

## Data Model

A star schema with two dimensions and four fact tables:

- **Dimensions:** Lines, Stations
- **Facts:** line_status, reliability_summary, arrivals_raw, arrivals_summary

All Facts relate Many-to-One into Dimensions.

## Running the Pipeline

### In the cloud (GitHub Actions)

`.github/workflows/collect.yml` runs `pipeline_1.py` every 5 minutes and commits the updated CSVs in `data/`:

| File | Contents | Updated |
|---|---|---|
| `line_status.csv` | Every line-status snapshot (the reliability signal) | Appended each run |
| `reliability_summary.csv` | % Good Service and severity per line per hour | Recomputed each run |
| `arrivals_summary.csv` | Running next-train wait and arrival totals, one row per line + station | Refreshed hourly |
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

To repoint the existing report, open **Transform data**, select each query, open **Advanced Editor** and replace its `File.Contents("…\output - 2\<file>.csv")` source with:

```
Web.Contents(
    "https://raw.githubusercontent.com/AbdulmalikAdam27/tfl-reliability-dashboard/main/data/",
    [RelativePath = "line_status.csv"]
)
```

changing the file name per query. Keep the rest of each query's steps. When asked for credentials, choose **Anonymous**. Then **Close & Apply** and **Refresh**.

For scheduled refresh after publishing to the Power BI Service: dataset **Settings → Data source credentials → Anonymous**, then turn on **Scheduled refresh**. Keeping the fixed base URL with `RelativePath` (as above) is what lets the Service refresh without a gateway.

## Author

Abdul-Malik Adam
