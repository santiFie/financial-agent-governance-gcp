"""
Pydantic schemas and domain models for MCP Financial Action Server.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class TransferRequest(BaseModel):
    user_id: str = Field(..., description="ID del usuario titular de la cuenta origen")
    source_account_id: str = Field(..., description="ID de la cuenta de origen")
    destination_cbu_or_alias: str = Field(..., description="CBU/CVU de 22 dígitos o Alias del destinatario")
    amount: float = Field(..., gt=0, description="Monto a transferir (debe ser mayor a 0)")
    currency: str = Field("ARS", description="Moneda de la transferencia (ej. ARS, USD)")
    concept: str = Field("VARIOS", description="Concepto o motivo de la transferencia")
    idempotency_key: str = Field(..., description="Clave única para evitar duplicación de transacciones")


class TransferReceipt(BaseModel):
    status: Literal["EXECUTED", "ALREADY_PROCESSED"] = Field(..., description="Estado de la transferencia")
    transaction_id: str = Field(..., description="Identificador único de la transacción en el ledger")
    receipt_number: str = Field(..., description="Número de comprobante bancario emitido")
    user_id: str = Field(..., description="ID del usuario emisor")
    source_account_id: str = Field(..., description="Cuenta origen")
    destination_cbu_or_alias: str = Field(..., description="Destino de los fondos")
    amount: float = Field(..., description="Monto debitado")
    currency: str = Field(..., description="Moneda")
    concept: str = Field(..., description="Concepto bancario")
    remaining_balance: float = Field(..., description="Saldo remanente en la cuenta de origen")
    timestamp: str = Field(..., description="Fecha y hora de ejecución ISO 8601")
    idempotency_key: str = Field(..., description="Clave de idempotencia asociada")


class RegisterAccountRequest(BaseModel):
    user_id: str = Field(..., description="ID del usuario que agrega el destinatario a su agenda")
    cbu_or_cvu: str = Field(..., description="CBU o CVU de 22 dígitos del destinatario")
    alias: Optional[str] = Field(None, description="Alias bancario opcional del destinatario")
    holder_name: str = Field(..., min_length=2, description="Nombre y apellido o razón social del titular")
    bank_name: str = Field(..., min_length=2, description="Nombre de la entidad bancaria o fintech")


class RegisteredAccountReceipt(BaseModel):
    status: Literal["REGISTERED"] = "REGISTERED"
    recipient_id: str = Field(..., description="Identificador del destinatario registrado")
    user_id: str = Field(..., description="ID del usuario")
    cbu_or_cvu: str = Field(..., description="CBU/CVU registrado")
    alias: Optional[str] = Field(None, description="Alias registrado")
    holder_name: str = Field(..., description="Titular de la cuenta destino")
    bank_name: str = Field(..., description="Banco o entidad")
    created_at: str = Field(..., description="Fecha y hora de alta en agenda")


class ScheduleDebitRequest(BaseModel):
    user_id: str = Field(..., description="ID del usuario")
    account_id: str = Field(..., description="ID de la cuenta a debitar")
    service_provider: str = Field(
        ..., min_length=2, description="Empresa prestadora del servicio (ej. Edesur, Telecom)"
    )
    customer_reference: str = Field(..., min_length=1, description="Número de cliente o referencia de pago")
    max_amount: float = Field(..., gt=0, description="Tope máximo permitido para el débito automático")
    frequency: Literal["MONTHLY", "BIWEEKLY", "WEEKLY", "ANNUAL"] = Field(
        "MONTHLY", description="Frecuencia del débito"
    )


class ScheduledDebitReceipt(BaseModel):
    status: Literal["SCHEDULED"] = "SCHEDULED"
    debit_id: str = Field(..., description="ID de la suscripción o programación de débito")
    user_id: str = Field(..., description="ID del usuario")
    account_id: str = Field(..., description="Cuenta adherida")
    service_provider: str = Field(..., description="Empresa del servicio")
    customer_reference: str = Field(..., description="Referencia de cliente")
    max_amount: float = Field(..., description="Tope de débito acordado")
    frequency: str = Field(..., description="Frecuencia programada")
    created_at: str = Field(..., description="Fecha de programación")


class CancelDebitRequest(BaseModel):
    user_id: str = Field(..., description="ID del usuario titular")
    debit_id: str = Field(..., description="ID del débito programado a cancelar")


class CancelDebitReceipt(BaseModel):
    status: Literal["CANCELLED"] = "CANCELLED"
    debit_id: str = Field(..., description="ID del débito cancelado")
    user_id: str = Field(..., description="ID del usuario")
    cancelled_at: str = Field(..., description="Fecha de cancelación")
