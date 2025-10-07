# app.py
import io
import re
import urllib3
from flask import Flask, request, jsonify
import requests
from pdfminer.high_level import extract_text

# Disable SSL warnings for demo purposes
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# -----------------------------
# Helpers
# -----------------------------
def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """Return plain text from a PDF byte string. Empty string on failure."""
    try:
        with io.BytesIO(pdf_bytes) as fh:
            txt = extract_text(fh) or ""
            # Normalize whitespace a bit so we can display clean excerpts
            txt = re.sub(r"[ \t]+", " ", txt)
            txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
            return txt
    except Exception:
        return ""

def summarize_text_locally(text: str, max_chars: int = 1200) -> str:
    """
    Super-light 'summary' for demo purposes:
    - If the doc is long, grab the intro + first section headings/snippets.
    - Keep it short and deterministic so your demo is stable.
    """
    if not text:
        return "No extractable text was found in the PDF."

    # crude "section start" heuristics to capture bill intro + first items
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    head = []
    for ln in lines[:80]:  # first ~80 logical lines
        head.append(ln)
        # stop early if we hit a section marker
        if re.search(r"^SECTION\s+\d+\.|^Sec\.\s*\d", ln, re.IGNORECASE):
            break

    excerpt = " ".join(head)
    if len(excerpt) < 300:
        excerpt = " ".join(lines[:200])  # fall back to a bit more context

    excerpt = excerpt[:max_chars].rstrip()
    return (
        "Summary (rule-based, demo):\n"
        "- Captures the bill header and early sections for context.\n"
        "- Use this as a stand-in for Document/ADL summarization.\n\n"
        f"{excerpt}"
    )

def build_openapi_json(base_url: str) -> dict:
    """
    Minimal OpenAPI 3.0 describing all endpoints for External Services.
    """
    return {
        "openapi": "3.0.0",
        "info": {"title": "Texas Bill Summarizer API", "version": "1.2.0"},
        "servers": [{"url": base_url.rstrip("/")}],
        "paths": {
            "/health": {
                "get": {
                    "operationId": "health",
                    "summary": "Health check",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["ok"],
                                        "properties": {"ok": {"type": "boolean"}},
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/summarizeFile": {
                "post": {
                    "operationId": "summarizeFile",
                    "summary": "Upload and summarize a Texas bill PDF",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "required": ["file"],
                                    "properties": {
                                        "file": {"type": "string", "format": "binary"}
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Summary response",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["filename", "summary"],
                                        "properties": {
                                            "filename": {"type": "string"},
                                            "summary": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        },
                        "400": {"description": "Bad Request"},
                        "500": {"description": "Server Error"},
                    },
                }
            },
            "/summarizeByUrl": {
                "post": {
                    "operationId": "summarizeByUrl",
                    "summary": "Summarize a Texas bill PDF by URL",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["pdf_url"],
                                    "properties": {"pdf_url": {"type": "string"}},
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Summary response",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["url", "summary"],
                                        "properties": {
                                            "url": {"type": "string"},
                                            "summary": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        },
                        "400": {"description": "Bad Request"},
                        "500": {"description": "Server Error"},
                    },
                }
            },
            "/getFiscalNote": {
                "post": {
                    "operationId": "getFiscalNote",
                    "summary": "Fetch and extract text from a Texas fiscal note PDF by URL",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["fiscal_note_url"],
                                    "properties": {
                                        "fiscal_note_url": {"type": "string"}
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Fiscal note content",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["url", "text", "exists"],
                                        "properties": {
                                            "url": {"type": "string"},
                                            "text": {"type": "string"},
                                            "exists": {"type": "boolean"},
                                        },
                                    }
                                }
                            },
                        },
                        "400": {"description": "Bad Request"},
                        "404": {
                            "description": "Fiscal note not found",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["url", "exists", "message"],
                                        "properties": {
                                            "url": {"type": "string"},
                                            "exists": {"type": "boolean"},
                                            "message": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        },
                        "500": {"description": "Server Error"},
                    },
                }
            },
            "/getFiscalNoteByBill": {
                "post": {
                    "operationId": "getFiscalNoteByBill",
                    "summary": "Fetch fiscal note by bill number (e.g., 'HB 103')",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["bill_number"],
                                    "properties": {
                                        "bill_number": {"type": "string"}
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Fiscal note content",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["bill_number", "url", "text", "exists"],
                                        "properties": {
                                            "bill_number": {"type": "string"},
                                            "url": {"type": "string"},
                                            "text": {"type": "string"},
                                            "exists": {"type": "boolean"},
                                        },
                                    }
                                }
                            },
                        },
                        "400": {"description": "Bad Request"},
                        "404": {
                            "description": "Fiscal note not found",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["bill_number", "url", "exists", "message"],
                                        "properties": {
                                            "bill_number": {"type": "string"},
                                            "url": {"type": "string"},
                                            "exists": {"type": "boolean"},
                                            "message": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        },
                        "500": {"description": "Server Error"},
                    },
                }
            },
        },
    }

# -----------------------------
# Routes
# -----------------------------
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})

@app.route("/summarizeFile", methods=["POST"])
def summarize_file():
    if "file" not in request.files:
        return jsonify({"error": "file is required (multipart/form-data)"}), 400

    f = request.files["file"]
    data = f.read()
    if not data:
        return jsonify({"error": "empty file"}), 400

    text = extract_text_from_pdf_bytes(data)
    if not text:
        return jsonify({"error": "no text extracted"}), 500

    summary = summarize_text_locally(text)
    return jsonify({"filename": f.filename or "uploaded.pdf", "summary": summary})

@app.route("/summarizeByUrl", methods=["POST"])
def summarize_by_url():
    payload = request.get_json(silent=True) or {}
    pdf_url = payload.get("pdf_url")
    
    # DEBUG LOGGING
    print(f"[DEBUG summarizeByUrl] Received payload: {payload}")
    print(f"[DEBUG summarizeByUrl] Extracted pdf_url: {pdf_url}")
    
    if not pdf_url:
        print("[ERROR summarizeByUrl] pdf_url is missing from request")
        return jsonify({"error": "pdf_url is required"}), 400

    print(f"[DEBUG summarizeByUrl] Fetching PDF from: {pdf_url}")
    
    try:
        r = requests.get(pdf_url, timeout=25, verify=False)
        print(f"[DEBUG summarizeByUrl] Response status: {r.status_code}, Content length: {len(r.content) if r.content else 0}")
    except Exception as e:
        print(f"[ERROR summarizeByUrl] Fetch failed with exception: {e}")
        return jsonify({"error": f"fetch failed: {e}"}), 400

    if r.status_code != 200 or not r.content:
        print(f"[ERROR summarizeByUrl] Bad response - Status: {r.status_code}")
        return jsonify({"error": f"fetch failed: {r.status_code}"}), 400

    text = extract_text_from_pdf_bytes(r.content)
    if not text:
        print("[ERROR summarizeByUrl] Failed to extract text from PDF")
        return jsonify({"error": "no text extracted"}), 500

    print(f"[DEBUG summarizeByUrl] Successfully extracted {len(text)} characters")
    summary = summarize_text_locally(text)
    print(f"[DEBUG summarizeByUrl] Generated summary of {len(summary)} characters")
    
    return jsonify({"url": pdf_url, "summary": summary})

@app.route("/getFiscalNote", methods=["POST"])
def get_fiscal_note():
    """
    Fetch a Texas fiscal note PDF and return its full text.
    Returns 404 with exists=false if the fiscal note doesn't exist yet.
    """
    payload = request.get_json(silent=True) or {}
    fiscal_note_url = payload.get("fiscal_note_url")
    
    # DEBUG LOGGING
    print(f"[DEBUG getFiscalNote] Received payload: {payload}")
    print(f"[DEBUG getFiscalNote] Extracted fiscal_note_url: {fiscal_note_url}")
    
    if not fiscal_note_url:
        print("[ERROR getFiscalNote] fiscal_note_url is missing from request")
        return jsonify({"error": "fiscal_note_url is required"}), 400

    print(f"[DEBUG getFiscalNote] Fetching fiscal note from: {fiscal_note_url}")
    
    try:
        r = requests.get(fiscal_note_url, timeout=25, verify=False)
        print(f"[DEBUG getFiscalNote] Response status: {r.status_code}")
    except Exception as e:
        print(f"[ERROR getFiscalNote] Fetch failed with exception: {e}")
        return jsonify({"error": f"fetch failed: {e}"}), 400

    # Handle 404 gracefully - fiscal note may not exist yet
    if r.status_code == 404:
        print(f"[INFO getFiscalNote] Fiscal note not found (404) - may not be published yet")
        return jsonify({
            "url": fiscal_note_url,
            "exists": False,
            "message": "Fiscal note not yet available for this bill"
        }), 404

    # Other non-200 status codes are errors
    if r.status_code != 200 or not r.content:
        print(f"[ERROR getFiscalNote] Bad response - Status: {r.status_code}")
        return jsonify({"error": f"fetch failed: {r.status_code}"}), 400

    # Extract full text from the fiscal note PDF
    text = extract_text_from_pdf_bytes(r.content)
    if not text:
        print("[ERROR getFiscalNote] Failed to extract text from fiscal note PDF")
        return jsonify({"error": "no text extracted from fiscal note"}), 500

    print(f"[DEBUG getFiscalNote] Successfully extracted {len(text)} characters from fiscal note")
    return jsonify({
        "url": fiscal_note_url,
        "text": text,
        "exists": True
    })

@app.route("/getFiscalNoteByBill", methods=["POST"])
def get_fiscal_note_by_bill():
    """
    Fetch fiscal note by bill number (e.g., "HB 103", "SB 45")
    Constructs the Telicon URL automatically.
    """
    payload = request.get_json(silent=True) or {}
    bill_number = payload.get("bill_number")
    
    # DEBUG LOGGING
    print(f"[DEBUG getFiscalNoteByBill] Received payload: {payload}")
    print(f"[DEBUG getFiscalNoteByBill] Extracted bill_number: {bill_number}")
    
    if not bill_number:
        print("[ERROR getFiscalNoteByBill] bill_number is missing from request")
        return jsonify({"error": "bill_number is required (e.g., 'HB 103')"}), 400
    
    # Parse bill number (e.g., "HB 103" or "HB103")
    match = re.match(r"([HS][BRJ])\s*(\d+)", bill_number.upper().strip())
    if not match:
        print(f"[ERROR getFiscalNoteByBill] Invalid bill format: {bill_number}")
        return jsonify({"error": "Invalid bill format. Use 'HB 103' or 'SB 45'"}), 400
    
    bill_type = match.group(1)
    bill_num = match.group(2).zfill(5)  # Zero-pad to 5 digits
    
    # Construct fiscal note URL
    fiscal_note_url = f"https://www.telicon.com/www/TX/89R/fnote/TX89R{bill_type}{bill_num}FIL.pdf"
    
    print(f"[DEBUG getFiscalNoteByBill] Constructed URL: {fiscal_note_url}")
    print(f"[DEBUG getFiscalNoteByBill] Bill type: {bill_type}, Bill number (padded): {bill_num}")
    
    # Now use the same logic as getFiscalNote
    try:
        r = requests.get(fiscal_note_url, timeout=25, verify=False)
        print(f"[DEBUG getFiscalNoteByBill] Response status: {r.status_code}")
    except Exception as e:
        print(f"[ERROR getFiscalNoteByBill] Fetch failed with exception: {e}")
        return jsonify({"error": f"fetch failed: {e}"}), 400

    if r.status_code == 404:
        print(f"[INFO getFiscalNoteByBill] Fiscal note not found (404) for {bill_number}")
        return jsonify({
            "bill_number": bill_number,
            "url": fiscal_note_url,
            "exists": False,
            "message": "Fiscal note not yet available for this bill"
        }), 404

    if r.status_code != 200 or not r.content:
        print(f"[ERROR getFiscalNoteByBill] Bad response - Status: {r.status_code}")
        return jsonify({"error": f"fetch failed: {r.status_code}"}), 400

    text = extract_text_from_pdf_bytes(r.content)
    if not text:
        print("[ERROR getFiscalNoteByBill] Failed to extract text from fiscal note PDF")
        return jsonify({"error": "no text extracted from fiscal note"}), 500

    print(f"[DEBUG getFiscalNoteByBill] Successfully extracted {len(text)} characters from fiscal note for {bill_number}")
    return jsonify({
        "bill_number": bill_number,
        "url": fiscal_note_url,
        "text": text,
        "exists": True
    })

@app.route("/openapi.json", methods=["GET"])
def openapi_json():
    # Use the incoming host to build an accurate server URL for External Services
    base = request.host_url.rstrip("/")
    # Force https in Heroku context for External Services sanity
    if base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    return jsonify(build_openapi_json(base))

# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    # Heroku sets PORT; default for local runs
    import os
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)