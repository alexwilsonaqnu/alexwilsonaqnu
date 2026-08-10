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


# --- brain selection ---------------------------------------------------------
# Model ids are config, never literals in agent code — ids churn between releases and a
# swap must be an env change.
DEFAULT_LLM_PROVIDER = "anthropic"


def llm_provider() -> str:
    return os.environ.get("LLM_PROVIDER", DEFAULT_LLM_PROVIDER).strip().lower()


# --- Claude (default brain, API key only) ------------------------------------
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
# The judge is high-volume and low-stakes, so it runs on the cheapest model that
# reliably emits the JSON schema. That is a deliberate cost choice for this role, not a
# default — the orchestrator stays on Opus.
DEFAULT_ANTHROPIC_EVAL_MODEL = "claude-haiku-4-5"
# Depth control on Claude is `effort`, not temperature: sampling parameters are rejected
# on current models. "low" keeps thinking on (safer and cheaper than disabling it) while
# staying inside a phone call's latency budget. Raise to "high" for harder diagnostics.
DEFAULT_EFFORT = "low"


def anthropic_model() -> str:
    return os.environ.get("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL)


def anthropic_eval_model() -> str:
    return os.environ.get("ANTHROPIC_EVAL_MODEL", DEFAULT_ANTHROPIC_EVAL_MODEL)


def effort() -> str:
    return os.environ.get("CLAUDE_EFFORT", DEFAULT_EFFORT)


def anthropic_fallbacks() -> str | None:
    """Server-side refusal fallback. Set ANTHROPIC_FALLBACKS="" to disable."""
    value = os.environ.get("ANTHROPIC_FALLBACKS", "default")
    return value or None


# --- Gemini (alternate brain, Model Garden only) -----------------------------
# Verified GA in Model Garden as of 2026-08: gemini-3.5-flash, gemini-3.5-flash-lite,
# gemini-3.6-flash, gemini-3.1-pro.
DEFAULT_ORCHESTRATOR_MODEL = "gemini-3.5-flash"
DEFAULT_EVAL_MODEL = "gemini-3.5-flash-lite"


def orchestrator_model() -> str:
    return os.environ.get("GEMINI_MODEL", DEFAULT_ORCHESTRATOR_MODEL)


def eval_model() -> str:
    return os.environ.get("GEMINI_EVAL_MODEL", DEFAULT_EVAL_MODEL)


def gcp_project() -> str | None:
    return os.environ.get("GOOGLE_CLOUD_PROJECT")


def gcp_location() -> str:
    return os.environ.get("GOOGLE_CLOUD_LOCATION", "global")


# --- agent loop --------------------------------------------------------------
MAX_TOOL_ROUNDS = 15  # per user turn
MAX_EVALUATOR_TOOL_ROUNDS = 2  # the judge may refine retrieval at most twice

# On Claude, max_tokens caps thinking + response text together, so leave headroom above
# what a two-sentence spoken reply needs.
MAX_TURN_TOKENS = 4096
MAX_EVALUATOR_TOKENS = 2048

# post-hook trigger: retrieval is "weak" below this BM25 score, or when hits span >1 doc
WEAK_RETRIEVAL_SCORE = 3.0


# --- external services -------------------------------------------------------
def service_matters_url() -> str:
    return os.environ.get("SERVICE_MATTERS_URL", "https://search.servicematters.com/search")


HTTP_TIMEOUT_SECONDS = 8.0

# --- voice -------------------------------------------------------------------
DEFAULT_VOICE_PROVIDER = "elevenlabs"
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
