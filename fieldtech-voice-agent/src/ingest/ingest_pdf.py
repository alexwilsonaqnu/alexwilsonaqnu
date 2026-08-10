"""Ingest a service manual PDF into page chunks + extracted figures.

    python -m src.ingest.ingest_pdf <pdf> --models WTW5057LW0

Thin slice on purpose: one chunk per page, regex safety flagging, embedded images cropped
to PNG. Seam: production replaces pdfplumber text extraction with Document AI layout
parsing (tables, multi-column, OCR) — the chunk record shape is what everything downstream
depends on, so keep it stable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from src.config import CHUNKS_PATH, DOC_REGISTRY_PATH, FIGURES_DIR
from src.guards import guard_write_path

# Hazard lexicon. A page matching any of these is flagged safety=True, which is what
# `manual_search` reports and what the eval grader checks for safety tasks.
SAFETY_PATTERN = re.compile(
    r"warning|danger|caution|electrical shock|disconnect power|unplug|capacitor|gas|scald|sharp edge",
    re.IGNORECASE,
)

MIN_FIGURE_SIDE = 24.0  # points; ignore rules, bullets and other decorative slivers


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _clamp_bbox(image: dict[str, Any], page) -> tuple[float, float, float, float] | None:
    px0, ptop, px1, pbottom = page.bbox
    x0 = max(float(image["x0"]), px0)
    x1 = min(float(image["x1"]), px1)
    top = max(float(image["top"]), ptop)
    bottom = min(float(image["bottom"]), pbottom)
    if x1 - x0 < MIN_FIGURE_SIDE or bottom - top < MIN_FIGURE_SIDE:
        return None
    return (x0, top, x1, bottom)


def extract_page_figures(page, doc_id: str, page_number: int) -> list[str]:
    """Crop each embedded image on the page to data/figures/<doc>_pN_figM.png."""
    figure_ids: list[str] = []
    for index, image in enumerate(page.images, start=1):
        bbox = _clamp_bbox(image, page)
        if bbox is None:
            continue
        figure_id = f"{doc_id}_p{page_number}_fig{index}"
        out_path = guard_write_path(FIGURES_DIR / f"{figure_id}.png", actor="ingest_pdf")
        try:
            page.crop(bbox).to_image(resolution=150).save(str(out_path))
        except Exception as exc:  # a malformed image must not sink the whole doc
            print(f"  ! could not extract {figure_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        figure_ids.append(figure_id)
    return figure_ids


def ingest(pdf_path: Path, models: list[str], title: str | None = None) -> dict[str, Any]:
    import pdfplumber

    doc_id = slugify(pdf_path.stem)
    doc_title = title or pdf_path.stem.replace("_", " ").strip()

    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    chunks: list[dict[str, Any]] = []
    figure_total = 0

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").strip()
            figure_ids = extract_page_figures(page, doc_id, page_number)
            figure_total += len(figure_ids)
            chunks.append(
                {
                    "chunk_id": f"{doc_id}_p{page_number}",
                    "doc_id": doc_id,
                    "doc_title": doc_title,
                    "models": models,
                    "page": page_number,
                    "text": text,
                    "figures": figure_ids,
                    "safety": bool(SAFETY_PATTERN.search(text)),
                }
            )
        page_count = len(pdf.pages)

    _rewrite_chunks(doc_id, chunks)
    _register_doc(
        {
            "doc_id": doc_id,
            "title": doc_title,
            "models": models,
            "pages": page_count,
            "figure_count": figure_total,
            "source_path": str(pdf_path),
        }
    )
    return {
        "doc_id": doc_id,
        "doc_title": doc_title,
        "pages": page_count,
        "chunks": len(chunks),
        "figures": figure_total,
        "safety_pages": sum(1 for c in chunks if c["safety"]),
    }


def _rewrite_chunks(doc_id: str, new_chunks: list[dict[str, Any]]) -> None:
    """Replace this doc's chunks in place; leave other docs alone (re-ingest is idempotent)."""
    path = guard_write_path(CHUNKS_PATH, actor="ingest_pdf")
    kept: list[dict[str, Any]] = []
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if record.get("doc_id") != doc_id:
                    kept.append(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in kept + new_chunks:
            fh.write(json.dumps(record) + "\n")


def _register_doc(entry: dict[str, Any]) -> None:
    path = guard_write_path(DOC_REGISTRY_PATH, actor="ingest_pdf")
    registry: dict[str, Any] = {"docs": []}
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            registry = json.load(fh)
    docs = [d for d in registry.get("docs", []) if d.get("doc_id") != entry["doc_id"]]
    docs.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump({"docs": docs}, fh, indent=2)
        fh.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest a service manual PDF.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--models", nargs="+", required=True, help="model numbers this doc covers")
    parser.add_argument("--title", default=None)
    args = parser.parse_args(argv)

    if not args.pdf.exists():
        print(f"No such PDF: {args.pdf}", file=sys.stderr)
        return 2

    summary = ingest(args.pdf, [m.upper() for m in args.models], args.title)
    print(json.dumps(summary, indent=2))
    if summary["chunks"] == 0:
        print("No chunks produced — is the PDF text-bearing?", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
