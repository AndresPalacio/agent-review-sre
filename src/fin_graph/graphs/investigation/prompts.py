"""Editable reasoning instructions; security policies remain in services/middleware."""

PLAN_PROMPT = (
    "Propose 1-4 falsifiable hypotheses and one concrete task per useful available domain."
)
SPECIALIST_PROMPT = "Return a finding grounded in the observed evidence."
SYNTHESIS_PROMPT = (
    "Write an incident-command diagnosis. Keep unsupported causes unknown and cite evidence ids."
)
JUDGE_PROMPT = "Decide whether these specific observations support the factual claim. Be skeptical."
