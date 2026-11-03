"""Editable reasoning instructions; safety policies stay in services, middleware and the graph.

Short and literal on purpose: they must work on a 3B local model, which follows a few direct
rules far better than a long essay. Prompts are in English (what small models follow best); the
report is rendered in Spanish.
"""

EXTRACT_PROMPT = (
    "Read the user's message. If it asks about companies, stocks or markets, list the ticker "
    "symbols it refers to. Greetings and thanks are noise."
)
PLAN_PROMPT = (
    "Propose 1-4 falsifiable investment hypotheses about the tickers and one task per useful "
    "available analyst. Each hypothesis says what would confirm it and what would refute it."
)
ANALYST_PROMPT = (
    "You receive scores already computed from market data. Do not recompute or invent numbers. "
    "Explain in two or three sentences what the scores show, and say for each hypothesis "
    "whether the evidence supports, refutes or leaves it inconclusive, citing evidence ids."
)
SYNTHESIS_PROMPT = (
    "Write one investment thesis for the ticker from the analysts' signals and the evidence. "
    "Give the stance, a category from the taxonomy, the bull case, the bear case, the risks "
    "and factual claims. Every claim must cite the evidence ids it states. If the analysts "
    "disagree, say so. Never claim a price target."
)
NARRATIVE_PROMPT = (
    "The stance and the category were decided from the scores: do not change them. Write in "
    "Spanish: a two or three sentence summary, the bull case, the bear case, catalysts and "
    "risks, and up to three factual claims. Each claim states one number from an analyst's "
    "numbers and cites that analyst's evidence_id. Never claim a price target."
)
JUDGE_PROMPT = (
    "Decide whether the cited evidence supports this specific claim. Compare the numbers. "
    "Be skeptical: a claim the evidence does not state is not supported."
)
PORTFOLIO_PROMPT = (
    "Pick exactly one of the allowed actions for the ticker and a quantity no larger than its "
    "maximum. Prefer hold when the thesis is weak, unverified or neutral. No cash or margin "
    "math: the limits are already applied. Reason in one short sentence."
)
