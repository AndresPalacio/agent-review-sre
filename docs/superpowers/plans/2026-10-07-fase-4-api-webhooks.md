# Fase 4 — API FastAPI y webhooks

Diseño rector: `docs/specs/2026-10-07-sre-graph-design.md`, sección API y fila 4.
Rama de implementación: `fase-4`, creada desde `main` tras integrar fase 3. Se usa
una rama normal; se conserva el worktree preexistente y los archivos ajenos.

## Alcance y decisiones

El servidor exige Postgres en operación y monta un grafo compilado una sola vez con
checkpointer. `RunManager` limita concurrencia y serializa cada hilo, registra corridas
en SQL, transmite eventos SSE con cursor y recupera corridas interrumpidas usando el
checkpoint. Al reiniciar no reenvía automáticamente una alerta: el operador reanuda.
Las pruebas inyectan un grafo guionado y SQLite para verificar la API sin proveedores.

Se crean tablas propias con SQLAlchemy async y migración Alembic: `runs`,
`alert_fingerprints`, `api_keys`, `deliveries` y `schedules` (reservada para fase 6).
El contenido de la alerta se guarda solo en el checkpoint; las filas de API contienen
metadatos, estado y eventos de progreso acotados. API keys aleatorias se comparan por
hash SHA-256, con roles viewer/operator/approver/admin. Una clave bootstrap opcional
se carga desde secreto de entorno sin escribirla en logs. Ninguna ruta operativa queda
abierta sin clave. CORS es una allowlist explícita.

Los ocho webhooks verifican autenticación antes de parsear o deduplicar: token fijo o
HMAC según proveedor. CloudWatch SNS exige TopicArn permitido y firma RSA de SNS;
el certificado se obtiene solo de un host HTTPS SNS permitido. No se siguen URLs de
confirmación. El parser recibe la fuente obligatoria y rechaza payloads incompatibles.
La deduplicación es transaccional por `source + fingerprint` y ventana configurada.

La fase 4 expone investigación, listado/historial, SSE, reanudación, integraciones y
health/ready. Chat y aprobaciones HITL llegan en fase 5; schedules y gateways en 6;
la UI estática en 7. El endpoint de chat se deja sin registrar, sin simular capacidad.
Dockerfile empaqueta solo la app; no se despliega infraestructura.

## Tareas y verificación

1. Añadir dependencias FastAPI, Uvicorn, SQLAlchemy, Alembic y cryptography. Test de
   configuración fail-closed y migración SQLite/Postgres; implementar modelo y repo.
2. Testear auth y firma de los ocho webhooks, límites de cuerpo y source mismatch;
   implementar verificación y dedupe atómico.
3. Testear RunManager con semáforo, lock, eventos, estado, recuperación y resume;
   implementar usando el grafo/checkpointer y context fresco por corrida.
4. Test ASGI de rutas y SSE con alerta Alertmanager que produce reporte offline;
   implementar API, lifespan, integraciones y health/ready.
5. Añadir `serve`, Dockerfile y README de configuración. Registrar evidencia de fase
   3 integrada y fase 4. Revisar independientemente y corregir hallazgos.
6. Gate: pytest general y Postgres local obligatorios, Ruff, formato, mypy,
   import-linter, smoke offline, build del wheel y prueba de arranque ASGI.

Servicios y credenciales externos no forman parte del gate local; se documentarán
como validación pendiente, sin afirmar que se probaron.
