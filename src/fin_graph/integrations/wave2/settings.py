"""Settings classes also form the declarative setup field catalog."""

from __future__ import annotations

import re
from typing import ClassVar, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class HttpSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )
    token_required: ClassVar[bool] = True
    enabled: bool = False
    url: str | None = None
    token: SecretStr | None = None
    request_timeout_seconds: float = Field(default=15, gt=0, le=60)

    @model_validator(mode="after")
    def validate_connection(self) -> Self:
        if self.url:
            parts = urlsplit(self.url)
            if (
                parts.scheme not in {"http", "https"}
                or not parts.hostname
                or parts.username
                or parts.password
                or parts.query
                or parts.fragment
            ):
                raise ValueError("url must be HTTP(S), without credentials, query or fragment")
        if self.enabled and (not self.url or (self.token_required and not self.token)):
            raise ValueError("enabled integration requires url and credentials")
        return self


class OpsgenieSettings(HttpSettings):
    model_config = SettingsConfigDict(env_prefix="OPSGENIE_")


class IncidentIoSettings(HttpSettings):
    model_config = SettingsConfigDict(env_prefix="INCIDENT_IO_")


class JiraSettings(HttpSettings):
    model_config = SettingsConfigDict(env_prefix="JIRA_")
    email: str | None = None

    @model_validator(mode="after")
    def validate_email(self) -> Self:
        if self.enabled and (not self.email or "@" not in self.email):
            raise ValueError("Jira Cloud requires an email and API token")
        return self


class HoneycombSettings(HttpSettings):
    model_config = SettingsConfigDict(env_prefix="HONEYCOMB_")
    dataset: str | None = None
    poll_attempts: int = Field(default=5, ge=1, le=10)
    poll_interval_seconds: float = Field(default=0.5, ge=0, le=5)

    @model_validator(mode="after")
    def validate_dataset(self) -> Self:
        if self.enabled and (not self.dataset or not re.fullmatch(r"[\w-]{1,128}", self.dataset)):
            raise ValueError("Honeycomb requires a safe dataset slug")
        return self


class SearchSettings(HttpSettings):
    token_required = False
    index: str | None = None
    username: str | None = None
    password: SecretStr | None = None

    @model_validator(mode="after")
    def validate_search(self) -> Self:
        if self.enabled:
            if not self.index or not re.fullmatch(r"[a-zA-Z0-9_.*,-]{1,128}", self.index):
                raise ValueError("search requires a configured index pattern")
            if bool(self.username) != bool(self.password) or (self.token and self.username):
                raise ValueError("choose API token or complete username/password")
            if not self.token and not self.username:
                raise ValueError("search credentials are required")
        return self


class ElasticsearchSettings(SearchSettings):
    model_config = SettingsConfigDict(env_prefix="ELASTICSEARCH_")


class OpenSearchSettings(SearchSettings):
    model_config = SettingsConfigDict(env_prefix="OPENSEARCH_")


class SplunkSettings(HttpSettings):
    model_config = SettingsConfigDict(env_prefix="SPLUNK_")
    index: str | None = None
    api_version: str = Field(default="v2", pattern=r"^v[12]$")

    @model_validator(mode="after")
    def validate_index(self) -> Self:
        if self.enabled and (not self.index or not re.fullmatch(r"[\w-]{1,128}", self.index)):
            raise ValueError("Splunk requires one safe index name")
        return self


class VictoriaLogsSettings(HttpSettings):
    model_config = SettingsConfigDict(env_prefix="VICTORIA_LOGS_")
    token_required = False
    account_id: int = Field(default=0, ge=0)
    project_id: int = Field(default=0, ge=0)


class CoralogixSettings(HttpSettings):
    model_config = SettingsConfigDict(env_prefix="CORALOGIX_")


class AzureSettings(HttpSettings):
    model_config = SettingsConfigDict(env_prefix="AZURE_")
    workspace_id: str | None = None
    table: str = Field(default="AppTraces", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")

    @model_validator(mode="after")
    def validate_workspace(self) -> Self:
        if self.enabled and (
            not self.workspace_id
            or not re.fullmatch(
                r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}", self.workspace_id
            )
        ):
            raise ValueError("Azure requires a workspace GUID")
        return self


class ArgoCDSettings(HttpSettings):
    model_config = SettingsConfigDict(env_prefix="ARGOCD_")
    project: str | None = Field(default=None, pattern=r"^[\w.-]{1,128}$")
    namespace: str | None = Field(default=None, pattern=r"^[\w.-]{1,128}$")


class GitLabSettings(HttpSettings):
    model_config = SettingsConfigDict(env_prefix="GITLAB_")
    project: str | None = None

    @model_validator(mode="after")
    def validate_project(self) -> Self:
        if self.enabled and (
            not self.project
            or not re.fullmatch(r"[\w.-]+(?:/[\w.-]+)*", self.project)
            or any(part in {".", ".."} for part in self.project.split("/"))
        ):
            raise ValueError("GitLab requires a project ID or safe project path")
        return self


class HelmSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HELM_", env_file=".env", extra="ignore", env_ignore_empty=True
    )
    enabled: bool = False
    executable: str = "helm"
    namespace: str = Field(default="default", pattern=r"^[a-z0-9][a-z0-9.-]{0,62}$")
    context: str | None = None
    kubeconfig: str | None = None
    request_timeout_seconds: float = Field(default=15, gt=0, le=60)

    @model_validator(mode="after")
    def validate_options(self) -> Self:
        for value in (self.executable, self.context, self.kubeconfig):
            if value is not None and (not value or any(ord(char) < 32 for char in value)):
                raise ValueError("Helm configuration contains an invalid value")
        return self


WAVE2_SETTINGS: dict[str, type[BaseSettings]] = {
    "opsgenie": OpsgenieSettings,
    "incident_io": IncidentIoSettings,
    "jira": JiraSettings,
    "honeycomb": HoneycombSettings,
    "elasticsearch": ElasticsearchSettings,
    "opensearch": OpenSearchSettings,
    "splunk": SplunkSettings,
    "victoria_logs": VictoriaLogsSettings,
    "coralogix": CoralogixSettings,
    "azure": AzureSettings,
    "argocd": ArgoCDSettings,
    "helm": HelmSettings,
    "gitlab": GitLabSettings,
}
