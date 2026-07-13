from functools import wraps
from flask import jsonify
from flask_jwt_extended import verify_jwt_in_request, get_jwt

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