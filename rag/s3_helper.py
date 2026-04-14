"""
S3 utilities for RAG — uploading page images and downloading source PDFs.

Credentials are resolved by boto3 automatically in this order:
  1. AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in .env / environment
  2. ~/.aws/credentials (AWS CLI profile)
  3. EC2 IAM instance role (no keys needed if a role is attached)
"""
from __future__ import annotations

import os
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

BUCKET         = os.getenv("S3_BUCKET_NAME", "")
REGION         = os.getenv("S3_REGION", "us-east-2")
IMAGES_PREFIX  = os.getenv("S3_IMAGES_PREFIX", "rag-images")  # rag-images/<source>/page_001.png
DOCS_PREFIX    = os.getenv("S3_DOCS_PREFIX", "rag-docs")      # rag-docs/procedure-manual.pdf
PRESIGN_EXPIRY = 3600  # seconds — pre-signed URLs expire after 1 hour


def is_configured() -> bool:
    return bool(BUCKET)


def _client():
    return boto3.client("s3", region_name=REGION)


def upload_page_image(local_path: Path, source: str, page_num: int) -> str:
    """Upload a rendered page PNG to S3. Returns the S3 key."""
    key = f"{IMAGES_PREFIX}/{source}/page_{page_num:03d}.png"
    _client().upload_file(
        str(local_path),
        BUCKET,
        key,
        ExtraArgs={"ContentType": "image/png"},
    )
    return key


def get_page_image_url(source: str, page_num: int) -> str | None:
    """Return a pre-signed URL for a page image, or None if it doesn't exist."""
    key = f"{IMAGES_PREFIX}/{source}/page_{page_num:03d}.png"
    try:
        return _client().generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET, "Key": key},
            ExpiresIn=PRESIGN_EXPIRY,
        )
    except ClientError:
        return None


def download_pdf(source: str, dest_path: Path) -> None:
    """Download a PDF from S3 to a local path. Raises FileNotFoundError if missing."""
    key = f"{DOCS_PREFIX}/{source}.pdf"
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _client().download_file(BUCKET, key, str(dest_path))
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            raise FileNotFoundError(
                f"PDF not found in S3: s3://{BUCKET}/{key}\n"
                f"Upload it first:  aws s3 cp procedure-manual.pdf s3://{BUCKET}/{key}"
            )
        raise
