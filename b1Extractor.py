from io import BytesIO
from pdf2image import convert_from_bytes
import pytesseract
from pypdf import PdfReader, PdfWriter
import difflib
import gc

pytesseract.pytesseract.tesseract_cmd = r"/usr/bin/tesseract"

def extract_b1_from_uploaded_pdf(
    uploaded_file,
    keyword="Exhibit B.1: Subcontract Scope of Work",
    dpi=150,
    crop_margins_in=(0.50, 0.57, 0.30, 6.37),
):
    file_bytes = uploaded_file.read()

    # Read PDF once (for page count + later output)
    reader = PdfReader(BytesIO(file_bytes))
    total_pages = len(reader.pages)

    candidate_pages = []
    found_b1 = False

    def fuzzy_line_match(text_lines, keyword, threshold=0.75):
        for line in text_lines:
            if difflib.SequenceMatcher(None, keyword.lower(), line.lower()).ratio() > threshold:
                return True
        return False

    # convert crop margins inches -> pixels
    left_in, top_in, right_in, bottom_in = crop_margins_in
    left_px = int(left_in * dpi)
    top_px = int(top_in * dpi)
    right_px = int(right_in * dpi)
    bottom_px = int(bottom_in * dpi)

    # ✅ IMPORTANT CHANGE: loop over pages and convert ONE PAGE at a time
    for i in range(total_pages):
        page_num = i + 1  # pdf2image uses 1-based page numbers

        try:
            image = convert_from_bytes(
                file_bytes,
                dpi=dpi,
                first_page=page_num,
                last_page=page_num,
                poppler_path="/usr/bin",
            )[0]
        except Exception as e:
            print(f"Render failed on page {page_num}: {e}")
            continue

        w_px, h_px = image.size

        # crop box
        x0 = max(0, left_px)
        y0 = max(0, top_px)
        x1 = min(w_px, w_px - right_px)
        y1 = min(h_px, h_px - bottom_px)

        if x1 <= x0 or y1 <= y0:
            print(f"❌ Invalid crop box on page {page_num}. Check crop margins.")
            image.close()
            continue

        cropped = image.crop((x0, y0, x1, y1))

        try:
            text = pytesseract.image_to_string(cropped, timeout=2000)
        except Exception as e:
            print(f"OCR Failed on page {page_num}: {e}")
            image.close()
            continue

        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        print(f"Page {page_num} cropped first lines: {lines[:5]}")

        if fuzzy_line_match(lines, keyword):
            found_b1 = True
            candidate_pages.append(i)
        elif found_b1:
            if any("Exhibit" in line and not fuzzy_line_match([line], keyword) for line in lines):
                break
            candidate_pages.append(i)

        # ✅ Free memory aggressively (important on 1GB RAM)
        image.close()
        del image, cropped, text, lines
        gc.collect()

    if not candidate_pages:
        print("❌ No B.1 section found.")
        return None

    # output full original pages
    writer = PdfWriter()
    for idx in candidate_pages:
        writer.add_page(reader.pages[idx])

    output_pdf = BytesIO()
    writer.write(output_pdf)
    output_pdf.seek(0)
    return output_pdf
