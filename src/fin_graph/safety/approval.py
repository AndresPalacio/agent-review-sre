"""Which tool calls need a human decision before they run."""

from __future__ import annotations

from dataclasses import dataclass

from fin_graph.config.settings import Settings
from fin_graph.domain.tools import SideEffectLevel, ToolMeta

#: The only environments where ``auto_approve`` applies; any other name counts as production.
NON_PRODUCTION_ENVIRONMENTS = frozenset({"dev", "development", "local", "test", "staging"})


@dataclass(frozen=True)
class ApprovalPolicy:
    """Read-only calls run freely; everything else waits for a human.

    Tools named in ``auto_approve`` skip the wait only in a known
    non-production environment. Any other name (prod, production, a typo)
    counts as production, where nothing that changes state is ever
    auto-approved. A tool that declares no metadata is treated as able to
    change state.
    """

    environment: str = "dev"
    auto_approve: frozenset[str] = frozenset()
    allowed_approvers: frozenset[str] = frozenset()

    @classmethod
    def from_settings(cls, settings: Settings) -> ApprovalPolicy:
        """Build from ``ENVIRONMENT`` and ``AUTO_APPROVE_TOOLS``."""
        return cls(
            environment=settings.environment,
            auto_approve=frozenset(settings.auto_approve_tools),
            allowed_approvers=frozenset(settings.approver_ids),
        )

    def allows_approver(self, actor: str | None, role: str | None) -> bool:
        """Require a privileged role and, when configured, an exact actor allowlist match."""
        return bool(
            actor
            and role in {"approver", "admin"}
            and (not self.allowed_approvers or actor in self.allowed_approvers)
        )

    def requires_approval(self, tool_name: str, meta: ToolMeta | None) -> bool:
        """Whether a call to ``tool_name`` must pause for a human decision."""
        if meta is not None and meta.side_effect_level is SideEffectLevel.READ_ONLY:
            return False
        if self.environment not in NON_PRODUCTION_ENVIRONMENTS:
            return True
        return tool_name not in self.auto_approve
