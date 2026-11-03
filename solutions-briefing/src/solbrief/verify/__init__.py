"""The verifier: deterministic checks that decide whether a solution card may reach a reader."""

from solbrief.verify.card import CardReport, check_support, verify_card
from solbrief.verify.fetch import Document, Fetcher, http_fetcher

__all__ = ["CardReport", "Document", "Fetcher", "check_support", "http_fetcher", "verify_card"]
