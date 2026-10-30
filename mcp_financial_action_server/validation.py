"""
Validation rules and financial business constraints for transactions.
"""
import re


def validate_cbu_or_cvu(cbu: str) -> bool:
    """
    Validates Argentine CBU / CVU standard format (22 digits).
    """
    cleaned = cbu.strip()
    if not re.match(r"^\d{22}$", cleaned):
        raise ValueError(f"El CBU/CVU '{cbu}' es inválido. Debe contener exactamente 22 dígitos numéricos.")
    return True


def validate_alias(alias: str) -> bool:
    """
    Validates Argentine Bank Alias format:
    6 to 20 alphanumeric characters, dots or hyphens.
    Must not be purely numeric.
    """
    cleaned = alias.strip().lower()
    if not re.match(r"^[a-z0-9.-]{6,20}$", cleaned):
        raise ValueError(
            f"El Alias '{alias}' es inválido. Debe tener entre 6 y 20 caracteres alfanuméricos (letras, números, puntos o guiones)."
        )
    if cleaned.isdigit():
        raise ValueError(f"El Alias '{alias}' no puede consistir exclusivamente en dígitos.")
    return True


def validate_destination_cbu_or_alias(destination: str) -> str:
    """
    Validates and normalizes destination identifier (either 22-digit CBU/CVU or valid Alias).
    If the string is purely numeric, it must be a valid 22-digit CBU/CVU.
    Otherwise, it is validated as a bank Alias.
    """
    cleaned = destination.strip()
    if cleaned.isdigit():
        validate_cbu_or_cvu(cleaned)
        return cleaned

    validate_alias(cleaned)
    return cleaned.lower()


def validate_transfer_amount(amount: float) -> None:
    """
    Validates that the transfer amount is strictly positive and a valid numeric float.
    """
    if amount <= 0:
        raise ValueError(f"El monto a transferir debe ser estrictamente positivo. Se recibió: {amount}")


def validate_daily_limit(amount: float, current_daily_total: float, daily_limit: float) -> None:
    """
    Enforces maximum daily outgoing transfer limit per user.
    """
    if current_daily_total + amount > daily_limit:
        raise ValueError(
            f"Límite diario de transferencias superado. Límite máximo: ${daily_limit:,.2f}, "
            f"Total acumulado hoy: ${current_daily_total:,.2f}, Monto solicitado: ${amount:,.2f}."
        )
