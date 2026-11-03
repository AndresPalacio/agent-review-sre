# Supervisor de chat

## Entrada

Mensajes del usuario, contexto autenticado, registro de integraciones, cartera y política de
aprobación.

## Estado

El hilo persistente guarda mensajes y resultados de herramientas; secretos y clientes permanecen
en el contexto de ejecución.

## Agentes

`make_chat_graph` crea el supervisor conversacional. Puede consultar especialistas o proponer una
orden, pero no omite las reglas deterministas de trading.

## Herramientas

Lecturas financieras, memoria de tesis, skills/playbooks y `OrderDesk` para propuestas paper. El
middleware limita llamadas, reintenta errores seguros y enmascara datos sensibles.

## Salida

Una respuesta conversacional y, si corresponde, una propuesta de orden pendiente de aprobación.

## Pruebas

`tests/agents/chat`, `tests/server/test_chat_api.py` y `tests/cli` verifican herramientas, streaming
y persistencia.
