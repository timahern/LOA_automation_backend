from pypdf import PdfReader, PdfWriter
from io import BytesIO
import zipfile
from b1Extractor import extract_b1_from_uploaded_pdf  # import your refactored function


class LoaGenerator:
    def __init__(self, exA, exB, exB1_list, exC, exD, exH):
        self.exhibit_a = exA
        self.exhibit_b = exB
        self.exhibit_c = exC
        self.exhibit_d = exD
        self.exhibit_h = exH

        self.exhibit_b1_list = []

        for b1_file in exB1_list:
            b1_file.seek(0)

            cleaned_b1 = extract_b1_from_uploaded_pdf(b1_file)

            if cleaned_b1:
                # Check if extracted B.1 has at least one page
                reader = PdfReader(cleaned_b1)
                if len(reader.pages) > 0:
                    cleaned_b1.seek(0)  # reset pointer after reading
                    cleaned_b1.filename = getattr(b1_file, "filename", "Subcontractor")
                    self.exhibit_b1_list.append(cleaned_b1)
                else:
                    print(f"❌ Skipped: No B.1 pages found in {b1_file.filename}")
            else:
                print(f"❌ Skipped: B.1 extractor returned None for {b1_file.filename}")

    def generate_loas_zip(self):
        zip_buffer = BytesIO()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
            for b1_file in self.exhibit_b1_list:
                b1_file.seek(0)
                writer = PdfWriter()

                pdfs_to_add = [
                    self.exhibit_a,
                    self.exhibit_b,
                    b1_file,
                    self.exhibit_c,
                    self.exhibit_d,
                ]

                if self.exhibit_h:
                    pdfs_to_add.append(self.exhibit_h)

                for pdf in pdfs_to_add:
                    self._add_pdf(writer, pdf)

                base_name = b1_file.filename.rsplit(".", 1)[0]
                file_name = f"{base_name}_LOA.pdf"

                buffer = BytesIO()
                writer.write(buffer)
                buffer.seek(0)

                zipf.writestr(file_name, buffer.read())

        zip_buffer.seek(0)
        return zip_buffer

    def _add_pdf(self, writer, file_obj):
        file_obj.seek(0)
        reader = PdfReader(file_obj)
        for page in reader.pages:
            writer.add_page(page)
