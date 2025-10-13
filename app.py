# app.py - PRODUCTION VERSION 4.0 (Salesforce Auth Removed)
import io
import os
import re
import urllib3
from flask import Flask, request, jsonify
import requests
from pdfminer.high_level import extract_text

# Disable SSL warnings for Telicon (uses self-signed cert)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# -----------------------------
# Configuration
# -----------------------------
CURRENT_SESSION = os.environ.get('TX_LEGISLATURE_SESSION', '89R')
TELICON_BASE_URL = "https://www.telicon.com/www/TX"

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

def infer_fiscal_category(context_text: str, amount: float) -> str:
    """Infer fiscal category from surrounding text context."""
    context_lower = context_text.lower()
    
    # Check for staffing indicators
    staffing_keywords = ['fte', 'full-time equivalent', 'employees', 'staff', 'personnel', 'headcount']
    if any(keyword in context_lower for keyword in staffing_keywords):
        return 'Staffing'
    
    # Check for revenue indicators (positive amounts)
    revenue_keywords = ['revenue', 'income', 'receipts', 'collections', 'gain']
    if any(keyword in context_lower for keyword in revenue_keywords):
        return 'Revenue'
    
    # Check for savings
    savings_keywords = ['savings', 'reduction', 'decrease', 'efficiency']
    if any(keyword in context_lower for keyword in savings_keywords):
        return 'Savings'
    
    # Default to Expense for costs
    return 'Expense'

def infer_impact_type(context_text: str, year: str) -> str:
    """Infer if impact is one-time or recurring."""
    context_lower = context_text.lower()
    
    # One-time indicators
    onetime_keywords = ['one-time', 'initial', 'startup', 'implementation', 'first year only']
    if any(keyword in context_lower for keyword in onetime_keywords):
        return 'One-time'
    
    # Recurring indicators
    recurring_keywords = ['annual', 'yearly', 'ongoing', 'recurring', 'per year']
    if any(keyword in context_lower for keyword in recurring_keywords):
        return 'Recurring'
    
    # Default to Ongoing
    return 'Ongoing'

def extract_fiscal_impacts(fiscal_note_text: str) -> list:
    """
    Extract structured fiscal impact data from fiscal note text.
    Returns list of dicts with full Financial_Impact__c fields.
    """
    if not fiscal_note_text:
        return []
    
    impacts = []
    seen_years = set()
    
    # Pattern: Match fiscal years with dollar amounts and surrounding context
    # Captures 100 chars before and after for context
    pattern = r'.{0,100}(?:FY\s*)?(\d{4})[:\s]+\(?\$?([\d,]+(?:\.\d{2})?)\)?.{0,100}'
    
    matches = re.finditer(pattern, fiscal_note_text, re.IGNORECASE)
    
    for match in matches:
        year = match.group(1)
        
        # Skip if not a valid fiscal year (2020-2040 range)
        if not (2020 <= int(year) <= 2040):
            continue
            
        # Skip duplicate years
        if year in seen_years:
            continue
        
        amount_str = match.group(2).replace(',', '')
        context_text = match.group(0)
        
        try:
            amount = float(amount_str)
            
            # Check if it's in parentheses (negative/cost)
            if '(' in context_text and ')' in context_text:
                amount = -amount
            
            # Infer category and impact type from context
            category = infer_fiscal_category(context_text, amount)
            impact_type = infer_impact_type(context_text, year)
            
            # Clean up description text
            description = context_text.strip()
            description = re.sub(r'\s+', ' ', description)  # Normalize whitespace
            
            impacts.append({
                "fiscal_year": year,
                "amount": amount,
                "category": category,
                "impact_type": impact_type,
                "description": description[:500]  # Limit to 500 chars
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
        "version": "4.0.0",
        "endpoints": ["/health", "/session", "/analyzeBill"]
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
    """
    Analyze bill and return structured data for Salesforce Flow to consume.
    Flow will create all Salesforce records.
    """
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
        "fiscal_impacts": fiscal_impacts,  # Now includes category, impact_type, description
        "has_fiscal_note": bool(fiscal_text),
        "exists": True,
        "success": True
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print(f"[INFO] Starting Texas Bill Analyzer v4.0 on port {port}")
    print(f"[INFO] Current legislative session: {CURRENT_SESSION}")
    app.run(host="0.0.0.0", port=port)