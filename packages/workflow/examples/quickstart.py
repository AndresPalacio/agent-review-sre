"""Ejemplo offline: contratos, registro, persistencia y simulación."""

from pydantic import BaseModel, ConfigDict

from bm_workflow import (
    InMemoryRunStore,
    RunService,
    RunStatus,
    WorkflowRegistry,
    compile_workflow,
    dry_run,
    typed_step,
    workflow,
)


class Number(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


def increment(number: Number) -> Number:
    return Number(value=number.value + 1)


def double(number: Number) -> Number:
    return Number(value=number.value * 2)


def main() -> None:
    definition = workflow(name="numbers", version="1", input=Number, output=Number).flow(
        typed_step(
            "increment",
            increment,
            input_schema=Number,
            output_schema=Number,
            artifact_version="1",
        ),
        typed_step(
            "double",
            double,
            input_schema=Number,
            output_schema=Number,
            artifact_version="1",
        ),
    )
    compiled = compile_workflow(definition)
    registry = WorkflowRegistry([compiled])

    result = registry.run(name="numbers", version="1", input_data={"value": 2})
    assert result.status == RunStatus.COMPLETE
    assert isinstance(result.output, Number)
    assert result.output.value == 6
    print("Resultado:", result.output.model_dump())
    print("Digest:", compiled.definition_digest)

    service = RunService(
        registry,
        InMemoryRunStore(),
        artifact_revision="quickstart-1",
        namespace="demo",
    )
    accepted = service.admit(name="numbers", version="1", input_data={"value": 2})
    terminal = service.execute(accepted.run_id, expected_version=accepted.version)
    print("Run persistido:", terminal.run_id, terminal.state)

    # Cada fixture sustituye un nodo completo por sus actualizaciones de estado.
    simulation = dry_run(
        compiled,
        {"value": 2},
        fixtures={"increment": {"value": 3}, "double": {"value": 6}},
    )
    assert simulation.result.status == RunStatus.COMPLETE
    print("Simulación:", simulation.result.output)
    print("Eventos:", len(simulation.events))


if __name__ == "__main__":
    main()
