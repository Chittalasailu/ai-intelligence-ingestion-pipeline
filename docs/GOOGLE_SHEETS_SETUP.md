# Google Sheets export — one-time setup

The pipeline can push its output straight to a Google Sheet
(`python -m scripts.export_data --sheets`), but Google requires a
credential that only you can create — this is the one step in this whole
project that needs a human at a keyboard with your Google account.

## Steps

1. **Create a GCP project** (or reuse one) at
   [console.cloud.google.com](https://console.cloud.google.com/).
2. **Enable two APIs** for that project: *Google Sheets API* and
   *Google Drive API* (APIs & Services → Library → search each → Enable).
3. **Create a service account**: APIs & Services → Credentials → Create
   Credentials → Service Account. Give it any name (e.g.
   `frontieratlas-pipeline`). No project role is required.
4. **Create a JSON key** for that service account: open it → Keys → Add
   Key → Create new key → JSON. This downloads a `.json` file — save it as
   `service-account.json` in the project root (already gitignored — never
   commit this file).
5. **Point the pipeline at it**: in `.env`, set
   `GOOGLE_SERVICE_ACCOUNT_FILE=./service-account.json`.
6. **(Optional) Use an existing spreadsheet** instead of letting the
   pipeline create one: open the target sheet, click Share, and add the
   service account's email (looks like
   `frontieratlas-pipeline@your-project.iam.gserviceaccount.com`, found in
   the JSON key file or the GCP console) as an **Editor**. Then set
   `GOOGLE_SHEET_ID` in `.env` to the ID from the sheet's URL
   (`docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`).

If you skip step 6, leave `GOOGLE_SHEET_ID` blank — the pipeline creates a
new spreadsheet on first export, sets it to "anyone with the link can
view," and prints its URL.

## Running the export

```bash
python -m scripts.export_data --sheets
```

This writes the same 6 tabs (Startups, Products, Research Papers, Jobs,
News, Entity Mapping Log) with a frozen, bold header row on each, reading
current data straight from the database — no crawler re-run needed.

If `service-account.json` isn't present, this command still writes
CSV/XLSX to `data/` and exits with a message telling you it skipped the
Sheets push, rather than failing the whole export.
