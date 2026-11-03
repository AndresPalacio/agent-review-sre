# Inventario de retirada del producto SRE

Este inventario registra la decisión de propiedad aplicada antes de retirar el producto de
investigación de incidentes. La prueba ejecutable es `tests/test_no_sre_product.py`.

## Eliminado

- Dominio de alertas, diagnóstico, investigación, remediación, verificación, reportes RCA y
  runbooks de incidentes.
- Grafos de investigación y remediación, incluyendo acciones mutantes.
- Suites y datos de evaluación EKS/RDS, grabación, replay y comparación RCA.
- Integraciones Alertmanager, AWS, Datadog, GitHub, Grafana, Kubernetes, Loki, New Relic,
  Prometheus/Mimir, Sentry, Tempo y Wave2.
- Comandos `investigate`, `eval record`, `eval replay`, `eval compare` y suites `sre:*`.
- SDK de Kubernetes y boto3 como dependencias base.

## Conservado como producto financiero

- Análisis multiagente, equipo de personas compatible con AI Hedge Fund y evaluación de acciones.
- Chat, API `/v1`, API compatible heredada, flujos, backtests v1/v2 y cartera paper.
- Propuestas de órdenes con aprobación humana, ledger, auditoría y límites de riesgo.
- Yahoo Finance, Financial Datasets, analítica local, fixtures financieros y MCP configurado.
- Scheduler de mercado, gateways Slack/Telegram y entrega de reportes financieros.
- Harness financiero, trazas, persistencia SQLite/Postgres y paquete `bm-workflow`.

## Conservado como utilidad genérica

- Cliente HTTP seguro, registro de integraciones y metadatos tipados de herramientas.
- Evidencia y ventanas temporales usadas por el grafo financiero.
- Infraestructura de secretos, autenticación, entregas idempotentes y almacenamiento.
- Marcadores históricos `remediation:*` en migraciones/retención: son datos de compatibilidad de
  almacenamiento, no un endpoint ni un grafo SRE activo.

## Evidencia

```text
uv run pytest tests/test_no_sre_product.py -q
uv run lint-imports
uv run pytest tests/cli tests/graphs tests/gateway tests/scheduler tests/harness -q
uv run --project packages/workflow pytest packages/workflow/tests -q
```
