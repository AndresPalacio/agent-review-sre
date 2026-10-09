"""Demo educativa offline: grafo real, decisiones guionadas y evidencia local.

uv run python examples/demo_agentes.py
uv run python examples/demo_agentes.py --serve
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from langgraph.checkpoint.memory import InMemorySaver

from sre_graph.config.tracing import disable_external_tracing
from sre_graph.domain.diagnosis import Claim, Diagnosis, HypothesisOutcome, IncidentStatus
from sre_graph.domain.investigation import Hypothesis, InvestigationPlan, SpecialistTask, Verdict
from sre_graph.domain.tools import Domain
from sre_graph.evals.fixtures import FixtureBackend
from sre_graph.evals.runner import ScriptedServices
from sre_graph.evals.scenarios import load_scenario
from sre_graph.graphs.investigation.graph import make_investigation_graph
from sre_graph.graphs.investigation.services import ClaimJudgment

STAGES = {
    "intake": "Normalizar la alerta simulada de payments-api",
    "gather_context": "Seleccionar herramientas locales disponibles",
    "plan": "Plan-and-Execute: asignar la consulta al especialista Kubernetes",
    "specialist": "ReAct guionado: modelo -> k8s_list_pods -> observacion -> respuesta",
    "synthesize": "Redactar una conclusion y citar evidencia",
    "verify": "Critic: comprobar citas y revisar la afirmacion",
    "refine": "La primera conclusion no tiene respaldo: volver a planificar",
    "propose_remediation": "Proponer siguientes pasos; no ejecutar cambios",
    "report": "Construir el reporte",
    "deliver": "Finalizar; esta demo no envia mensajes",
}


class DemoServices(ScriptedServices):
    """The first synthesis deliberately cites missing evidence to show refinement."""

    def __init__(self, scenario: Any) -> None:
        super().__init__(scenario)
        self.round = 0

    async def plan(self, alert: Any, window: Any, available: Any, findings: Any) -> Any:
        self.round += 1
        return InvestigationPlan(
            hypotheses=[
                Hypothesis(
                    id="H1",
                    statement="Un contenedor fue terminado por falta de memoria",
                    confirm_if="El estado registrado contiene OOMKilled",
                    rule_out_if="No hay terminaciones OOMKilled en los datos consultados",
                )
            ],
            tasks=[
                SpecialistTask(
                    domain=Domain.KUBERNETES,
                    objective="Consultar el estado de los contenedores de payments-api",
                    hypothesis_ids=["H1"],
                )
            ],
        )

    async def synthesize(self, alert: Any, plan: Any, findings: Any, evidence: Any) -> Diagnosis:
        # The first, intentionally incorrect citation demonstrates the real verifier.
        ids = [item.id for item in evidence] if self.round > 1 else ["ev_no_recopilada"]
        return Diagnosis(
            triage_summary="payments-api presenta un contenedor que no esta listo",
            status=IncidentStatus(
                confirmed="La alerta fue recibida",
                open="Origen del consumo excesivo",
                next="Revisar limites y consumo de memoria",
                owner="Equipo SRE",
            ),
            root_cause="Terminacion OOMKilled observada; falta determinar su causa subyacente.",
            category="unknown",
            hypotheses=[
                HypothesisOutcome(
                    hypothesis_id="H1",
                    statement=plan.hypotheses[0].statement,
                    verdict=Verdict.SUPPORTS,
                    reason="El estado del contenedor registra OOMKilled",
                )
            ],
            claims=[Claim(statement="El estado consultado contiene OOMKilled", evidence_ids=ids)],
            verification=["Consultar el estado registrado de Kubernetes"],
            follow_up_questions=["Hay una fuga o un limite de memoria insuficiente?"],
            remediation_tradeoffs="Aumentar limites consume recursos; investigar requiere tiempo",
            remediation_steps=["Revisar consumo y limites antes de modificar el servicio"],
            confidence=0.8,
        )

    async def judge(self, claim: Any, evidence: Any) -> ClaimJudgment:
        supported = bool(evidence) and all(
            "OOMKilled" in json.dumps(item.data) for item in evidence
        )
        return ClaimJudgment(
            supported=supported,
            reason="Regla didactica: buscar OOMKilled en la evidencia citada; no es un juez LLM",
        )


async def run_demo() -> dict[str, Any]:
    disable_external_tracing()
    backend = FixtureBackend(load_scenario("eks", "001-oomkilled-crashloop"))
    graph = make_investigation_graph(
        DemoServices(backend.scenario),
        backend.registry,
        clock=lambda: backend.scenario.reference_time,
        checkpointer=InMemorySaver(),
    )
    thread_id = str(uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    steps = []
    async for update in graph.astream(
        {"raw_alert": backend.scenario.alert, "user_requested": True},
        config,
        context=backend.context,
        stream_mode="updates",
    ):
        for node, values in update.items():
            step = {"node": node, "description": STAGES.get(node, node)}
            if node == "verify":
                step["passed"] = values["verification"]["passed"]
            steps.append(step)
    state = (await graph.aget_state(config)).values
    return {
        "thread_id": thread_id,
        "mode": "offline_scripted",
        "steps": steps,
        "diagnosis": state["diagnosis"],
        "verification": state["verification"],
        "evidence": state["evidence"],
        "errors": state.get("errors", []),
    }


def create_demo_app() -> FastAPI:
    app = FastAPI(
        title="SRE Graph: demo offline",
        description="API educativa: sin claves, sin LLM, datos en memoria. POST sin body. "
        "Ejecuta el grafo real y espera el resultado; no es la API de produccion.",
    )
    runs: dict[str, dict[str, Any]] = {}

    @app.post("/v1/investigations")
    async def investigate() -> dict[str, str]:
        result = await run_demo()
        # Bound memory in a long-lived teaching session.
        if len(runs) >= 20:
            del runs[next(iter(runs))]
        runs[result["thread_id"]] = result
        return {"thread_id": result["thread_id"], "status": "completed"}

    @app.get("/v1/investigations/{thread_id}")
    async def get_result(thread_id: str) -> dict[str, Any]:
        if thread_id not in runs:
            raise HTTPException(404, "Demo no encontrada; ejecuta primero POST /v1/investigations")
        return {key: value for key, value in runs[thread_id].items() if key != "steps"}

    @app.get("/v1/investigations/{thread_id}/explain")
    async def explain(thread_id: str) -> dict[str, Any]:
        await get_result(thread_id)
        return {"thread_id": thread_id, "steps": runs[thread_id]["steps"]}

    return app


async def print_demo() -> None:
    print("DEMO OFFLINE: grafo real, modelo guionado, herramientas con datos locales.\n")
    result = await run_demo()
    for index, step in enumerate(result["steps"], 1):
        suffix = f" (passed={step['passed']})" if "passed" in step else ""
        print(f"{index:02}. {step['node']}: {step['description']}{suffix}")
    print(f"\nResultado: {result['diagnosis']['root_cause']}")
    print(f"Verificacion: {result['verification']['passed']}")
    print("El ejemplo verifica una observacion; no demuestra una causa raiz completa.")
    if result["errors"] or not result["verification"]["passed"]:
        raise RuntimeError(f"La demo no termino como se esperaba: {result['errors']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help="Abrir API local en puerto 8010")
    args = parser.parse_args()
    if args.serve:
        import uvicorn

        print("Abre http://127.0.0.1:8010/docs para probar los tres endpoints.")
        uvicorn.run(create_demo_app(), host="127.0.0.1", port=8010)
    else:
        asyncio.run(print_demo())
