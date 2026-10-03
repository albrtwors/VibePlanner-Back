import re

from flask import Blueprint, request, jsonify

from database import db
from models import Permission, Role, RolePermission, User, ROLE_ADMIN
from utils.decorators import permissions_required
from utils.permissions import (
    PERMISSION_CATALOG,
    default_permissions_for,
    permissions_by_module,
)

roles_bp = Blueprint('roles_bp', __name__)

ROLE_NAME_PATTERN = re.compile(r'^[a-z][a-z0-9_]{1,29}$')


def _serialize_role(role: Role, include_permissions=True):
    payload = {
        "id": role.id,
        "name": role.name,
        "description": role.description,
        "is_system": role.is_system,
        "is_protected": role.is_protected,
        "users_count": User.query.filter_by(role=role.name).count(),
    }
    if include_permissions:
        payload["permissions"] = role.permissions
    return payload


def _valid_role_name(name):
    if not name or not ROLE_NAME_PATTERN.match(name):
        return False
    return True


# ==========================================
# 1. LISTAR ROLES (con su matriz de permisos)
# ==========================================
@roles_bp.route('/api/roles', methods=['GET'])
@permissions_required('roles.view')
def get_roles():
    roles = Role.query.order_by(Role.is_protected.desc(), Role.name.asc()).all()
    return jsonify([_serialize_role(role) for role in roles]), 200


# ==========================================
# 2. CATÁLOGO DE PERMISOS (agrupado por módulo)
#    Es de solo lectura: el admin decide qué rol tiene qué, no qué existe.
# ==========================================
@roles_bp.route('/api/permissions', methods=['GET'])
@permissions_required('roles.view')
def get_permissions():
    return jsonify({
        "modules": permissions_by_module(),
        "total": len(PERMISSION_CATALOG)
    }), 200


# ==========================================
# 3. CREAR UN ROL NUEVO
# ==========================================
@roles_bp.route('/api/roles', methods=['POST'])
@permissions_required('roles.manage')
def create_role():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip().lower()
    description = (data.get('description') or '').strip() or None
    permissions = data.get('permissions') or []

    if not _valid_role_name(name):
        return jsonify({
            "error": "Nombre de rol inválido.",
            "message": "Usá minúsculas, números y guiones bajos (ej: 'bajo_produccion')."
        }), 400

    if Role.query.filter_by(name=name).first():
        return jsonify({"error": f"Ya existe un rol llamado '{name}'."}), 409

    catalog_keys = {key for key, _, _, _ in PERMISSION_CATALOG}
    unknown = [p for p in permissions if p not in catalog_keys]
    if unknown:
        return jsonify({"error": f"Permisos desconocidos: {', '.join(unknown)}"}), 400

    nuevo = Role(
        name=name,
        description=description,
        is_system=False,
        is_protected=False
    )
    db.session.add(nuevo)
    db.session.flush()

    for key in dict.fromkeys(permissions):
        permission = Permission.query.filter_by(key=key).first()
        if permission:
            db.session.add(RolePermission(role_id=nuevo.id, permission_id=permission.id))

    db.session.commit()
    return jsonify({
        "message": f"Rol '{nuevo.name}' creado con éxito.",
        "role": _serialize_role(nuevo)
    }), 201


# ==========================================
# 4. EDITAR NOMBRE / DESCRIPCIÓN DE UN ROL
# ==========================================
@roles_bp.route('/api/roles/<int:role_id>', methods=['PUT'])
@permissions_required('roles.manage')
def update_role(role_id):
    role = Role.query.get(role_id)
    if not role:
        return jsonify({"error": "El rol indicado no existe."}), 404

    if role.is_protected:
        return jsonify({"error": "El rol de administrador está protegido y no se puede modificar."}), 403

    data = request.get_json(silent=True) or {}

    if 'name' in data:
        new_name = (data.get('name') or '').strip().lower()
        if not _valid_role_name(new_name):
            return jsonify({"error": "Nombre de rol inválido."}), 400

        duplicate = Role.query.filter_by(name=new_name).first()
        if duplicate and duplicate.id != role.id:
            return jsonify({"error": f"Ya existe un rol llamado '{new_name}'."}), 409

        # users.role guarda el nombre, así que hay que arrastrar el cambio
        affected = User.query.filter_by(role=role.name).all()
        for user in affected:
            user.role = new_name
        role.name = new_name

    if 'description' in data:
        role.description = (data.get('description') or '').strip() or None

    db.session.commit()
    return jsonify({
        "message": "Rol actualizado correctamente.",
        "role": _serialize_role(role),
        "users_updated": User.query.filter_by(role=role.name).count()
    }), 200


# ==========================================
# 5. CAMBIAR LA MATRIZ DE PERMISOS DE UN ROL
# ==========================================
@roles_bp.route('/api/roles/<int:role_id>/permissions', methods=['PUT'])
@permissions_required('roles.manage')
def set_role_permissions(role_id):
    role = Role.query.get(role_id)
    if not role:
        return jsonify({"error": "El rol indicado no existe."}), 404

    if role.is_protected:
        return jsonify({
            "error": "El administrador tiene todos los permisos siempre. No se puede modificar su matriz."
        }), 403

    data = request.get_json(silent=True) or {}
    requested = data.get('permissions')

    if not isinstance(requested, list):
        return jsonify({"error": "Se esperaba una lista de permisos."}), 400

    catalog_keys = {key for key, _, _, _ in PERMISSION_CATALOG}
    unknown = [p for p in requested if p not in catalog_keys]
    if unknown:
        return jsonify({"error": f"Permisos desconocidos: {', '.join(unknown)}"}), 400

    RolePermission.query.filter_by(role_id=role.id).delete(synchronize_session=False)
    db.session.flush()

    for key in dict.fromkeys(requested):
        permission = Permission.query.filter_by(key=key).first()
        if permission:
            db.session.add(RolePermission(role_id=role.id, permission_id=permission.id))

    db.session.commit()
    return jsonify({
        "message": f"Permisos de '{role.name}' actualizados.",
        "role": _serialize_role(role)
    }), 200


# ==========================================
# 6. RESTAURAR LA MATRIZ DE FÁBRICA
# ==========================================
@roles_bp.route('/api/roles/<int:role_id>/restore-permissions', methods=['POST'])
@permissions_required('roles.manage')
def restore_role_permissions(role_id):
    role = Role.query.get(role_id)
    if not role:
        return jsonify({"error": "El rol indicado no existe."}), 404

    RolePermission.query.filter_by(role_id=role.id).delete(synchronize_session=False)
    db.session.flush()

    for key in default_permissions_for(role.name):
        permission = Permission.query.filter_by(key=key).first()
        if permission:
            db.session.add(RolePermission(role_id=role.id, permission_id=permission.id))

    db.session.commit()
    return jsonify({
        "message": f"Permisos originales de '{role.name}' restaurados.",
        "role": _serialize_role(role)
    }), 200


# ==========================================
# 7. ELIMINAR UN ROL
# ==========================================
@roles_bp.route('/api/roles/<int:role_id>', methods=['DELETE'])
@permissions_required('roles.manage')
def delete_role(role_id):
    role = Role.query.get(role_id)
    if not role:
        return jsonify({"error": "El rol indicado no existe."}), 404

    if role.is_protected:
        return jsonify({"error": "No se puede eliminar el rol de administrador."}), 403

    users_with_role = User.query.filter_by(role=role.name).count()
    if users_with_role > 0:
        return jsonify({
            "error": f"{users_with_role} usuario(s) tienen este rol. Reasignalos antes de eliminarlo."
        }), 409

    db.session.delete(role)
    db.session.commit()
    return jsonify({"message": f"Rol '{role.name}' eliminado."}), 200