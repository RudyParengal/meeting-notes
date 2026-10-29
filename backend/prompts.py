"""Meeting type-specific prompts with template support."""

CLASSIFIER_PROMPT = """Classify this meeting transcript into ONE category. Return only the category name, nothing else.
Categories: client, team, project, sales, investment, technical, interview, standup, generic

Transcript (first 2000 chars):
{transcript_head}

Category:"""

# --- Template-specific prompt fragments ---

TEMPLATE_SUMMARY_HINTS = {
    'client': 'Focus on commitments made to the client, deliverables discussed, next steps, client satisfaction signals, and any concerns raised by the client.',
    'sales': 'Focus on objections raised, buying signals, pricing discussed, decision makers identified, competitive mentions, and concrete next steps toward closing.',
    'technical': 'Focus on architecture decisions, technical debt discussed, blockers identified, performance concerns, and technical action items.',
    'investment': 'Focus on metrics discussed, valuation points, due diligence items, investor concerns, funding timeline, and key asks from either side.',
    'standup': 'Use a brief per-person format: what was done yesterday, what is planned today, and any blockers. Keep it concise.',
    'team': 'Focus on project updates, decisions made, ownership assignments, team morale signals, and cross-functional dependencies.',
    'interview': 'Focus on candidate assessment, strengths demonstrated, concerns or red flags, culture fit signals, and a hiring recommendation.',
    'generic': 'Provide a general-purpose summary covering all important discussion points.',
}

TEMPLATE_ACTION_HINTS = {
    'client': 'Pay special attention to client commitments, deliverable deadlines, follow-up meetings, and any promises made. Flag anything that could affect client satisfaction.',
    'sales': 'Prioritize actions that move the deal forward: proposals to send, demos to schedule, stakeholders to loop in, objections to address, and pricing follow-ups.',
    'technical': 'Prioritize architecture decisions to document, technical debt to address, blockers to resolve, code reviews needed, and infrastructure changes.',
    'investment': 'Prioritize due diligence items, financial documents to prepare, investor follow-ups, legal reviews, and timeline-critical actions.',
    'standup': 'List blockers that need immediate resolution and any cross-team dependencies. Keep actions brief and person-specific.',
    'team': 'Focus on project ownership assignments, decision follow-ups, process improvements agreed upon, and inter-team coordination items.',
    'interview': 'List follow-up actions: reference checks, next interview rounds, hiring committee review, offer preparation, or rejection communication.',
    'generic': 'Extract all action items comprehensively using the standard Eisenhower Matrix prioritization.',
}

TEMPLATES = {
    'client': 'Client Meeting — focus on commitments, deliverables, next steps, client satisfaction signals',
    'sales': 'Sales Call — focus on objections, buying signals, pricing discussed, decision makers, next steps',
    'technical': 'Technical Discussion — focus on architecture decisions, technical debt, blockers, action items',
    'investment': 'Investment Meeting — focus on metrics discussed, valuation, due diligence items, investor concerns',
    'standup': 'Standup — brief format: what was done, what\'s planned, blockers (per person)',
    'team': 'Team Meeting — focus on project updates, decisions, ownership assignments',
    'interview': 'Interview — focus on candidate assessment, strengths, concerns, hiring recommendation',
    'generic': 'Generic — general-purpose meeting notes',
}


SUMMARY_PROMPT = """You are a precise meeting assistant. Analyze this {meeting_type} meeting transcript and produce a structured summary.

{template_hint}

Output in Markdown with these exact sections:

## Meeting Title
(Inferred one-line title)

## Metadata
- **Type:** {meeting_type}
- **Duration:** {duration_min} minutes
- **Date:** {date_str}
- **Participants:** (list names/roles detected; if none detected say 'Not identified')

## Executive Summary
(5 bullet points covering the core of the meeting)

## Key Decisions
(Bulleted list of decisions made. If none, say 'No formal decisions recorded')

## Open Questions
(Unresolved items that need follow-up)

## Context & Background
(1-2 sentences on what prompted this meeting, if clear)

Be concise, factual. Do NOT invent details not in the transcript.

---
TRANSCRIPT:
{transcript}
"""

ACTION_ITEMS_PROMPT = """You are Action Scribe, a specialist in extracting complete, prioritised action plans from meeting transcripts.

Analyze this {meeting_type} meeting and produce a structured action plan in Markdown.

{template_hint}

Your job:
- Identify EVERY explicit and implicit task, commitment, or decision from any participant
- Capture context: deadlines, ownership, dependencies, rationale
- Prioritise into the Eisenhower Matrix

Output EXACTLY this structure:

## A. Executive Snapshot
3-5 key imperatives — the must-move items.

## B. Action Matrix

| Priority | Task | Owner | Suggested Due Date | Context / Notes |
|----------|------|-------|--------------------|-----------------|
| Do Now (Urgent + Important) | ... | ... | ... | ... |
| Schedule (Important, Not Urgent) | ... | ... | ... | ... |
| Delegate (Urgent, Not Important) | ... | ... | ... | ... |
| Eliminate (Neither) | ... | ... | ... | ... |

## C. Recommended Workflow
Step-by-step guidance on how to execute these tasks (calendar blocks, delegations, tools).

## D. Follow-ups & Risks
- **Clarifications needed:** (questions worth raising)
- **Risks if delayed:** (consequences of inaction)
- **Suggested messages:** (templates for delegation/follow-up emails, only if relevant)

Principles: exhaustive, atomic, neutral, time-smart (infer deadlines with assumptions noted).

---
TRANSCRIPT:
{transcript}
"""

def _with_context(hint, context):
    """Add what the person typed in "Add context for the AI", if anything."""
    context = (context or "").strip()
    if not context:
        return hint
    return (hint + "\n\nContext from someone who was in the meeting. Use it for names, spellings "
            "and purpose; it overrides the transcript where they disagree:\n" + context[:4000])

def summary_prompt(transcript, meeting_type, duration_min, date_str, context=""):
    hint = TEMPLATE_SUMMARY_HINTS.get(meeting_type, TEMPLATE_SUMMARY_HINTS['generic'])
    return SUMMARY_PROMPT.format(
        transcript=transcript, meeting_type=meeting_type,
        duration_min=duration_min, date_str=date_str,
        template_hint=_with_context(hint, context)
    )

def action_prompt(transcript, meeting_type, context=""):
    hint = TEMPLATE_ACTION_HINTS.get(meeting_type, TEMPLATE_ACTION_HINTS['generic'])
    return ACTION_ITEMS_PROMPT.format(transcript=transcript, meeting_type=meeting_type,
                                      template_hint=_with_context(hint, context))

def classify_prompt(transcript):
    head = transcript[:2000]
    return CLASSIFIER_PROMPT.format(transcript_head=head)
