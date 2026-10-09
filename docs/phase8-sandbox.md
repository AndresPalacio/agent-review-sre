# Guía de aceptación posterior en sandbox

Estado al 2026-10-08: el usuario aún no tiene credenciales de proveedores. Esta guía **no se ha ejecutado**: no se ha creado un cluster, instalado charts, consultado un LLM real, enviado mensajes ni modificado recursos externos. La aceptación local está en [phase8-acceptance.md](phase8-acceptance.md). Esta guía describe el trabajo que el operador podrá ejecutar cuando tenga un sandbox y elija hacerlo; no constituye evidencia de validación real.

## Preparación y configuración

Requisitos futuros: Docker funcionando; kind, kubectl y Helm en PATH; credencial LLM con presupuesto; imágenes y charts fijados por el operador; namespace exclusivo `sre-demo`; Postgres local; kubeconfig exclusivo del sandbox. Preflight solo comprueba presencia, nunca credenciales o disponibilidad:

```powershell
uv run python scripts/phase8_acceptance.py preflight
uv run python scripts/phase8_acceptance.py load
uv run sre-graph integrations setup --help
uv run sre-graph eval run --help
```

Copia `.env.example` a `.env` únicamente si `.env` todavía no existe. Introduce credenciales por un mecanismo local seguro; no las incluyas en comandos, archivos versionados o capturas. Configura `LLM_PROVIDER` y modelos por rol que el proveedor confirme disponibles; deja `LANGSMITH_TRACING=false`, `AUTO_APPROVE_TOOLS` vacío, y una clave `BOOTSTRAP_ADMIN_KEY` aleatoria de al menos 32 caracteres. El servidor requiere `DATABASE_URL` Postgres. La copia del ejemplo contiene una URL local para `docker compose`; eso no verifica conectividad.

El wizard valida y cambia únicamente los campos propuestos. Ejemplo sin secretos para Kubernetes, guardado por el operador como `kubernetes.sandbox.json`:

```json
{"enabled":true,"kubeconfig":"C:/ruta/al/kubeconfig-sandbox","context":"kind-sre-graph-sandbox","verify_namespace":"sre-demo"}
```

```powershell
uv run sre-graph integrations setup kubernetes --env-file .env --input-file kubernetes.sandbox.json --dry-run
uv run sre-graph integrations setup kubernetes --env-file .env --input-file kubernetes.sandbox.json --verify
uv run sre-graph integrations list --env-file .env
uv run sre-graph integrations verify --env-file .env
```

Para Prometheus/Loki usa sus URLs reales accesibles desde el proceso Python, incluyendo puertos reenviados si procede. Un archivo de setup puede referenciar un secreto mediante `{"token":{"env":"SANDBOX_PROMETHEUS_TOKEN"}}`; nunca escribas el token literal en el archivo. Usa `--verify` únicamente cuando ya hayas elegido consultar ese sandbox. Las integraciones no configuradas permanecen desactivadas. Consulta [wave2](integrations-wave2.md) para campos nuevos; listar/verificar MCP con `--mcp-config` puede iniciar los servidores MCP configurados, por lo que se debe usar solo con configuración revisada.

## Calidad RCA: 36 escenarios con modelo real

Los escenarios usan fixtures de integraciones; el modelo real sí llama al proveedor LLM. Ejecuta estas operaciones solo cuando haya una credencial y presupuesto. El operador fija el mismo `provider:model`, juez opcional y tres repeticiones para ambas corridas. No se prescribe un ID posiblemente obsoleto:

```powershell
$Model = '<provider:model confirmado por el operador>'
$Judge = '<provider:model del juez confirmado>'
uv run sre-graph eval run --suite all --model $Model --judge-model $Judge --repeat 3 --output-dir .eval-results/sandbox-baseline
uv run sre-graph eval run --suite all --model $Model --judge-model $Judge --repeat 3 --output-dir .eval-results/sandbox-current
uv run sre-graph eval compare .eval-results/sandbox-baseline/results.json .eval-results/sandbox-current/results.json
```

`all` cubre los 36 escenarios. Revisa que cada resultado tenga 108 intentos completados, cero fallos/bloqueos y `baseline_eligible=true`, con scores/citas/trayectorias, tokens y latencia registrados cuando disponibles. El primer baseline debe revisarse frente a las referencias independientes; conservar un baseline malo no demuestra calidad. Comparar exige mismo corpus/fingerprint, suite, modelos y repeticiones; una regresión devuelve exit no cero. Resultados `scripted` nunca sirven de baseline de calidad. Para una investigación real grabada, usa `eval record --answer-file ... --reviewer ...` con una respuesta suministrada independientemente del RCA, y `eval replay` para lecturas estrictas; no derives ground truth del reporte del agente.

## Demo kind con métricas y logs

Estos son comandos futuros de creación/modificación de un sandbox, no ejecutados por esta sesión. Verifica antes que el contexto indicado corresponde al sandbox elegido:

```powershell
kind create cluster --name sre-graph-sandbox
kubectl --context kind-sre-graph-sandbox create namespace sre-demo
kubectl --context kind-sre-graph-sandbox cluster-info
```

Instala kube-prometheus-stack y Loki mediante charts locales previamente revisados y versiones fijadas por el operador. No se proporcionan números de versión ni values universales: dependen del Kubernetes elegido. Conserva los paquetes, imágenes/digests y values para repetir el experimento:

```powershell
helm upgrade --install monitoring '<ruta-chart-kube-prometheus-stack-fijado>' --kube-context kind-sre-graph-sandbox --namespace sre-demo -f '<values-monitoring-sandbox.yaml>' --wait
helm upgrade --install logs '<ruta-chart-Loki-fijado>' --kube-context kind-sre-graph-sandbox --namespace sre-demo -f '<values-Loki-sandbox.yaml>' --wait
kubectl --context kind-sre-graph-sandbox -n sre-demo get pods,services
```

Configura el colector de logs compatible con tu Loki para ingerir stdout del namespace. Descubre los Services creados por esos charts y usa port-forward hacia el proceso Python; no presupongas sus nombres. Configura y verifica Prometheus y Loki con el wizard. Debe existir evidencia de consultas sanas y logs del workload antes de inyectar el fallo.

Despliega un workload pequeño con una imagen conocida y fijada, guardando su YAML y estado sano. Ejemplo futuro para una imagen HTTP que el operador seleccione:

```powershell
$ImageGood = '<imagen:tag o digest revisado>'
kubectl --context kind-sre-graph-sandbox -n sre-demo create deployment checkout --image=$ImageGood
kubectl --context kind-sre-graph-sandbox -n sre-demo rollout status deployment/checkout --timeout=120s
kubectl --context kind-sre-graph-sandbox -n sre-demo rollout history deployment/checkout
```

Añade una regla Prometheus que detecte replicas no disponibles del deployment y labels `service=checkout`, `namespace=sre-demo`, `cluster=sre-graph-sandbox`. Inyecta exclusivamente en ese deployment una imagen inexistente que el operador elija. Conserva las revisiones sana y fallida y los eventos `ImagePullBackOff`/`ErrImagePull`. No asumas números de revisión: consulta rollout history y la anotación actual.

## Webhook, UI, reporte y aprobación

Arranca Postgres local y el servidor con la configuración de sandbox. Compila la UI como se indica en el [README](../README.md#consola-web), o usa Vite durante desarrollo:

```powershell
docker compose up -d --wait
uv run sre-graph serve
```

Configura `WEBHOOK_TOKENS` para `alertmanager` y selecciona una URL `/v1/alerts/alertmanager` accesible desde el cluster. El verificador requiere `X-SRE-Webhook-Token`: si la configuración de Alertmanager elegida no permite ese header, usa un proxy de entrada controlado que lo añada desde un Secret y limite origen/ruta; no desactives autenticación ni presupongas que un header Authorization es equivalente. Comprueba la conectividad cluster a API y registra que fue **Alertmanager**, no un POST manual, quien disparó la investigación.

Antes de esa prueba puedes verificar el contrato con un POST manual del operador:

```powershell
$Payload = @{version='4';groupKey='sandbox-checkout';status='firing';commonLabels=@{alertname='CheckoutUnavailable';service='checkout';namespace='sre-demo'};alerts=@(@{status='firing';labels=@{alertname='CheckoutUnavailable';service='checkout';namespace='sre-demo'};startsAt=(Get-Date).ToUniversalTime().ToString('o')})} | ConvertTo-Json -Depth 8
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/alerts/alertmanager -ContentType application/json -Headers @{'X-SRE-Webhook-Token'=$env:SANDBOX_WEBHOOK_TOKEN} -Body $Payload
```

La respuesta incluye thread/run IDs. Abre la UI y autentícate con una API key provisionada; verifica timeline, hipótesis, evidencia K8s/Prometheus/Loki y reporte. Consulta `/v1/investigations/{thread_id}` para contrastar citas y estado; `/v1/threads/{thread_id}/stream` admite `Last-Event-ID`. Reenviar la misma alerta dentro del periodo de dedupe debe devolver el hilo existente.

La remediación es una operación separada del RCA. En la UI el operador prepara `k8s_rollout_undo` para `namespace=sre-demo`, `deployment=checkout`, `target_revision=<sana>` y `expected_revision=<fallida actual>`. Comprueba argumentos y dry-run antes de decidir. API equivalente: `POST /v1/remediations` con `{"tool":"k8s_rollout_undo","args":{...}}`; sigue `GET /v1/remediations/{thread_id}` y aprueba en `/v1/remediations/{thread_id}/approve`, usando la API key approver/admin allowlisted y los `approval_ids` vigentes. La aprobación efectiva es una decisión explícita del operador, nunca parte de la preparación automática de esta guía.

Comprueba estado de aprobación, resultado, verificación de salud del deployment y recuperación de la regla Prometheus. Si se propone rollback, necesita una nueva aprobación. Un resultado `uncertain` requiere inspección/reconciliación; no repitas la mutación automáticamente. Registra la auditoría de aprobación sin incluir claves.

Slack es **opcional y solo si el operador elige un workspace/canal sandbox**. Configura sus credenciales y rutas de entrega; para botones configura además gateway y IDs allowlisted. Valida permisos y firma/Socket Mode conforme a [fase 6](progress/2026-10-08-fase-6.md). Solo después de esa elección ejecuta el flujo que envía reporte/solicitud al canal. No actives rutas predeterminadas a canales de producción.

## Reinicio y multi-réplica

Repite con un hilo suficientemente largo para interrumpir una investigación: detén el servidor, conserva Postgres, reinicia, espera reconciliación y verifica `interrupted` o `completed` según checkpoint. Para `interrupted`, el operador solicita `POST /v1/threads/{thread_id}/resume`; el mismo checkpoint debe continuar y generar un reporte, sin dos resumes aceptados simultáneamente. Si no hay checkpoint, inspecciona el fallo en lugar de asumir reanudación.

Reinicia también con una remediación pendiente, antes de aprobar. Debe conservarse la solicitud y no ejecutar cambios al arrancar. Después de aprobar, comprueba el número de revisiones y la action ledger: no debe repetir una operación completada. Para corte durante ejecución, registra el estado incierto y verifica el recurso antes de cualquier nueva acción.

Finalmente inicia dos servidores con la misma base y distintos puertos: verifica un único líder scheduler, apagado del líder a promoción automática, y SSE en B de una investigación iniciada en A. Las lecturas SSE deben reanudarse por cursor tras reconexión. El trigger manual de schedules sobre standby conserva respuesta 503; úsalo en el líder. Estas pruebas son externas pendientes; las regresiones locales Postgres se documentan en [aceptación offline](phase8-acceptance.md).

Conserva como evidencia: versiones/configuración sin secretos, timestamp y operador, resultados de los 36 escenarios, JSON de comparación, IDs de hilos, capturas sanitizadas, evidencia/citas, revisión sana/fallida/final, aprobación y verificaciones de reinicio. Marca cada paso `pasó`, `falló` o `no ejecutado`. Hasta disponer de credenciales y efectuar esta guía, calidad LLM real, conectividad/privilegios, entrega Slack y recuperación de workloads reales siguen **no ejecutados**.
