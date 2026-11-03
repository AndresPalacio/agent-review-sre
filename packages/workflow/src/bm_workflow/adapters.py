"""Optional provider boundaries; providers and credentials belong to the host.

No model, tool, or agent library is imported on module import. Each adapter is
an external step so the runtime can intercept it before provider construction.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import partial
from typing import Any

from pydantic import BaseModel, ConfigDict, create_model

from .agent_types import AgentInvocation, AgentManifest, InvocationFactory
from .definition import StepNode, step, validate_model

Mapper = Callable[[dict[str, Any]], Any]


def _validated(schema: type[BaseModel], value: Any) -> dict[str, Any]:
    return validate_model(schema, value).model_dump(mode="python", round_trip=True)


def _output_contract(output_schema: type[BaseModel], output_key: str) -> type[BaseModel]:
    """Let runtime fixtures uphold the same contract as a real provider call."""
    if not isinstance(output_key, str) or not output_key:
        raise ValueError("output_key must be a nonempty string")
    if output_key.startswith("_"):
        raise ValueError("output_key cannot start with an underscore")
    fields: dict[str, Any] = {output_key: (output_schema, ...)}
    return create_model(
        "AdapterOutput",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


async def _call(handler: Callable[..., Any], value: Any) -> Any:
    from .runtime import call_handler

    return await call_handler(handler, value)


async def _invoke(provider: Any, value: Any, invocation: AgentInvocation | None = None) -> Any:
    kwargs: dict[str, Any] = {}
    if invocation is not None:
        # Only payloads enter call_handler's deepcopy boundary. Runtime clients,
        # cancellation controls and context keep their host-owned identity.
        kwargs["config"] = dict(invocation.config)
        if invocation.context is not None:
            kwargs["context"] = invocation.context
    if callable(getattr(provider, "ainvoke", None)):
        return await _call(partial(provider.ainvoke, **kwargs), value)
    if callable(getattr(provider, "invoke", None)):
        return await _call(partial(provider.invoke, **kwargs), value)
    if callable(provider):
        return await _call(partial(provider, **kwargs), value)
    raise TypeError("provider must expose ainvoke, invoke, or be callable")


async def _agent_invocation(
    factory: InvocationFactory | None, state: dict[str, Any]
) -> AgentInvocation | None:
    if factory is None:
        return None
    from .runtime import current_context

    context = current_context()
    invocation = await _call(lambda isolated: factory(isolated, context), state)
    if not isinstance(invocation, AgentInvocation):
        raise TypeError("invocation_factory must return AgentInvocation")
    return invocation


def _agent_metadata(manifest: AgentManifest | None) -> dict[str, Any]:
    if manifest is None:
        return {}
    if not isinstance(manifest, AgentManifest):
        raise TypeError("manifest must be AgentManifest")
    return {"agent_manifest": {"revisions": dict(manifest.revisions)}}


def _metadata(
    adapter: str, output_schema: type[BaseModel], output_key: str, **extra: Any
) -> dict[str, Any]:
    if not isinstance(output_key, str) or not output_key:
        raise ValueError("output_key must be a nonempty string")
    return {
        "adapter": adapter,
        "output_key": output_key,
        "output_schema": output_schema.model_json_schema(),
        **extra,
    }


def tool_step(
    id: str,
    tool: Any,
    *,
    input_schema: type[BaseModel],
    output_schema: type[BaseModel],
    output_key: str,
    artifact_version: str = "1",
    input_mapper: Mapper | None = None,
) -> StepNode:
    """Validate mapped tool arguments and the provider's structured result."""

    async def handler(state: dict[str, Any]) -> dict[str, Any]:
        arguments = await _call(input_mapper, state) if input_mapper is not None else state
        result = await _invoke(tool, _validated(input_schema, arguments))
        return {output_key: _validated(output_schema, result)}

    return step(
        id,
        handler,
        kind="external",
        artifact_version=artifact_version,
        output_schema=_output_contract(output_schema, output_key),
        metadata=_metadata(
            "tool", output_schema, output_key, input_schema=input_schema.model_json_schema()
        ),
    )


def llm_step(
    id: str,
    model: Any,
    *,
    prompt: Mapper,
    output_schema: type[BaseModel],
    output_key: str,
    artifact_version: str = "1",
) -> StepNode:
    """Build a prompt and invoke the injected model's structured-output port."""

    async def handler(state: dict[str, Any]) -> dict[str, Any]:
        messages = await _call(prompt, state)
        structured = await _call(lambda _: model.with_structured_output(output_schema), {})
        result = await _invoke(structured, messages)
        return {output_key: _validated(output_schema, result)}

    return step(
        id,
        handler,
        kind="external",
        artifact_version=artifact_version,
        output_schema=_output_contract(output_schema, output_key),
        metadata=_metadata("llm", output_schema, output_key),
    )


def agent_step(
    id: str,
    graph: Any,
    *,
    output_schema: type[BaseModel],
    output_key: str,
    artifact_version: str = "1",
    input_mapper: Mapper | None = None,
    invocation_factory: InvocationFactory | None = None,
    manifest: AgentManifest | None = None,
) -> StepNode:
    """Run a reusable host-owned graph and validate its structured_response.

    An invocation factory supplies per-attempt config/context after dry-run
    interception. Without one, the provider receives just its legacy payload.
    """

    async def handler(state: dict[str, Any]) -> dict[str, Any]:
        invocation = await _agent_invocation(invocation_factory, state)
        arguments = await _call(input_mapper, state) if input_mapper is not None else state
        result = await _invoke(graph, arguments, invocation)
        if not isinstance(result, Mapping) or "structured_response" not in result:
            raise ValueError("agent must return structured_response")
        return {output_key: _validated(output_schema, result["structured_response"])}

    return step(
        id,
        handler,
        kind="external",
        artifact_version=artifact_version,
        output_schema=_output_contract(output_schema, output_key),
        metadata=_metadata("agent", output_schema, output_key, **_agent_metadata(manifest)),
    )


def deep_agent_step(
    id: str,
    *,
    output_schema: type[BaseModel],
    output_key: str,
    artifact_version: str = "1",
    input_mapper: Mapper | None = None,
    factory: Callable[..., Any] | None = None,
    factory_kwargs: Mapping[str, Any] | None = None,
    invocation_factory: InvocationFactory | None = None,
    manifest: AgentManifest | None = None,
) -> StepNode:
    """Create DeepAgents lazily for each invocation, after dry-run interception.

    For explicit graph reuse, build in the host lifespan and use agent_step.
    An injected factory requires no agent extras; host resources stay live.
    """
    configuration = dict(factory_kwargs or {})
    if "response_format" in configuration:
        raise ValueError("response_format is derived from output_schema")

    async def handler(state: dict[str, Any]) -> dict[str, Any]:
        invocation = await _agent_invocation(invocation_factory, state)
        create = factory
        if create is None:
            try:
                from deepagents import create_deep_agent
            except ImportError as exc:
                raise ImportError("deep_agent_step requires bm-workflow[agents]") from exc
            create = create_deep_agent
        agent = await _call(lambda _: create(**configuration, response_format=output_schema), {})
        arguments = await _call(input_mapper, state) if input_mapper is not None else state
        result = await _invoke(agent, arguments, invocation)
        if not isinstance(result, Mapping) or "structured_response" not in result:
            raise ValueError("agent must return structured_response")
        return {output_key: _validated(output_schema, result["structured_response"])}

    return step(
        id,
        handler,
        kind="external",
        artifact_version=artifact_version,
        output_schema=_output_contract(output_schema, output_key),
        metadata=_metadata("deep_agent", output_schema, output_key, **_agent_metadata(manifest)),
    )
