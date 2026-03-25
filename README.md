# Voice-Agent

Voice-Agent is an inbound AI voice agent built with Python, Flask, Twilio, Ultravox, and Zoho CRM.
It accepts an inbound phone call, starts an Ultravox call session, captures the conversation summary via a callback endpoint, and then creates/updates a Lead in Zoho CRM.

## Overview
This project provides a working skeleton for an AI-driven inbound voice workflow:
it receives a call from Twilio, streams the call audio into Ultravox, receives the conversation summary and transcript, and then uses that information to create a lead and log the call in Zoho CRM.

Main components:
- `app.py`: Flask server (Twilio webhook, Ultravox callback, dashboards/endpoints)
- `crm.py`: Zoho CRM integration (OAuth token handling, Lead creation, call logging)
- `calls.html`: Template used to display Ultravox call records

## How it works (high level)
1. Twilio sends an HTTP POST to the Flask endpoint `POST /incoming` when a call arrives.
2. The server creates an Ultravox call session and responds with TwiML that connects the call audio stream to Ultravox.
3. Ultravox calls the tool callback endpoint `POST /ultravox-handler` with the structured conversation summary.
4. The server waits for the Ultravox call to end, fetches the transcript, and runs the lead-processing flow:
   - Writes data files
   - Creates a Lead in Zoho CRM
   - Logs the Call in Zoho CRM
   - Triggers an email/meeting decision based on the call result

## Project layout
This repository contains:
- `app.py` - Flask server (Twilio webhook, Ultravox callback, dashboards/endpoints)
- `crm.py` - Zoho CRM helpers (token handling, Lead creation, call logging)
- `calls.html` - Template used by the `GET /ultravox-calls` endpoint (must be placed under `templates/`)
- `zoho_tokens.json` - Token cache for Zoho (edit locally with your own values)

Create these folders before running:
- `transcripts/` - used to save Ultravox call transcripts
- `templates/` - used by Flask to render `calls.html`

## Prerequisites
- Python 3
- A Twilio account and phone number capable of sending inbound calls to your webhook
- An Ultravox API key
- Zoho CRM OAuth credentials (or a valid `zoho_tokens.json`)

Python packages (install in your virtual environment):
```bash
pip install flask requests twilio pytz
```

## Configuration you must update
### 1) `app.py`
Update the placeholders at the top of `app.py`:
- `ULTRAVOX_API_KEY`
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_PHONE_NUMBER` (if needed by your Twilio setup)
- `TWILIO_WEBHOOK_URL` (the public webhook URL Twilio will call for `POST /incoming`)
- `SYSTEM_PROMPT` (your agent instruction prompt)

Also update the Ultravox tool callback URL inside `ULTRAVOX_CALL_CONFIG`:
- `selectedTools[0].temporaryTool.http.baseUrlPattern`
- Set it to your public server base URL plus `/ultravox-handler`

Important notes:
- Your `TWILIO_WEBHOOK_URL` and Ultravox `baseUrlPattern` must point to the same public server that exposes your Flask app.
- The Ultravox callback writes the received tool payload to `data.txt`, and later uses it in `/convertjson`.

### 2) `crm.py`
If you do not already have working tokens in `zoho_tokens.json`, update:
- `CLIENT_ID`
- `CLIENT_SECRET`
- `REDIRECT_URI`
- `AUTH_CODE`

If `zoho_tokens.json` exists and contains valid tokens, `app.py`/`crm.py` will use it for API calls.

## Run the server
Start the Flask server:
```bash
python app.py
```
It listens on port `9211`.

To receive inbound calls and callbacks, you must expose your local server to the internet using a tunneling service (for example, ngrok) and then set:
- Twilio webhook URL to your public URL plus `/incoming`
- Ultravox tool callback base URL pattern to your public URL plus `/ultravox-handler`

## Required files setup
1. Create `transcripts/` in the project root.
2. Create `templates/` in the project root.
3. Copy `calls.html` into `templates/calls.html` so Flask can render it for `GET /ultravox-calls`.

## Useful endpoints
- `POST /incoming`  
  Twilio webhook entrypoint for inbound calls.
- `POST /ultravox-handler`  
  Ultravox tool callback endpoint that receives the conversation summary payload.
- `GET /transcripts`  
  Renders a simple HTML view for the latest transcript.
- `GET /ultravox-calls`  
  Renders Ultravox call records using `templates/calls.html`.
- `GET /convertjson`  
  Converts the last tool payload to the Zoho Lead/Call/Meeting payload and triggers the Zoho workflow.
- `GET /zoho-leads`  
  Fetches recent Zoho Leads and renders them as HTML.
- `GET /recording/<caller_id>`  
  Streams the Ultravox recording for a given call ID.
- `GET /twilio-calls` and `GET /call-recording`  
  Renders Twilio call logs and a list of Ultravox recordings.

## Notes and limitations
- The application uses a global dictionary (`calldic`) to store the current call context. This is not designed for concurrent calls; concurrent requests can overwrite each other.
- Data files used in the workflow:
  - `data.txt` and `finaldata.json` are generated during the Ultravox callback processing
  - `crm.py` uses an intermediate `test.json` file when creating/updating CRM records
- The lead-processing flow is driven by the `Call_Result` value produced by the Ultravox tool callback. Ensure your tool schema and CRM logic agree on the expected values/casing.
