from flask import Flask, request, jsonify
import requests
import tempfile
import fitz  # PyMuPDF

app = Flask(__name__)

def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """Take raw PDF bytes and return extracted text."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(pdf_bytes)
        tmp.flush()
        path = tmp.name
    doc = fitz.open(path)
    text = "\n".join(page.get_text("text") for page in doc)
    return text

@app.get("/health")
def health():
    return jsonify({"ok": True})

@app.post("/summarizeByUrl")
def summarize_by_url():
    """
    Demo endpoint. Takes JSON: { "pdf_url": "https://..." }
    Fetches PDF, extracts some text, and returns a canned summary.
    """
    data = request.get_json(silent=True) or {}
    pdf_url = data.get("pdf_url")
    if not pdf_url:
        return jsonify({"error": "Provide 'pdf_url' in JSON body."}), 400

    try:
        # Fetch PDF from URL
        r = requests.get(pdf_url, timeout=60)
        if r.status_code != 200:
            return jsonify({"error": f"fetch failed: {r.status_code}"}), 400

        # Extract text (first 500 chars just to prove it works)
        full_text = extract_text_from_pdf_bytes(r.content)
        snippet = full_text[:500].replace("\n", " ")

        # Stub summary (replace with OpenAI/Gemini call later if desired)
        summary = (
            f"Demo summary for {pdf_url}. "
            "Key points extracted from the PDF: "
            f"{snippet[:200]}..."
        )

        return jsonify({
            "summary": summary,
            "source_url": pdf_url
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
