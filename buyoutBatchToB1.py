from typing import List, Union
from io import BytesIO

def extract_b1_batch_from_uploads(uploaded_files: List) -> List[Union[BytesIO, None]]:
    """
    Takes a list of uploaded files and returns a list of B.1-only PDFs.
    If no B.1 section is found in a file, the corresponding output is None.
    """
    from b1Extractor import extract_b1_from_uploaded_pdf  # Your existing function

    b1_pdfs = []

    for uploaded_file in uploaded_files:
        uploaded_file.seek(0)
        b1_pdf = extract_b1_from_uploaded_pdf(uploaded_file)

        if b1_pdf:
            original_name = getattr(uploaded_file, "filename", "Unknown.pdf")

            # Rename "Buyout.pdf" to "B1.pdf", or append "_B1.pdf"
            if original_name.endswith("Buyout.pdf"):
                new_name = original_name.replace("Buyout.pdf", "B1.pdf")
            elif original_name.lower().endswith(".pdf"):
                new_name = original_name[:-4] + "_B1.pdf"
            else:
                new_name = original_name + "_B1.pdf"

            b1_pdf.filename = new_name

        b1_pdfs.append(b1_pdf)

    return b1_pdfs

