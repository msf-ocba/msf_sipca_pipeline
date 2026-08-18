# SIPCA Weekly Review Pipeline

Downloads `event_index.json` from Azure Blob Storage, classifies records,
generates a report, and emails it when action is required — all driven by
one config file, runnable as a single cron job.

## Files

| File | Purpose |
|---|---|
| `download_event_index.py` | Downloads `event_index.json` from Azure container `sipca` / folder `raw` |
| `sipca_review.py` | Classifies records, updates `deleted_dhis2`, writes report + status JSON |
| `send_email.py` | Emails the report if action is required (per config) |
| `pipeline.py` | Runs all three in order; use this as the cron entry point |
| `common.py` | Shared config/logging helpers |
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
connection_string = DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net
container_name = sipca
folder_path = raw
blob_name = event_index.json
local_download_path = ./data/event_index.json
```

Get the connection string from **Azure Portal → Storage Account → Access keys**,
or generate a scoped SAS token if you'd rather not use the full account key
(Storage Account → Shared access signature → check "Read" + "List" permissions
for the `sipca` container only).

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
```

Leave `enabled = false` while you're testing so no real emails go out.

## Running manually

```bash
source .venv/bin/activate
python3 pipeline.py --config config.ini
```

Each step can also be run individually for debugging:

```bash
python3 download_event_index.py --config config.ini
python3 sipca_review.py --config config.ini
python3 send_email.py --config config.ini
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
- Exit code `2` means a step failed — wire your system's cron failure
  alerting (e.g. `MAILTO=` in crontab, or a monitoring wrapper) to that if
  you want failures flagged beyond the log file.

## Security notes

- `config.ini` is in `.gitignore` — never commit it.
- File permissions: `chmod 600 config.ini` so only the running user can read it.
- Prefer a SAS token scoped to just the `sipca` container with read/list
  permissions over the full storage account key, if your Azure setup allows it.
