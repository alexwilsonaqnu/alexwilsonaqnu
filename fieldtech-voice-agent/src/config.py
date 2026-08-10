"""Central configuration. Model ids and paths live here, never inline in agent code.

Garden model names churn (2.5 -> 3 -> 3.5 -> 3.6 within a year), so swapping the brain
must be an env change, not a code change.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# --- filesystem layout -------------------------------------------------------
DATA_DIR = ROOT / "data"
FIGURES_DIR = DATA_DIR / "figures"
CHUNKS_PATH = DATA_DIR / "chunks.jsonl"
DOC_REGISTRY_PATH = DATA_DIR / "doc_registry.json"
FIGURE_PUSHES_PATH = DATA_DIR / "figure_pushes.jsonl"
WRITEBACK_LOG_PATH = DATA_DIR / "writeback_log.jsonl"

FIXTURES_DIR = ROOT / "fixtures"
SALESFORCE_FIXTURE_PATH = FIXTURES_DIR / "salesforce_cases.json"

SKILLS_DIR = ROOT / "skills"
AGENTS_DIR = ROOT / "agents"

TRACES_PATH = ROOT / "observability" / "traces" / "spans.jsonl"

# Read-only to every tool and to the agent. Enforced in src/agent/hooks.py, not in a prompt.
PROTECTED_WRITE_PREFIXES = (ROOT / "evals", ROOT / "observability" / "manifest")


# --- models (env-selectable, Model Garden only) ------------------------------
# Verified GA in Model Garden as of 2026-08: gemini-3.5-flash, gemini-3.5-flash-lite,
# gemini-3.6-flash, gemini-3.1-pro. For harder reasoning at higher latency, set
# GEMINI_MODEL=gemini-3.1-pro (the current Pro GA id) — voice latency will suffer.
DEFAULT_ORCHESTRATOR_MODEL = "gemini-3.5-flash"
DEFAULT_EVAL_MODEL = "gemini-3.5-flash-lite"


def orchestrator_model() -> str:
    return os.environ.get("GEMINI_MODEL", DEFAULT_ORCHESTRATOR_MODEL)


def eval_model() -> str:
    return os.environ.get("GEMINI_EVAL_MODEL", DEFAULT_EVAL_MODEL)


def configured_models() -> dict[str, str]:
    """Every model this POC will call, keyed by role. Preflight walks this."""
    return {"orchestrator": orchestrator_model(), "retrieval_evaluator": eval_model()}


def gcp_project() -> str | None:
    return os.environ.get("GOOGLE_CLOUD_PROJECT")


def gcp_location() -> str:
    return os.environ.get("GOOGLE_CLOUD_LOCATION", "global")


# --- agent loop --------------------------------------------------------------
MAX_TOOL_ROUNDS = 15  # per user turn
MAX_EVALUATOR_TOOL_ROUNDS = 2  # the judge may refine retrieval at most twice

# post-hook trigger: retrieval is "weak" below this BM25 score, or when hits span >1 doc
WEAK_RETRIEVAL_SCORE = 3.0


# --- external services -------------------------------------------------------
def service_matters_url() -> str:
    return os.environ.get("SERVICE_MATTERS_URL", "https://search.servicematters.com/search")


HTTP_TIMEOUT_SECONDS = 8.0

# --- voice -------------------------------------------------------------------
AUDIO_SAMPLE_RATE = 16_000
AUDIO_CHANNELS = 1

FISH_TTS_URL = "https://api.fish.audio/v1/tts"
FISH_ASR_URL = "https://api.fish.audio/v1/asr"
# `model` is a *header* on the Fish TTS call; allowed: s1, s2-pro, s2.1-pro, s2.1-pro-free
DEFAULT_FISH_TTS_MODEL = "s2.1-pro"

ELEVENLABS_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
DEFAULT_ELEVENLABS_STT_MODEL = "scribe_v1"
DEFAULT_ELEVENLABS_TTS_MODEL = "eleven_turbo_v2_5"
DEFAULT_ELEVENLABS_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"

HANGUP_PHRASES = (
    "hang up",
    "goodbye",
    "good bye",
    "end the call",
    "end call",
    "that's all thanks",
    "we're done here",
)
