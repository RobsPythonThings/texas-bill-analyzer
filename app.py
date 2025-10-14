# app.py - VERSION 8.0 - REDIS CACHING + BACKGROUND JOBS FOR HUGE BILLS
import io
import os
import re
import json
import urllib3
from flask import Flask, request, jsonify
import requests
from pdfminer.high_level import extract_text
from datetime import datetime
from rq import Queue
from rq.job import Job

# ADD REDIS IMPORT
import redis

# Disable SSL warnings for Telicon (uses self-signed cert)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# -----------------------------
# Configuration
# -----------------------------
CURRENT_SESSION = os.environ.get('TX_LEGISLATURE_SESSION', '89R')
TELICON_BASE_URL = "https://www.telicon.com/www/TX"

# Redis Configuration
redis_client = None
CACHE_ENABLED = False
try:
    redis_url = os.environ.get('REDIS_URL')
    if redis_url:
        # Handle TLS connection - Heroku Redis uses self-signed certs
        redis_client = redis.from_url(
            redis_url, 
            decode_responses=True,
            ssl_cert_reqs=None  # Disable SSL verification for Heroku Redis
        )
        redis_client.ping()  # Test connection
        CACHE_ENABLED = True
        print('[INFO] Redis cache enabled')
except Exception as e:
    print(f'[WARN] Redis not available: {e}')
    redis_client = None

# Job Queue for background processing
job_queue = None
if CACHE_ENABLED:
    try:
        job_queue = Queue('default', connection=redis_client)
        print('[INFO] Job queue enabled')
    except Exception as e:
        print(f'[WARN] Job queue not available: {e}')

# -----------------------------
# Cache Helper Functions
# -----------------------------
def get_cache_key(bill_number: str, session: str) -> str:
    """Generate consistent cache key for bill analysis."""
    return f"bill_analysis:{session}:{bill_number.upper().replace(' ', '')}"

def get_cached_analysis(bill_number: str, session: str) -> dict:
    """Retrieve cached analysis if available."""
    if not CACHE_ENABLED:
        return None
    
    try:
        key = get_cache_key(bill_number, session)
        cached = redis_client.get(key)
        if cached:
            result = json.loads(cached)
            print(f"[CACHE HIT] Returning cached analysis for {bill_number}")
            return result
    except Exception as e:
        print(f"[CACHE ERROR] Failed to retrieve: {e}")
    
    return None

def cache_analysis(bill_number: str, session: str, data: dict, ttl: int = 86400):
    """
    Store analysis in cache.
    
    Args:
        bill_number: Bill number (e.g., "HB 150")
        session: Legislative session (e.g., "89R")
        data: Analysis result dictionary
        ttl: Time to live in seconds (default: 24 hours)
    """
    if not CACHE_ENABLED:
        return
    
    try:
        key = get_cache_key(bill_number, session)
        redis_client.setex(key, ttl, json.dumps(data))
        
        # Track last successful analysis
        redis_client.set('last_success_timestamp', datetime.utcnow().isoformat())
        redis_client.set('last_success_bill', bill_number)
        
        print(f"[CACHE STORED] Cached analysis for {bill_number} (TTL: {ttl}s)")
    except Exception as e:
        print(f"[CACHE ERROR] Failed to store: {e}")

def invalidate_cache(bill_number: str, session: str):
    """Manually invalidate cache for a specific bill."""
    if not CACHE_ENABLED:
        return
    
    try:
        key = get_cache_key(bill_number, session)
        redis_client.delete(key)
        print(f"[CACHE INVALIDATED] {bill_number}")
    except Exception as e:
        print(f"[CACHE ERROR] Failed to invalidate: {e}")

def get_cache_stats() -> dict:
    """Get cache statistics."""
    if not CACHE_ENABLED:
        return {"enabled": False}
    
    try:
        info = redis_client.info('stats')
        return {
            "enabled": True,
            "connected": True,
            "keyspace_hits": info.get('keyspace_hits', 0),
            "keyspace_misses": info.get('keyspace_misses', 0),
            "last_success": redis_client.get('last_success_timestamp'),
            "last_bill": redis_client.get('last_success_bill')
        }
    except:
        return {"enabled": True, "connected": False}

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

def get_appropriate_text_limit(text: str) -> int:
    """Dynamically adjust text limit based on content size."""
    length = len(text)
    if length < 50000:
        return min(length, 10000)  # Small bills: full text
    elif length < 100000:
        return 8000  # Medium bills
    elif length < 150000:
        return 6000  # Large bills
    else:
        return 4000  # HUGE bills (HB 2) - aggressive truncation

def extract_fiscal_summary_with_ai(fiscal_note_text: str) -> dict:
    """Use Claude via Heroku Managed Inference to extract fiscal summary and total."""
    if not fiscal_note_text:
        return {"fiscal_note_summary": "", "total_fiscal_impact": 0}
    
    # Check if Heroku Managed Inference is configured
    inference_url = os.environ.get('INFERENCE_URL')
    inference_key = os.environ.get('INFERENCE_KEY')
    inference_model = os.environ.get('INFERENCE_MODEL_ID')
    
    if not all([inference_url, inference_key, inference_model]):
        print('[WARN] Heroku Managed Inference not configured, using fallback')
        return {
            "fiscal_note_summary": fiscal_note_text[:3000],
            "total_fiscal_impact": 0
        }
    
    try:
        # ENHANCED PROMPT - More structured and specific
        text_limit = get_appropriate_text_limit(fiscal_note_text)
        
        prompt = f"""Analyze this Texas legislative fiscal note and provide a comprehensive summary.

Return ONLY valid JSON (no markdown, no code blocks, no explanation):
{{
  "fiscal_note_summary": "Your summary here",
  "total_fiscal_impact": -1234567.89
}}

SUMMARY REQUIREMENTS (2-3 paragraphs):

Paragraph 1 - Overview:
- State the total net fiscal impact (positive for revenue/savings, negative for costs)
- Indicate whether impact is significant, moderate, or minimal
- Mention if methodology is dynamic or static scoring (if stated)

Paragraph 2 - Year-by-Year Breakdown:
- List specific amounts for each fiscal year (e.g., "FY2026: -$50.2M, FY2027: -$48.9M")
- Break down by fund type (General Revenue, Federal Funds, Special Funds, etc.)
- Distinguish between one-time and recurring costs

Paragraph 3 - Implementation Details:
- Staffing requirements: Number of FTEs and their annual costs
- Implementation timeline and milestones
- Any notable assumptions or contingencies
- Long-term sustainability considerations

TOTAL FISCAL IMPACT RULES:
- Sum ALL fiscal years mentioned in the note
- Use NEGATIVE numbers for costs/expenses (-1234567.89)
- Use POSITIVE numbers for revenue/savings (1234567.89)
- If no clear total, calculate from year-by-year data
- Include both one-time and recurring amounts

Be specific with dollar amounts and fiscal years. Use clear, professional language suitable for legislators.

Fiscal Note Text (first {text_limit} characters):
{fiscal_note_text[:text_limit]}"""
        
        # Direct API call using requests
        headers = {
            'Authorization': f'Bearer {inference_key}',
            'Content-Type': 'application/json'
        }
        
        payload = {
            'model': inference_model,
            'messages': [{'role': 'user', 'content': prompt}],
            'temperature': 0.1,
            'max_tokens': 2500  # Increased for detailed summaries
        }
        
        response = requests.post(
            f'{inference_url}/v1/chat/completions',
            headers=headers,
            json=payload,
            timeout=45  # Increased timeout for complex analyses
        )
        
        if response.status_code != 200:
            print(f'[ERROR] API call failed: {response.status_code} - {response.text}')
            return {
                "fiscal_note_summary": fiscal_note_text[:3000],
                "total_fiscal_impact": 0
            }
        
        response_data = response.json()
        response_text = response_data['choices'][0]['message']['content'].strip()
        
        # Remove markdown code blocks if present
        if response_text.startswith('```'):
            lines = response_text.split('\n')
            response_text = '\n'.join(lines[1:-1]) if len(lines) > 2 else response_text
            if response_text.startswith('json'):
                response_text = response_text[4:].strip()
        
        result = json.loads(response_text)
        
        print(f'[SUCCESS] Claude generated fiscal summary and total: ${result.get("total_fiscal_impact", 0):,.2f}')
        return result
        
    except json.JSONDecodeError as e:
        print(f'[ERROR] JSON parsing failed: {e}')
        print(f'[ERROR] Response text: {response_text[:500]}')
        return {
            "fiscal_note_summary": fiscal_note_text[:3000],
            "total_fiscal_impact": 0
        }
    except Exception as e:
        print(f'[ERROR] AI extraction failed: {e}')
        return {
            "fiscal_note_summary": fiscal_note_text[:3000],
            "total_fiscal_impact": 0
        }

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
    """Enhanced health check with cache status."""
    cache_stats = get_cache_stats()
    
    return jsonify({
        "ok": True,
        "service": "Texas Bill Analyzer",
        "version": "8.0.0",
        "endpoints": ["/health", "/session", "/analyzeBill", "/job/<job_id>", "/cache/stats", "/cache/invalidate"],
        "ai_enabled": bool(os.environ.get('INFERENCE_URL')),
        "cache": cache_stats,
        "job_queue_enabled": job_queue is not None,
        "heroku_slug": os.environ.get('HEROKU_SLUG_COMMIT', 'unknown')[:7]
    })

@app.route("/session", methods=["GET"])
def get_current_session():
    """Return current legislative session."""
    return jsonify({
        "session": CURRENT_SESSION,
        "session_year": "2025-2026" if CURRENT_SESSION == "89R" else "Unknown",
        "chamber": "Texas Legislature"
    })

@app.route("/cache/stats", methods=["GET"])
def cache_stats():
    """Get cache statistics."""
    return jsonify(get_cache_stats())

@app.route("/cache/invalidate", methods=["POST"])
def cache_invalidate():
    """Invalidate cache for a specific bill."""
    payload = request.get_json(silent=True) or {}
    bill_number = payload.get("bill_number")
    
    if not bill_number:
        return jsonify({"error": "bill_number is required"}), 400
    
    session = payload.get("session", CURRENT_SESSION)
    invalidate_cache(bill_number, session)
    
    return jsonify({
        "success": True,
        "message": f"Cache invalidated for {bill_number}"
    })

@app.route("/job/<job_id>", methods=["GET"])
def get_job_status(job_id):
    """Check status of background job."""
    if not CACHE_ENABLED or not job_queue:
        return jsonify({"error": "Jobs not available"}), 503
    
    try:
        job = Job.fetch(job_id, connection=redis_client)
        
        if job.is_finished:
            result = job.result
            # Cache the result
            if result and result.get('success'):
                cache_analysis(result['bill_number'], result['session'], result)
            return jsonify({
                "status": "completed",
                "result": result
            })
        elif job.is_failed:
            return jsonify({
                "status": "failed",
                "error": str(job.exc_info)
            })
        else:
            return jsonify({
                "status": "processing",
                "job_id": job_id
            })
    except Exception as e:
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 404

@app.route("/analyzeBill", methods=["POST"])
def analyze_bill():
    """
    Analyze bill - use background job for huge bills to avoid timeout.
    """
    payload = request.get_json(silent=True) or {}
    bill_number = payload.get("bill_number")
    force_refresh = payload.get("force_refresh", False)
    use_async = payload.get("use_async", False)
    
    print(f"[INFO] analyzeBill - Request for: {bill_number}")
    
    if not bill_number:
        return jsonify({
            "error": "bill_number is required",
            "error_code": "MISSING_BILL_NUMBER",
            "success": False
        }), 400
    
    bill_type, bill_num = parse_bill_number(bill_number)
    if not bill_type or not bill_num:
        return jsonify({
            "error": "Invalid bill format. Use format like 'HB 150' or 'SB 2'",
            "error_code": "INVALID_BILL_FORMAT",
            "success": False
        }), 400
    
    session = CURRENT_SESSION
    formatted_bill = f"{bill_type}{bill_num}"
    
    # Check cache first
    if not force_refresh:
        cached_result = get_cached_analysis(bill_number, session)
        if cached_result:
            cached_result['cache_hit'] = True
            return jsonify(cached_result)
    
    # Determine if this should be a background job
    # Known huge bills that timeout
    huge_bills = ['HB00002', 'SB00001', 'HB00001']
    should_async = use_async or (formatted_bill in huge_bills)
    
    if should_async and job_queue:
        # Queue background job
        from tasks import analyze_bill_task
        try:
            job = job_queue.enqueue(
                analyze_bill_task,
                bill_number,
                session,
                job_timeout='10m'  # 10 minute max
            )
            
            print(f"[ASYNC] Queued background job {job.id} for {formatted_bill}")
            
            return jsonify({
                "job_id": job.id,
                "status": "processing",
                "bill_number": formatted_bill,
                "check_url": f"/job/{job.id}",
                "message": "Large bill queued for background processing. Check status at /job/{job_id}",
                "success": True
            }), 202
        except Exception as e:
            print(f"[ERROR] Failed to queue job: {e}")
            # Fall through to synchronous processing
    
    # Synchronous processing for normal bills
    # Try to find bill
    bill_url, bill_pattern = try_bill_url_patterns(bill_type, bill_num, session)
    
    if not bill_url:
        error_response = {
            "bill_number": formatted_bill,
            "session": session,
            "exists": False,
            "success": False,
            "error": "Bill not found in Telicon system",
            "error_code": "BILL_NOT_FOUND"
        }
        return jsonify(error_response), 404
    
    # Fetch bill PDF
    try:
        print(f"[INFO] Fetching bill from: {bill_url}")
        bill_response = requests.get(bill_url, timeout=30, verify=False)
        if bill_response.status_code != 200:
            return jsonify({
                "error": f"Failed to fetch bill (HTTP {bill_response.status_code})",
                "error_code": "BILL_FETCH_FAILED",
                "success": False
            }), 500
    except requests.exceptions.Timeout:
        return jsonify({
            "error": "Bill fetch timed out",
            "error_code": "TIMEOUT",
            "success": False
        }), 504
    except Exception as e:
        return jsonify({
            "error": str(e),
            "error_code": "BILL_FETCH_ERROR",
            "success": False
        }), 500
    
    # Extract bill text
    bill_text = extract_text_from_pdf_bytes(bill_response.content)
    if not bill_text:
        return jsonify({
            "error": "Could not extract bill text from PDF",
            "error_code": "PDF_EXTRACTION_FAILED",
            "success": False
        }), 500
    
    print(f"[INFO] analyzeBill - Extracted {len(bill_text)} characters")
    
    # Check for fiscal note
    fiscal_relevant = should_fetch_fiscal_note(bill_text)
    fiscal_text = None
    fiscal_url = None
    fiscal_note_summary = ""
    total_fiscal_impact = 0
    
    if fiscal_relevant:
        fiscal_url, fiscal_pattern = try_fiscal_note_patterns(bill_type, bill_num, session)
        
        if fiscal_url:
            try:
                print(f"[INFO] Fetching fiscal note from: {fiscal_url}")
                fiscal_response = requests.get(fiscal_url, timeout=15, verify=False)
                if fiscal_response.status_code == 200:
                    fiscal_text = extract_text_from_pdf_bytes(fiscal_response.content)
                    if fiscal_text:
                        print(f"[INFO] analyzeBill - Fiscal note found: {len(fiscal_text)} characters")
                        # Get summary and total from Claude
                        fiscal_data = extract_fiscal_summary_with_ai(fiscal_text)
                        fiscal_note_summary = fiscal_data.get('fiscal_note_summary', '')
                        total_fiscal_impact = fiscal_data.get('total_fiscal_impact', 0)
            except Exception as e:
                print(f"[WARN] Fiscal note fetch failed: {e}")
    
    # Build result
    result = {
        "bill_number": formatted_bill,
        "bill_type": bill_type,
        "session": session,
        "bill_url": bill_url,
        "fiscal_note_url": fiscal_url,
        "bill_text": bill_text[:3000],  # First 3000 chars for Flow
        "fiscal_note_summary": fiscal_note_summary,
        "total_fiscal_impact": total_fiscal_impact,
        "has_fiscal_note": bool(fiscal_text),
        "exists": True,
        "success": True,
        "cache_hit": False,
        "timestamp": datetime.utcnow().isoformat()
    }
    
    # Cache the result
    print(f"[SUCCESS] Analysis complete for {formatted_bill}")
    
    print(f"[SUCCESS] Analysis complete for {formatted_bill}")
    return jsonify(result)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print(f"[INFO] Starting Texas Bill Analyzer v8.0 on port {port}")
    print(f"[INFO] Current legislative session: {CURRENT_SESSION}")
    print(f"[INFO] AI extraction: {'Enabled' if os.environ.get('INFERENCE_URL') else 'Disabled'}")
    print(f"[INFO] Redis caching: {'Enabled' if CACHE_ENABLED else 'Disabled'}")
    print(f"[INFO] Job queue: {'Enabled' if job_queue else 'Disabled'}")
    app.run(host="0.0.0.0", port=port)