"""Curated playbooks exposed by exact identifier."""

from __future__ import annotations

SKILLS = {
    "investigating-incidents": (
        "Investiga incidentes con evidencia verificable; usa un runbook solo como guía.",
        "# Investigación de incidentes\n"
        "1. Define la alerta, el servicio afectado y la ventana temporal.\n"
        "2. Consulta métricas, logs, eventos y cambios con herramientas de solo lectura.\n"
        "3. Contrasta hipótesis con evidencia y cita sus identificadores.\n"
        "4. Expresa incertidumbre si la evidencia no sostiene una causa.\n"
        "5. Propón remedios con riesgo, radio de impacto y rollback. "
        "Toda acción mutante requiere aprobación humana.\n",
    )
}


def load_skill(skill_id: str, *, full: bool = False) -> str:
    """Reveal a short description first, then the full trusted playbook on request."""
    return SKILLS[skill_id][1 if full else 0]
