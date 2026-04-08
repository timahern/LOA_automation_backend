import os
import json
from dotenv import load_dotenv
from openai import OpenAI

import base64
#from io import BytesIO
#from PIL import Image
#import fitz


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Adjust this if .env is in repo root (one level up from gpt_api_interaction)
load_dotenv(os.path.join(BASE_DIR, "..", ".env"))

client = OpenAI()




def strip_base64_prefix(b64: str) -> str:
    return b64.split(",", 1)[1] if "," in b64 else b64

def extract_exhibit_b1_date(exhibit_b1_page1_b64: str) -> str | None:
    image_b64 = strip_base64_prefix(exhibit_b1_page1_b64)

    response = client.chat.completions.create(
        model="gpt-4o",
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "Extract structured fields from construction documents."},
            {"role": "user", "content": [
                {"type": "text", "text": (
                    "Look ONLY at the TOP RIGHT corner. Extract the DATE. "
                    "Return JSON: {\"date\": \"YYYY-MM-DD\" | null}. No other keys."
                )},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}}
            ]}
        ]
    )

    content = response.choices[0].message.content
    print("RAW:", content)  # remove later
    return json.loads(content).get("date")



def extract_buyout_data(buyout_page1_b64: str) -> dict:
    image_b64 = strip_base64_prefix(buyout_page1_b64)

    response = client.chat.completions.create(
        model="gpt-4o",
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You extract structured data from construction buyout summary pages. "
                    "Return strict JSON only."
                )
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "This image is page 1 of a construction BUYOUT summary.\n\n"
                            "TASK:\n"
                            "Extract the values associated with these fields (use the value shown on the page):\n"
                            "- Trade\n"
                            "- Cost Code\n"
                            "- Vendor Selected\n"
                            "- Subcontract Amount\n\n"
                            "RULES:\n"
                            "- Match the label on the page to the correct value next to it (same row/line).\n"
                            "- If a field is not present, return null.\n"
                            "- For Subcontract Amount: return both the raw string as shown AND a numeric value.\n"
                            "  - numeric should be a number (no $ or commas). Example: \"$1,234.50\" -> 1234.50\n"
                            "- Do not guess.\n"
                            "- Output JSON ONLY.\n\n"
                            "OUTPUT SCHEMA:\n"
                            "{\n"
                            '  "trade": string | null,\n'
                            '  "cost_code": string | null,\n'
                            '  "vendor_selected": string | null,\n'
                            '  "subcontract_amount_raw": string | null,\n'
                            '  "subcontract_amount": number | null\n'
                            "}"
                        )
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_b64}"}
                    }
                ]
            }
        ]
    )

    content = response.choices[0].message.content
    print("RAW:", content)  # remove later

    result = json.loads(content)

    # Optional: enforce keys exist
    return {
        "trade": result.get("trade"),
        "cost_code": result.get("cost_code"),
        "vendor_selected": result.get("vendor_selected"),
        "subcontract_amount_raw": result.get("subcontract_amount_raw"),
        "subcontract_amount": result.get("subcontract_amount"),
    }

'''

def pdf_page_to_png_base64(pdf_path: str, page_number: int = 0, zoom: float = 3.0) -> str:
    """
    Render a PDF page to PNG and return raw base64 (no data: prefix).
    zoom=3.0 is a good default for text legibility.
    """
    doc = fitz.open(pdf_path)
    page = doc.load_page(page_number)

    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)

    png_bytes = pix.tobytes("png")
    return base64.b64encode(png_bytes).decode("utf-8")


def extract_exhibit_b1_date_from_pdf(pdf_path: str) -> str | None:
    image_b64 = pdf_page_to_png_base64(pdf_path, page_number=0, zoom=3.0)
    # Use data URL format because the model expects it
    return extract_exhibit_b1_date(f"data:image/png;base64,{image_b64}")

def extract_buyout_data_from_pdf(pdf_path: str) -> str | None:
    image_b64 = pdf_page_to_png_base64(pdf_path, page_number=0, zoom=3.0)
    # Use data URL format because the model expects it
    return extract_buyout_data(f"data:image/png;base64,{image_b64}")



exhibit_dir = os.path.join(BASE_DIR, "exhibit pages")
exhibit_b1_pdf = os.path.join(exhibit_dir, "B.1 page 1.pdf")
exhibit_buyout_pdf = os.path.join(exhibit_dir, "Buyout page 1.pdf")

info = extract_buyout_data_from_pdf(exhibit_buyout_pdf)
print("Extracted date:", info)

'''
