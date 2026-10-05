"""
Catálogo de permisos y matriz por defecto de los roles.

Este módulo es la única fuente de verdad de:
  - Qué permisos existen en el sistema (PERMISSION_CATALOG).
  - Qué permisos tiene cada rol de fábrica (DEFAULT_ROLE_PERMISSIONS).

El admin puede cambiar la matriz en caliente desde el panel de roles; esto
solo define el punto de partida que se carga en el seed y sirve para el botón
"restaurar por defecto".
"""
from database import db
from models import Permission, Role, RolePermission, ROLE_ADMIN

# ==========================================
# CATÁLOGO DE PERMISOS
# Formato: (key, label, módulo, descripción)
# ==========================================
PERMISSION_CATALOG = [
    # --- Panel ---
    ("dashboard.view", "Ver panel de control", "Panel", "Entrar al dashboard y ver el estado general"),

    # --- Canciones ---
    ("songs.view", "Ver canciones", "Canciones", "Consultar el catálogo de canciones"),
    ("songs.create", "Crear canciones", "Canciones", "Cargar canciones nuevas, incluso con IA"),
    ("songs.edit", "Editar canciones", "Canciones", "Modificar letras, acordes y estructura"),
    ("songs.delete", "Eliminar canciones", "Canciones", "Borrar canciones del catálogo"),
    ("songs.suggest", "Sugerir arreglos", "Canciones", "Proponer cambios de letra, acordes o tono en una canción"),
    ("songs.moderate", "Moderar canciones", "Canciones", "Revisar canciones en revisión y resolver las sugerencias del equipo"),
    ("songs.manage_catalog", "Administrar géneros y autores", "Canciones", "Crear y editar los listados de géneros y autores"),

    # --- Repertorio (cancioneros) ---
    ("files.view", "Ver cancioneros", "Repertorio", "Consultar los cancioneros y sus setlists"),
    ("files.create", "Crear cancioneros", "Repertorio", "Armar nuevos cancioneros"),
    ("files.edit", "Editar cancioneros", "Repertorio", "Modificar el orden y el contenido de un cancionero"),
    ("files.delete", "Eliminar cancioneros", "Repertorio", "Borrar cancioneros"),
    ("files.export", "Exportar cancioneros", "Repertorio", "Descargar el cancionero compilado en PDF"),

    # --- Eventos ---
    ("events.view", "Ver eventos", "Eventos", "Consultar eventos, itinerarios y asistentes"),
    ("events.create", "Crear eventos", "Eventos", "Agendar nuevos eventos con su hoja logística"),
    ("events.edit", "Editar eventos", "Eventos", "Modificar datos, staff, inventario y asistentes"),
    ("events.delete", "Eliminar eventos", "Eventos", "Borrar eventos"),

    # --- Inventario ---
    ("inventory.view", "Ver inventario", "Inventario", "Consultar el catálogo de bodega"),
    ("inventory.create", "Crear artículos", "Inventario", "Registrar artículos en la bodega"),
    ("inventory.edit", "Editar artículos", "Inventario", "Modificar stock y datos de los artículos"),
    ("inventory.delete", "Eliminar artículos", "Inventario", "Borrar artículos de la bodega"),

    # --- Gastos ---
    ("expenses.view", "Ver gastos", "Gastos", "Consultar el balance de gastos de los eventos"),

    # --- Usuarios ---
    ("users.view", "Ver usuarios", "Usuarios", "Listar las cuentas del sistema"),
    ("users.create", "Crear usuarios", "Usuarios", "Dar de alta cuentas sin pasar por el registro público"),
    ("users.edit", "Editar usuarios", "Usuarios", "Cambiar datos, rol y estado de las cuentas"),
    ("users.delete", "Eliminar usuarios", "Usuarios", "Borrar cuentas del sistema"),

    # --- Roles y permisos ---
    ("roles.view", "Ver roles", "Roles", "Consultar los roles y su matriz de permisos"),
    ("roles.manage", "Administrar roles", "Roles", "Crear roles y cambiar qué permisos tiene cada uno"),

    # --- IA ---
    ("ai.use", "Usar asistentes de IA", "Inteligencia Artificial", "VibeAI y generación de canciones con IA"),
]

# ==========================================
# ROLES DEL SISTEMA
# Formato: (name, description, is_protected)
# El admin es el único protegido: no se borra ni se le toca la matriz.
# ==========================================
ROLE_DEFINITIONS = [
    ("admin", "Control total del sistema: usuarios, roles, eventos, inventario y gastos", True),
    ("musico", "Carga y edita canciones, arma cancioneros y usa los asistentes de IA", False),
    ("cantante", "Ajusta su material (canciones y cancioneros) y usa los asistentes de IA", False),
    ("apoyo_logistico", "Coordina eventos, controla la bodega y revisa los gastos", False),
    ("usuario", "Acceso de solo lectura al panel, las canciones y los eventos", False),
]

# Orden de los módulos para armar la matriz de checkboxes
MODULE_ORDER = [
    "Panel",
    "Eventos",
    "Canciones",
    "Repertorio",
    "Inventario",
    "Gastos",
    "Usuarios",
    "Roles",
    "Inteligencia Artificial",
]

# ==========================================
# MATRIZ POR DEFECTO
# El admin tiene SIEMPRE todos los permisos (se resuelve al vuelo, no se
# necesita guardar cada uno en la base).
# ==========================================
DEFAULT_ROLE_PERMISSIONS = {
    ROLE_ADMIN: "*",  # '*' = todos los permisos del catálogo

    # Usuario común: solo mirar el panel y el material general
    "usuario": [
        "dashboard.view",
        "songs.view",
        "files.view",
        "events.view",
    ],

    # Músico: carga y edita canciones, arma cancioneros y modera el repertorio
    "musico": [
        "dashboard.view",
        "songs.view",
        "songs.create",
        "songs.edit",
        "songs.suggest",
        "songs.moderate",
        "files.view",
        "files.create",
        "files.edit",
        "files.export",
        "events.view",
        "ai.use",
    ],

    # Cantante: canta, así que ajusta su material, propone arreglos y tiene AI
    "cantante": [
        "dashboard.view",
        "songs.view",
        "songs.edit",
        "songs.suggest",
        "files.view",
        "files.export",
        "events.view",
        "ai.use",
    ],

    # Apoyo logístico: IU de eventos, bodega y balance de gastos
    "apoyo_logistico": [
        "dashboard.view",
        "events.view",
        "events.create",
        "events.edit",
        "inventory.view",
        "inventory.create",
        "inventory.edit",
        "expenses.view",
        "ai.use",
    ],
}


def catalog_as_dicts():
    """Permisos del catálogo listos para insertar en la base."""
    return [
        {"key": key, "label": label, "module": module, "description": description}
        for key, label, module, description in PERMISSION_CATALOG
    ]


def default_permissions_for(role_name: str):
    """Permisos de fábrica de un rol como lista de claves (admin = todas)."""
    configured = DEFAULT_ROLE_PERMISSIONS.get(role_name)
    if configured == "*":
        return [key for key, _, _, _ in PERMISSION_CATALOG]
    return list(configured or [])


def permissions_for_role(role_name: str):
    """
    Permisos efectivos de un rol, leídos de la base.

    El admin siempre resuelve a la lista completa aunque la matriz guardada
    esté incompleta: es la red de seguridad para no dejar al sistema sin
    ningún administrador.
    """
    if role_name == ROLE_ADMIN:
        return {key for key, _, _, _ in PERMISSION_CATALOG}

    rows = (
        db.session.query(Permission.key)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(Role, Role.id == RolePermission.role_id)
        .filter(Role.name == role_name)
        .all()
    )
    return {row[0] for row in rows}


def permissions_by_module():
    """Catálogo agrupado por módulo y en el orden de MODULE_ORDER."""
    grouped = {}
    for key, label, module, description in PERMISSION_CATALOG:
        grouped.setdefault(module, []).append({
            "key": key,
            "label": label,
            "description": description
        })

    ordered = {module: grouped[module] for module in MODULE_ORDER if module in grouped}

    # Módulos nuevos que no estén en MODULE_ORDER, al final
    for module, items in grouped.items():
        ordered.setdefault(module, items)

    return ordered