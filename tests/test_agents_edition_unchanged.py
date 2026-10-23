"""The `agents` edition must carry exactly the editorial config the pipeline had
before it was extracted into editions/agents/ (issue #300: no behaviour change).

The literals below are the old module constants, copied verbatim from
scripts/prefilter.py (KEYWORDS), scripts/rank.py (SECTION_RULES, VALID_TAGS) and
scripts/write.py (READER_PROFILE) as of the extraction. They pin the extraction;
once the agents edition is tuned on purpose, update them in the same PR.
"""
import edition

OLD_KEYWORDS = [
    "\\bagent(s|ic|s')?\\b",
    '\\btool[- ]use\\b',
    '\\btool[- ]calling\\b',
    '\\bllm\\b',
    '\\blanguage model\\b',
    '\\bmcp\\b',
    '\\bmodel context protocol\\b',
    '\\brag\\b',
    '\\bretrieval[- ]augmented\\b',
    '\\beval(s|uation)?\\b',
    '\\bmulti[- ]agent\\b',
    '\\bcode generation\\b',
    '\\bcopilot\\b',
    '\\bchain[- ]of[- ]thought\\b',
    '\\bplanning\\b',
    '\\bworkflow\\b',
    '\\bautonomous\\b',
    '\\bclaude code\\b',
    '\\bgpt[- ]?\\d?\\b',
    '\\bclaude\\b',
    '\\bgemini\\b',
    '\\bdspy\\b',
    '\\blanggraph\\b',
    '\\blangchain\\b',
    '\\bautogen\\b',
    '\\bcrewai\\b',
]

OLD_READER_PROFILE = (
    'Senior engineer / staff+ / architect who already knows what an LLM is,'
    ' writes production code, and cares about: building agents (frameworks,'
    ' tool use, memory, planning, evals, cost/latency); agents for software'
    ' work (codegen, review, devops, incident response, SRE); running '
    'agents in production (observability, safety, failure modes, '
    'guardrails, deployment); state-of-the-art papers with concrete '
    'techniques; and agents for non-software tech work (managing servers, '
    'k8s, fleets). Down-weighted: consumer AI hype, funding/VC takes, '
    'prompt-engineering tips, generic ML news, model benchmarks without '
    'methodological interest.'
)

OLD_SECTION_RULES = {'papers': {'featured_min': 7, 'appendix_min': 5, 'cap': 5, 'burst_cap': 10, 'burst_trigger_score': 10, 'burst_trigger_count': 10}, 'news': {'featured_min': 6, 'appendix_min': 4, 'cap': 6}, 'blogs': {'featured_min': 6, 'appendix_min': 4, 'cap': 6}}

OLD_VALID_TAGS = {'code-agents', 'observability', 'research', 'multi-agent', 'evals', 'tool-use', 'safety', 'memory', 'infra', 'planning', 'devops-agents', 'frameworks', 'cost-latency'}



def _agents():
    return edition.load("agents")


def test_keywords_equal_old_constant():
    assert list(_agents().keywords) == OLD_KEYWORDS


def test_reader_profile_equals_old_constant():
    assert _agents().reader_profile == OLD_READER_PROFILE


def test_section_rules_equal_old_constant():
    assert _agents().section_rules() == OLD_SECTION_RULES


def test_tags_equal_old_constant():
    assert _agents().valid_tags() == OLD_VALID_TAGS
    assert len(_agents().tags) == len(OLD_VALID_TAGS)  # no duplicates


def test_stage_modules_use_the_edition_values():
    import prefilter, rank, write
    assert prefilter.KEYWORDS == OLD_KEYWORDS
    assert rank.SECTION_RULES == OLD_SECTION_RULES
    assert rank.VALID_TAGS == OLD_VALID_TAGS
    assert write.READER_PROFILE == OLD_READER_PROFILE


def test_agents_edition_identity():
    ed = _agents()
    assert (ed.slug, ed.name, ed.status, ed.order) == ("agents", "AI Agents Daily", "live", 10)
