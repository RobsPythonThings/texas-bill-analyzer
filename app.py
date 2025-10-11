# app.py - VERSION 3.0 WITH SALESFORCE INTEGRATION
import io
import os
import re
import urllib3
from flask import Flask, request, jsonify
import requests
from pdfminer.high_level import extract_text
import json

# Disable SSL warnings for Telicon (uses self-signed cert)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# -----------------------------
# Configuration (Environment Variables)
# -----------------------------
CURRENT_SESSION = os.environ.get('TX_LEGISLATURE_SESSION', '89R')
TELICON_BASE_URL = "https://www.telicon.com/www/TX"

# Salesforce credentials (set via Heroku config vars tomorrow)
SF_INSTANCE_URL = os.environ.get('SF_INSTANCE_URL', 'https://storm-e5f313f236a2a7.lightning.force.com')
SF_CLIENT_ID = os.environ.get('SF_CLIENT_ID', '')
SF_CLIENT_SECRET = os.environ.get('SF_CLIENT_SECRET', '')
SF_USERNAME = os.environ.get('SF_USERNAME', '')
SF_PASSWORD = os.environ.get('SF_PASSWORD', '')
SF_SECURITY_TOKEN = os.environ.get('SF_SECURITY_TOKEN', '')

# Salesforce API version
SF_API_VERSION = 'v65.0'

# -----------------------------
# Salesforce Helper Functions
# -----------------------------
def get_salesforce_access_token():
    """Authenticate with Salesforce using Client Credentials flow."""
    if not all([SF_CLIENT_ID, SF_CLIENT_SECRET]):
        print('[WARN] Salesforce credentials not configured')
        return None
    
    auth_url = f"{SF_INSTANCE_URL}/services/oauth2/token"
    
    payload = {
        'grant_type': 'client_credentials',
        'client_id': SF_CLIENT_ID,
        'client_secret': SF_CLIENT_SECRET
    }
    
    try:
        response = requests.post(auth_url, data=payload, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            print('[SUCCESS] Salesforce authentication successful')
            return result['access_token']
        else:
            print(f'[ERROR] Salesforce auth failed: {response.status_code} - {response.text}')
            return None
            
    except Exception as e:
        print(f'[ERROR] Salesforce auth exception: {e}')
        return None

def create_salesforce_record(object_name, data, access_token):
    """Create a record in Salesforce."""
    url = f"{SF_INSTANCE_URL}/services/data/{SF_API_VERSION}/sobjects/{object_name}"
    
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }
    
    try:
        response = requests.post(url, json=data, headers=headers, timeout=10)
        
        if response.status_code == 201:
            result = response.json()
            print(f'[SUCCESS] Created {object_name}: {result["id"]}')
            return result['id']
        else:
            print(f'[ERROR] Failed to create {object_name}: {response.status_code} - {response.text}')
            return None
            
    except Exception as e:
        print(f'[ERROR] Exception creating {object_name}: {e}')
        return None

def query_salesforce(soql, access_token):
    """Query Salesforce using SOQL."""
    url = f"{SF_INSTANCE_URL}/services/data/{SF_API_VERSION}/query"
    
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }
    
    params = {'q': soql}
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            return result.get('records', [])
        else:
            print(f'[ERROR] Query failed: {response.status_code} - {response.text}')
            return []
            
    except Exception as e:
        print(f'[ERROR] Query exception: {e}')
        return []

# -----------------------------
# Helper Functions
# -----------------------------
def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """Extract plain text from PDF bytes. Returns empty string on failure."""
    try:
        with io.BytesIO(pdf_bytes) as fh:
            txt = extract_text(fh) or ""
            # Normalize whitespace
            txt = re.sub(r"[ \t]+", " ", txt)
            txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
            return txt
    except Exception as e:
        print(f"[ERROR] PDF extraction failed: {e}")
        return ""

def extract_fiscal_impacts(fiscal_note_text: str) -> list:
    """
    Extract structured fiscal impact data from fiscal note text.
    Returns list of dicts: [{"year": "2026", "amount": -88715399}, ...]
    """
    if not fiscal_note_text:
        return []
    
    impacts = []
    seen_years = set()
    
    # Pattern: Match fiscal years with dollar amounts
    pattern = r'(?:FY\s*)?(\d{4})[:\s]+\(?\$?([\d,]+(?:\.\d{2})?)\)?'
    
    matches = re.finditer(pattern, fiscal_note_text)
    
    for match in matches:
        year = match.group(1)
        
        # Skip if not a valid fiscal year (2020-2040 range)
        if not (2020 <= int(year) <= 2040):
            continue
            
        # Skip duplicate years
        if year in seen_years:
            continue
        
        amount_str = match.group(2).replace(',', '')
        
        try:
            amount = float(amount_str)
            
            # Check if it's in parentheses (negative)
            match_text = fiscal_note_text[match.start():match.end()]
            if '(' in match_text:
                amount = -amount
            
            impacts.append({
                "year": year,
                "amount": amount
            })
            
            seen_years.add(year)
            
        except ValueError:
            continue
    
    print(f"[INFO] Extracted {len(impacts)} fiscal impacts from fiscal note")
    return impacts

def parse_bill_number(bill_number: str) -> tuple:
    """Parse bill number into (bill_type, bill_num)."""
    match = re.match(r"([HS][BRJ])\s*(\d+)", bill_number.upper().strip())
    if not match:
        return None, None
    
    bill_type = match.group(1)
    bill_num = match.group(2).zfill(5)
    return bill_type, bill_num

def try_bill_url_patterns(bill_type: str, bill_num: str, session: str) -> tuple:
    """Try multiple URL patterns until one works."""
    patterns = [
        {
            "url": f"{TELICON_BASE_URL}/{session}/pdf/TX{session}{bill_type}{bill_num}FIL.pdf",
            "type": "primary"
        },
        {
            "url": f"{TELICON_BASE_URL}/{session}/pdf/{bill_type}{bill_num}FIL.pdf",
            "type": "fallback_no_session_in_name"
        },
        {
            "url": f"{TELICON_BASE_URL}/{session}/bills/TX{session}{bill_type}{bill_num}.pdf",
            "type": "fallback_bills_dir"
        },
        {
            "url": f"{TELICON_BASE_URL}/bills/{session}/{bill_type}{bill_num}.pdf",
            "type": "fallback_flat"
        }
    ]
    
    for pattern in patterns:
        try:
            response = requests.head(pattern["url"], timeout=5, verify=False)
            if response.status_code == 200:
                print(f"[SUCCESS] Found bill using {pattern['type']}: {pattern['url']}")
                return pattern["url"], pattern["type"]
        except:
            continue
    
    return None, None

def try_fiscal_note_patterns(bill_type: str, bill_num: str, session: str) -> tuple:
    """Try multiple fiscal note URL patterns."""
    patterns = [
        {
            "url": f"{TELICON_BASE_URL}/{session}/fnote/TX{session}{bill_type}{bill_num}FIL.pdf",
            "type": "primary"
        },
        {
            "url": f"{TELICON_BASE_URL}/{session}/fnote/{bill_type}{bill_num}FIL.pdf",
            "type": "fallback_no_session_in_name"
        },
        {
            "url": f"{TELICON_BASE_URL}/{session}/fiscal/{bill_type}{bill_num}.pdf",
            "type": "fallback_fiscal_dir"
        }
    ]
    
    for pattern in patterns:
        try:
            response = requests.head(pattern["url"], timeout=5, verify=False)
            if response.status_code == 200:
                print(f"[SUCCESS] Found fiscal note using {pattern['type']}: {pattern['url']}")
                return pattern["url"], pattern["type"]
        except:
            continue
    
    return None, None

def should_fetch_fiscal_note(bill_text: str) -> bool:
    """Determine if fiscal note is relevant based on bill content."""
    fiscal_keywords = [
        "appropriation", "funding", "budget", "fiscal impact",
        "cost", "revenue", "expenditure", "million", "billion",
        "grant", "allocation", "financial"
    ]
    
    bill_text_lower = bill_text.lower()
    return any(keyword in bill_text_lower for keyword in fiscal_keywords)

# -----------------------------
# API Routes
# -----------------------------
@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({
        "ok": True,
        "service": "Texas Bill Analyzer",
        "version": "3.0.0",
        "salesforce_configured": bool(SF_CLIENT_ID and SF_CLIENT_SECRET)
    })

@app.route("/session", methods=["GET"])
def get_current_session():
    """Return current legislative session."""
    return jsonify({
        "session": CURRENT_SESSION,
        "session_year": "2025-2026" if CURRENT_SESSION == "89R" else "Unknown",
        "chamber": "Texas Legislature"
    })

@app.route("/analyzeBill", methods=["POST"])
def analyze_bill():
    """Analyze bill and return data (does NOT create Salesforce records)."""
    payload = request.get_json(silent=True) or {}
    bill_number = payload.get("bill_number")
    
    print(f"[INFO] analyzeBill - Request for: {bill_number}")
    
    if not bill_number:
        return jsonify({"error": "bill_number is required"}), 400
    
    bill_type, bill_num = parse_bill_number(bill_number)
    if not bill_type or not bill_num:
        return jsonify({"error": "Invalid bill format"}), 400
    
    session = CURRENT_SESSION
    
    # Try to find bill
    bill_url, bill_pattern = try_bill_url_patterns(bill_type, bill_num, session)
    
    if not bill_url:
        return jsonify({
            "bill_number": f"{bill_type}{bill_num}",
            "session": session,
            "exists": False,
            "success": False
        }), 404
    
    # Fetch bill PDF
    try:
        bill_response = requests.get(bill_url, timeout=25, verify=False)
        if bill_response.status_code != 200:
            return jsonify({"error": f"Failed to fetch bill"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
    # Extract bill text
    bill_text = extract_text_from_pdf_bytes(bill_response.content)
    if not bill_text:
        return jsonify({"error": "Could not extract bill text"}), 500
    
    print(f"[INFO] analyzeBill - Extracted {len(bill_text)} characters")
    
    # Check for fiscal note
    fiscal_relevant = should_fetch_fiscal_note(bill_text)
    fiscal_text = None
    fiscal_url = None
    fiscal_impacts = []
    
    if fiscal_relevant:
        fiscal_url, fiscal_pattern = try_fiscal_note_patterns(bill_type, bill_num, session)
        
        if fiscal_url:
            try:
                fiscal_response = requests.get(fiscal_url, timeout=10, verify=False)
                if fiscal_response.status_code == 200:
                    fiscal_text = extract_text_from_pdf_bytes(fiscal_response.content)
                    if fiscal_text:
                        print(f"[INFO] analyzeBill - Fiscal note found: {len(fiscal_text)} characters")
                        fiscal_impacts = extract_fiscal_impacts(fiscal_text)
            except Exception as e:
                print(f"[WARN] Fiscal note fetch failed: {e}")
    
    return jsonify({
        "bill_number": f"{bill_type}{bill_num}",
        "bill_type": bill_type,
        "session": session,
        "bill_url": bill_url,
        "fiscal_note_url": fiscal_url,
        "bill_text": bill_text[:3000],
        "fiscal_note_text": fiscal_text[:3000] if fiscal_text else None,
        "fiscal_impacts": fiscal_impacts,
        "has_fiscal_note": bool(fiscal_text),
        "exists": True,
        "success": True
    })

@app.route("/saveBillAnalysis", methods=["POST"])
def save_bill_analysis():
    """
    NEW ENDPOINT: Analyze bill AND create all Salesforce records.
    This is the all-in-one endpoint that does everything.
    """
    payload = request.get_json(silent=True) or {}
    bill_number = payload.get("bill_number")
    analysis_summary = payload.get("analysis_summary", "")
    
    print(f"[INFO] saveBillAnalysis - Request for: {bill_number}")
    
    if not bill_number:
        return jsonify({"error": "bill_number is required"}), 400
    
    # Get Salesforce access token
    access_token = get_salesforce_access_token()
    
    if not access_token:
        return jsonify({
            "error": "Salesforce authentication failed. Please configure SF credentials.",
            "success": False
        }), 500
    
    # Parse bill number
    bill_type, bill_num = parse_bill_number(bill_number)
    if not bill_type or not bill_num:
        return jsonify({"error": "Invalid bill format"}), 400
    
    session = CURRENT_SESSION
    formatted_bill_number = f"{bill_type}{bill_num}"
    
    # Fetch bill and fiscal data using analyzeBill logic
    bill_url, _ = try_bill_url_patterns(bill_type, bill_num, session)
    
    if not bill_url:
        return jsonify({"error": "Bill not found", "success": False}), 404
    
    try:
        bill_response = requests.get(bill_url, timeout=25, verify=False)
        bill_text = extract_text_from_pdf_bytes(bill_response.content)
    except:
        return jsonify({"error": "Could not fetch bill", "success": False}), 500
    
    # Get fiscal data
    fiscal_impacts = []
    fiscal_relevant = should_fetch_fiscal_note(bill_text)
    
    if fiscal_relevant:
        fiscal_url, _ = try_fiscal_note_patterns(bill_type, bill_num, session)
        if fiscal_url:
            try:
                fiscal_response = requests.get(fiscal_url, timeout=10, verify=False)
                fiscal_text = extract_text_from_pdf_bytes(fiscal_response.content)
                if fiscal_text:
                    fiscal_impacts = extract_fiscal_impacts(fiscal_text)
            except:
                pass
    
    # STEP 1: Find or create Legislation record
    soql = f"SELECT Id FROM Legislation__c WHERE Bill_Number__c = '{formatted_bill_number}' AND Session__c = '{session}' LIMIT 1"
    existing_legislation = query_salesforce(soql, access_token)
    
    if existing_legislation:
        legislation_id = existing_legislation[0]['Id']
        print(f'[INFO] Found existing Legislation: {legislation_id}')
    else:
        legislation_data = {
            'Bill_Number__c': formatted_bill_number,
            'Session__c': session,
            'Name': f"{formatted_bill_number} - {session}"
        }
        legislation_id = create_salesforce_record('Legislation__c', legislation_data, access_token)
        
        if not legislation_id:
            return jsonify({"error": "Failed to create Legislation record", "success": False}), 500
    
    # STEP 2: Check if Bill Analysis already exists
    soql = f"SELECT Id FROM Bill_Analysis__c WHERE Legislation__c = '{legislation_id}' LIMIT 1"
    existing_analysis = query_salesforce(soql, access_token)
    
    if existing_analysis:
        return jsonify({
            "message": f"Analysis already exists for {formatted_bill_number}",
            "success": False,
            "duplicate": True
        }), 200
    
    # STEP 3: Create Bill Analysis record
    analysis_data = {
        'Legislation__c': legislation_id,
        'Analysis_Summary__c': analysis_summary[:131000] if analysis_summary else "Analysis pending",
        'Analysis_Date__c': None  # Let Salesforce set the datetime
    }
    
    bill_analysis_id = create_salesforce_record('Bill_Analysis__c', analysis_data, access_token)
    
    if not bill_analysis_id:
        return jsonify({"error": "Failed to create Bill Analysis record", "success": False}), 500
    
    # STEP 4: Create Financial Impact records
    created_impacts = 0
    
    for impact in fiscal_impacts:
        impact_data = {
            'Bill_Analysis__c': bill_analysis_id,
            'Fiscal_Year__c': impact['year'],
            'Amount__c': impact['amount'],
            'Impact_Type__c': 'Recurring',
            'Description__c': f"Fiscal year {impact['year']} impact: ${impact['amount']:,.2f}"
        }
        
        impact_id = create_salesforce_record('Financial_Impact__c', impact_data, access_token)
        if impact_id:
            created_impacts += 1
    
    print(f'[SUCCESS] Created {created_impacts} Financial Impact records')
    
    # Return success
    return jsonify({
        "success": True,
        "message": f"✅ Bill analysis saved for {formatted_bill_number} with {created_impacts} fiscal impact records",
        "legislation_id": legislation_id,
        "bill_analysis_id": bill_analysis_id,
        "fiscal_impacts_created": created_impacts
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print(f"[INFO] Starting Texas Bill Analyzer v3.0 on port {port}")
    print(f"[INFO] Current legislative session: {CURRENT_SESSION}")
    print(f"[INFO] Salesforce integration: {'✅ Configured' if SF_CLIENT_ID else '❌ Not configured'}")
    app.run(host="0.0.0.0", port=port)