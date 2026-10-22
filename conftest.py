"""Register eval CLI flags before pytest resolves its testpaths/collection roots."""


def pytest_addoption(parser):
    parser.addoption("--harness-model", default="scripted")
    parser.addoption("--harness-repeat", type=int, default=None)
    parser.addoption("--harness-config", default="harness/sre.toml")
    parser.addoption("--evals-report-file", default=None)
