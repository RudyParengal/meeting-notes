"""AI processing pipeline: Gemini (summary + classify) + DeepSeek (action items), parallel."""
import asyncio, os, re, time, logging
from openai import AsyncOpenAI
from google import genai
from prompts import summary_prompt, action_prompt, classify_prompt

log = logging.getLogger('pipeline')

GEMINI_KEY = os.environ['GEMINI_API_KEY']
DEEPSEEK_KEY = os.environ['DEEPSEEK_API_KEY']
DEEPSEEK_URL = os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')

_gemini = genai.Client(api_key=GEMINI_KEY)
_deepseek = AsyncOpenAI(api_key=DEEPSEEK_KEY, base_url=DEEPSEEK_URL)

SUMMARY_MODEL = 'gemini-2.5-flash'
CLASSIFY_MODEL = 'gemini-2.5-flash'
ACTION_MODEL = 'deepseek-chat'


# -----------------------------------------------------------------
# Filler word cleanup (B2)
# -----------------------------------------------------------------
_FILLER_PATTERNS = [
    # Standalone fillers at sentence boundaries or between commas
    r'\b[Uu]h\b[,.]?\s*',
    r'\b[Uu]m\b[,.]?\s*',
    r'\b[Ee]r\b[,.]?\s*',
    r'\b[Aa]h\b[,.]?\s*',
    # Phrase-level fillers (only when clearly filler - at start of clause or between commas)
    r'(?:,\s*)?(?:[Yy]ou know)\s*,\s*',
    r'(?:,\s*)?(?:[Ii] mean)\s*,\s*',
    r'(?:,\s*)?(?:[Ss]ort of)\s*,?\s*',
    r'(?:,\s*)?(?:[Kk]ind of)\s*,?\s*',
    r'(?:,\s*)?(?:[Ll]ike)\s*,\s+',
]


def clean_transcript(text: str) -> str:
    """Remove common filler words from transcript text.
    Conservative: only removes clearly filler patterns, not content-bearing uses."""
    cleaned = text
    for pattern in _FILLER_PATTERNS:
        cleaned = re.sub(pattern, ' ', cleaned)
    # Collapse multiple spaces
    cleaned = re.sub(r'  +', ' ', cleaned)
    # Fix orphaned punctuation
    cleaned = re.sub(r'\s+([,.])', r'\1', cleaned)
    # Fix double commas/periods
    cleaned = re.sub(r'[,]{2,}', ',', cleaned)
    cleaned = re.sub(r'[.]{2,}', '.', cleaned)
    return cleaned.strip()


# -----------------------------------------------------------------
# LLM calls
# -----------------------------------------------------------------
async def _gemini_call(prompt: str, model: str = SUMMARY_MODEL) -> str:
    """Wrap Gemini in an executor since google-genai SDK is sync."""
    def _sync():
        resp = _gemini.models.generate_content(model=model, contents=prompt)
        return resp.text or ''
    return await asyncio.get_event_loop().run_in_executor(None, _sync)


async def classify_meeting(transcript: str) -> str:
    try:
        cat = (await _gemini_call(classify_prompt(transcript), CLASSIFY_MODEL)).strip().lower()
        valid = {'client', 'team', 'project', 'sales', 'investment', 'technical', 'interview', 'generic'}
        return cat if cat in valid else 'generic'
    except Exception as e:
        log.warning(f'classify failed: {e}')
        return 'generic'


async def generate_summary(transcript: str, meeting_type: str, duration_min: int, date_str: str) -> str:
    prompt = summary_prompt(transcript, meeting_type, duration_min, date_str)
    return await _gemini_call(prompt)


async def generate_actions(transcript: str, meeting_type: str) -> str:
    prompt = action_prompt(transcript, meeting_type)
    resp = await _deepseek.chat.completions.create(
        model=ACTION_MODEL,
        messages=[{'role': 'user', 'content': prompt}],
        temperature=0.3,
        max_tokens=4000,
    )
    return resp.choices[0].message.content or ''


async def process_transcript(transcript: str, duration_sec: int, date_str: str,
                              meeting_type: str = 'auto'):
    """Full pipeline. Clean fillers, classify (if auto) then run summary + actions in parallel."""
    t0 = time.time()
    duration_min = max(1, duration_sec // 60)

    # Clean filler words before LLM processing
    cleaned_transcript = clean_transcript(transcript)
    log.info(f'Filler cleanup: {len(transcript)} -> {len(cleaned_transcript)} chars')

    if meeting_type == 'auto':
        meeting_type = await classify_meeting(cleaned_transcript)
        log.info(f'classified as: {meeting_type}')

    t1 = time.time()
    summary, actions = await asyncio.gather(
        generate_summary(cleaned_transcript, meeting_type, duration_min, date_str),
        generate_actions(cleaned_transcript, meeting_type),
        return_exceptions=True,
    )
    t2 = time.time()

    if isinstance(summary, Exception):
        log.error(f'summary failed: {summary}')
        summary = f'_Summary generation failed: {summary}_'
    if isinstance(actions, Exception):
        log.error(f'actions failed: {actions}')
        actions = f'_Action extraction failed: {actions}_'

    return {
        'meeting_type': meeting_type,
        'summary': summary,
        'actions': actions,
        'timing': {
            'classify_sec': round(t1 - t0, 2),
            'llm_parallel_sec': round(t2 - t1, 2),
            'total_sec': round(t2 - t0, 2),
        },
    }
