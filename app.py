# app.py
from flask import Flask, request, jsonify
import fitz  # PyMuPDF
import os
import tempfile

app = Flask(__name__)

# --------- helpers ---------
def extract_text_from_pdf(path: str) -> str:
    """Extract plain text from a PDF file."""
    doc = fitz.open(path)
    chunks = []
    for page in doc:
        chunks.append(page.get_text("text"))
    return "\n".join(chunks)

def demo_summarize(text: str, filename: str) -> dict:
    """
    Demo summary (stub) to keep the flow simple for the SE demo.
    Swap this with an LLM call later if you want.
    """
    snippet = (text or "").strip().replace("\r", " ").replace("\n", " ")
    snippet = snippet[:1200]  # keep response small
    summary = (
        f"Summary for '{filename}'. "
        "This is a demo extractor returning the first portion of text. "
        "Key sections likely include purpose, definitions, funding/appropriations, "
        "effective dates, and penalties. Snippet:\n"
        f"{snippet}"
    )
    return {"filename": filename, "summary": summary}

# --------- routes ----------
@app.get("/health")
def health():
    return jsonify({"ok": True})

@app.post("/summarizeFile")
def summarize_file():
    """
    Accepts multipart/form-data with a PDF file field named 'file'.
    Returns JSON: { filename, summary }
    """
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f = request.files["file"]
    if not f.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files are supported"}), 400

    # Save to a temp file in the dyno
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        f.save(tmp.name)
        tmp_path = tmp.name

    try:
        text = extract_text_from_pdf(tmp_path)
        result = demo_summarize(text, f.filename)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

@app.get("/openapi.json")
def openapi():
    """
    Minimal OpenAPI schema so Salesforce External Services can register this API.
    """
    return {
        "openapi": "3.0.0",
        "info": {"title": "Texas Bill Summarizer API", "version": "1.0.0"},
        "servers": [{"url": request.url_root.rstrip("/")}],
        "paths": {
            "/summarizeFile": {
                "post": {
                    "summary": "Upload and summarize a Texas bill PDF",
                    "operationId": "summarizeFile",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "file": {"type": "string", "format": "binary"}
                                    },
                                    "required": ["file"]
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Summary response",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "filename": {"type": "string"},
                                            "summary": {"type": "string"}
                                        },
                                        "required": ["filename", "summary"]
                                    }
                                }
                            }
                        },
                        "400": {"description": "Bad Request"},
                        "500": {"description": "Server Error"}
                    }
                }
            },
            "/health": {
                "get": {
                    "summary": "Health check",
                    "operationId": "health",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"ok": {"type": "boolean"}},
                                        "required": ["ok"]
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
