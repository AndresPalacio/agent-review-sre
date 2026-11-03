# Despliegue remoto

El perfil remoto usa Postgres y la API nativa autenticada. Termina TLS en un proxy confiable, limita
CORS y no publiques la API compatible sin una capa adicional de autenticación.

## Compose distribuido

```powershell
$env:BOOTSTRAP_ADMIN_KEY = "genere-una-clave-aleatoria-de-32-caracteres-o-mas"
docker compose --profile distributed up --build -d --wait
```

La API queda en `http://127.0.0.1:8001` según el mapeo de Compose. En producción cambia las
credenciales Postgres, usa secretos del orquestador, no montes `.env` dentro de la imagen y publica
solo el proxy TLS.

## Requisitos de seguridad

- `DATABASE_URL` debe apuntar a Postgres y `BOOTSTRAP_ADMIN_KEY` tener al menos 32 caracteres.
- Crea claves viewer/operator/approver/admin con `POST /v1/api-keys` y rota el bootstrap.
- Configura `CORS_ORIGINS` con orígenes exactos; evita `*` con credenciales.
- Mantén `FIN_GRAPH_ALLOW_INSECURE_LEGACY_API=false` para binds remotos.
- Configura `FIN_GRAPH_TRUSTED_PROXIES` solo con redes del proxy.
- Usa HMAC para webhooks y allowlists de usuarios para gateways.
- Mantén short selling y autoaprobación deshabilitados salvo decisión explícita.

## Operación

`/v1/health` es liveness y `/v1/ready` readiness. El scheduler elige un líder mediante advisory
lock; los locks de runs, retención, claves y ledger requieren Postgres para múltiples réplicas.
SQLite informa `multi_worker=false` y no debe compartirse entre contenedores.

Antes de promover una imagen ejecuta las pruebas, construye el wheel, valida Compose y ejecuta el
runner de aceptación con el mismo backend. Los smokes contra LLM/datos reales se habilitan aparte y
sus resultados no sustituyen la aceptación offline.
