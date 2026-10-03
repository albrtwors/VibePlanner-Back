from functools import wraps

from flask import jsonify, g
from flask_jwt_extended import verify_jwt_in_request, get_jwt

from utils.permissions import permissions_for_role


def roles_required(*allowed_roles):
    """
    Decorador para restringir el acceso a ciertos roles de la navbar.
    Soporta múltiples roles, ej: @roles_required('admin', 'coordinator')
    """
    def wrapper(fn):
        @wraps(fn)
        def decorator(*args, **kwargs):
            # 1. Verifica que el token JWT sea válido y esté presente en la petición
            verify_jwt_in_request()
            
            # 2. Extrae los claims adicionales del token
            claims = get_jwt()
            user_role = claims.get("role")

            # 3. Valida si el rol del usuario está en la lista de permitidos
            if user_role not in allowed_roles:
                return jsonify({
                    "error": "Acceso denegado.",
                    "message": f"Tu rol '{user_role}' no tiene permisos para realizar esta acción, varón."
                }), 403

            # Si todo está bien, continúa con la función del endpoint
            return fn(*args, **kwargs)
        return decorator
    wrapper.__wrapped_is_setup = True # Evita advertencias en algunas extensiones de Flask
    return wrapper


def permissions_required(*required_permissions):
    """
    Autoriza la ruta si el usuario tiene TODOS los permisos indicados.

    A diferencia de roles_required, acá lo que manda es la matriz
    rol→permisos: el admin puede reconfigurar qué puede hacer cada rol sin
    tocar el código.

    Ej: @permissions_required('events.view')
        @permissions_required('events.create', 'events.edit')

    Requiere que el JWT sea válido (verifica la sesión y el blocklist).
    """
    def wrapper(fn):
        @wraps(fn)
        def decorator(*args, **kwargs):
            verify_jwt_in_request()

            claims = get_jwt()
            role = claims.get("role")

            # Se resuelven en la base (no desde el JWT) para que un cambio en la
            # matriz de permisos tenga efecto sin necesidad de volver a loguearse.
            granted = permissions_for_role(role)
            missing = [p for p in required_permissions if p not in granted]

            if missing:
                return jsonify({
                    "error": "Acceso denegado.",
                    "message": "Tu rol no tiene permisos para realizar esta acción, varón.",
                    "role": role,
                    "missing_permissions": missing
                }), 403

            # Lo dejamos disponible para las vistas que quieran mostrarlo
            g.current_role = role
            g.current_permissions = granted

            return fn(*args, **kwargs)
        return decorator
    return wrapper


def any_permission_required(*required_permissions):
    """
    Variante permisiva: alcanza con tener UNO de los permisos indicados.
    Útil para pantallas que comparten comportamiento entre varios roles.
    """
    def wrapper(fn):
        @wraps(fn)
        def decorator(*args, **kwargs):
            verify_jwt_in_request()

            claims = get_jwt()
            role = claims.get("role")
            granted = permissions_for_role(role)

            if not any(p in granted for p in required_permissions):
                return jsonify({
                    "error": "Acceso denegado.",
                    "message": "Tu rol no tiene permisos para realizar esta acción, varón.",
                    "role": role
                }), 403

            g.current_role = role
            g.current_permissions = granted

            return fn(*args, **kwargs)
        return decorator
    return wrapper