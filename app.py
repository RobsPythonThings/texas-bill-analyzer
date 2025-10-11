# app.py - RESILIENT VERSION
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
# Configuration (Environment Variables)
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

def parse_bill_number(bill_number: str) -> tuple:
    """
    Parse bill number into (bill_type, bill_num).
    Examples: "HB 150" -> ("HB", "00150"), "SB45" -> ("SB", "00045")
    """
    match = re.match(r"([HS][BRJ])\s*(\d+)", bill_number.upper().strip())
    if not match:
        return None, None
    
    bill_type = match.group(1)
    bill_num = match.group(2).zfill(5)  # Zero-pad to 5 digits
    return bill_type, bill_num

def try_bill_url_patterns(bill_type: str, bill_num: str, session: str) -> tuple:
    """
    Try multiple URL patterns until one works.
    Returns: (url, pattern_type) or (None, None)
    """
    patterns = [
        # Current primary pattern
        {
            "url": f"{TELICON_BASE_URL}/{session}/pdf/TX{session}{bill_type}{bill_num}FIL.pdf",
            "type": "primary"
        },
        # Fallback: Without session in filename
        {
            "url": f"{TELICON_BASE_URL}/{session}/pdf/{bill_type}{bill_num}FIL.pdf",
            "type": "fallback_no_session_in_name"
        },
        # Fallback: Different directory structure
        {
            "url": f"{TELICON_BASE_URL}/{session}/bills/TX{session}{bill_type}{bill_num}.pdf",
            "type": "fallback_bills_dir"
        },
        # Fallback: Flat structure
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
        except Exception as e:
            print(f"[RETRY] Pattern {pattern['type']} failed: {e}")
            continue
    
    return None, None

def try_fiscal_note_patterns(bill_type: str, bill_num: str, session: str) -> tuple:
    """
    Try multiple fiscal note URL patterns.
    Returns: (url, pattern_type) or (None, None)
    """
    patterns = [
        # Current primary pattern
        {
            "url": f"{TELICON_BASE_URL}/{session}/fnote/TX{session}{bill_type}{bill_num}FIL.pdf",
            "type": "primary"
        },
        # Fallback patterns
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

def build_openapi_json(base_url: str) -> dict:
    """OpenAPI 3.0 specification for External Services."""
    return {
        "openapi": "3.0.0",
        "info": {
            "title": "Texas Bill Analyzer API",
            "version": "2.0.0",
            "description": "API for analyzing Texas legislative bills and fiscal notes"
        },
        "servers": [{"url": base_url.rstrip("/")}],
        "paths": {
            "/health": {
                "get": {
                    "operationId": "health",
                    "summary": "Health check endpoint",
                    "responses": {
                        "200": {
                            "description": "Service is healthy",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "ok": {"type": "boolean"},
                                            "service": {"type": "string"},
                                            "version": {"type": "string"}
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/session": {
                "get": {
                    "operationId": "getCurrentSession",
                    "summary": "Get current legislative session",
                    "responses": {
                        "200": {
                            "description": "Current session info",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "session": {"type": "string"},
                                            "session_year": {"type": "string"},
                                            "chamber": {"type": "string"}
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/analyzeBill": {
                "post": {
                    "operationId": "analyzeBill",
                    "summary": "Analyze Texas bill and auto-fetch fiscal note if relevant",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["bill_number"],
                                    "properties": {
                                        "bill_number": {
                                            "type": "string",
                                            "description": "Bill number (e.g., 'HB 150', 'SB45')"
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Bill analysis successful",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["bill_number", "exists", "success"],
                                        "properties": {
                                            "bill_number": {"type": "string"},
                                            "bill_type": {"type": "string"},
                                            "session": {"type": "string"},
                                            "bill_url": {"type": "string"},
                                            "fiscal_note_url": {"type": "string"},
                                            "bill_text": {"type": "string"},
                                            "fiscal_note_text": {"type": "string"},
                                            "has_fiscal_note": {"type": "boolean"},
                                            "fiscal_was_relevant": {"type": "boolean"},
                                            "exists": {"type": "boolean"},
                                            "success": {"type": "boolean"},
                                            "url_pattern_used": {"type": "string"}
                                        }
                                    }
                                }
                            }
                        },
                        "400": {"description": "Invalid request"},
                        "404": {"description": "Bill not found"},
                        "500": {"description": "Server error"}
                    }
                }
            },
            "/getBillByNumber": {
                "post": {
                    "operationId": "getBillByNumber",
                    "summary": "Fetch bill text by bill number",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["bill_number"],
                                    "properties": {
                                        "bill_number": {"type": "string"}
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Bill text retrieved",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "bill_number": {"type": "string"},
                                            "bill_text": {"type": "string"},
                                            "bill_url": {"type": "string"},
                                            "exists": {"type": "boolean"}
                                        }
                                    }
                                }
                            }
                        },
                        "404": {"description": "Bill not found"}
                    }
                }
            },
            "/getFiscalNoteByBill": {
                "post": {
                    "operationId": "getFiscalNoteByBill",
                    "summary": "Fetch fiscal note by bill number",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["bill_number"],
                                    "properties": {
                                        "bill_number": {"type": "string"}
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Fiscal note retrieved",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "bill_number": {"type": "string"},
                                            "fiscal_note_text": {"type": "string"},
                                            "fiscal_note_url": {"type": "string"},
                                            "exists": {"type": "boolean"}
                                        }
                                    }
                                }
                            }
                        },
                        "404": {"description": "Fiscal note not found"}
                    }
                }
            }
        }
    }

# -----------------------------
# API Routes
# -----------------------------
@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({
        "ok": True,
        "service": "Texas Bill Analyzer",
        "version": "2.0.0"
    })

@app.route("/session", methods=["GET"])
def get_current_session():
    """Return current legislative session being tracked."""
    return jsonify({
        "session": CURRENT_SESSION,
        "session_year": "2025-2026" if CURRENT_SESSION == "89R" else "Unknown",
        "chamber": "Texas Legislature"
    })

@app.route("/analyzeBill", methods=["POST"])
def analyze_bill():
    """
    Smart endpoint that analyzes a bill and automatically fetches fiscal note if relevant.
    This is the PRIMARY endpoint for Salesforce to use.
    """
    payload = request.get_json(silent=True) or {}
    bill_number = payload.get("bill_number")
    
    print(f"[INFO] analyzeBill - Request for: {bill_number}")
    
    if not bill_number:
        return jsonify({"error": "bill_number is required (e.g., 'HB 150')"}), 400
    
    # Parse bill number
    bill_type, bill_num = parse_bill_number(bill_number)
    if not bill_type or not bill_num:
        return jsonify({
            "error": "Invalid bill format. Use 'HB 150' or 'SB 45'"
        }), 400
    
    session = CURRENT_SESSION
    
    # Try multiple URL patterns for bill
    bill_url, bill_pattern = try_bill_url_patterns(bill_type, bill_num, session)
    
    if not bill_url:
        print(f"[ERROR] analyzeBill - Bill not found: {bill_type}{bill_num}")
        return jsonify({
            "bill_number": f"{bill_type}{bill_num}",
            "session": session,
            "exists": False,
            "message": f"Bill {bill_type}{bill_num} not found for session {session}",
            "success": False
        }), 404
    
    # Fetch bill PDF
    try:
        bill_response = requests.get(bill_url, timeout=25, verify=False)
        
        if bill_response.status_code != 200:
            return jsonify({
                "error": f"Failed to fetch bill (HTTP {bill_response.status_code})"
            }), 500
            
    except Exception as e:
        print(f"[ERROR] analyzeBill - Fetch failed: {e}")
        return jsonify({"error": f"Network error: {str(e)}"}), 500
    
    # Extract bill text
    bill_text = extract_text_from_pdf_bytes(bill_response.content)
    if not bill_text:
        return jsonify({"error": "Could not extract text from bill PDF"}), 500
    
    print(f"[INFO] analyzeBill - Extracted {len(bill_text)} characters from bill")
    
    # Decide if fiscal note is relevant
    fiscal_relevant = should_fetch_fiscal_note(bill_text)
    print(f"[INFO] analyzeBill - Fiscal note relevant: {fiscal_relevant}")
    
    # Fetch fiscal note if relevant
    fiscal_text = None
    fiscal_exists = False
    fiscal_url = None
    fiscal_pattern = None
    
    if fiscal_relevant:
        fiscal_url, fiscal_pattern = try_fiscal_note_patterns(bill_type, bill_num, session)
        
        if fiscal_url:
            try:
                fiscal_response = requests.get(fiscal_url, timeout=10, verify=False)
                
                if fiscal_response.status_code == 200:
                    fiscal_text = extract_text_from_pdf_bytes(fiscal_response.content)
                    fiscal_exists = bool(fiscal_text)
                    if fiscal_exists:
                        print(f"[INFO] analyzeBill - Fiscal note found: {len(fiscal_text)} characters")
                    
            except Exception as e:
                print(f"[WARN] analyzeBill - Fiscal note fetch failed: {e}")
    
    # Return structured response
    return jsonify({
        "bill_number": f"{bill_type}{bill_num}",
        "bill_type": bill_type,
        "session": session,
        "bill_url": bill_url,
        "fiscal_note_url": fiscal_url or f"{TELICON_BASE_URL}/{session}/fnote/TX{session}{bill_type}{bill_num}FIL.pdf",
        "bill_text": bill_text[:3000],  # First 3000 chars for AI summarization
        "fiscal_note_text": fiscal_text[:3000] if fiscal_text else None,
        "has_fiscal_note": fiscal_exists,
        "fiscal_was_relevant": fiscal_relevant,
        "exists": True,
        "success": True,
        "url_pattern_used": bill_pattern  # For monitoring
    })

@app.route("/getBillByNumber", methods=["POST"])
def get_bill_by_number():
    """Simple endpoint to fetch just the bill text."""
    payload = request.get_json(silent=True) or {}
    bill_number = payload.get("bill_number")
    
    print(f"[INFO] getBillByNumber - Request for: {bill_number}")
    
    if not bill_number:
        return jsonify({"error": "bill_number is required"}), 400
    
    bill_type, bill_num = parse_bill_number(bill_number)
    if not bill_type or not bill_num:
        return jsonify({"error": "Invalid bill format"}), 400
    
    bill_url, pattern = try_bill_url_patterns(bill_type, bill_num, CURRENT_SESSION)
    
    if not bill_url:
        return jsonify({
            "bill_number": f"{bill_type}{bill_num}",
            "exists": False,
            "message": "Bill not found"
        }), 404
    
    try:
        response = requests.get(bill_url, timeout=25, verify=False)
        
        if response.status_code != 200:
            return jsonify({"error": f"HTTP {response.status_code}"}), 500
        
        bill_text = extract_text_from_pdf_bytes(response.content)
        if not bill_text:
            return jsonify({"error": "Could not extract bill text"}), 500
        
        print(f"[INFO] getBillByNumber - Success: {len(bill_text)} characters")
        
        return jsonify({
            "bill_number": f"{bill_type}{bill_num}",
            "bill_text": bill_text[:3000],
            "bill_url": bill_url,
            "exists": True
        })
        
    except Exception as e:
        print(f"[ERROR] getBillByNumber - {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/getFiscalNoteByBill", methods=["POST"])
def get_fiscal_note_by_bill():
    """Fetch fiscal note for a specific bill."""
    payload = request.get_json(silent=True) or {}
    bill_number = payload.get("bill_number")
    
    print(f"[INFO] getFiscalNoteByBill - Request for: {bill_number}")
    
    if not bill_number:
        return jsonify({"error": "bill_number is required"}), 400
    
    bill_type, bill_num = parse_bill_number(bill_number)
    if not bill_type or not bill_num:
        return jsonify({"error": "Invalid bill format"}), 400
    
    fiscal_url, pattern = try_fiscal_note_patterns(bill_type, bill_num, CURRENT_SESSION)
    
    if not fiscal_url:
        return jsonify({
            "bill_number": f"{bill_type}{bill_num}",
            "fiscal_note_url": f"{TELICON_BASE_URL}/{CURRENT_SESSION}/fnote/TX{CURRENT_SESSION}{bill_type}{bill_num}FIL.pdf",
            "exists": False,
            "message": "Fiscal note not yet available for this bill"
        }), 404
    
    try:
        response = requests.get(fiscal_url, timeout=10, verify=False)
        
        if response.status_code != 200:
            return jsonify({"error": f"HTTP {response.status_code}"}), 500
        
        fiscal_text = extract_text_from_pdf_bytes(response.content)
        if not fiscal_text:
            return jsonify({"error": "Could not extract fiscal note text"}), 500
        
        print(f"[INFO] getFiscalNoteByBill - Success: {len(fiscal_text)} characters")
        
        return jsonify({
            "bill_number": f"{bill_type}{bill_num}",
            "fiscal_note_text": fiscal_text[:3000],
            "fiscal_note_url": fiscal_url,
            "exists": True
        })
        
    except Exception as e:
        print(f"[ERROR] getFiscalNoteByBill - {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/openapi.json", methods=["GET"])
def openapi_json():
    """OpenAPI specification for Salesforce External Services."""
    base = request.host_url.rstrip("/")
    
    # Force HTTPS for Heroku
    if base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    
    return jsonify(build_openapi_json(base))

# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print(f"[INFO] Starting Texas Bill Analyzer on port {port}")
    print(f"[INFO] Current legislative session: {CURRENT_SESSION}")
    app.run(host="0.0.0.0", port=port)