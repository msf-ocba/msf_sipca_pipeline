# SIPCA Weekly Review Pipeline

Downloads `event_index.json` from Azure Blob Storage, checks that it is
up to date, classifies records, generates a report, and emails it when action
is required — all driven by one config file, runnable as a single cron job.

If the data in Azure is stale (or any step fails), the pipeline stops and
emails an explanation instead of silently reporting on old data.

## Files

| File | Purpose |
|---|---|
| `01_download_event_index.py` | Checks `event_index.json` in Azure container `sipca` / folder `raw` is fresh, then downloads it |
| `02_sipca_review.py` | Classifies records, updates `deleted_dhis2`, writes report + status JSON |
| `03_send_email.py` | Emails the report if action is required (per config) |
| `pipeline.py` | Runs all three in order and sends a failure alert if one fails; use this as the cron entry point |
| `common.py` | Shared config / logging / SMTP helpers |
| `config.example.ini` | Template — copy to `config.ini` and fill in real values |

## Setup

```bash
cd sipca_pipeline
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp config.example.ini config.ini
chmod 600 config.ini      # restrict access — this file holds secrets
nano config.ini           # fill in Azure + SMTP details
```

### `config.ini` — Azure section

Use **either** a connection string **or** an account URL + SAS token:

```ini
[azure]
; Option A
connection_string = DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net

; Option B (leave connection_string empty)
account_url = https://<storage-account>.blob.core.windows.net
sas_token = <token>

container_name = sipca
folder_path = raw
blob_name = event_index.json
local_download_path = ./data/event_index.json

max_age_days = 0
freshness_timezone = Africa/Nairobi
```

Get the connection string from **Azure Portal → Storage Account → Access keys**,
or generate a scoped SAS token if you'd rather not use the full account key
(read permission on the `sipca` container or on `raw/event_index.json`).
`account_url` is the storage account only — no container or path.

### Freshness check

Before downloading, step 1 reads the blob's *last modified* time in Azure and
stops the pipeline if the file is too old. This protects against reviewing
stale data when the upstream job that writes `event_index.json` has not run.

| Setting | Meaning |
|---|---|
| `max_age_days = 0` | Blob must have been updated **today** |
| `max_age_days = 1` | Updated today or yesterday (use this if the upstream job doesn't run daily) |
| `max_age_days = off` | Disable the check |
| `freshness_timezone` | Decides what "today" means. Azure stores UTC; use e.g. `Africa/Nairobi`. Default `UTC`. On Windows you may need `pip install tzdata`. |

To bypass the check once (testing, backfills):

```bash
python3 01_download_event_index.py --config config.ini --allow-stale
```

### `config.ini` — SMTP section

```ini
[smtp]
enabled = true
host = smtp.gmail.com
port = 587
user = your-account@gmail.com
password = your-new-app-password
from_addr = your-account@gmail.com
to_addrs = person1@org.org, person2@org.org
send_only_if_action_required = true

send_on_failure = true
```

Leave `enabled = false` while you're testing so no real emails go out.

### Failure alerts

When `smtp.enabled` and `smtp.send_on_failure` are true and a step fails,
`pipeline.py` emails the recipients what went wrong, for example:

> **Subject:** SIPCA Pipeline STOPPED : Source data in Azure is stale
> STALE DATA: 'raw/event_index.json' in container 'sipca' was last updated in
> Azure on 2026-08-30 18:11 UTC (21 day(s) ago), but it should have been
> updated today. The upstream job that writes this file has probably not run.

Other failures (bad credentials, blob not found, review error) get the subject
`SIPCA Pipeline FAILED : <step>` plus the error lines. Subjects can be changed
with `subject_stale` and `subject_failure`.

## Running manually

```bash
source .venv/bin/activate
python3 pipeline.py --config config.ini
```

Each step can also be run individually for debugging:

```bash
python3 01_download_event_index.py --config config.ini
python3 02_sipca_review.py --config config.ini
python3 03_send_email.py --config config.ini
```

Logs go to `logs/sipca_pipeline.log` (rotated at 5 MB, 5 backups kept) and
also print to stdout.

## Setting up the cron job

Run `crontab -e` and add a line. Example: every Monday at 07:00, using the
venv's Python explicitly (cron doesn't activate your shell environment):

```cron
0 7 * * 1 cd /path/to/sipca_pipeline && /path/to/sipca_pipeline/.venv/bin/python3 pipeline.py --config /path/to/sipca_pipeline/config.ini >> /path/to/sipca_pipeline/logs/cron.log 2>&1
```

Notes:
- Use **absolute paths** everywhere in the cron line — cron runs with a
  minimal environment and no working directory assumptions.
- `pipeline.py` uses a lock file (`pipeline.lock`) so if one run is still
  going (e.g. slow download) the next scheduled run won't overlap it.
- Exit code `2` means a step failed (including stale data) — the failure
  email covers most cases, and you can also wire your system's cron failure
  alerting (e.g. `MAILTO=` in crontab, or a monitoring wrapper) to that exit code.

## Security notes

- `config.ini` is in `.gitignore` — never commit it.
- File permissions: `chmod 600 config.ini` so only the running user can read it.
- Prefer a SAS token scoped to just the `sipca` container (or the one blob)
  with read permission over the full storage account key, if your Azure setup allows it.
