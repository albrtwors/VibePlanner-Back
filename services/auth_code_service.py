"""
Servicio de códigos de un solo uso enviados por email.

Centraliza la emisión, el envío y la validación de los códigos usados en:
  - Verificación de correo al registrarse  (purpose='register')
  - Recuperación / cambio de contraseña  (purpose='password_reset')

Todas las políticas (vencimiento, intentos, cooldown de reenvío) salen de
app.config para poder ajustarlas por entorno.
"""
import secrets
from datetime import datetime, timedelta

from flask import current_app
from database import db
from models import VerificationCode
from services.email_service import EmailNotifierService

notifier = EmailNotifierService()

CODE_LENGTH = 6


def _config(key, default):
    return current_app.config.get(key, default)


def generate_code() -> str:
    """Genera un código numérico de 6 dígitos criptográficamente seguro."""
    return f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"


def _invalidate_previous(user_id: int, purpose: str):
    """Deja activo solo el último código emitido para ese usuario/propósito."""
    VerificationCode.query.filter_by(
        user_id=user_id, purpose=purpose
    ).delete(synchronize_session=False)


def seconds_since_last_code(user_id: int, purpose: str) -> int:
    """Segundos transcurridos desde el último código emitido (o None si nunca hubo)."""
    last = (
        VerificationCode.query.filter_by(user_id=user_id, purpose=purpose)
        .order_by(VerificationCode.created_at.desc())
        .first()
    )
    if not last or not last.created_at:
        return None
    return int((datetime.utcnow() - last.created_at).total_seconds())


def issue_code(user, purpose: str, enforce_cooldown: bool = True):
    """
    Genera y persiste un código nuevo para el usuario.

    Returns: (registro, codigo)  |  (None, mensaje_de_error)
    """
    if purpose not in VerificationCode.PURPOSES:
        return None, "Propósito de código desconocido."

    cooldown = _config('AUTH_CODE_RESEND_COOLDOWN_SECONDS', 60)
    if enforce_cooldown:
        elapsed = seconds_since_last_code(user.id, purpose)
        if elapsed is not None and elapsed < cooldown:
            wait = cooldown - elapsed
            return None, f"Esperá {wait} segundos antes de solicitar otro código."

    code = generate_code()
    ttl_minutes = _config('AUTH_CODE_TTL_MINUTES', 15)

    _invalidate_previous(user.id, purpose)

    record = VerificationCode(
        user_id=user.id,
        purpose=purpose,
        code=code,
        attempts_left=_config('AUTH_CODE_MAX_ATTEMPTS', 5),
        expires_at=datetime.utcnow() + timedelta(minutes=ttl_minutes)
    )
    db.session.add(record)
    db.session.commit()

    return record, code


def deliver_code(user, record, code: str, purpose: str) -> bool:
    """
    Envía el código por email. Si el SMTP no está configurado y estamos en
    modo desarrollo, lo deja impreso en la consola para poder completar el
    flujo de prueba. Devuelve True si el envío real se hizo.
    """
    sent = False
    try:
        sent = notifier.send_auth_code(
            recipient=user.email,
            code=code,
            purpose=purpose,
            username=user.username
        )
    except Exception as exc:
        print(f"[MAIL] Error enviando código a {user.email}: {exc}")

    if not sent:
        print(f"[MAIL-DEV] Código para {user.email} ({purpose}): {code}")

    return sent


def dev_code_hint(code: str) -> dict:
    """
    Solo en modo desarrollo devuelve el código dentro de la respuesta para que
    el frontend pueda mostrarlo (con Gmail sin configurar no hay buzón donde leerlo).
    """
    if _config('MAIL_DEV_MODE', False):
        return {"dev_code": code}
    return {}


def consume_code(user, purpose: str, code: str, consume: bool = True):
    """
    Valida un código recibido contra el usuario indicado.

    Con consume=True (default) el código se borra al acertar, de modo que queda
    de un solo uso. Con consume=False solo se valida (los intentos fallidos
    igual se descuentan) y sirve para los pasos previos al cambio de contraseña.

    Returns: (True, None) si es correcto
             (False, mensaje) con el motivo del rechazo
    """
    record = (
        VerificationCode.query.filter_by(user_id=user.id, purpose=purpose)
        .order_by(VerificationCode.created_at.desc())
        .first()
    )

    if not record:
        return False, "No hay ningún código vigente. Pedí uno nuevo."

    if record.is_expired:
        db.session.delete(record)
        db.session.commit()
        return False, "El código expiró. Pedí uno nuevo."

    if record.attempts_left <= 0:
        db.session.delete(record)
        db.session.commit()
        return False, "Demasiados intentos fallidos. Pedí un código nuevo."

    if not secrets.compare_digest(record.code, code or ''):
        record.attempts_left -= 1
        db.session.commit()
        remaining = record.attempts_left
        if remaining <= 0:
            db.session.delete(record)
            db.session.commit()
            return False, "Código incorrecto. Se agotaron los intentos: pedí uno nuevo."
        return False, f"Código incorrecto. Te quedan {remaining} intentos."

    # Código correcto: lo consumimos para que no pueda reutilizarse
    if consume:
        db.session.delete(record)
    db.session.commit()
    return True, None