"""
Ingest the procedure manual PDF from S3 into ChromaDB.

Usage (on EC2):
    python -m rag.ingest
    python -m rag.ingest --source some-other-manual   # defaults to "procedure-manual"

Flow:
  1. Download PDF from s3://<S3_BUCKET_NAME>/rag-docs/<source>.pdf
  2. Render each page as a PNG, upload to S3, delete local copy
  3. Extract text, chunk, embed with text-embedding-3-small
  4. Store embeddings in ChromaDB (rag/chroma_db/)
  5. Delete the locally downloaded PDF
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import fitz  # PyMuPDF
from openai import OpenAI
import chromadb
from dotenv import load_dotenv

load_dotenv()

CHROMA_DIR       = Path(__file__).parent / "chroma_db"
CHUNK_SIZE       = 1000
CHUNK_OVERLAP    = 200
EMBED_BATCH_SIZE = 100


def _chunk_text(text: str, page_num: int) -> list[dict]:
    chunks = []
    start = 0
    while start < len(text):
        chunk = text[start : start + CHUNK_SIZE]
        if chunk.strip():
            chunks.append({"text": chunk, "page": page_num})
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def _embed_texts(client: OpenAI, texts: list[str]) -> list[list[float]]:
    response = client.embeddings.create(model="text-embedding-3-small", input=texts)
    return [item.embedding for item in response.data]


def ingest(source: str = "procedure-manual") -> None:
    from rag import s3_helper

    if not s3_helper.is_configured():
        raise RuntimeError("S3_BUCKET_NAME is not set in .env — cannot run ingest.")

    # ── 1. Download PDF from S3 ───────────────────────────────────────────────
    tmp_dir  = Path(tempfile.mkdtemp())
    pdf_path = tmp_dir / f"{source}.pdf"
    print(f"Downloading s3://{s3_helper.BUCKET}/{s3_helper.DOCS_PREFIX}/{source}.pdf ...")
    s3_helper.download_pdf(source, pdf_path)
    print("  → Downloaded")

    # ── 2. Render page images + extract text ─────────────────────────────────
    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    doc           = fitz.open(str(pdf_path))
    total_pages   = len(doc)
    all_chunks: list[tuple[dict, str]] = []

    print(f"Processing {total_pages} pages ...")

    with tempfile.TemporaryDirectory() as tmp_img_dir:
        for page_idx in range(total_pages):
            page     = doc[page_idx]
            page_num = page_idx + 1

            pix       = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            local_img = Path(tmp_img_dir) / f"page_{page_num:03d}.png"
            pix.save(str(local_img))
            s3_helper.upload_page_image(local_img, source, page_num)

            text = page.get_text()
            if text.strip():
                for chunk in _chunk_text(text, page_num):
                    all_chunks.append((chunk, source))

    doc.close()
    print(f"  → {total_pages} page images uploaded to S3")
    print(f"  → {len(all_chunks)} text chunks extracted")

    # ── 3. Delete the local PDF copy ─────────────────────────────────────────
    pdf_path.unlink(missing_ok=True)
    print("  → Local PDF copy deleted")

    # ── 4. Store embeddings in ChromaDB ───────────────────────────────────────
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    chroma = chromadb.PersistentClient(path=str(CHROMA_DIR))

    try:
        chroma.delete_collection(name=source)
        print(f"  → Wiped existing '{source}' collection")
    except Exception:
        pass

    collection = chroma.create_collection(name=source)

    for batch_start in range(0, len(all_chunks), EMBED_BATCH_SIZE):
        batch      = all_chunks[batch_start : batch_start + EMBED_BATCH_SIZE]
        texts      = [c["text"] for c, _ in batch]
        embeddings = _embed_texts(openai_client, texts)

        collection.add(
            ids        = [f"{source}_chunk_{batch_start + j}" for j in range(len(batch))],
            embeddings = embeddings,
            documents  = texts,
            metadatas  = [{"page": c["page"], "source": src} for c, src in batch],
        )
        print(f"  → Stored chunks {batch_start}–{batch_start + len(batch) - 1}")

    print(f"\nDone. '{source}' collection has {collection.count()} chunks.")


if __name__ == "__main__":
    source = "procedure-manual"
    if "--source" in sys.argv:
        idx    = sys.argv.index("--source")
        source = sys.argv[idx + 1]
    ingest(source)
