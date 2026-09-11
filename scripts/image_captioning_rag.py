"""
image_captioning_rag.py  (v2 — full-page render)

For each PDF (FCOM, FCTM):
  - Render every page as a full PNG (page.get_pixmap) — captures ALL content
    including vector diagrams, tables, charts (not just embedded raster images)
  - If Claude detects only a logo/header page → SKIP (no upsert)
  - Extract "Applicable to: MSN ..." text from the same page
  - Generate a technical caption via Claude API (vision)
  - Embed the caption via OpenAI text-embedding-3-small
  - Upsert into Qdrant with metadata

Checkpoint: docs/pdfs/.image_caption_checkpoint_v2.json
"""

import base64
import json
import os
import re
import sys
import time
from pathlib import Path

import fitz  # PyMuPDF
from anthropic import Anthropic
from dotenv import load_dotenv
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    PointStruct,
    VectorParams,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY")
QDRANT_URL        = os.getenv("QDRANT_URL")
QDRANT_API_KEY    = os.getenv("QDRANT_API_KEY")

COLLECTION_NAME   = "documents"
EMBEDDING_MODEL   = "text-embedding-3-small"
EMBEDDING_DIM     = 1536
CLAUDE_MODEL      = "claude-sonnet-4-6"

# Only process FCOM — FCTM is already done correctly
PDF_MAP = {
    "FCOM": Path("docs/pdfs/FCOM.pdf"),
    # "FCTM": Path("docs/pdfs/FCTM.pdf"),  # already done, skip
}

# Separate checkpoint for v2 (full-page render approach)
CHECKPOINT_FILE = Path("docs/pdfs/.image_caption_checkpoint_v2.json")

# Render resolution — 150 DPI is sufficient for Claude vision
RENDER_DPI = 150

# ---------------------------------------------------------------------------
# MSN extraction
# ---------------------------------------------------------------------------

MSN_PATTERN = re.compile(
    r"Applicable\s+to\s*[:\-]?\s*MSN\s+([\d\s,\-–TO to]+)",
    re.IGNORECASE,
)


def extract_msn_range(page: fitz.Page) -> str:
    text = page.get_text("text")
    m = MSN_PATTERN.search(text)
    if m:
        return m.group(1).strip()
    return ""


# ---------------------------------------------------------------------------
# Full-page render
# ---------------------------------------------------------------------------


def render_page(page: fitz.Page) -> bytes:
    """Render the full page as PNG bytes at RENDER_DPI."""
    zoom = RENDER_DPI / 72  # PyMuPDF default is 72 DPI
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    return pix.tobytes("png")


def page_has_content(page: fitz.Page) -> bool:
    """
    Quick pre-filter: skip pages that are pure text with no images or drawings.
    A page with diagrams/tables will have either embedded images OR vector drawings.
    """
    # Has embedded raster images?
    if page.get_images(full=False):
        return True
    # Has vector drawings (lines, rects, curves)?
    drawings = page.get_drawings()
    if len(drawings) > 5:  # more than 5 vector elements = likely a diagram/table
        return True
    return False


# ---------------------------------------------------------------------------
# Caption generation (Claude) with SKIP logic
# ---------------------------------------------------------------------------

CAPTION_SYSTEM = (
    "You are an expert aviation technical writer analyzing Airbus A320-family manual pages.\n\n"
    "IMPORTANT: If the page contains ONLY a logo, company header, cover page, title page, "
    "or blank/nearly-blank page with no technical aviation content, respond with exactly: SKIP\n\n"
    "Otherwise, write a precise technical caption (2-5 sentences) describing:\n"
    "- What the diagram, chart, table, or procedure shows\n"
    "- Key components, labels, colors, and their operational meaning\n"
    "- The MSN applicability if visible\n"
    "- The operational significance for flight crew\n\n"
    "Be specific. Do not write generic phrases. Focus on what a pilot needs to know."
)


def generate_caption(
    client: Anthropic,
    png_bytes: bytes,
    page_context: str,
    doc_code: str,
    page_num: int,
) -> str:
    """Returns caption string, or 'SKIP' if Claude detects a non-content page."""
    b64_data = base64.standard_b64encode(png_bytes).decode()

    user_content = [
        {
            "type": "image",
            "source": {
                "type":       "base64",
                "media_type": "image/png",
                "data":       b64_data,
            },
        },
        {
            "type": "text",
            "text": (
                f"Document: {doc_code}, Page: {page_num}.\n"
                f"Page text context: {page_context[:600] if page_context else 'N/A'}\n\n"
                "Write a technical caption for this page, or respond SKIP if it is a logo/header/cover page."
            ),
        },
    ]

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=512,
        system=CAPTION_SYSTEM,
        messages=[{"role": "user", "content": user_content}],
    )
    return response.content[0].text.strip()


# ---------------------------------------------------------------------------
# Embedding (OpenAI)
# ---------------------------------------------------------------------------


def embed_text(client: OpenAI, text: str) -> list[float]:
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=text)
    return response.data[0].embedding


# ---------------------------------------------------------------------------
# Qdrant helpers
# ---------------------------------------------------------------------------


def ensure_collection(qdrant: QdrantClient) -> None:
    existing = {c.name for c in qdrant.get_collections().collections}
    if COLLECTION_NAME not in existing:
        qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )
        print(f"[qdrant] Created collection '{COLLECTION_NAME}'")
    else:
        print(f"[qdrant] Collection '{COLLECTION_NAME}' already exists")


def upsert_point(
    qdrant: QdrantClient,
    point_key: str,
    vector: list[float],
    payload: dict,
) -> None:
    import hashlib
    uid = int(hashlib.sha256(point_key.encode()).hexdigest(), 16) % (2**63)
    qdrant.upsert(
        collection_name=COLLECTION_NAME,
        points=[PointStruct(id=uid, vector=vector, payload=payload)],
    )


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------


def load_checkpoint() -> set[str]:
    if CHECKPOINT_FILE.exists():
        try:
            data = json.loads(CHECKPOINT_FILE.read_text())
            return set(data.get("done", []))
        except Exception:
            pass
    return set()


def save_checkpoint(done: set[str]) -> None:
    CHECKPOINT_FILE.write_text(json.dumps({"done": sorted(done)}, indent=2))


# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------


def process_pdf(
    doc_code: str,
    pdf_path: Path,
    anthropic_client: Anthropic,
    openai_client: OpenAI,
    qdrant: QdrantClient,
    done: set[str],
) -> None:
    print(f"\n{'='*60}")
    print(f"Processing {doc_code}: {pdf_path}")
    print(f"{'='*60}")

    doc = fitz.open(str(pdf_path))
    total_pages = doc.page_count
    print(f"Total pages: {total_pages}")

    skipped_no_content = 0
    skipped_logo       = 0
    processed          = 0

    for page_index in range(total_pages):
        page_num = page_index + 1  # 1-based
        page     = doc.load_page(page_index)

        # Checkpoint key — v2 format (page-based, no xref)
        point_key = f"{doc_code}_v2_p{page_num}"
        if point_key in done:
            continue

        # Pre-filter: skip pure-text pages with no drawings
        if not page_has_content(page):
            skipped_no_content += 1
            # Mark as done so we don't recheck
            done.add(point_key)
            if page_num % 500 == 0:
                save_checkpoint(done)
                print(f"  [progress] Page {page_num}/{total_pages} — "
                      f"processed={processed}, skipped_no_content={skipped_no_content}, "
                      f"skipped_logo={skipped_logo}")
            continue

        # Render full page
        try:
            png_bytes = render_page(page)
        except Exception as e:
            print(f"  [warn] Page {page_num}: render failed: {e}")
            continue

        page_text = page.get_text("text")
        msn_range = extract_msn_range(page)

        print(f"  [caption] Page {page_num}/{total_pages} "
              f"(MSN: {msn_range or 'N/A'})...")

        # Generate caption
        try:
            caption = generate_caption(
                anthropic_client,
                png_bytes,
                page_text,
                doc_code,
                page_num,
            )
        except Exception as e:
            print(f"    [warn] Caption failed: {e}; retrying once...")
            time.sleep(5)
            try:
                caption = generate_caption(
                    anthropic_client, png_bytes, page_text, doc_code, page_num
                )
            except Exception as e2:
                print(f"    [error] Caption failed again: {e2}; skipping page.")
                continue

        # SKIP if Claude says logo/header page
        if caption.strip().upper() == "SKIP":
            skipped_logo += 1
            done.add(point_key)
            print(f"    [skip] Logo/header page — not upserted")
            if page_num % 100 == 0:
                save_checkpoint(done)
            continue

        print(f"    [caption] {caption[:100]}...")

        # Embed caption
        try:
            vector = embed_text(openai_client, caption)
        except Exception as e:
            print(f"    [warn] Embedding failed: {e}; skipping.")
            time.sleep(2)
            continue

        # Build payload
        image_b64  = base64.standard_b64encode(png_bytes).decode()
        source_ref = f"{doc_code} p.{page_num}"

        payload = {
            "doc_code":   f"{doc_code}_IMG",
            "has_image":  True,
            "image_b64":  image_b64,
            "msn_range":  msn_range,
            "page_num":   page_num,
            "source_ref": source_ref,
            "caption":    caption,
        }

        # Upsert
        try:
            upsert_point(qdrant, point_key, vector, payload)
        except Exception as e:
            print(f"    [warn] Qdrant upsert failed: {e}; skipping.")
            time.sleep(2)
            continue

        done.add(point_key)
        processed += 1
        save_checkpoint(done)
        print(f"    [ok] Page {page_num} upserted.")

        time.sleep(0.1)  # polite rate-limit buffer

    doc.close()
    print(f"\n[done] {doc_code}: processed={processed}, "
          f"skipped_no_content={skipped_no_content}, skipped_logo={skipped_logo}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    missing = [k for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "QDRANT_URL")
               if not os.getenv(k)]
    if missing:
        print(f"[error] Missing env vars: {', '.join(missing)}")
        sys.exit(1)

    anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)
    openai_client    = OpenAI(api_key=OPENAI_API_KEY)

    qdrant_kwargs = {"url": QDRANT_URL}
    if QDRANT_API_KEY:
        qdrant_kwargs["api_key"] = QDRANT_API_KEY
    qdrant = QdrantClient(**qdrant_kwargs)

    ensure_collection(qdrant)

    done = load_checkpoint()
    print(f"[checkpoint] {len(done)} pages already processed (v2).")

    for doc_code, pdf_path in PDF_MAP.items():
        if not pdf_path.exists():
            print(f"[warn] {pdf_path} not found, skipping.")
            continue
        process_pdf(doc_code, pdf_path, anthropic_client, openai_client, qdrant, done)

    print("\n[all done]")


if __name__ == "__main__":
    main()
