# TfL Network Reliability Dashboard

A live data pipeline and 5-page Power BI dashboard measuring London Underground service reliability, wait times and disruption patterns using the TfL Unified API.

## What the Project Does

A Python pipeline pulls data from the TfL Unified API every 5 minutes, capturing live status updates and arrival data for 10 Underground lines. Data is appended to a CSV file, modelled in Power BI and surfaced across 5 dashboard pages covering network reliability, geographic wait times, time of day patterns, disruption causes and a methodology overview.

**Stack:** TfL Unified API, Python (pandas + requests), Power BI (DAX + Power Query), Windows Task Scheduler

## Dashboard Pages

1. Network Reliability Overview
2. Geographic Wait Time Map
3. Reliability by Time of Day
4. Disruption Overview
5. Methodology and Limitations

## Methodology

Reliability = the percentage of time a line is operating at Good Service based on TfL's own status-severity assessment rather than a derived calculation. Overnight and planned closures are excluded as they are not service failures.

### Glossary

- **Status Severity:** Use of TfL's operational score (10 = Good Service, 9 = Minor Delays, 6 = Severe Delays, lower = worse)
- **% Good Service:** share of operational snapshots at severity 10.
- **Average Wait:** average time to the next train across snapshots; not a modelled passenger-wait figure.
- **Disruption Minutes:** snapshot count × 5; estimate that assumes an even 5-minute sampling.

## Limitations

- Collection occurs during machine uptime, so time-of-day coverage has gaps; figures reflect observed periods, not continuous monitoring.
- Reliability percentages need sufficient sampling depth per period to stabilise.
- Wait time is a next-train proxy, not a true passenger-wait model.
- An earlier delay metric was retired after validation showed it measured script processing-noise, not schedule deviation — the dashboard deliberately uses status-severity reliability instead.

## Data Model

A star schema with two dimensions and four fact tables:

- **Dimensions:** Lines, Stations
- **Facts:** line_status, reliability_summary, arrivals_raw, arrivals_summary

All Facts relate Many-to-One into Dimensions.

## Running the Pipeline

You will need a free API key from api.tfl.gov.uk

The pipeline writes CSVs to a directory which Power BI reads to update the model.

For continuous collection, schedule `pipeline.py` to run every 5 minutes via Windows Task Scheduler.

## Author

Abdul-Malik Adam
