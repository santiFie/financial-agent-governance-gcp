# FastMCP Financial Action Server (Gestor Transaccional y de Pagos)

Servidor MCP construido con **FastMCP** para gestionar y ejecutar operaciones bancarias críticas que modifican los activos financieros del cliente.

## 🛠️ Herramientas Expuestas (`Tools`)

| Herramienta | Parámetros | Anotaciones | Descripción |
| :--- | :--- | :--- | :--- |
| `execute_bank_transfer` | `user_id`, `source_account_id`, `destination_cbu_or_alias`, `amount`, `currency`, `concept`, `idempotency_key` | `destructiveHint: true`, `idempotentHint: true`, `readOnlyHint: false` | Realiza transferencias bancarias debitando fondos de forma atómica y emitiendo comprobante. Valida CBU/Alias, solvencia, límite diario e idempotencia. |
| `register_destination_account` | `user_id`, `cbu_or_cvu`, `alias`, `holder_name`, `bank_name` | `destructiveHint: false`, `idempotentHint: true`, `readOnlyHint: false` | Da de alta un nuevo CBU/CVU o Alias en la agenda de destinatarios del cliente. |
| `schedule_direct_debit` | `user_id`, `account_id`, `service_provider`, `customer_reference`, `max_amount`, `frequency` | `destructiveHint: false`, `idempotentHint: false`, `readOnlyHint: false` | Programa un débito automático periódico contra la cuenta seleccionada. |
| `get_account_balances` | `user_id` | `readOnlyHint: true` | Saldos del usuario (reflejan transferencias ejecutadas). |
| `get_recent_transactions` | `user_id`, `limit` | `readOnlyHint: true` | Últimos movimientos del usuario. |
| `cancel_scheduled_debit` | `user_id`, `debit_id` | `destructiveHint: true`, `idempotentHint: true`, `readOnlyHint: false` | Da de baja una adhesión a débito automático previamente programada. |

> El backend **no** llama a este servidor por HTTP: `MCPActionClient` (`backend/app/core/mcp_action_client.py`) importa las funciones de `server.py` in-process. El servicio HTTP de `docker-compose` (puerto 8001) expone las mismas tools por protocolo MCP.

## 🛡️ Reglas y Validaciones de Seguridad
1. **Validación de CBU/CVU**: Requiere exactamente 22 dígitos numéricos conforme al estándar BCRA.
2. **Validación de Alias**: Cadena alfanumérica de 6 a 20 caracteres con puntos/guiones (no numérico puro).
3. **Idempotencia Transaccional**: Toda transferencia exige `idempotency_key`. Si se envía repetida, se devuelve el comprobante original sin debitar saldos dos veces.
4. **Límite Diario Acumulado**: Control de tope máximo de transferencia por usuario por día calendario.
5. **Atomicidad Concurrente**: Bloqueo reentrante (`threading.RLock`) que garantiza consistencia de saldos ante invocaciones concurrentes.

## 🚀 Ejecución Local

```bash
# Instalar dependencias
pip install -r requirements.txt

# Iniciar servidor en puerto 8001
uvicorn mcp_financial_action_server.server:app --host 0.0.0.0 --port 8001
```

## 🐳 Ejecución con Docker

```bash
# Construir imagen
docker build -t mcp-financial-action-server .

# Correr contenedor
docker run -p 8001:8001 mcp-financial-action-server
```

## 🧪 Pruebas Automatizadas

```bash
pytest mcp_financial_action_server/tests -v
```
