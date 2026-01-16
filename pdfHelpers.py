from pypdf import PdfReader, PdfWriter
from io import BytesIO
from typing import List
from werkzeug.datastructures import FileStorage
import fitz  # PyMuPDF
import base64

def merge_uploaded_pdfs(files: List[FileStorage]) -> BytesIO:
    writer = PdfWriter()

    for f in files:
        if not f:
            continue
        f.stream.seek(0)
        reader = PdfReader(f.stream)
        for page in reader.pages:
            writer.add_page(page)

    output = BytesIO()
    writer.write(output)
    output.seek(0)
    return output

def _seek0(obj):
    if obj is None:
        return
    if hasattr(obj, "stream"):
        obj.stream.seek(0)
    else:
        obj.seek(0)

def get_pdf_page_count(obj):
    if obj is None:
        return 0

    if hasattr(obj, "stream"):
        obj.stream.seek(0)
        reader = PdfReader(obj.stream)
        count = len(reader.pages)
        obj.stream.seek(0)
        return count
    else:
        obj.seek(0)
        reader = PdfReader(obj)
        count = len(reader.pages)
        obj.seek(0)
        return count


def pdf_first_page_to_data_url(obj, fmt="png", max_width=1400):
    # Get a file-like object
    f = obj.stream if hasattr(obj, "stream") else obj

    f.seek(0)
    pdf_bytes = f.read()
    f.seek(0)

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(0)

    rect = page.rect
    scale = max_width / rect.width
    mat = fitz.Matrix(scale, scale)

    pix = page.get_pixmap(matrix=mat, alpha=False)

    if fmt.lower() in ("jpg", "jpeg"):
        img_bytes = pix.tobytes("jpeg")
        mime = "image/jpeg"
    else:
        img_bytes = pix.tobytes("png")
        mime = "image/png"

    b64 = base64.b64encode(img_bytes).decode("utf-8")
    return f"data:{mime};base64,{b64}"


import fitz
import base64

def pdf_first_page_to_cropped_data_url(
    obj,
    fmt="png",
    max_width=1400,
    crop_inches=None
):
    """
    Render page 1 of a PDF, crop to fixed margins, return data URL.

    crop_inches format:
    {
        "top": float,
        "bottom": float,
        "left": float,
        "right": float
    }
    """

    # Default crop (your Foxit values)
    crop_inches = crop_inches or {
        "top": 0.69,
        "bottom": 7.42,
        "left": 0.65,
        "right": 1.34,
    }

    # Get file-like object
    f = obj.stream if hasattr(obj, "stream") else obj
    f.seek(0)
    pdf_bytes = f.read()
    f.seek(0)

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(0)

    page_rect = page.rect  # full page in points

    # Convert inches → points
    top = crop_inches["top"] * 72
    bottom = crop_inches["bottom"] * 72
    left = crop_inches["left"] * 72
    right = crop_inches["right"] * 72

    # Build crop rectangle
    crop_rect = fitz.Rect(
        left,
        top,
        page_rect.width - right,
        page_rect.height - bottom,
    )

    # Scale after cropping
    scale = max_width / crop_rect.width
    mat = fitz.Matrix(scale, scale)

    pix = page.get_pixmap(
        matrix=mat,
        clip=crop_rect,
        alpha=False
    )

    if fmt.lower() in ("jpg", "jpeg"):
        img_bytes = pix.tobytes("jpeg")
        mime = "image/jpeg"
    else:
        img_bytes = pix.tobytes("png")
        mime = "image/png"

    b64 = base64.b64encode(img_bytes).decode("utf-8")
    return f"data:{mime};base64,{b64}"
