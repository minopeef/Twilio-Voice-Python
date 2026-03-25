import requests
import json
import os
from datetime import datetime, timezone

# Configuration
CLIENT_ID = "YOUR_ZOHO_CLIENT_ID"
CLIENT_SECRET = "YOUR_ZOHO_CLIENT_SECRET"
REDIRECT_URI = "http://localhost:8000/callback"
AUTH_CODE = "YOUR_ZOHO_AUTH_CODE"
TOKEN_FILE = "zoho_tokens.json"
LOG_FILE = "lead_update_log.txt"

# Get Access and Refresh Tokens
def get_tokens():
    token_url = "https://accounts.zoho.com.au/oauth/v2/token"
    payload = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "code": AUTH_CODE
    }
    response = requests.post(token_url, data=payload)
    tokens = response.json()
    print(":closed_lock_with_key: Token Response:", tokens)
    if "access_token" in tokens:
        with open(TOKEN_FILE, "w") as f:
            json.dump(tokens, f, indent=4)
        return tokens
    else:
        return None
print("Token Got!")

# Refresh Token
def refresh_access_token(refresh_token):
    url = "https://accounts.zoho.com.au/oauth/v2/token"
    payload = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token
    }
    response = requests.post(url, data=payload)

    # First, check response content
    if response.status_code != 200:
        print(f"❌ Refresh token request failed with status code {response.status_code}")
        print("Raw Response Text:", response.text)
        return None

    try:
        tokens = response.json()
        print("🔄 Refreshed Token:", tokens)
        if "access_token" in tokens:
            tokens["refresh_token"] = refresh_token
            with open(TOKEN_FILE, "w") as f:
                json.dump(tokens, f, indent=4)
            return tokens
        else:
            print("❌ 'access_token' not found in token response")
            return None
    except json.JSONDecodeError:
        print("❌ Failed to parse token refresh response as JSON.")
        print("Raw Response Text:", response.text)
        return None

# Creating  a New Lead
def create_lead(access_token):
    url = "https://www.zohoapis.com.au/crm/v7/Leads"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }
    try:
        with open('test.json', 'r') as file:
            data = json.load(file)
            lead_data = data['lead_data']
    except FileNotFoundError:
        print("Error: test.json file not found")
        return None
    except json.JSONDecodeError:
        print("Error: Invalid JSON format in test.json")
        return None
    except KeyError:
        print("Error: lead_data not found in test.json")
        return None

    response = requests.post(url, headers=headers, json=lead_data)
    result = response.json()
    print("Status Code:", response.status_code)
    print("Response Text:", response.text)

    if response.status_code == 201:
        print("✅ Lead Created Successfully")
        try:
            return result["data"][0]["details"]["id"]
        except KeyError:
            print("Warning: Could not extract lead ID from response.")
            return None
    else:
        print("❌ Lead Creation Failed")
        try:
            print("Zoho Error:", json.dumps(result, indent=2))
        except json.JSONDecodeError:
            print("Failed to parse response as JSON.")
        return None

# Log Call (with Fixed Call_Start_Time Format)
def log_call(access_token, lead_id):
    url = "https://www.zohoapis.com.au/crm/v7/Calls"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }
    try:
        with open('test.json', 'r') as file:
            data = json.load(file)
            call_data = data['call_data']
            call_data['data'][0]['What_Id'] = lead_id
            #call_data['data'][0]['Call_Result'] = call_result
    except FileNotFoundError:
        print("Error: test.json file not found")
        return
    except json.JSONDecodeError:
        print("Error: Invalid JSON format in test.json")
        return
    except KeyError:
        print("Error: call_data not found in test.json")
        return
    response = requests.post(url, headers=headers, json=call_data)
    result = response.json()
    if response.status_code == 201:
        print("Call Logged Successfully")
    else:
        print("Call Logging Failed:", result)

def call():
    try:
        with open('test.json', 'r') as file:
            data = json.load(file)
            call_data = data['call_data']
            call_result = call_data['data'][0]['Call_Result']
            return call_result
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as e:
        print(f"Error reading call result from JSON: {str(e)}")
        return None

def meetings(access_token, lead_id):
    url="https://www.zohoapis.com.au/crm/v7/Events"
    headers={
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }
    try:
        with open('test.json', 'r') as file:
            data = json.load(file)
            meeting_data = data['meeting_data']
            meeting_data['data'][0]['What_Id'] = lead_id
    except FileNotFoundError:
        print("Error: test.json file not found")
        return
    except json.JSONDecodeError:
        print("Error: Invalid JSON format in test.json")
        return
    except KeyError:
        print("Error: meeting_data not found in test.json")
        return
    response = requests.post(url, headers=headers, json=meeting_data)
    if response.status_code == 201:
        print("Meeting created successfully!")
    else:
        print(f"Failed to create meeting. Status code: {response.status_code}")
        try:
            print("Response JSON:", response.json())
        except json.JSONDecodeError:
            print("Could not parse response as JSON")

# Send Emails Based on Call Outcome
def send_email(call_result, access_token, lead_id):
    if call_result == "Interested":
        print("Sending Product Description Email...")
    elif call_result == "demo":
        meetings(access_token, lead_id)
        print("Sending Appointment Email...")
        print(call_result)
    else:
        print("Sending Thank a You Email...")

# Main Script
def main():
    if not os.path.exists(TOKEN_FILE):
        tokens = get_tokens()
    else:
        with open(TOKEN_FILE, "r") as f:
            tokens = json.load(f)
        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")
        
        # Check if the access token is still valid
        test = requests.get("https://www.zohoapis.com.au/crm/v7/Leads", headers={
            "Authorization": f"Zoho-oauthtoken {access_token}"})
        
        if test.status_code == 401:
            print("🔄 Access token expired, refreshing...")
            tokens = refresh_access_token(refresh_token)

    if not tokens:
        print("❌ Unable to get access token. Exiting.")
        return

    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    # Workflow
    lead_id = create_lead(access_token)
    if lead_id:
        call_result = call()
        if call_result:
            log_call(access_token, lead_id)
            send_email(call_result, access_token, lead_id)
        else:
            print("❌ Failed to get call result from JSON")

if __name__ == "__main__":
    main()