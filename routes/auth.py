from flask import Blueprint, request, jsonify, make_response
from flask_jwt_extended import (
    create_access_token,
    jwt_required,
    get_jwt_identity,
    get_jwt
)
from database import db
from models import User, TokenBlocklist
from werkzeug.security import generate_password_hash, check_password_hash
import os

auth_bp = Blueprint('auth', __name__)

# ==========================================
# 1. CREAR ADMIN POR DEFECTO
# ==========================================
@auth_bp.route('/api/auth/bootstrap-admin', methods=['POST'])
def bootstrap_admin():
    MASTER_KEY = os.getenv("BOOTSTRAP_KEY", "1234")
    client_key = request.headers.get('X-Bootstrap-Key')

    if not client_key or client_key != MASTER_KEY:
        return jsonify({"error": "No autorizado para inicializar el sistema."}), 403

    existing_admin = User.query.filter_by(role='admin').first()
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
            role='admin',
            is_active=True
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

    existing_user = User.query.filter_by(email=email).first()
    if existing_user:
        return jsonify({"error": "Ya existe una cuenta registrada con ese correo."}), 409

    try:
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')

        # Rol base para cualquiera que se registre solo: 'operator' (el más bajo).
        # Un admin puede subirle el rol después desde el panel correspondiente.
        new_user = User(
            username=username,
            email=email,
            password_hash=hashed_password,
            role='operator',
            is_active=True
        )
        db.session.add(new_user)
        db.session.commit()

        # Auto-login: mismo patrón exacto que en /login, para que el usuario
        # entre directo a la app sin tener que loguearse una segunda vez.
        additional_claims = {
            "role": new_user.role,
            "username": new_user.username
        }
        access_token = create_access_token(
            identity=str(new_user.id),
            additional_claims=additional_claims
        )

        response = make_response(jsonify({
            "message": "¡Cuenta creada con éxito!",
            "user": {
                "id": new_user.id,
                "username": new_user.username,
                "email": new_user.email,
                "role": new_user.role
            }
        }), 201)

        response.set_cookie(
            key="vibe_token",
            value=access_token,
            httponly=False,
            secure=False,
            samesite="Lax",
            max_age=12 * 60 * 60
        )

        return response

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# ==========================================
# 3. INICIO DE SESIÓN (LOGIN CON INYECCIÓN DE COOKIE)
# ==========================================
@auth_bp.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({"error": "Faltan credenciales obligatorias, varón."}), 400

    user = User.query.filter_by(email=email).first()

    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"error": "Credenciales inválidas."}), 401

    if not user.is_active:
        return jsonify({"error": "Este usuario ha sido deshabilitado."}), 403

    additional_claims = {
        "role": user.role,
        "username": user.username
    }

    access_token = create_access_token(
        identity=str(user.id),
        additional_claims=additional_claims
    )

    response = make_response(jsonify({
        "message": "Login exitoso",
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": user.role
        }
    }), 200)

    response.set_cookie(
        key="vibe_token",
        value=access_token,
        httponly=False,
        secure=False,
        samesite="Lax",
        max_age=12 * 60 * 60
    )

    return response


# ==========================================
# 4. CIERRE DE SESIÓN (LOGOUT Y LIMPIEZA)
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
# 5. RUTA DE PRUEBA (PERFIL)
# ==========================================
@auth_bp.route('/api/auth/profile', methods=['GET'])
@jwt_required()
def profile():
    current_user_id = get_jwt_identity()
    claims = get_jwt()

    return jsonify({
        "user_id": current_user_id,
        "username": claims.get("username"),
        "role": claims.get("role")
    }), 200