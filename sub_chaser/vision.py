#AI vision analysis for Sub-Chaser 2000.
#
#One job: given the bytes of a merged invoice PDF, decide whether a completed,
#signed, notarized AIA and a signed Waiver & Release of Lien are present.
#
#The whole model is behind ONE function:
#
#    analyze(pdf_bytes) -> {"aia": ..., "lien": ..., "confidence": ..., "notes": ...}
#
#where aia/lien are "yes" | "no" | "uncertain". Swapping models means editing
#_callModel() only — nothing else in the app knows what's behind it.
#
#Claude takes PDFs natively (base64 document block), so there's no page->image
#conversion step. An OpenAI implementation would need one (render pages with
#PyMuPDF first); that difference stays hidden in here.

import base64
import json
import os
import re

#the prompt, proven in the Power Automate + AI Builder flow. it handles the key
#quirk: these packages usually lead with a BLANK AIA template, and naive prompts
#read that first blank copy and answer "no". DO NOT reword casually.
VISION_PROMPT = """Review the attached construction payment PDF. It may contain MULTIPLE documents and MULTIPLE copies of the same form — commonly a BLANK AIA template first, followed by one or more COMPLETED copies of the AIA, and then a Waiver and Release of Lien. Examine EVERY page before answering.

Your task is to determine whether a completed, signed version exists ANYWHERE in the document — not whether the first copy you encounter is complete.

For the AIA (Application for Payment): answer "yes" if AT LEAST ONE copy anywhere in the PDF has the payment fields filled in, a handwritten contractor's signature, and the notary acknowledgment section filled out and signed. IGNORE blank or template copies entirely — a blank AIA at the front does NOT make the answer "no" when a completed copy appears later. A notary stamp/seal may be faint or absent; if the notary section is signed and completed, treat it as notarized.

For the Waiver and Release of Lien: answer "yes" if a copy is filled in and signed (notarized where applicable) anywhere in the document.

A typed name alone is not a signature; look for a handwritten signature. If a scan is too illegible to judge, answer "uncertain."

In notes, state which page numbers the completed AIA and the lien waiver were found on.

Respond ONLY with JSON, no other text:
{"aia_signed_and_notarized":"yes|no|uncertain","lien_waiver_signed":"yes|no|uncertain","confidence":0.0,"notes":"found completed AIA on pp. X-Y, lien waiver on p. Z"}"""

MODEL = os.getenv("SUB_CHASER_VISION_MODEL", "claude-opus-5")

#thinking depth vs speed/cost. this is a well-scoped classification with a proven
#prompt, so medium is a good default — raise to "high" if verdicts look sloppy.
EFFORT = os.getenv("SUB_CHASER_VISION_EFFORT", "medium")

#room for thinking + the small json answer (max_tokens caps BOTH on opus 5)
MAX_TOKENS = 8000

#the api caps a request at 32MB, and base64 inflates bytes by ~33%. pdfs bigger
#than this can't be sent — they come back as "uncertain" rather than crashing
#the whole period's run.
MAX_PDF_BYTES = 22 * 1024 * 1024


def analyze(pdf_bytes):

    #the ONE function the rest of the app calls. never raises — a failure comes
    #back as an "uncertain" verdict with the reason in notes, so one bad invoice
    #can't kill a period's analysis.

    if not pdf_bytes:
        return _verdict("uncertain", "uncertain", 0.0, "no PDF content to analyze")

    if len(pdf_bytes) > MAX_PDF_BYTES:
        size_mb = round(len(pdf_bytes) / (1024 * 1024), 1)
        return _verdict("uncertain", "uncertain", 0.0,
                        f"PDF too large to analyze ({size_mb} MB) — review manually")

    try:
        raw = _callModel(pdf_bytes)
    except Exception as e:
        print(f"[vision] model call failed: {e}")
        return _verdict("uncertain", "uncertain", 0.0, f"analysis failed: {e}")

    return _parseVerdict(raw)


def _callModel(pdf_bytes):

    #the only model-specific code. returns the model's raw text response.

    from anthropic import Anthropic

    client = Anthropic()  #reads ANTHROPIC_API_KEY from the environment

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        output_config={"effort": EFFORT},
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.b64encode(pdf_bytes).decode("utf-8"),
                    },
                },
                {"type": "text", "text": VISION_PROMPT},
            ],
        }],
    )

    #safety classifiers can decline a request — that arrives as a normal 200 with
    #stop_reason "refusal" and empty content, so check before reading content
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined to analyze this document")

    return "".join(block.text for block in response.content if block.type == "text")


def _parseVerdict(raw):

    #parse defensively: the model is asked for bare JSON but may wrap it in
    #markdown fences or add a stray sentence.

    text = (raw or "").strip()

    #strip ```json ... ``` fences if present
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    #fall back to the outermost {...} if there's leading/trailing prose
    if not text.startswith("{"):
        braces = re.search(r"\{.*\}", text, re.DOTALL)
        if braces:
            text = braces.group(0)

    try:
        data = json.loads(text)
    except Exception:
        print(f"[vision] unparseable response: {raw[:300]}")
        return _verdict("uncertain", "uncertain", 0.0, "could not parse analysis response")

    return _verdict(
        _normalize(data.get("aia_signed_and_notarized")),
        _normalize(data.get("lien_waiver_signed")),
        data.get("confidence"),
        data.get("notes") or "",
    )


def _normalize(value):

    #anything that isn't a clean yes/no becomes "uncertain" — which the UI treats
    #as not-satisfied but shows distinctly from a confident "no"

    text = str(value or "").strip().lower()
    return text if text in ("yes", "no", "uncertain") else "uncertain"


def _verdict(aia, lien, confidence, notes):
    try:
        confidence = round(float(confidence), 2)
    except (TypeError, ValueError):
        confidence = 0.0

    return {"aia": aia, "lien": lien, "confidence": confidence, "notes": notes}
