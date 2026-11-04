from __future__ import annotations

import ast
from pathlib import Path

from fin_graph.agents.catalog import agent_catalog

SRC = Path(__file__).resolve().parents[2] / "src" / "fin_graph"


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _deepagent_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    function_aliases: set[str] = set()
    module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "deepagents":
            function_aliases.update(
                item.asname or item.name for item in node.names if item.name == "create_deep_agent"
            )
        elif isinstance(node, ast.Import):
            module_aliases.update(
                item.asname or item.name for item in node.names if item.name == "deepagents"
            )
    return function_aliases, module_aliases


def _is_create_deep_agent_call(
    node: ast.AST,
    function_aliases: set[str],
    module_aliases: set[str],
) -> bool:
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Name):
        return node.func.id in function_aliases
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_deep_agent"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in module_aliases
    )


def _create_deep_agent_call_paths(root: Path) -> set[str]:
    found: set[str] = set()
    for path in _python_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        function_aliases, module_aliases = _deepagent_aliases(tree)
        if any(
            _is_create_deep_agent_call(node, function_aliases, module_aliases)
            for node in ast.walk(tree)
        ):
            found.add(path.relative_to(root).as_posix())
    return found


def _create_deep_agent_factory_paths(root: Path) -> set[str]:
    factories: set[str] = set()
    for path in _python_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        function_aliases, module_aliases = _deepagent_aliases(tree)
        if not function_aliases and not module_aliases:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_"):
                continue
            if any(
                _is_create_deep_agent_call(child, function_aliases, module_aliases)
                for child in ast.walk(node)
            ):
                module = ".".join(("fin_graph", *path.relative_to(root).with_suffix("").parts))
                factories.add(f"{module}:{node.name}")
    return factories


def _package_imports(root: Path) -> set[str]:
    imports: set[str] = set()
    for path in _python_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            for name in names:
                parts = name.split(".")
                if len(parts) >= 2 and parts[0] == "fin_graph":
                    imports.add(".".join(parts[:2]))
    return imports


def _is_langchain_create_agent_call(
    node: ast.AST,
    function_aliases: set[str],
    langchain_aliases: set[str],
    agents_aliases: set[str],
) -> bool:
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Name):
        return node.func.id in function_aliases
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "create_agent":
        return False
    owner = node.func.value
    if isinstance(owner, ast.Name):
        return owner.id in agents_aliases
    return (
        isinstance(owner, ast.Attribute)
        and owner.attr == "agents"
        and isinstance(owner.value, ast.Name)
        and owner.value.id in langchain_aliases
    )


def _langchain_create_agent_references(root: Path) -> set[str]:
    found: set[str] = set()
    for path in _python_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        function_aliases: set[str] = set()
        langchain_aliases: set[str] = set()
        agents_aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "langchain.agents":
                function_aliases.update(
                    item.asname or item.name for item in node.names if item.name == "create_agent"
                )
            elif isinstance(node, ast.ImportFrom) and node.module == "langchain":
                agents_aliases.update(
                    item.asname or item.name for item in node.names if item.name == "agents"
                )
            elif isinstance(node, ast.Import):
                for item in node.names:
                    if item.name == "langchain":
                        langchain_aliases.add(item.asname or item.name)
                    elif item.name == "langchain.agents":
                        if item.asname:
                            agents_aliases.add(item.asname)
                        else:
                            langchain_aliases.add("langchain")
        has_qualified_call = any(
            _is_langchain_create_agent_call(
                node,
                function_aliases,
                langchain_aliases,
                agents_aliases,
            )
            for node in ast.walk(tree)
        )
        if function_aliases or has_qualified_call:
            found.add(path.relative_to(root).as_posix())
    return found


def test_create_deep_agent_calls_live_only_under_agents() -> None:
    assert _create_deep_agent_call_paths(SRC) == {
        "agents/analysis/persona_team.py",
        "agents/chat/supervisor.py",
        "agents/common/factory.py",
        "agents/solver/supervisor.py",
        "agents/optimizer/proposer.py",
    }


def test_fin_graph_has_no_direct_langchain_agent_factory_imports() -> None:
    assert _langchain_create_agent_references(SRC) == set()


def test_agents_do_not_depend_on_workflows_runtime_or_work() -> None:
    assert _package_imports(SRC / "agents").isdisjoint(
        {"fin_graph.workflows", "fin_graph.runtime", "fin_graph.work"}
    )


def test_comments_do_not_count_as_deepagent_calls(tmp_path: Path) -> None:
    (tmp_path / "comment.py").write_text("# create_deep_agent()\n", encoding="utf-8")
    (tmp_path / "aliased.py").write_text(
        "from deepagents import create_deep_agent as build\nbuild()\n", encoding="utf-8"
    )

    assert _create_deep_agent_call_paths(tmp_path) == {"aliased.py"}


def test_qualified_deepagent_calls_are_detected(tmp_path: Path) -> None:
    (tmp_path / "qualified.py").write_text(
        "import deepagents\ndeepagents.create_deep_agent()\n",
        encoding="utf-8",
    )
    (tmp_path / "module_alias.py").write_text(
        "import deepagents as da\nda.create_deep_agent()\n",
        encoding="utf-8",
    )

    assert _create_deep_agent_call_paths(tmp_path) == {
        "module_alias.py",
        "qualified.py",
    }


def test_qualified_langchain_create_agent_calls_are_detected(tmp_path: Path) -> None:
    fixtures = {
        "root_module.py": "import langchain\nlangchain.agents.create_agent()\n",
        "root_alias.py": "import langchain as lc\nlc.agents.create_agent()\n",
        "agents_module.py": ("import langchain.agents\nlangchain.agents.create_agent()\n"),
        "agents_alias.py": ("import langchain.agents as agents\nagents.create_agent()\n"),
        "from_agents_alias.py": ("from langchain.agents import create_agent as build\n"),
        "from_langchain_alias.py": (
            "from langchain import agents as lc_agents\nlc_agents.create_agent()\n"
        ),
    }
    for filename, source in fixtures.items():
        (tmp_path / filename).write_text(source, encoding="utf-8")

    assert _langchain_create_agent_references(tmp_path) == set(fixtures)


def test_non_target_create_agent_references_are_ignored(tmp_path: Path) -> None:
    fixtures = {
        "comment.py": "# langchain.agents.create_agent()\n",
        "local.py": "def create_agent():\n    return None\ncreate_agent()\n",
        "other_package.py": ("import other_package.agents as agents\nagents.create_agent()\n"),
        "other_langchain_call.py": (
            "import langchain.agents\nlangchain.agents.some_other_factory()\n"
        ),
    }
    for filename, source in fixtures.items():
        (tmp_path / filename).write_text(source, encoding="utf-8")

    assert _langchain_create_agent_references(tmp_path) == set()


def test_every_deepagent_factory_is_catalogued() -> None:
    catalogued = {item.factory_path for item in agent_catalog()}
    factories = _create_deep_agent_factory_paths(SRC)

    assert factories == {
        "fin_graph.agents.analysis.persona_team:make_persona_agent",
        "fin_graph.agents.chat.supervisor:make_chat_graph",
        "fin_graph.agents.common.factory:make_agent",
        "fin_graph.agents.optimizer.proposer:make_optimizer_agent",
        "fin_graph.agents.solver.supervisor:make_solver_child",
    }
    assert factories <= catalogued
