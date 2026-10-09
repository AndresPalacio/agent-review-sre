# Demo de agentes sin credenciales

Desde la raíz del repositorio, ejecuta:

```powershell
uv run python examples/demo_agentes.py
```

Necesitas Python 3.12 y `uv`, como el proyecto. La primera ejecución puede descargar
las dependencias del proyecto; después la demo trabaja con datos locales.
No requiere `.env`, Docker, Kubernetes, Datadog ni una clave de un proveedor LLM.

## Qué vas a ver

El incidente predefinido es un contenedor de `payments-api` que registra
`OOMKilled`. El ejemplo ejecuta el grafo de investigación real:

```text
Alerta → contexto → plan → especialista → herramienta local → síntesis
                                                               ↓
                                primera verificación: cita inexistente
                                                               ↓
                              refinar → plan → especialista → síntesis
                                                               ↓
                                segunda verificación: evidencia citada
                                                               ↓
                                               propuesta → reporte
```

- **Workflow determinista:** el grafo controla el orden y la repetición.
- **Plan-and-Execute:** se plantea una hipótesis y una tarea para Kubernetes.
- **ReAct:** un modelo guionado solicita `k8s_list_pods`, recibe el resultado
  de una herramienta real respaldada por un cliente simulado y termina su turno.
- **Critic:** la primera síntesis cita deliberadamente un ID inexistente.
  El verificador real rechaza la cita. La segunda usa el ID recopilado y una regla
  didáctica comprueba que los datos contienen `OOMKilled`.

El plan y la síntesis están predefinidos; el juez es una regla, no un LLM.
La segunda ronda repite la consulta para enseñar el ciclo, no descubre nuevos datos.
Se verifica una observación, no la causa subyacente del exceso de memoria.
Esta demo muestra un especialista, no el supervisor de chat ni un swarm.
No ejecuta remediaciones ni envía reportes a servicios externos.

## Probar por endpoint

```powershell
uv run python examples/demo_agentes.py --serve
```

Abre <http://127.0.0.1:8010/docs>. En cada operación usa **Try it out → Execute**:

1. `POST /v1/investigations`: sin body ni credenciales. Ejecuta el caso completo
   y devuelve `thread_id`.
2. `GET /v1/investigations/{thread_id}`: pega ese ID para consultar evidencia,
   diagnóstico y verificación.
3. `GET /v1/investigations/{thread_id}/explain`: muestra las etapas recorridas;
   busca `verify` con `passed: false`, luego `refine` y finalmente `passed: true`.

También puedes usar otra terminal PowerShell:

```powershell
$demo = Invoke-RestMethod -Method Post http://127.0.0.1:8010/v1/investigations
Invoke-RestMethod "http://127.0.0.1:8010/v1/investigations/$($demo.thread_id)/explain" |
    ConvertTo-Json -Depth 10
```

Esta API educativa es independiente de la API de producción: usa los mismos
 nombres de tres rutas, pero espera el resultado del POST (HTTP 200), simplifica
las respuestas y conserva solo las últimas 20 ejecuciones en memoria. Al cerrar
con `Ctrl+C`, se pierden. Solo escucha en `127.0.0.1:8010`.
