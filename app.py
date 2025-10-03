from flask import Flask, request, jsonify
import tempfile
import fitz  # PyMuPDF

app = Flask(__name__)

def extract_text_from_pdf(file_path: str) -> str:
    """Extract all text from a PDF."""
    doc = fitz.open(file_path)
    text = "\n".join(page.get_text("text") for page in doc)
    return text

@app.get("/health")
def health():
    return jsonify({"ok": True})

@app.post("/summarizeFile")
def summarize_file():
    """
    Accepts multipart/form-data with a single file field named 'file'.
    Returns a summary (stubbed for demo).
    """
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f = request.files["file"]
    tmp_path = f"/tmp/{f.filename}"
    f.save(tmp_path)

    try:
        # Extract raw text (truncate just to avoid overloading response)
        full_text = extract_text_from_pdf(tmp_path)[:2000]

        # Stub summary (replace with LLM call later if desired)
        summary = (
            f"Demo summary for uploaded file '{f.filename}'. "
            "First text snippet extracted:\n"
            f"{full_text[:300]}..."
        )

        return jsonify({
            "summary": summary,
            "filename": f.filename
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
