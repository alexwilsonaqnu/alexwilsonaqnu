"""Pre-index a fleet of models so the corpus is warm before anyone calls.

    python scripts/prewarm.py --models-file fixtures/fleet_models.txt
    python scripts/prewarm.py MVW7232HW WTW5057LW0 --max-mb 5
    python scripts/prewarm.py --models-file fixtures/fleet_models.txt --notes-only

On-the-fly acquisition (src/tools/doc_cache.py) makes any document reachable mid-call, but
a 156-page Technical Manual costs ~86s to index and a technician is not waiting for that.
This is the same pipeline run ahead of time, so the call hits a cache.

Measured on the 59-model fleet this was built for:

    341 unique documents, 948 MB   — 121 of them shared by more than one model
    345 techline notes             — inline text, no download at all
    166 Tech Sheets · 81 Service Pointers · 51 Parts Lists · 24 Technical Manuals

Deduplication matters more than it looks: routing 59 models naively would fetch documents
hundreds of times over. Work is keyed by document, not by model, and each document records
the union of every model that routed to it.

Resumable and idempotent — anything already in the registry is skipped, so an interrupted
run costs only what it had not finished. Start with `--max-mb 5` to warm every Tech Sheet
and Service Pointer in a couple of minutes, then run again without it to backfill the
Technical Manuals.

Seam: in production this is the document pipeline (Document AI -> vector index) running on
a schedule against the model list the fleet actually services, not a script.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import DATA_DIR  # noqa: E402
from src.tools.service_matters import CATEGORY_RANK, service_matters_search  # noqa: E402

DEFAULT_CATEGORIES = sorted(CATEGORY_RANK, key=lambda c: CATEGORY_RANK[c])


def read_models(args) -> list[str]:
    models: list[str] = list(args.models)
    if args.models_file:
        models += Path(args.models_file).read_text(encoding="utf-8").split()
    seen, out = set(), []
    for m in (m.strip().upper() for m in models if m.strip()):
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def route_fleet(models: list[str], workers: int) -> tuple[dict, dict, list]:
    """Stage 1 for every model, deduplicated by document."""
    docs: dict[str, dict] = {}
    doc_models: dict[str, set] = collections.defaultdict(set)
    notes: dict[str, dict] = {}
    note_models: dict[str, set] = collections.defaultdict(set)
    empty: list[str] = []

    def route(model):
        try:
            return model, service_matters_search(model, k=12)
        except Exception as exc:
            return model, {"results": [], "notes": [], "error": str(exc)}

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for model, result in pool.map(route, models):
            done += 1
            print(f"\r  routing {done}/{len(models)}", end="", flush=True)
            if not result.get("results") and not result.get("notes"):
                empty.append(model)
            for record in result.get("results", []):
                docs.setdefault(record["doc_id"], record)
                doc_models[record["doc_id"]].add(model)
            for note in result.get("notes", []):
                key = note.get("doc_id")
                if key:
                    notes.setdefault(key, note)
                    note_models[key].add(model)
    print()
    for doc_id, models_for in doc_models.items():
        docs[doc_id]["_models"] = sorted(models_for)
    for note_id, models_for in note_models.items():
        notes[note_id]["_models"] = sorted(models_for)
    return docs, notes, empty


def warm_document(record: dict, max_bytes: int | None) -> tuple[str, str, int]:
    """Fetch + ingest one document. Returns (doc_id, status, chunks)."""
    from src.ingest.ingest_pdf import ingest
    from src.tools.doc_cache import local_doc_id

    doc_id = record["doc_id"]
    models = record.get("_models") or []
    if local_doc_id(doc_id):
        return doc_id, "cached", 0

    import re

    slug = re.sub(r"[^a-z0-9]+", "_", (record.get("category") or "doc").lower()).strip("_")
    path = DATA_DIR / f"{doc_id.lower()}_{slug}.pdf"

    # Resume without re-downloading. A run interrupted after the download but before the
    # index leaves the PDF on disk; re-fetching it would have cost 260 MB of the 948 for
    # nothing. The registry is not evidence the document is searchable, so the skip test
    # above is chunk-based — this one is purely "do we still need the bytes".
    if not path.exists() or path.stat().st_size == 0:
        url = record.get("url")
        if not url:
            return doc_id, "no_url", 0
        try:
            import httpx

            response = httpx.get(url, timeout=180, follow_redirects=True)
            response.raise_for_status()
            body = response.content
        except Exception as exc:
            return doc_id, f"download_failed:{type(exc).__name__}", 0
        if max_bytes and len(body) > max_bytes:
            return doc_id, f"skipped_size:{len(body)//1024//1024}MB", 0
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        except Exception as exc:
            return doc_id, f"download_failed:{type(exc).__name__}", 0
    elif max_bytes and path.stat().st_size > max_bytes:
        return doc_id, f"skipped_size:{path.stat().st_size//1024//1024}MB", 0

    try:
        summary = ingest(path, models, title=record.get("doc_title"))
    except Exception as exc:
        return doc_id, f"ingest_failed:{type(exc).__name__}", 0
    return doc_id, "indexed", summary["chunks"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("models", nargs="*", help="model numbers")
    parser.add_argument("--models-file", help="file of whitespace-separated model numbers")
    parser.add_argument("--max-mb", type=float, default=None,
                        help="skip documents larger than this (run again without it to backfill)")
    parser.add_argument("--categories", nargs="+", default=None,
                        help=f"document categories to warm (default: all service categories). "
                             f"Known: {', '.join(DEFAULT_CATEGORIES)}")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--notes-only", action="store_true",
                        help="index the inline techline notes and skip every PDF")
    parser.add_argument("--dry-run", action="store_true", help="route and report, fetch nothing")
    args = parser.parse_args(argv)

    models = read_models(args)
    if not models:
        parser.error("give model numbers, or --models-file")
    print(f"{len(models)} models\n")

    started = time.time()
    docs, notes, empty = route_fleet(models, args.workers)
    wanted = set(args.categories or DEFAULT_CATEGORIES)
    selected = {d: r for d, r in docs.items() if r.get("category") in wanted}

    print(f"\n  {len(docs)} unique documents ({len(selected)} in scope), "
          f"{len(notes)} techline notes, {len(empty)} models with nothing")
    by_cat = collections.Counter(r["category"] for r in selected.values())
    for cat, n in by_cat.most_common():
        print(f"    {cat:<30} {n}")
    if empty:
        print(f"  no documents: {' '.join(empty)}")

    if args.dry_run:
        print("\ndry run — nothing fetched")
        return 0

    # Notes first: they are free, and they are the field knowledge.
    from src.ingest.ingest_note import ingest_note

    note_chunks = 0
    for index, (_, note) in enumerate(notes.items(), start=1):
        try:
            summary = ingest_note(note, extra_models=note.get("_models"))
            note_chunks += summary.get("chunks", 0)
        except Exception as exc:
            print(f"  ! note {note.get('doc_id')}: {type(exc).__name__}: {exc}")
        print(f"\r  notes {index}/{len(notes)}", end="", flush=True)
    print(f"\r  notes: {len(notes)} indexed, {note_chunks} chunks" + " " * 20)

    if args.notes_only:
        print(f"\ndone in {time.time()-started:.0f}s (notes only)")
        return 0

    max_bytes = int(args.max_mb * 1024 * 1024) if args.max_mb else None
    outcomes: collections.Counter = collections.Counter()
    total_chunks = 0
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(warm_document, r, max_bytes): d for d, r in selected.items()}
        for future in as_completed(futures):
            doc_id, status, chunks = future.result()
            done += 1
            total_chunks += chunks
            outcomes[status.split(":")[0]] += 1
            print(f"\r  documents {done}/{len(selected)}  "
                  f"indexed={outcomes['indexed']} cached={outcomes['cached']} "
                  f"skipped={outcomes['skipped_size']} failed="
                  f"{outcomes['download_failed']+outcomes['ingest_failed']}",
                  end="", flush=True)
    print()

    elapsed = time.time() - started
    print(f"\n{total_chunks + note_chunks} chunks from {outcomes['indexed']} documents "
          f"and {len(notes)} notes in {elapsed:.0f}s")
    for status, count in outcomes.most_common():
        print(f"  {status:<20} {count}")
    if outcomes["skipped_size"]:
        print("\n  re-run without --max-mb to backfill the documents skipped for size.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
