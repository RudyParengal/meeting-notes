"""AI processing: classify -> (summary || action items), against any
OpenAI-compatible chat endpoint.

One client type covers OpenAI, Google Gemini, Ollama (fully local), Groq,
DeepSeek, Together, LM Studio, vLLM and anything else that speaks
`/v1/chat/completions`. Each stage can use a different provider and model:

    LLM_API_KEY / LLM_BASE_URL / LLM_MODEL        defaults for every stage
    SUMMARY_*  CLASSIFY_*  ACTION_*               per-stage overrides

The earlier version hard-coded four vendors and chose between them by looking
at the model *name*. That is how every request kept going to a provider whose
account had run out of credit, and how model ids belonging to one vendor were
sent to another and 404'd. There is no routing here: a stage uses exactly the
endpoint it was configured with.
"""
import asyncio
import logging
import re
import time
from dataclasses import dataclass

from openai import AsyncOpenAI

import config
from prompts import action_prompt, classify_prompt, summary_prompt

log = logging.getLogger("pipeline")

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
# A 46-minute meeting is ~27k characters and a slow provider can take minutes.
# The original 60s timeout lost the action plan of an important call.
TIMEOUT_SEC = 300.0

# Providers return 503 "high demand" or 429 "slow down" in short bursts - seen in
# practice with Gemini. A patient retry turns most of those into a normal note.
# Seconds to wait before each further attempt; "" or "0" disables retrying.
RETRY_DELAYS = tuple(float(x) for x in config.env("LLM_RETRY_DELAYS", "3,8,20").split(",") if x.strip())
_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}

# Output budgets. Reasoning models count their hidden thinking against these, so
# they are generous. Raise LLM_MAX_TOKENS if long meetings come back cut off.
MAX_TOKENS = config.env_int("LLM_MAX_TOKENS", 8192)
CLASSIFY_MAX_TOKENS = 1024

VALID_TYPES = {"client", "team", "project", "sales", "investment", "technical",
               "interview", "standup", "generic"}


@dataclass
class Stage:
    name: str
    model: str
    base_url: str
    client: AsyncOpenAI

    def describe(self) -> str:
        return f"{self.name}: {self.model} @ {self.base_url}"


def _build_stage(name: str) -> Stage:
    """Resolve one stage's endpoint: STAGE_* beats LLM_* beats the built-in default."""
    up = name.upper()
    base = config.env(f"{up}_BASE_URL") or config.env("LLM_BASE_URL") or DEFAULT_BASE_URL
    model = config.env(f"{up}_MODEL") or config.env("LLM_MODEL") or DEFAULT_MODEL
    key = config.env(f"{up}_API_KEY") or config.env("LLM_API_KEY")

    if config.is_placeholder(key):
        if config.is_local_url(base):
            key = "not-needed"          # Ollama / LM Studio ignore the key
        else:
            raise config.ConfigError(
                f"No API key for the '{name}' stage ({base}).\n"
                f"  Set LLM_API_KEY in backend/.env (or {up}_API_KEY for this stage only).\n"
                f"  Running a local model instead? Set LLM_BASE_URL=http://localhost:11434/v1 "
                f"and LLM_MODEL to the model you pulled.")
    # max_retries=0: retrying is done in _chat(), with delays long enough to matter.
    client = AsyncOpenAI(api_key=key, base_url=base, timeout=TIMEOUT_SEC, max_retries=0)
    return Stage(name=name, model=model, base_url=base, client=client)


_stages: dict[str, Stage] = {}


def init() -> dict[str, Stage]:
    """Build every stage. Called once at start-up so a bad setting stops the
    server immediately instead of failing on the first real meeting."""
    for s in ("classify", "summary", "action"):
        _stages[s] = _build_stage(s)
    for st in _stages.values():
        log.info("LLM stage -> %s", st.describe())
    return _stages


def stages() -> dict[str, Stage]:
    if not _stages:
        init()
    return _stages


# -----------------------------------------------------------------
# Filler word cleanup
# -----------------------------------------------------------------
_FILLER_PATTERNS = [
    r"\b[Uu]h\b[,.]?\s*", r"\b[Uu]m\b[,.]?\s*", r"\b[Ee]r\b[,.]?\s*", r"\b[Aa]h\b[,.]?\s*",
    r"(?:,\s*)?(?:[Yy]ou know)\s*,\s*",
    r"(?:,\s*)?(?:[Ii] mean)\s*,\s*",
    r"(?:,\s*)?(?:[Ss]ort of)\s*,?\s*",
    r"(?:,\s*)?(?:[Kk]ind of)\s*,?\s*",
    r"(?:,\s*)?(?:[Ll]ike)\s*,\s+",
]


def clean_transcript(text: str) -> str:
    """Remove clearly-filler words. Conservative: content-bearing uses stay."""
    cleaned = text
    for pattern in _FILLER_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned)
    cleaned = re.sub(r"  +", " ", cleaned)
    cleaned = re.sub(r"\s+([,.])", r"\1", cleaned)
    cleaned = re.sub(r"[,]{2,}", ",", cleaned)
    cleaned = re.sub(r"[.]{2,}", ".", cleaned)
    return cleaned.strip()


# -----------------------------------------------------------------
# LLM calls
# -----------------------------------------------------------------
def _retryable(e: Exception) -> bool:
    """Worth another attempt? Temporary overload, rate limit or network blip: yes.
    A spent quota, a bad key, a wrong model name: no - waiting cannot fix those,
    and retrying a spent quota just burns a minute before reporting it."""
    if "insufficient_quota" in str(e).lower():       # the standard code for "your plan is used up"
        return False
    status = getattr(e, "status_code", None)
    if status is not None:
        return status in _RETRYABLE_STATUS
    import openai
    return isinstance(e, (openai.APIConnectionError, openai.APITimeoutError))


class EmptyAnswer(Exception):
    """The provider answered, but with no text. Never silently turned into an empty note."""


async def _chat(stage: Stage, prompt: str, *, temperature: float = 0.3, max_tokens: int = None) -> str:
    max_tokens = max_tokens or MAX_TOKENS
    attempt = 0
    while True:
        try:
            resp = await stage.client.chat.completions.create(
                model=stage.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            choice = resp.choices[0]
            text = (choice.message.content or "").strip()
            if not text:
                reason = getattr(choice, "finish_reason", None)
                raise EmptyAnswer(
                    f"the model returned no text (finish_reason={reason}). "
                    + ("It ran out of output tokens - reasoning models spend some on thinking; "
                       "raise LLM_MAX_TOKENS." if reason == "length" else "Check the model name."))
            return text
        except Exception as e:
            if attempt >= len(RETRY_DELAYS) or not _retryable(e):
                raise
            delay = RETRY_DELAYS[attempt]
            attempt += 1
            log.warning("%s stage: %s - retry %d/%d in %.0fs",
                        stage.name, _short(e), attempt, len(RETRY_DELAYS), delay)
            await asyncio.sleep(delay)


async def classify_meeting(transcript: str) -> str:
    try:
        # NOT a tiny limit. "Thinking" models (Gemini 2.5, o-series, DeepSeek-R1)
        # spend output tokens on hidden reasoning first; with max_tokens=16 or 64
        # they ran out before saying a word (finish_reason=length, content=None)
        # and every meeting was silently classified "generic". Found in testing
        # against the real provider, not by the unit tests - the fake AI never thinks.
        answer = (await _chat(stages()["classify"], classify_prompt(transcript),
                              temperature=0.0, max_tokens=CLASSIFY_MAX_TOKENS)).lower()
        # The first word that is a real category wins ("Category: sales." -> sales).
        for word in re.findall(r"[a-z]+", answer):
            if word in VALID_TYPES:
                return word
        log.warning("classify gave %r, which is not a category; using 'generic'", answer[:60])
        return "generic"
    except Exception as e:
        log.warning("classify failed, using 'generic': %s", e)
        return "generic"


async def generate_summary(transcript: str, meeting_type: str, duration_min: int, date_str: str) -> str:
    return await _chat(stages()["summary"],
                       summary_prompt(transcript, meeting_type, duration_min, date_str))


async def generate_actions(transcript: str, meeting_type: str) -> str:
    try:
        return await _chat(stages()["action"], action_prompt(transcript, meeting_type))
    except Exception as e:
        # A provider failure must degrade the note, never hang the session.
        # The reason is written into the note so it is visible, not just logged.
        log.error("action stage failed: %s", e)
        return f"_Action extraction unavailable for this meeting: {_short(e)}_"


def _short(e: Exception) -> str:
    msg = str(e).replace("\n", " ")
    return msg[:200] + ("..." if len(msg) > 200 else "")


async def process_transcript(transcript: str, duration_sec: int, date_str: str,
                             meeting_type: str = "auto") -> dict:
    """Clean fillers, classify if asked, then run summary and actions in parallel."""
    t0 = time.time()
    duration_min = max(1, duration_sec // 60)

    cleaned = clean_transcript(transcript)
    log.info("filler cleanup: %d -> %d chars", len(transcript), len(cleaned))

    if meeting_type == "auto":
        meeting_type = await classify_meeting(cleaned)
        log.info("classified as: %s", meeting_type)

    t1 = time.time()
    summary, actions = await asyncio.gather(
        generate_summary(cleaned, meeting_type, duration_min, date_str),
        generate_actions(cleaned, meeting_type),
        return_exceptions=True,
    )
    t2 = time.time()

    if isinstance(summary, Exception):
        log.error("summary failed: %s", summary)
        summary = f"_Summary generation failed: {_short(summary)}_"
    if isinstance(actions, Exception):
        log.error("actions failed: %s", actions)
        actions = f"_Action extraction failed: {_short(actions)}_"

    return {
        "meeting_type": meeting_type,
        "summary": summary,
        "actions": actions,
        "timing": {
            "classify_sec": round(t1 - t0, 2),
            "llm_parallel_sec": round(t2 - t1, 2),
            "total_sec": round(t2 - t0, 2),
        },
    }
