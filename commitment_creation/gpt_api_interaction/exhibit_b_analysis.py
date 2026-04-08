import os
import json
from dotenv import load_dotenv
from openai import OpenAI

import base64
#from io import BytesIO
#from PIL import Image
import fitz


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Adjust this if .env is in repo root (one level up from gpt_api_interaction)
load_dotenv(os.path.join(BASE_DIR, "..", ".env"))

client = OpenAI()




def strip_base64_prefix(b64: str) -> str:
    return b64.split(",", 1)[1] if "," in b64 else b64

def extract_exhibit_b_date(exhibit_b_page1_b64: str) -> str | None:
    image_b64 = strip_base64_prefix(exhibit_b_page1_b64)

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


def extract_exhibit_b_date_from_pdf(pdf_path: str) -> str | None:
    image_b64 = pdf_page_to_png_base64(pdf_path, page_number=0, zoom=3.0)
    # Use data URL format because the model expects it
    return extract_exhibit_b_date(f"data:image/png;base64,{image_b64}")



exhibit_dir = os.path.join(BASE_DIR, "exhibit pages")
exhibit_b_pdf = os.path.join(exhibit_dir, "Exhibit B pg1.pdf")

date = extract_exhibit_b_date_from_pdf(exhibit_b_pdf)
print("Extracted date:", date)


'''
