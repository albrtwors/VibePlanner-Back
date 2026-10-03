from flask import Blueprint, request, jsonify
from werkzeug.security import generate_password_hash

from database import db
from models import User, Role, ROLE_ADMIN
from utils.decorators import permissions_required
from utils.permissions import permissions_for_role
from services import auth_code_service

users_bp = Blueprint('users_bp', __name__)


def _serialize_user(user: User, with_permissions=False):
    payload = {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
        "is_verified": user.is_verified,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }
    if with_permissions:
        payload["permissions"] = sorted(permissions_for_role(user.role))
    return payload


def _active_admins_count():
    return User.query.filter_by(role=ROLE_ADMIN, is_active=True).count()


def _validate_role(role_name):
    """Devuelve (role, error)."""
    role = Role.query.filter_by(name=(role_name or '').strip().lower()).first()
    if not role:
        return None, "Ese rol no existe en el catálogo."
    return role, None


# ==========================================
# 1. LISTAR USUARIOS
# ==========================================
@users_bp.route('/api/users', methods=['GET'])
@permissions_required('users.view')
def get_users():
    query = User.query

    search = request.args.get('search')
    if search:
        pattern = f"%{search.strip().lower()}%"
        query = query.filter(
            db.or_(db.func.lower(User.username).like(pattern),
                   db.func.lower(User.email).like(pattern))
        )

    role_filter = request.args.get('role')
    if role_filter:
        query = query.filter_by(role=role_filter.strip().lower())

    status = request.args.get('status')
    if status == 'active':
        query = query.filter_by(is_active=True)
    elif status == 'inactive':
        query = query.filter_by(is_active=False)
    elif status == 'unverified':
        query = query.filter_by(is_verified=False)

    users = query.order_by(User.created_at.asc()).all()
    return jsonify([_serialize_user(user) for user in users]), 200


# ==========================================
# 2. DETALLE DE UN USUARIO
# ==========================================
@users_bp.route('/api/users/<int:user_id>', methods=['GET'])
@permissions_required('users.view')
def get_user(user_id):
    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "El usuario no existe."}), 404
    return jsonify(_serialize_user(user, with_permissions=True)), 200


# ==========================================
# 3. CREAR USUARIO (alta manual, sin pasar por el registro público)
# ==========================================
@users_bp.route('/api/users', methods=['POST'])
@permissions_required('users.create')
def create_user():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    role_name = (data.get('role') or 'usuario').strip().lower()

    if not username or not email or not password:
        return jsonify({"error": "Faltan campos obligatorios (username, email, password)."}), 400

    if len(password) < 6:
        return jsonify({"error": "La contraseña debe tener al menos 6 caracteres."}), 400

    role, error = _validate_role(role_name)
    if error:
        return jsonify({"error": error}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({"error": "Ya existe una cuenta con ese correo."}), 409

    if User.query.filter_by(username=username).first():
        return jsonify({"error": "Ese nombre de usuario ya está en uso."}), 409

    # Por defecto el alta manual queda verificada: el admin es quien respondió
    # por esa cuenta. Si se pide explícitamente, queda pendiente de código.
    is_verified = bool(data.get('is_verified', True))

    nuevo = User(
        username=username,
        email=email,
        password_hash=generate_password_hash(password, method='pbkdf2:sha256'),
        role=role.name,
        is_active=bool(data.get('is_active', True)),
        is_verified=is_verified
    )
    db.session.add(nuevo)
    db.session.commit()

    # Si quedó pendiente de verificar, mandamos el código como en el registro
    dev_hint = {}
    if not is_verified:
        record, code = auth_code_service.issue_code(nuevo, 'register')
        if record:
            auth_code_service.deliver_code(nuevo, record, code, 'register')
            dev_hint = auth_code_service.dev_code_hint(code)

    return jsonify({
        "message": f"Usuario '{nuevo.username}' creado correctamente.",
        "user": _serialize_user(nuevo),
        **dev_hint
    }), 201


# ==========================================
# 4. EDITAR USUARIO (datos, rol, estado)
# ==========================================
@users_bp.route('/api/users/<int:user_id>', methods=['PUT'])
@permissions_required('users.edit')
def update_user(user_id):
    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "El usuario no existe."}), 404

    data = request.get_json(silent=True) or {}

    # --- Username ---
    if 'username' in data:
        username = (data.get('username') or '').strip()
        if not username:
            return jsonify({"error": "El nombre de usuario no puede quedar vacío."}), 400
        duplicate = User.query.filter_by(username=username).first()
        if duplicate and duplicate.id != user.id:
            return jsonify({"error": "Ese nombre de usuario ya está en uso."}), 409
        user.username = username

    # --- Email ---
    if 'email' in data:
        email = (data.get('email') or '').strip().lower()
        if not email:
            return jsonify({"error": "El correo no puede quedar vacío."}), 400
        duplicate = User.query.filter_by(email=email).first()
        if duplicate and duplicate.id != user.id:
            return jsonify({"error": "Ese correo ya está en uso."}), 409
        user.email = email

    # --- Rol ---
    if 'role' in data:
        role, error = _validate_role(data.get('role'))
        if error:
            return jsonify({"error": error}), 400

        # Nadie se baja su propio rol: es la forma más fácil de dejar el
        # sistema sin nadie que administre los permisos.
        if user.id == _caller_id() and role.name != user.role:
            return jsonify({"error": "No podés cambiar tu propio rol, varón."}), 403

        # Ni dejar el sistema sin ningún admin activo
        if user.role == ROLE_ADMIN and role.name != ROLE_ADMIN and _active_admins_count() <= 1:
            return jsonify({"error": "Tenés que quedar al menos un administrador activo."}), 409

        user.role = role.name

    # --- Estado activo ---
    if 'is_active' in data:
        is_active = bool(data.get('is_active'))
        if not is_active and user.id == _caller_id():
            return jsonify({"error": "No podés desactivar tu propia cuenta."}), 403
        if not is_active and user.role == ROLE_ADMIN and _active_admins_count() <= 1:
            return jsonify({"error": "Tenés que quedar al menos un administrador activo."}), 409
        user.is_active = is_active

    # --- Verificación manual ---
    if 'is_verified' in data:
        user.is_verified = bool(data.get('is_verified'))

    db.session.commit()
    return jsonify({
        "message": "Usuario actualizado correctamente.",
        "user": _serialize_user(user)
    }), 200


# ==========================================
# 5. CAMBIAR LA CONTRASEÑA DE UN USUARIO
# ==========================================
@users_bp.route('/api/users/<int:user_id>/password', methods=['PUT'])
@permissions_required('users.edit')
def set_user_password(user_id):
    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "El usuario no existe."}), 404

    data = request.get_json(silent=True) or {}
    password = data.get('password') or ''

    if len(password) < 6:
        return jsonify({"error": "La contraseña debe tener al menos 6 caracteres."}), 400

    user.password_hash = generate_password_hash(password, method='pbkdf2:sha256')
    db.session.commit()

    return jsonify({"message": f"Contraseña de '{user.username}' actualizada."}), 200


# ==========================================
# 6. ELIMINAR USUARIO
# ==========================================
@users_bp.route('/api/users/<int:user_id>', methods=['DELETE'])
@permissions_required('users.delete')
def delete_user(user_id):
    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "El usuario no existe."}), 404

    if user.id == _caller_id():
        return jsonify({"error": "No podés eliminar tu propia cuenta, varón."}), 403

    if user.role == ROLE_ADMIN and _active_admins_count() <= 1:
        return jsonify({"error": "Tenés que quedar al menos un administrador activo."}), 409

    username = user.username
    db.session.delete(user)
    db.session.commit()

    return jsonify({"message": f"Usuario '{username}' eliminado."}), 200


def _caller_id():
    """Id del admin que está haciendo la operación (viene del JWT)."""
    from flask_jwt_extended import get_jwt_identity

    identity = get_jwt_identity()
    try:
        return int(identity)
    except (TypeError, ValueError):
        return None