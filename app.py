import io
import json
import os
import threading
import time
from datetime import datetime, timedelta

import pytz
import requests
from flask import Flask, Response, jsonify, render_template, render_template_string, request, send_file
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse

from crm import call, create_lead, get_tokens, log_call, refresh_access_token, send_email

app = Flask(__name__)

# Public HTTPS base URL of this app (no trailing slash), e.g. your tunnel URL.
# Used for Ultravox tool callbacks and recording links in Zoho.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

ZOHO_API_BASE = os.environ.get("ZOHO_CRM_API_BASE", "https://www.zohoapis.com.au").rstrip("/")

ULTRAVOX_API_KEY = os.environ.get("ULTRAVOX_API_KEY", "YOUR_ULTRAVOX_API_KEY")
ULTRAVOX_API_URL = "https://api.ultravox.ai/api/calls"
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "YOUR_TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "YOUR_TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER", "YOUR_TWILIO_PHONE_NUMBER")

SYSTEM_PROMPT = """
YOUR_SYSTEM_PROMPT_HERE
"""

_ULTRAVOX_HANDLER_URL = (
    f"{PUBLIC_BASE_URL}/ultravox-handler"
    if PUBLIC_BASE_URL
    else "YOUR_PUBLIC_BASE_URL/ultravox-handler"
)

ULTRAVOX_CALL_CONFIG = {
    "systemPrompt": SYSTEM_PROMPT,
    "selectedTools": [
        {
            "temporaryTool": {
                "modelToolName": "sendConversationSummary",
                "description": "Use this tool to send the customer's details and call summary to the server.",
                "dynamicParameters": [
                    {
                        "name": "Company",
                        "location": "PARAMETER_LOCATION_BODY",
                        "schema": {
                            "description": "The company the customer represents.",
                            "type": "string",
                        },
                        "required": False,
                    },
                    {
                        "name": "First_Name",
                        "location": "PARAMETER_LOCATION_BODY",
                        "schema": {
                            "description": "The first name of the customer.",
                            "type": "string",
                        },
                        "required": True,
                    },
                    {
                        "name": "Last_Name",
                        "location": "PARAMETER_LOCATION_BODY",
                        "schema": {
                            "description": "The last name of the customer.",
                            "type": "string",
                        },
                        "required": True,
                    },
                    {
                        "name": "Email",
                        "location": "PARAMETER_LOCATION_BODY",
                        "schema": {
                            "description": "The email address of the customer.",
                            "type": "string",
                        },
                        "required": True,
                    },
                    {
                        "name": "Phone",
                        "location": "PARAMETER_LOCATION_BODY",
                        "schema": {
                            "description": "The customer's phone number.",
                            "type": "string",
                        },
                        "required": False,
                    },
                    {
                        "name": "Platform",
                        "location": "PARAMETER_LOCATION_BODY",
                        "schema": {
                            "description": "The platform of demo such as Zoom, Google Meet, etc.",
                            "type": "string",
                        },
                        "required": True,
                    },
                    {
                        "name": "Subject",
                        "location": "PARAMETER_LOCATION_BODY",
                        "schema": {
                            "description": "Subject related to call purpose",
                            "type": "string",
                        },
                        "required": True,
                    },
                    {
                        "name": "Call_Purpose",
                        "location": "PARAMETER_LOCATION_BODY",
                        "schema": {
                            "description": "The purpose of the call (e.g., Product Inquiry, Demo Request).",
                            "type": "string",
                        },
                        "required": True,
                    },
                    {
                        "name": "Call_Result",
                        "location": "PARAMETER_LOCATION_BODY",
                        "schema": {
                            "description": "The result of the call (It should be one from the following 'interested', 'not interested' or 'demo').",
                            "type": "string",
                        },
                        "required": True,
                    },
                    {
                        "name": "Call_Agenda",
                        "location": "PARAMETER_LOCATION_BODY",
                        "schema": {
                            "description": "The main topic discussed or requested during the call.",
                            "type": "string",
                        },
                        "required": False,
                    },
                    {
                        "name": "Meeting_DateTime",
                        "location": "PARAMETER_LOCATION_BODY",
                        "schema": {
                            "description": "The scheduled meeting datetime values in ISO 8601 format with timezone offset, like this: YYYY-MM-DDTHH:MM:SS±HH:MM (e.g., 2025-04-15T12:00:00+10:00).",
                            "type": "string",
                            "format": "date-time",
                        },
                        "required": False,
                    },
                ],
                "http": {
                    "baseUrlPattern": _ULTRAVOX_HANDLER_URL,
                    "httpMethod": "POST",
                },
            }
        }
    ],
    "model": "fixie-ai/ultravox",
    "voice": "Deobra",
    "temperature": 0.3,
    "firstSpeaker": "FIRST_SPEAKER_AGENT",
    "recordingEnabled": True,
    "medium": {"twilio": {}},
}

# Single in-flight call context (not safe for concurrent calls).
calldic = {}

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


def ultravox_headers():
    return {"Content-Type": "application/json", "X-API-Key": ULTRAVOX_API_KEY}


def recording_public_url(ultravox_call_id):
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/recording/{ultravox_call_id}"
    return f"YOUR_PUBLIC_BASE_URL/recording/{ultravox_call_id}"


def parse_iso_datetime(value):
    """Parse ISO 8601 datetimes; handles trailing Z (UTC)."""
    if not value:
        raise ValueError("empty datetime")
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def normalize_call_result_for_crm(raw):
    """Map model output to values expected by crm.send_email."""
    if raw is None:
        return ""
    s = str(raw).strip().lower()
    if s == "interested":
        return "Interested"
    if s == "demo":
        return "demo"
    return "not interested"


def create_ultravox_call():
    response = requests.post(
        ULTRAVOX_API_URL, json=ULTRAVOX_CALL_CONFIG, headers=ultravox_headers(), timeout=60
    )
    response.raise_for_status()
    data = response.json()
    return data["callId"], data["joinUrl"]


def finalize_ultravox_call(ultravox_call_id):
    """
    Build Zoho payload from data.txt and Ultravox call metadata, then process_lead.
    Called from a background thread when the Ultravox call ends.
    """
    if not ultravox_call_id:
        print("finalize_ultravox_call: missing ultravox_call_id")
        return None

    headers = ultravox_headers()
    call_detail_url = f"https://api.ultravox.ai/api/calls/{ultravox_call_id}"
    detail_response = requests.get(call_detail_url, headers=headers, timeout=60)
    detail_response.raise_for_status()

    call_data = detail_response.json()
    joined_time = call_data.get("joined")
    ended_time = call_data.get("ended")
    if not joined_time or not ended_time:
        print("finalize_ultravox_call: missing joined/ended timestamps")
        return None

    joined_dt = parse_iso_datetime(joined_time)
    ended_dt = parse_iso_datetime(ended_time)
    duration_seconds = int((ended_dt - joined_dt).total_seconds())
    print("Call duration (seconds):", duration_seconds)

    if not os.path.isfile("data.txt"):
        print("finalize_ultravox_call: data.txt not found (Ultravox tool may not have posted yet)")
        return None

    with open("data.txt", "r", encoding="utf-8") as f:
        original = json.load(f)

    print(calldic)
    india_tz = pytz.timezone("Asia/Kolkata")

    if joined_dt.tzinfo is None:
        joined_dt = joined_dt.replace(tzinfo=pytz.utc)
    joined_dt_local = joined_dt.astimezone(india_tz)
    joined_time_with_offset = joined_dt_local.strftime("%Y-%m-%dT%H:%M:%S%z")
    joined_time_with_offset = joined_time_with_offset[:-2] + ":" + joined_time_with_offset[-2:]

    meeting_dt_raw = original.get("Meeting_DateTime")
    if meeting_dt_raw:
        start_time = parse_iso_datetime(meeting_dt_raw)
        if start_time.tzinfo is None:
            start_time_ist = india_tz.localize(start_time)
        else:
            start_time_ist = start_time.astimezone(india_tz)
    else:
        start_time_ist = joined_dt_local
    end_time_ist = start_time_ist + timedelta(hours=1)
    start_time_str = start_time_ist.isoformat()
    end_time_str = end_time_ist.isoformat()

    caller_number = calldic.get("caller-number", original.get("Phone", ""))
    call_result_crm = normalize_call_result_for_crm(original.get("Call_Result"))

    refactored = {
        "lead_data": {
            "data": [
                {
                    "Company": original.get("Company", ""),
                    "Last_Name": original.get("Last_Name", ""),
                    "First_Name": original.get("First_Name", ""),
                    "Email": original.get("Email", ""),
                    "Phone": caller_number,
                    "Lead_Status": "Contacted",
                    "Status": "Contacted",
                }
            ]
        },
        "call_data": {
            "data": [
                {
                    "$se_module": "Leads",
                    "Subject": original.get("Subject", ""),
                    "Call_Type": "Inbound",
                    "Call_Purpose": original.get("Call_Purpose", ""),
                    "Call_Result": call_result_crm,
                    "Call_Agenda": original.get("Call_Agenda", ""),
                    "Call_Duration": str(duration_seconds),
                    "Description": original.get("Call_Agenda", ""),
                    "Call_Start_Time": joined_time_with_offset,
                    "Call_Status": "Completed",
                    "Voice_Recording__s": recording_public_url(ultravox_call_id),
                }
            ]
        },
        "meeting_data": {
            "data": [
                {
                    "$se_module": "Leads",
                    "Description": original.get("Call_Agenda", ""),
                    "Meeting_Venue__s": "Online",
                    "Meeting_Provider__s": "Microsoft Teams",
                    "Start_DateTime": start_time_str,
                    "End_DateTime": end_time_str,
                    "Event_Title": original.get("Subject", ""),
                    "Participants": [{"type": "email", "Email": original.get("Email", "")}],
                    "Send_Invitation_Email": True,
                }
            ]
        },
    }

    with open("finaldata.json", "w", encoding="utf-8") as outfile:
        json.dump(refactored, outfile, indent=4, default=str)
    process_lead("finaldata.json")
    return refactored


def check_call_status(ultravox_call_id):
    headers = ultravox_headers()
    while True:
        call_detail_url = f"https://api.ultravox.ai/api/calls/{ultravox_call_id}"
        detail_response = requests.get(call_detail_url, headers=headers, timeout=60)
        detail_response.raise_for_status()
        call_data = detail_response.json()
        ended_time = call_data.get("ended")

        if ended_time:
            print(f"Call {ultravox_call_id} has ended at {ended_time}")
            fetch_transcript_for_call(ultravox_call_id)
            try:
                finalize_ultravox_call(ultravox_call_id)
            except Exception as e:
                print(f"finalize_ultravox_call error: {e}")
            break

        print(f"Call {ultravox_call_id} still ongoing, checking again in 5 seconds...")
        time.sleep(5)


@app.route("/convertjson")
def convert_json_route():
    try:
        cid = calldic.get("caller-id")
        result = finalize_ultravox_call(cid)
        if result is None:
            return jsonify({"status": "error", "message": "Nothing to convert (missing call id or data)"}), 400
        return json.dumps(result, indent=4), 200, {"Content-Type": "application/json"}
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/ultravox-handler", methods=["POST"])
def handle_ultravox_tool():
    try:
        data = request.json
        print("Received Tool Data from Ultravox:", data)
        with open("data.txt", "w", encoding="utf-8") as file:
            file.write(json.dumps(data, indent=2))
        return jsonify({"status": "success"}), 200
    except Exception as e:
        print("Error handling tool data:", e)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/incoming", methods=["POST"])
def handle_incoming_call():
    try:
        caller_number = request.form.get("From")
        print(f"Incoming call received from: {caller_number}")
        calldic["caller-number"] = caller_number

        ultravox_call_id, join_url = create_ultravox_call()
        calldic["caller-id"] = ultravox_call_id
        print(f"Call ID: {ultravox_call_id}")

        threading.Thread(
            target=check_call_status, args=(ultravox_call_id,), daemon=True
        ).start()

        twiml = VoiceResponse()
        connect = twiml.connect()
        connect.stream(url=join_url, name="ultravox")
        return Response(str(twiml), mimetype="text/xml")
    except Exception as e:
        print(f"Error handling incoming call: {e}")
        twiml = VoiceResponse()
        twiml.say("Sorry, there was an error connecting your call.")
        return Response(str(twiml), mimetype="text/xml")


@app.route("/ultravox-calls")
def list_calls():
    response = requests.get(
        "https://api.ultravox.ai/api/calls", headers=ultravox_headers(), timeout=60
    )
    if response.status_code == 200:
        data = response.json()
        calls = data.get("results", [])
        return render_template("calls.html", calls=calls)
    return f"Error fetching calls data: {response.status_code}", response.status_code


def fetch_transcript_for_call(call_id):
    headers = ultravox_headers()
    try:
        stages_url = f"https://api.ultravox.ai/api/calls/{call_id}/stages"
        stages_response = requests.get(stages_url, headers=headers, timeout=60)
        stages_response.raise_for_status()
        stages_data = stages_response.json()

        call_stage_ids = [stage["callStageId"] for stage in stages_data.get("results", [])]
        if not call_stage_ids:
            raise RuntimeError("No call stage IDs found")
        req_stage_id = call_stage_ids[0]
        print("Stage ID:", req_stage_id)

        transcript_url = f"https://api.ultravox.ai/api/calls/{call_id}/stages/{req_stage_id}/messages"
        transcript_response = requests.get(transcript_url, headers=headers, timeout=60)
        transcript_response.raise_for_status()
        transcript_data = transcript_response.json()

        os.makedirs("transcripts", exist_ok=True)
        with open(f"transcripts/{call_id}.txt", "a", encoding="utf-8") as file:
            file.write(json.dumps(transcript_data, indent=2))
        print("Transcript saved")
        return transcript_data
    except Exception as e:
        print(f"Error fetching transcript: {e}")
        return None


@app.route("/transcripts")
def list_all_transcripts():
    headers = ultravox_headers()
    try:
        calls_url = "https://api.ultravox.ai/api/calls"
        calls_response = requests.get(calls_url, headers=headers, timeout=60)
        calls_response.raise_for_status()
        calls_data = calls_response.json()
        call_ids = [c["callId"] for c in calls_data.get("results", [])]
        if not call_ids:
            raise RuntimeError("No call IDs found")
        req_call_id = call_ids[0]

        stages_url = f"https://api.ultravox.ai/api/calls/{req_call_id}/stages"
        stages_response = requests.get(stages_url, headers=headers, timeout=60)
        stages_response.raise_for_status()
        stages_data = stages_response.json()
        call_stage_ids = [stage["callStageId"] for stage in stages_data.get("results", [])]
        if not call_stage_ids:
            raise RuntimeError("No call stage IDs found")
        req_stage_id = call_stage_ids[0]

        transcript_url = f"https://api.ultravox.ai/api/calls/{req_call_id}/stages/{req_stage_id}/messages"
        transcript_response = requests.get(transcript_url, headers=headers, timeout=60)
        transcript_response.raise_for_status()
        transcript_data = transcript_response.json()
        messages = transcript_data.get("results", [])

        html_template = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Call Transcript</title>
            <style>
                body { font-family: Arial, sans-serif; padding: 2rem; background-color: #f9f9f9; }
                .message { margin-bottom: 1.5rem; padding: 1rem; border-radius: 8px; }
                .agent { background-color: #e3f2fd; }
                .user { background-color: #fff3e0; }
                .role { font-weight: bold; margin-bottom: 4px; }
                .timestamp { color: gray; font-size: 0.9rem; }
            </style>
        </head>
        <body>
            <h1>Call Transcript</h1>
            {% for msg in messages %}
                <div class="message {{ 'agent' if 'AGENT' in msg.role else 'user' }}">
                    <div class="role">{{ 'Cara (Agent)' if 'AGENT' in msg.role else 'You (User)' }}</div>
                    <div class="text">{{ msg.text }}</div>
                    {% if msg.timespan %}
                        <div class="timestamp">From {{ msg.timespan.start }} to {{ msg.timespan.end }}</div>
                    {% endif %}
                </div>
            {% endfor %}
        </body>
        </html>
        """
        return render_template_string(html_template, messages=messages)
    except requests.RequestException as e:
        return f"<h3>API request failed:</h3><pre>{e}</pre>"
    except Exception as e:
        return f"<h3>Error:</h3><pre>{e}</pre>"


def process_lead(json_filename):
    with open(json_filename, "r", encoding="utf-8") as f:
        data = json.load(f)
    with open("test.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

    if not os.path.exists("zoho_tokens.json"):
        tokens = get_tokens()
    else:
        with open("zoho_tokens.json", "r", encoding="utf-8") as f:
            tokens = json.load(f)

    if not tokens:
        print("Token error")
        return

    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    test = requests.get(
        f"{ZOHO_API_BASE}/crm/v7/Leads",
        headers={"Authorization": f"Zoho-oauthtoken {access_token}"},
        timeout=60,
    )
    if test.status_code == 401:
        tokens = refresh_access_token(refresh_token)
        if not tokens or not tokens.get("access_token"):
            print("Token refresh failed")
            return
        access_token = tokens["access_token"]

    lead_id = create_lead(access_token)
    if not lead_id:
        print("Lead creation failed.")
        return

    print("Lead created with ID:", lead_id)

    call_result = call()
    print("Call result:", call_result)

    if call_result:
        log_call(access_token, lead_id)
        send_email(call_result, access_token, lead_id)
    else:
        print("Failed to get call result")


def fetch_leads():
    if not os.path.exists("zoho_tokens.json"):
        tokens = get_tokens()
    else:
        with open("zoho_tokens.json", "r", encoding="utf-8") as f:
            tokens = json.load(f)

    if not tokens:
        print("Token error")
        return None

    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    url = f"{ZOHO_API_BASE}/crm/v7/Leads"
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
    response = requests.get(url, headers=headers, timeout=60)
    if response.status_code == 401 and refresh_token:
        tokens = refresh_access_token(refresh_token)
        if not tokens or not tokens.get("access_token"):
            return None
        access_token = tokens["access_token"]
        response = requests.get(
            url, headers={"Authorization": f"Zoho-oauthtoken {access_token}"}, timeout=60
        )

    data = response.json()
    leads = data.get("data", [])

    simplified_leads = []
    for lead in leads:
        simplified_leads.append(
            {
                "Full Name": lead.get("Full_Name", ""),
                "Email": lead.get("Email", ""),
                "Phone": lead.get("Phone", ""),
                "Company": lead.get("Company", ""),
                "Lead Source": lead.get("Lead_Source", ""),
            }
        )
    return simplified_leads


TEMPLATE = """
<!doctype html>
<html>
<head>
    <title>Zoho Leads</title>
    <style>
        table { border-collapse: collapse; width: 100%; }
        th, td { padding: 8px 12px; border: 1px solid #ccc; text-align: left; }
        th { background-color: #f2f2f2; }
    </style>
</head>
<body>
    <h2>Zoho Leads</h2>
    <table>
        <tr>
            {% for key in leads[0].keys() %}
                <th>{{ key }}</th>
            {% endfor %}
        </tr>
        {% for lead in leads %}
        <tr>
            {% for value in lead.values() %}
                <td>{{ value }}</td>
            {% endfor %}
        </tr>
        {% endfor %}
    </table>
</body>
</html>
"""


@app.route("/zoho-leads")
def show_leads():
    leads = fetch_leads()
    if leads is not None and len(leads) > 0:
        return render_template_string(TEMPLATE, leads=leads)
    return "<h3>No leads found or failed to fetch leads.</h3>"


@app.route("/recording/<caller_id>")
def get_recording(caller_id):
    url = f"https://api.ultravox.ai/api/calls/{caller_id}/recording"
    response = requests.get(url, headers=ultravox_headers(), timeout=120)
    if response.status_code == 200:
        audio_data = io.BytesIO(response.content)
        audio_data.seek(0)
        return send_file(
            audio_data,
            mimetype=response.headers.get("Content-Type", "audio/wav"),
            as_attachment=False,
            download_name=f"{caller_id}.wav",
        )
    return f"<h3>Failed to fetch recording: {response.status_code}</h3>", response.status_code


@app.route("/twilio-calls")
def show_twilio_calls():
    calls = fetch_twilio_calls()
    if calls:
        return render_template_string(TWILIO_TEMPLATE, calls=calls)
    return "<h3>No call logs found or failed to fetch data.</h3>"


def fetch_twilio_calls():
    try:
        calls = twilio_client.calls.list(limit=20)
        simplified_calls = []
        for c in calls:
            simplified_calls.append(
                {
                    "From": c._from,
                    "To": c.to,
                    "Status": c.status,
                    "Duration (s)": c.duration,
                    "Start Time": str(c.start_time),
                    "SID": c.sid,
                }
            )
        return simplified_calls
    except Exception as e:
        print("Error fetching Twilio calls:", e)
        return []


TWILIO_TEMPLATE = """
<!doctype html>
<html>
<head>
    <title>Twilio Call Logs</title>
    <style>
        table { border-collapse: collapse; width: 100%; }
        th, td { padding: 8px 12px; border: 1px solid #ccc; text-align: left; }
        th { background-color: #f9f9f9; }
    </style>
</head>
<body>
    <h2>Twilio Call Logs</h2>
    <table>
        <tr>
            {% for key in calls[0].keys() %}
                <th>{{ key }}</th>
            {% endfor %}
        </tr>
        {% for call in calls %}
        <tr>
            {% for value in call.values() %}
                <td>{{ value }}</td>
            {% endfor %}
        </tr>
        {% endfor %}
    </table>
</body>
</html>
"""


@app.route("/call-recording")
def show_call_recordings():
    try:
        response = requests.get(
            "https://api.ultravox.ai/api/calls", headers=ultravox_headers(), timeout=60
        )
        response.raise_for_status()
        data = response.json()
        calls = data.get("results", [])

        table_html = """
        <!doctype html>
        <html>
        <head>
            <title>Call Recordings</title>
            <style>
                table { border-collapse: collapse; width: 100%; }
                th, td { padding: 8px 12px; border: 1px solid #ccc; text-align: left; }
                th { background-color: #f2f2f2; }
            </style>
        </head>
        <body>
            <h2>Ultravox Call Recordings</h2>
            <table>
                <tr>
                    <th>Call ID</th>
                    <th>Created</th>
                    <th>Ended</th>
                    <th>Recording</th>
                </tr>
                {% for call in calls %}
                <tr>
                    <td>{{ call['callId'] }}</td>
                    <td>{{ call['created'] }}</td>
                    <td>{{ call['ended'] }}</td>
                    <td><a href="/recording/{{ call['callId'] }}" target="_blank">View Recording</a></td>
                </tr>
                {% endfor %}
            </table>
        </body>
        </html>
        """

        return render_template_string(table_html, calls=calls)
    except Exception as e:
        return f"<h3>Error fetching call data: {e}</h3>"


HOMEPAGE_TEMPLATE = """
<!doctype html>
<html>
<head>
    <title>AI Bot Dashboard</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            padding: 40px;
            background-color: #f4f4f4;
        }
        h1 {
            color: #333;
        }
        .card {
            background: white;
            padding: 20px;
            margin: 20px 0;
            border-radius: 12px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        a {
            text-decoration: none;
            color: #007bff;
            font-size: 18px;
        }
        a:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <h1>Welcome to AI Bot Dashboard</h1>

    <div class="card">
        <a href="/twilio-calls">View Twilio Call Logs</a>
    </div>
    <div class="card">
        <a href="/ultravox-calls">View Ultravox Call Logs</a>
    </div>
    <div class="card">
        <a href="/call-recording">Call Recording</a>
    </div>
    <div class="card">
        <a href="/zoho-leads">View Zoho Leads</a>
    </div>
    <div class="card">
        <a href="/transcripts">View last Transcript</a>
    </div>
    <div class="card">
        <a href="/convertjson">Convert JSON</a>
    </div>
</body>
</html>
"""


@app.route("/")
def homepage():
    return render_template_string(HOMEPAGE_TEMPLATE)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=9211)
