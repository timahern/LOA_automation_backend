from io import BytesIO
from pdf2image import convert_from_bytes
import pytesseract
from pypdf import PdfReader, PdfWriter
import difflib

def extract_b1_from_uploaded_pdf(uploaded_file, keyword="Exhibit B.1: Subcontract Scope of Work"):
    

    # Read uploaded file as bytes and convert to images
    file_bytes = uploaded_file.read()
    pages = convert_from_bytes(file_bytes, dpi=150)

    candidate_pages = []
    found_b1 = False

    def fuzzy_line_match(text_lines, keyword, threshold=0.75):
        for line in text_lines:
            if difflib.SequenceMatcher(None, keyword.lower(), line.lower()).ratio() > threshold:
                return True
        return False

    for i, image in enumerate(pages):
        text = pytesseract.image_to_string(image)
        lines = text.splitlines()

        print(f"Page {i+1} first lines: {lines[:5]}")

        if fuzzy_line_match(lines, keyword):
            found_b1 = True
            candidate_pages.append(i)
        elif found_b1:
            # End condition if another unrelated "Exhibit" section begins
            if any("Exhibit" in line and not fuzzy_line_match([line], keyword) for line in lines):
                break
            candidate_pages.append(i)

    if not candidate_pages:
        print("❌ No B.1 section found.")
        return None

    # Build the B.1-only PDF
    reader = PdfReader(BytesIO(file_bytes))
    writer = PdfWriter()
    for i in candidate_pages:
        writer.add_page(reader.pages[i])

    output_pdf = BytesIO()
    writer.write(output_pdf)
    output_pdf.seek(0)
    return output_pdf
