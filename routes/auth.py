from flask import Blueprint, request, jsonify, make_response
from flask_jwt_extended import (
    create_access_token,
    jwt_required,
    get_jwt_identity,
    get_jwt
)
from database import db
from models import User, TokenBlocklist, ROLE_ADMIN, ROLE_USUARIO
from utils.permissions import permissions_for_role
from services import auth_code_service
from werkzeug.security import generate_password_hash, check_password_hash
import os

auth_bp = Blueprint('auth', __name__)

TOKEN_COOKIE_NAME = "vibe_token"
TOKEN_MAX_AGE = 12 * 60 * 60  # 12 horas, alineado con JWT_ACCESS_TOKEN_EXPIRES


def _serialize_user(user):
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "is_verified": user.is_verified
    }


def _issue_session_cookie(response, user):
    """Inyecta el JWT en la cookie 'vibe_token' (mismo patrón que /login)."""
    additional_claims = {
        "role": user.role,
        "username": user.username
    }
    access_token = create_access_token(
        identity=str(user.id),
        additional_claims=additional_claims
    )

    response.set_cookie(
        key=TOKEN_COOKIE_NAME,
        value=access_token,
        httponly=False,
        secure=False,
        samesite="Lax",
        max_age=TOKEN_MAX_AGE
    )
    return response

# ==========================================
# 1. CREAR ADMIN POR DEFECTO
# ==========================================
@auth_bp.route('/api/auth/bootstrap-admin', methods=['POST'])
def bootstrap_admin():
    MASTER_KEY = os.getenv("BOOTSTRAP_KEY", "1234")
    client_key = request.headers.get('X-Bootstrap-Key')

    if not client_key or client_key != MASTER_KEY:
        return jsonify({"error": "No autorizado para inicializar el sistema."}), 403

    existing_admin = User.query.filter_by(role=ROLE_ADMIN).first()
    if existing_admin:
        return jsonify({"message": "El administrador ya fue inicializado previamente."}), 400

    data = request.get_json() or {}
    username = data.get('username', 'admin')
    email = data.get('email', 'admin@test.com')
    password = data.get('password', '123456')

    try:
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        new_admin = User(
            username=username,
            email=email,
            password_hash=hashed_password,
            role=ROLE_ADMIN,
            is_active=True,
            is_verified=True
        )
        db.session.add(new_admin)
        db.session.commit()
        return jsonify({
            "message": "Administrador de desarrollo creado con éxito.",
            "credentials_created": {"email": email, "password": password}
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# ==========================================
# 2. REGISTRO PÚBLICO DE USUARIOS (ROL BASE: OPERATOR)
#    El usuario queda creado pero INACTIVO para el login hasta que confirme
#    su correo con el código que le mandamos por email (ver /verify-email).
# ==========================================
@auth_bp.route('/api/auth/register', methods=['POST'])
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    if not username or not email or not password:
        return jsonify({"error": "Faltan campos obligatorios (username, email, password), varón."}), 400

    if len(password) < 6:
        return jsonify({"error": "La contraseña debe tener al menos 6 caracteres."}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({"error": "Ya existe una cuenta registrada con ese correo."}), 409

    if User.query.filter_by(username=username).first():
        return jsonify({"error": "Ese nombre de usuario ya está en uso."}), 409

    try:
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')

        # Rol base para cualquiera que se registre solo: 'usuario' (el más bajo).
        # Un admin puede cambiarlo después desde el panel de usuarios.
        new_user = User(
            username=username,
            email=email,
            password_hash=hashed_password,
            role=ROLE_USUARIO,
            is_active=True,
            is_verified=False
        )
        db.session.add(new_user)
        db.session.commit()

        # Emitimos y enviamos el código de verificación antes de responder.
        record, code = auth_code_service.issue_code(new_user, 'register')
        if not record:
            return jsonify({"error": record}), 429

        auth_code_service.deliver_code(new_user, record, code, 'register')

        return jsonify({
            "message": "¡Cuenta creada! Te enviamos un código a tu correo para verificarla.",
            "requires_verification": True,
            "email": new_user.email,
            **auth_code_service.dev_code_hint(code)
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# ==========================================
# 3. VERIFICACIÓN DEL CORREO CON CÓDIGO (fin del registro)
# ==========================================
@auth_bp.route('/api/auth/verify-email', methods=['POST'])
def verify_email():
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    code = (data.get('code') or '').strip()

    if not email or not code:
        return jsonify({"error": "Necesitamos el correo y el código de verificación."}), 400

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({"error": "No encontramos ninguna cuenta con ese correo."}), 404

    if user.is_verified:
        return jsonify({"message": "La cuenta ya estaba verificada. Podés iniciar sesión."}), 200

    ok, error = auth_code_service.consume_code(user, 'register', code)
    if not ok:
        return jsonify({"error": error}), 400

    user.is_verified = True
    db.session.commit()

    # Autoingreso con el mismo patrón exacto que /login para no obligar a
    # escribir las credenciales otra vez apenas se confirma el correo.
    response = make_response(jsonify({
        "message": "¡Correo verificado con éxito! Bienvenido a VibePlanner.",
        "user": _serialize_user(user)
    }), 200)

    return _issue_session_cookie(response, user)


# ==========================================
# 4. REENVÍO DEL CÓDIGO DE VERIFICACIÓN
# ==========================================
@auth_bp.route('/api/auth/resend-verification', methods=['POST'])
def resend_verification():
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()

    if not email:
        return jsonify({"error": "Falta el correo electrónico."}), 400

    user = User.query.filter_by(email=email).first()

    # No revelamos si el correo existe o ya está verificado.
    if not user or user.is_verified:
        return jsonify({"message": "Si la cuenta existe y está sin verificar, recibirás un código en breve."}), 200

    record, result = auth_code_service.issue_code(user, 'register')
    if not record:
        return jsonify({"error": result}), 429

    auth_code_service.deliver_code(user, record, result, 'register')

    return jsonify({
        "message": "Te enviamos un nuevo código de verificación.",
        **auth_code_service.dev_code_hint(result)
    }), 200


# ==========================================
# 5. RECUPERACIÓN DE CONTRASEÑA - PASO 1: SOLICITUD DEL CÓDIGO
# ==========================================
@auth_bp.route('/api/auth/forgot-password', methods=['POST'])
def forgot_password():
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()

    if not email:
        return jsonify({"error": "Falta el correo electrónico."}), 400

    user = User.query.filter_by(email=email).first()

    # Misma respuesta siempre: no filtramos qué correos están registrados.
    if not user:
        return jsonify({
            "message": "Si el correo está registrado, te enviamos un código para restablecer la contraseña.",
            "email": email
        }), 200

    if not user.is_active:
        return jsonify({"error": "Esta cuenta está deshabilitada. Contactá al administrador."}), 403

    record, code = auth_code_service.issue_code(user, 'password_reset')
    if not record:
        return jsonify({"error": code}), 429

    auth_code_service.deliver_code(user, record, code, 'password_reset')

    return jsonify({
        "message": "Si el correo está registrado, te enviamos un código para restablecer la contraseña.",
        "email": email,
        **auth_code_service.dev_code_hint(code)
    }), 200


# ==========================================
# 6. VALIDACIÓN DEL CÓDIGO DE RECUPERACIÓN (sin consumirlo)
#    Permite mostrar el paso de "elegí la nueva contraseña" solo cuando el
#    código es correcto; el consumo real ocurre en /reset-password.
# ==========================================
@auth_bp.route('/api/auth/verify-code', methods=['POST'])
def verify_code():
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    code = (data.get('code') or '').strip()

    if not email or not code:
        return jsonify({"error": "Necesitamos el correo y el código."}), 400

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({"error": "No encontramos ninguna cuenta con ese correo."}), 404

    ok, error = auth_code_service.consume_code(user, 'password_reset', code, consume=False)
    if not ok:
        return jsonify({"error": error}), 400

    return jsonify({"message": "Código validado. Ahora elegí tu contraseña nueva."}), 200


# ==========================================
# 7. RECUPERACIÓN DE CONTRASEÑA - PASO 2: CÓDIGO + NUEVA CONTRASEÑA
# ==========================================
@auth_bp.route('/api/auth/reset-password', methods=['POST'])
def reset_password():
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    code = (data.get('code') or '').strip()
    password = data.get('password') or ''
    confirm_password = data.get('confirm_password') or ''

    if not email or not code or not password:
        return jsonify({"error": "Faltan campos obligatorios (email, code, password)."}), 400

    if len(password) < 6:
        return jsonify({"error": "La contraseña debe tener al menos 6 caracteres."}), 400

    if confirm_password and password != confirm_password:
        return jsonify({"error": "Las contraseñas no coinciden."}), 400

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({"error": "No encontramos ninguna cuenta con ese correo."}), 404

    ok, error = auth_code_service.consume_code(user, 'password_reset', code)
    if not ok:
        return jsonify({"error": error}), 400

    user.password_hash = generate_password_hash(password, method='pbkdf2:sha256')
    db.session.commit()

    return jsonify({
        "message": "¡Contraseña actualizada! Ya podés iniciar sesión con la nueva."
    }), 200


# ==========================================
# 8. INICIO DE SESIÓN (LOGIN CON INYECCIÓN DE COOKIE)
# ==========================================
@auth_bp.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({"error": "Faltan credenciales obligatorias, varón."}), 400

    user = User.query.filter_by(email=email.strip().lower()).first()

    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"error": "Credenciales inválidas."}), 401

    if not user.is_active:
        return jsonify({"error": "Este usuario ha sido deshabilitado."}), 403

    if not user.is_verified:
        return jsonify({
            "error": "Tu cuenta todavía no está verificada. Revisá tu correo e ingresá el código que te enviamos.",
            "requires_verification": True,
            "email": user.email
        }), 403

    response = make_response(jsonify({
        "message": "Login exitoso",
        "user": _serialize_user(user)
    }), 200)

    return _issue_session_cookie(response, user)


# ==========================================
# 9. CIERRE DE SESIÓN (LOGOUT Y LIMPIEZA)
# ==========================================
@auth_bp.route('/api/auth/logout', methods=['POST'])
@jwt_required()
def logout():
    jti = get_jwt()["jti"]
    try:
        blocked_token = TokenBlocklist(jti=jti)
        db.session.add(blocked_token)
        db.session.commit()

        response = make_response(jsonify({"message": "Sesión cerrada correctamente y token revocado."}), 200)
        response.delete_cookie("vibe_token")
        return response
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# ==========================================
# 10. RUTA DE PRUEBA (PERFIL)
# ==========================================
@auth_bp.route('/api/auth/profile', methods=['GET'])
@jwt_required()
def profile():
    current_user_id = get_jwt_identity()
    claims = get_jwt()

    role = claims.get("role")

    return jsonify({
        "user_id": current_user_id,
        "username": claims.get("username"),
        "role": role,
        "permissions": sorted(permissions_for_role(role))
    }), 200