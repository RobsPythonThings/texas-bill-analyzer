# app.py
from flask import Flask, request, jsonify
import os, requests, tempfile
import fitz  # PyMuPDF
from openai import OpenAI

app = Flask(__name__)

# set this after deploy: heroku config:set OPENAI_API_KEY=xxxxx
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
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
    JSON body: { "pdf_url": "https://..." }
    Returns: { "summary": "...", "source_url": "..." }
    """
    data = request.get_json(silent=True) or {}
    pdf_url = data.get("pdf_url")
    if not pdf_url:
        return jsonify({"error": "Provide 'pdf_url' in JSON body."}), 400

    r = requests.get(pdf_url, timeout=60)
    if r.status_code != 200:
        return jsonify({"error": f"fetch failed: {r.status_code}"}), 400

    full_text = extract_text_from_pdf_bytes(r.content)[:200000]  # safety cap

    prompt = (
        "You are a legislative analyst. Summarize this Texas bill.\n"
        "Return:\n- 4–8 concise bullet points\n- funding/appropriations\n"
        "- effective dates\n- cite sections if obvious."
    )

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.2,
        max_tokens=700,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": full_text},
        ],
    )

    summary = resp.choices[0].message.content.strip()
    return jsonify({"summary": summary, "source_url": pdf_url})
