import io
from flask import Flask, jsonify, request, Response, render_template_string,abort, send_file
import requests
from twilio.twiml.voice_response import VoiceResponse
from twilio.rest import Client
import json
import time
import threading
from datetime import datetime, timedelta
import pytz
import os
from crm import get_tokens, refresh_access_token, create_lead, log_call, send_email, call

app = Flask(__name__)

# Use your own Server URL and run it in terminal to start ngrok server
# e.g. ngrok http 9211 --url=https://exact-bluegill-separately.ngrok-free.app

# Configuration
calldic = {
}
call_id="" # Global var

ULTRAVOX_API_KEY = 'YOUR_ULTRAVOX_API_KEY'
ULTRAVOX_API_URL = 'https://api.ultravox.ai/api/calls'
TWILIO_ACCOUNT_SID = 'YOUR_TWILIO_ACCOUNT_SID'
TWILIO_AUTH_TOKEN = 'YOUR_TWILIO_AUTH_TOKEN'
TWILIO_PHONE_NUMBER = 'YOUR_TWILIO_PHONE_NUMBER'
TWILIO_WEBHOOK_URL = 'https://exact-bluegill-separately.ngrok-free.app/incoming'  # Change to your actual webhook URL

SYSTEM_PROMPT = """
YOUR_SYSTEM_PROMPT_HERE
"""
ULTRAVOX_CALL_CONFIG = {
    'systemPrompt': SYSTEM_PROMPT,
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
            "type": "string"
          },
          "required": False
        },
        {
          "name": "First_Name",
          "location": "PARAMETER_LOCATION_BODY",
          "schema": {
            "description": "The first name of the customer.",
            "type": "string"
          },
          "required": True
        },
        {
          "name": "Last_Name",
          "location": "PARAMETER_LOCATION_BODY",
          "schema": {
            "description": "The last name of the customer.",
            "type": "string"
          },
          "required": True
        },
        {
          "name": "Email",
          "location": "PARAMETER_LOCATION_BODY",
          "schema": {
            "description": "The email address of the customer.",
            "type": "string"
          },
          "required": True
        },
        {
          "name": "Phone",
          "location": "PARAMETER_LOCATION_BODY",
          "schema": {
            "description": "The customer's phone number.",
            "type": "string"
          },
          "required": False
        },
        {
          "name": "Platform",
          "location": "PARAMETER_LOCATION_BODY",
          "schema": {
            "description": "The platform of demo such as Zoom, Google Meet, etc.",
            "type": "string"
          },
          "required": True
        },
        {
          "name": "Subject",
          "location": "PARAMETER_LOCATION_BODY",
          "schema": {
            "description": "Subject related to call purpose",
            "type": "string"
          },
          "required": True
        },
        {
          "name": "Call_Purpose",
          "location": "PARAMETER_LOCATION_BODY",
          "schema": {
            "description": "The purpose of the call (e.g., Product Inquiry, Demo Request).",
            "type": "string"
          },
          "required": True
        },
        {
          "name": "Call_Result",
          "location": "PARAMETER_LOCATION_BODY",
          "schema": {
            "description": "The result of the call (It should be one from the following 'interested', 'not interested' or 'demo').",
            "type": "string"
          },
          "required": True
        },
        {
          "name": "Call_Agenda",
          "location": "PARAMETER_LOCATION_BODY",
          "schema": {
            "description": "The main topic discussed or requested during the call.",
            "type": "string"
          },
          "required": False
        },
        {
          "name": "Meeting_DateTime",
          "location": "PARAMETER_LOCATION_BODY",
          "schema": {
            "description": "The scheduled meeting datetime values in ISO 8601 format with timezone offset, like this: YYYY-MM-DDTHH:MM:SS±HH:MM (e.g., 2025-04-15T12:00:00+10:00).",
            "type": "string",
            "format": "date-time"
          },
          "required": False
        }
      ],
      "http": {
        "baseUrlPattern": "https://exact-bluegill-separately.ngrok-free.app/ultravox-handler",
        "httpMethod": "POST"
      }
    }
  }
],

    'model': 'fixie-ai/ultravox',
    'voice': 'Deobra',
    'temperature': 0.3,
    'firstSpeaker': 'FIRST_SPEAKER_AGENT',
    "recordingEnabled": True,
    'medium': {'twilio': {}},
}

# Initialize Twilio client
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Create Ultravox call and get join URL
def create_ultravox_call():
    
    headers = {
        'Content-Type': 'application/json',
        'X-API-Key': ULTRAVOX_API_KEY
    }
    response = requests.post(ULTRAVOX_API_URL, json=ULTRAVOX_CALL_CONFIG, headers=headers)
    response.raise_for_status()
    data = response.json()
    return data['callId'], data['joinUrl']

def check_call_status(call_id):
    headers = {
        'Content-Type': 'application/json',
        'X-API-Key': ULTRAVOX_API_KEY
    }

    while True:
        call_detail_url = f"https://api.ultravox.ai/api/calls/{call_id}"
        detail_response = requests.get(call_detail_url, headers=headers)
        detail_response.raise_for_status()
        call_data = detail_response.json()

        ended_time = call_data.get("ended")

        if ended_time:
            print(f"Call {call_id} has ended at {ended_time}")
            fetch_transcript_for_call(call_id)
            convert_json()
            break  # Exit the loop after the call ends

        print(f"Call {call_id} still ongoing, checking again in 5 seconds...")
        time.sleep(5)

@app.route('/convertjson')
def convert_json():

  headers = {
        'Content-Type': 'application/json',
        'X-API-Key': ULTRAVOX_API_KEY
    }

  call_detail_url = f"https://api.ultravox.ai/api/calls/{calldic['caller-id']}"
  detail_response = requests.get(call_detail_url, headers=headers)
  detail_response.raise_for_status()


  call_data = detail_response.json()
  joined_time = call_data.get("joined")
  ended_time = call_data.get("ended")
  joined_dt = datetime.fromisoformat(joined_time.replace("Z", ""))
  ended_dt = datetime.fromisoformat(ended_time.replace("Z", ""))
  duration = ended_dt - joined_dt
  duration_seconds = int(duration.total_seconds())
  print("Call Duration in Minutes:", duration_seconds)

  import json
  # Original data
  with open('data.txt', 'r') as f:
      original = json.load(f)
  print(calldic)
  india_tz = pytz.timezone("Asia/Kolkata")

  # Parse original meeting datetime
  start_time = datetime.fromisoformat(original["Meeting_DateTime"])

  # Safely apply IST timezone
  if start_time.tzinfo is None:
      start_time_ist = india_tz.localize(start_time)
  else:
      start_time_ist = start_time.astimezone(india_tz)

  # Add 1 hour
  end_time_ist = start_time_ist + timedelta(hours=1)

  # Format as ISO 8601
  start_time_str = start_time_ist.isoformat()
  end_time_str = end_time_ist.isoformat()

  # Ensure joined_dt is timezone-aware (UTC)
  if joined_dt.tzinfo is None:
      joined_dt = joined_dt.replace(tzinfo=pytz.utc)

  # Convert to IST
  joined_dt_local = joined_dt.astimezone(india_tz)

  # Format with proper ISO 8601 offset format (+05:30)
  joined_time_with_offset = joined_dt_local.strftime("%Y-%m-%dT%H:%M:%S%z")
  joined_time_with_offset = joined_time_with_offset[:-2] + ":" + joined_time_with_offset[-2:]

  # Refactored data
  refactored = {
      "lead_data": {
          "data": [{
              "Company": original["Company"],
              "Last_Name": original["Last_Name"],
              "First_Name": original["First_Name"],
              "Email": original["Email"],
              "Phone": calldic["caller-number"],
              "Lead_Status": "Contacted",
              "Status": "Contacted"
          }]
      },
      "call_data": {
          "data": [{
              "$se_module": "Leads",
              "Subject": original["Subject"],
              "Call_Type": "Inbound",
              "Call_Purpose": original["Call_Purpose"],
              "Call_Result": original["Call_Result"],
              "Call_Agenda": original["Call_Agenda"],
              "Call_Duration": str(duration_seconds),
              "Description": original["Call_Agenda"],
              "Call_Start_Time": joined_time_with_offset,
              "Call_Status": "Completed",
              "Voice_Recording__s":f"https://exact-bluegill-separately.ngrok-free.app/recording/{calldic['caller-id']}"
          }]
      },
        "meeting_data": {
            "data": [{
                "$se_module": "Leads",
                "Description": original["Call_Agenda"],
                "Meeting_Venue__s": "Online",
                "Meeting_Provider__s": "Microsoft Teams",
                "Start_DateTime": start_time_str,
                "End_DateTime": end_time_str,
                "Event_Title": original["Subject"],
                "Participants": [
                    {
                        "type": "email",
                        "Email": original["Email"]
                    }
                ],
                "Send_Invitation_Email": True
            }]
        }
  }
  # calldic.clear()
  import json
  with open("finaldata.json", "w") as outfile:
    json.dump(refactored, outfile, indent=4, default=str)
  process_lead('finaldata.json')  
  return(json.dumps(refactored, indent=4))
        
@app.route('/ultravox-handler', methods=['POST'])
def handle_ultravox_tool():
    try:
        data = request.json
        print("Received Tool Data from Ultravox:", data)
        with open("data.txt", 'w') as file:
            file.write(json.dumps(data, indent=2))
        
        print(f"Transcript written to file.txt")
        return jsonify({"status": "success"}), 200

    except Exception as e:
        print("Error handling tool data:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

# Handle incoming calls
@app.route('/incoming', methods=['POST'],)
def handle_incoming_call():
    try:
        caller_number = request.form.get("From")
        print(f"Incoming call received from: {caller_number}")
        calldic["caller-number"]=caller_number
        call_id, join_url = create_ultravox_call()  # Unpack callId and joinUrl
        threading.Thread(target=check_call_status, args=(call_id,)).start()
        # Store call_id somewhere (like a database or in-memory dict)
        print(f"Call ID: {call_id}")
        calldic["caller-id"]=call_id  
        twiml = VoiceResponse()
        connect = twiml.connect()
        connect.stream(url=join_url, name='ultravox')
        return Response(str(twiml), mimetype='text/xml')
    
    except Exception as e:
        print(f'Error handling incoming call: {e}')
        twiml = VoiceResponse()
        twiml.say('Sorry, there was an error connecting your call.')
        return Response(str(twiml), mimetype='text/xml')

# Optional debug endpoint
from flask import render_template
@app.route('/ultravox-calls')
def list_calls():
    headers = {
        'Content-Type': 'application/json',
        'X-API-Key': ULTRAVOX_API_KEY
    }

    response = requests.get('https://api.ultravox.ai/api/calls', headers=headers)

    if response.status_code == 200:
        data = response.json()
        calls = data.get('results', [])
        return render_template('calls.html', calls=calls)
    else:
        return f"Error fetching calls data: {response.status_code}", response.status_code

def fetch_transcript_for_call(call_id):
    headers = {
        'Content-Type': 'application/json',
        'X-API-Key': ULTRAVOX_API_KEY
    }

    try:
        # Step 1: Get latest call stage ID
        stages_url = f"https://api.ultravox.ai/api/calls/{call_id}/stages"
        stages_response = requests.get(stages_url, headers=headers)
        stages_response.raise_for_status()
        stages_data = stages_response.json()

        call_stage_ids = [stage["callStageId"] for stage in stages_data.get("results", [])]
        if not call_stage_ids:
            raise Exception("No call stage IDs found")
        req_stage_id = call_stage_ids[0]
        print("Stage ID:", req_stage_id)

        # Step 2: Get transcript
        transcript_url = f"https://api.ultravox.ai/api/calls/{call_id}/stages/{req_stage_id}/messages"
        transcript_response = requests.get(transcript_url, headers=headers)
        transcript_response.raise_for_status()
        transcript_data = transcript_response.json()

        os.makedirs("transcripts", exist_ok=True)
        # Write to file
        with open(f"transcripts/{call_id}.txt", 'a') as file:
            file.write(json.dumps(transcript_data, indent=2))
        
        print("Transcript saved")
        return transcript_data

    except Exception as e:
        print(f"Error fetching transcript: {e}")
        return None

@app.route('/transcripts')
def list_all_transcripts():
    headers = {
        'Content-Type': 'application/json',
        'X-API-Key': ULTRAVOX_API_KEY
    }

    try:
        # Step 1: Get latest call ID
        calls_url = "https://api.ultravox.ai/api/calls"
        calls_response = requests.get(calls_url, headers=headers)
        calls_response.raise_for_status()
        calls_data = calls_response.json()

        call_ids = [call["callId"] for call in calls_data.get("results", [])]
        if not call_ids:
            raise Exception("No call IDs found")
        req_call_id = call_ids[0]

        # Step 2: Get latest call stage ID
        stages_url = f"https://api.ultravox.ai/api/calls/{req_call_id}/stages"
        stages_response = requests.get(stages_url, headers=headers)
        stages_response.raise_for_status()
        stages_data = stages_response.json()

        call_stage_ids = [stage["callStageId"] for stage in stages_data.get("results", [])]
        if not call_stage_ids:
            raise Exception("No call stage IDs found")
        req_stage_id = call_stage_ids[0]

        # Step 3: Get transcript messages
        transcript_url = f"https://api.ultravox.ai/api/calls/{req_call_id}/stages/{req_stage_id}/messages"
        transcript_response = requests.get(transcript_url, headers=headers)
        transcript_response.raise_for_status()
        transcript_data = transcript_response.json()
        messages = transcript_data.get("results", [])

        # Render HTML
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
    # Save the file as 'test.json' which zoho_crm functions expect
    with open(json_filename, 'r') as f:
        data = json.load(f)

    with open('test.json', 'w') as f:
        json.dump(data, f, indent=4)

    # Use Zoho logic
    if not os.path.exists("zoho_tokens.json"):
        tokens = get_tokens()
    else:
        with open("zoho_tokens.json", "r") as f:
            tokens = json.load(f)

    if not tokens:
        print("Token error")
        return

    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    test = requests.get("https://www.zohoapis.in/crm/v7/Leads", headers={"Authorization": f"Zoho-oauthtoken {access_token}"})
    if test.status_code == 401:
        tokens = refresh_access_token(refresh_token)
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
        with open("zoho_tokens.json", "r") as f:
            tokens = json.load(f)

    if not tokens:
        print("Token error")
        return

    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    url = "https://www.zohoapis.in/crm/v7/Leads"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}"
    }
    response = requests.get(url, headers=headers)
    data = response.json()
    leads = data.get("data", [])

    # Simplify the output by keeping only a few fields
    simplified_leads = []
    for lead in leads:
        simplified_leads.append({
            "Full Name": lead.get("Full_Name", ""),
            "Email": lead.get("Email", ""),
            "Phone": lead.get("Phone", ""),
            "Company": lead.get("Company", ""),
            "Lead Source": lead.get("Lead_Source", "")
        })

    return simplified_leads

TEMPLATE = '''
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
'''

@app.route("/zoho-leads")
def show_leads():
    leads = fetch_leads()
    if leads:
        return render_template_string(TEMPLATE, leads=leads)
    else:
        return "<h3>No leads found or failed to fetch leads.</h3>"

@app.route('/recording/<caller_id>')
def get_recording(caller_id):
    headers = {
        'Content-Type': 'application/json',
        'X-API-Key': ULTRAVOX_API_KEY
    }
    url = f"https://api.ultravox.ai/api/calls/{caller_id}/recording"

    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        # Load the entire file into memory
        audio_data = io.BytesIO(response.content)
        audio_data.seek(0)
        
        # Send the WAV file to the client
        return send_file(
            audio_data,
            mimetype=response.headers.get("Content-Type", "audio/wav"),
            as_attachment=False,
            download_name=f"{caller_id}.wav"
        )
    else:
        return f"<h3>Failed to fetch recording: {response.status_code}</h3>", response.status_code
    
@app.route("/twilio-calls")
def show_twilio_calls():
    calls = fetch_twilio_calls()
    if calls:
        return render_template_string(TWILIO_TEMPLATE, calls=calls)
    else:
        return "<h3>No call logs found or failed to fetch data.</h3>"

def fetch_twilio_calls():
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    try:
        calls = client.calls.list(limit=20)  # Limit to recent 20 calls
        simplified_calls = []
        for call in calls:
            simplified_calls.append({
                "From": call._from,
                "To": call.to,
                "Status": call.status,
                "Duration (s)": call.duration,
                "Start Time": str(call.start_time),
                "SID": call.sid
            })
        return simplified_calls
    except Exception as e:
        print("Error fetching Twilio calls:", e)
        return []

TWILIO_TEMPLATE = '''
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
'''

@app.route('/call-recording')
def show_call_recordings():
    headers = {
        'Content-Type': 'application/json',
        'X-API-Key': ULTRAVOX_API_KEY
    }

    try:
        response = requests.get('https://api.ultravox.ai/api/calls', headers=headers)
        response.raise_for_status()
        data = response.json()
        calls = data.get('results', [])

        table_html = '''
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
        '''

        return render_template_string(table_html, calls=calls)

    except Exception as e:
        return f"<h3>Error fetching call data: {e}</h3>"

@app.route("/")
def homepage():
    return render_template_string(HOMEPAGE_TEMPLATE)

HOMEPAGE_TEMPLATE = '''
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
        <a href="/twilio-calls">📞 View Twilio Call Logs</a>
    </div>
        <div class="card">
        <a href="/ultravox-calls">📞 View Ultravox Call Logs</a>
    </div>
   <div class="card">
        <a href="/call-recording">📞 Call Recording</a>
    </div>
    <div class="card">
        <a href="/zoho-leads">👥 View Zoho Leads</a>
    </div>

    <div class="card">
        <a href="/transcripts">📝 View last Transcript</a>
    </div>

    <div class="card">
        <a href="/convertjson">🔄 Convert JSON</a>
    </div>
</body>
</html>
'''

# Start server
if __name__ == '__main__':
  app.run(debug=True, host='0.0.0.0',port=9211)