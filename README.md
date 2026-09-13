# Freebox — Google Drive backend

## What is already prepared

- Existing Freebox `index.html`
- Python server at `server.py`
- `/api/health`
- `/api/state`
- `/api/submissions`
- `/api/admin/login`
- `/api/admin/state`
- Google Drive database storage
- Google Drive `Freebox uploads` subfolder
- local JSON fallback if Google Drive is not configured

## Important security rule

Do NOT upload the Google Service Account JSON file to GitHub.

For Render, upload it as a Secret File named:

`google-service-account.json`

Render makes secret files available to the service at `/etc/secrets/<filename>`.

## Render

Build:
`pip install -r requirements.txt`

Start:
`python server.py`

Environment variables:

`PORT=10000`
`ADMIN_CODE=YOUR_PRIVATE_ADMIN_CODE`
`GOOGLE_DRIVE_FOLDER_ID=14qXbcPrRrRinqdMjptsG_U6GNyWo0N61`
`GOOGLE_SERVICE_ACCOUNT_FILE=/etc/secrets/google-service-account.json`

Then add the Google Service Account JSON as a Render Secret File.

## Google Drive

The Service Account must be shared on the `freebox` Drive folder with Editor access.

The first server start creates/uses:

- `freebox_database.json`
- `Freebox uploads/`

in that folder.

## Current limitation

The current frontend sends attachments as data URLs in the `/api/submissions` JSON request. This is convenient for the existing site, but not ideal for very large files. Later we can change uploads to multipart streaming/direct server upload so large files do not become huge JSON requests.

