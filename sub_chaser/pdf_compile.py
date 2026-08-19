#Merged invoice PDF download for Sub-Chaser 2000.
#
#Procore compiles a requisition's cover sheet + all its attachments into ONE
#pdf server-side (the same signed pay-app package the Procore_Sharepoint_Sync
#desktop tool pulls). The compile is asynchronous:
#   POST /rest/v1.0/requisitions/{id}/single_pdf_compilers  -> {"job_url": ...}
#   GET  job_url (poll)  -> {"status": ..., "result": {"url": ...}}
#   GET  result.url      -> merged pdf bytes (signed storage url, no auth)
#
#Auth is the standard session-token pattern (load_tokens + refresh on 401).
#PDF bytes are returned to the caller and never written to disk — we analyze
#and discard, the pdf lives in Procore.

import io
import time

import requests
from sub_chaser.procore_fetch import getAccessToken, refreshAccessToken, procoreGet

POLL_INTERVAL = 3      #seconds between job-status polls
MAX_POLLS = 60         #~3 min ceiling per compile
COMPILE_ATTEMPTS = 2   #one retry, compiler timeouts are usually transient

#procore merges in high-res page scans that dominate file size (a single invoice
#can be 24MB). downsampling to ~200 dpi jpeg shrinks ~90% with no meaningful
#readability loss, and keeps vision-api payloads sane.
COMPRESS_MAX_PX = 2200
COMPRESS_QUALITY = 80


def compileInvoicePdf(company_id, project_id, requisition, tokens=None):

    #kick off procore's pdf merge, poll to completion, return the merged pdf bytes.
    #retries once since observed compiler timeouts are usually transient.
    #`tokens` is the signed-in user's session tokens when called from a worker
    #thread (see sub_chaser/worker_tokens.py); None means read them from the session.

    last_err = None
    for attempt in range(1, COMPILE_ATTEMPTS + 1):
        try:
            return _compileOnce(company_id, project_id, requisition, tokens)
        except RuntimeError as e:
            last_err = e
            if attempt < COMPILE_ATTEMPTS:
                print(f"retry requisition {requisition.get('id')} (attempt {attempt} failed: {e})")
    raise last_err


def _compileOnce(company_id, project_id, requisition, tokens=None):

    files = [{"type": "cover_sheet", "id": ""}]
    for attachment in requisition.get('attachments') or []:
        files.append({"type": "prostore_file", "id": attachment.get('id'), "url": attachment.get('url')})

    url = f"https://api.procore.com/rest/v1.0/requisitions/{requisition['id']}/single_pdf_compilers"
    params = {"polling": "true", "project_id": project_id}

    def startCompile(access_token):
        return requests.post(url, headers={
            'Authorization': f'Bearer {access_token}',
            "Procore-Company-Id": str(company_id),
            "Accept": "application/json"
        }, params=params, json={"files": files}, timeout=60)

    response = startCompile(getAccessToken(tokens))
    print("Status:", response.status_code)

    if(response.status_code == 401):
        response = startCompile(refreshAccessToken(tokens))
        print("Status:", response.status_code)

    if(response.status_code >= 400):
        raise RuntimeError(f"PDF compile start failed ({response.status_code}): {response.text[:300]}")

    job_url = response.json().get('job_url')
    if not job_url:
        raise RuntimeError(f"No job_url for requisition {requisition['id']}: {response.text[:300]}")

    for _ in range(MAX_POLLS):
        status_response = procoreGet(job_url, company_id, tokens=tokens)
        status_doc = status_response.json() if status_response.status_code == 200 else {}
        result = status_doc.get('result') or {}
        if result.get('url'):
            return compressPdf(_downloadPdf(result['url']))
        if (status_doc.get('status') or "").lower() in ("failed", "error"):
            raise RuntimeError(f"PDF compile failed for requisition {requisition['id']}: {status_doc}")
        time.sleep(POLL_INTERVAL)

    raise RuntimeError(f"PDF compile timed out for requisition {requisition['id']}")


def _downloadPdf(url):

    #the merged pdf lives at a signed storage url, no auth header needed

    response = requests.get(url, timeout=120)
    if response.status_code != 200:
        raise RuntimeError(f"PDF download failed ({response.status_code}): {response.text[:200]}")
    return response.content


def compressPdf(blob, max_px=COMPRESS_MAX_PX, quality=COMPRESS_QUALITY):

    #shrink scanned-image pdfs by downsampling + jpeg-recompressing embedded images.
    #fully fail-safe: on any error, or if the result isn't smaller, returns the original bytes.

    try:
        import fitz  # PyMuPDF, already a dependency (see pdfHelpers.py)
        from PIL import Image

        doc = fitz.open(stream=blob, filetype="pdf")
        done = set()
        for page_number in range(doc.page_count):
            page = doc.load_page(page_number)
            for image_info in doc.get_page_images(page_number):
                xref = image_info[0]
                if xref in done:
                    continue
                done.add(xref)
                original = doc.extract_image(xref).get('image')
                if not original:
                    continue
                try:
                    img = Image.open(io.BytesIO(original))
                except Exception:
                    continue
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                w, h = img.size
                if max(w, h) > max_px:
                    scale = max_px / max(w, h)
                    img = img.resize((max(int(w * scale), 1), max(int(h * scale), 1)))
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=quality, optimize=True)
                if len(buffer.getvalue()) < len(original):  #only if it actually helps
                    page.replace_image(xref, stream=buffer.getvalue())
        out = io.BytesIO()
        doc.save(out, garbage=4, deflate=True, clean=True)
        doc.close()
        result = out.getvalue()
        return result if len(result) < len(blob) else blob
    except Exception as e:
        print(f"[compress] skipped (error: {e})")
        return blob
