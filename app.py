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
    Minimal OpenAPI 3.0 describing both endpoints for External Services.
    """
    return {
        "openapi": "3.0.0",
        "info": {"title": "Texas Bill Summarizer API", "version": "1.1.0"},
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
    if not pdf_url:
        return jsonify({"error": "pdf_url is required"}), 400

    try:
        r = requests.get(pdf_url, timeout=25, verify=False)
    except Exception as e:
        return jsonify({"error": f"fetch failed: {e}"}), 400

    if r.status_code != 200 or not r.content:
        return jsonify({"error": f"fetch failed: {r.status_code}"}), 400

    text = extract_text_from_pdf_bytes(r.content)
    if not text:
        return jsonify({"error": "no text extracted"}), 500

    summary = summarize_text_locally(text)
    return jsonify({"url": pdf_url, "summary": summary})

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