"""
Datos iniciales de demostración para VibePlanner-Back.

Uso:
    source .venv/bin/activate
    flask --app app seed

El seed es IDEMPOTENTE: se puede correr las veces que haga falta sin duplicar
registros. Para empezar de cero se puede agregar --reset, que borra primero los
datos de las tablas sembradas.
"""
from datetime import date, time, datetime, timedelta

from database import db
from models import (
    User,
    Author,
    Genre,
    Song,
    SONG_STATUS_PUBLICADA,
    File,
    FileSong,
    Event,
    EventStaff,
    EventInventory,
    InventoryItem,
    ParticipantGroup,
    Participant,
    TokenBlocklist,
    VerificationCode,
    Role,
    Permission,
    RolePermission,
)
from werkzeug.security import generate_password_hash

from utils.permissions import (
    ROLE_DEFINITIONS,
    catalog_as_dicts,
    default_permissions_for,
)

from services.transpose_service import detect_key

DEFAULT_PASSWORD = "vibeplanner123"

# ==========================================
# USUARIOS
# ==========================================
USERS = [
    {
        "username": "admin_vibe",
        "email": "admin@vibeplanner.com",
        "role": "admin",
        "is_verified": True,
    },
    {
        "username": "logistica_ana",
        "email": "logistica@vibeplanner.com",
        "role": "apoyo_logistico",
        "is_verified": True,
    },
    {
        "username": "musico_tomas",
        "email": "musico@vibeplanner.com",
        "role": "musico",
        "is_verified": True,
    },
    {
        "username": "cantante_luis",
        "email": "cantante@vibeplanner.com",
        "role": "cantante",
        "is_verified": True,
    },
    {
        "username": "invitado_maria",
        "email": "usuario@vibeplanner.com",
        "role": "usuario",
        "is_verified": True,
    },
    {
        # Sin verificar: sirve para probar el flujo de verificación por email
        "username": "op_pendiente",
        "email": "pendiente@vibeplanner.com",
        "role": "usuario",
        "is_verified": False,
    },
]

# ==========================================
# GÉNEROS Y AUTORES
# ==========================================
GENRES = ["Pop", "Rock", "Disco", "Reggaetón", "Bolero", "Salsa", "Electrónica", "Folclore"]

AUTHORS = [
    "ABBA",
    "The Beatles",
    "Daft Punk",
    "Gustavo Cerati",
    "Joan Manuel Serrat",
    "La Oreja de Van Gogh",
    "Rubén Blades",
    "Bad Bunny",
    "Francisco Canario",
    "Atahualpa Yupanqui",
]

# ==========================================
# CANCIONES: (nombre, autor, género, partes)
# ==========================================
SONGS = [
    ("Dancing Queen", "ABBA", "Disco", [
        ("verso 1", "Stop the music, stop the music\n'Cause it's driving me crazy\n'Cause I cannot stand it no more"),
        ("pre coro", "Ooohhh, ooohhh\nStop the music, stop the music\n'Cause it's driving me crazy"),
        ("coro", "You are the dancing queen\nYou are the dancing queen\nHold the crown from the king\nOh, oh, oh"),
    ]),
    ("Let It Be", "The Beatles", "Pop", [
        ("verso 1", "When I find myself in times of trouble\nMother Mary comes to me"),
        ("coro", "Let it be, let it be\nLet it be, let it be\nWhisper words of wisdom, let it be"),
    ]),
    ("Around the World", "Daft Punk", "Electrónica", [
        ("intro", "Around the world, around the world\nAround the world, around the world"),
        ("coro", "Around the world\nAround the world"),
    ]),
    ("Persiana Americana", "Gustavo Cerati", "Rock", [
        ("verso 1", "Fue ayer, fue una noche rara\nY el mundo al revés"),
        ("coro", "Persiana americana\nSombra en el living"),
    ]),
    ("La víctima", "Joan Manuel Serrat", "Bolero", [
        ("verso 1", "Hola, fue una noche de otoño\nCaminamos por la rambla"),
        ("coro", "La víctima es siempre la última\nY la única que nunca ha sido"),
    ]),
    ("Rosas", "La Oreja de Van Gogh", "Pop", [
        ("verso 1", "Yo quisiera tener diecisiete rosas\nPara dartelas a vos"),
        ("coro", "Rosas, rosas\nDulces rosas"),
    ]),
    ("Cuando seas mía", "Rubén Blades", "Salsa", [
        ("verso 1", "Dicen que nadie te va a querer\nCuando seas mía van a ver"),
        ("coro", "Cuando seas mía, nena\nMañana será mejor"),
    ]),
    ("Tití Me Preguntó", "Bad Bunny", "Reggaetón", [
        ("verso 1", "Eh, na, na na na na\nAyy yo no soy de esos"),
        ("coro", "Tití me preguntó por qué no le escribo\nTití me preguntó si todavía la quiero"),
    ]),
    ("Desde el alma", "Francisco Canario", "Salsa", [
        ("coro", "Desde el alma te expreso mi sentir\nY con esta copla te quiero decir"),
    ]),
    ("Antiguos dueños", "Atahualpa Yupanqui", "Folclore", [
        ("verso 1", "Antiguos dueños de las tierras\nQue ya no existen los que fueron"),
        ("coro", "Antiguos dueños, así cantonaba\nCaminando voy"),
    ]),
]

# ==========================================
# INVENTARIO
# ==========================================
INVENTORY = [
    ("Agua Mineral 500ml", "Bebidas", 120, "botellas", False, 2.50),
    ("Cerveza Artesanal IPA", "Bebidas", 48, "botellas", True, 6.00),
    ("Vino Tinto Malbec", "Bebidas", 24, "botellas", True, 18.00),
    ("Papas Fritas", "Comida", 30, "paquetes", True, 4.50),
    ("Pizzas Congeladas", "Comida", 15, "unidades", True, 7.00),
    ("Helado Artesanal", "Comida", 20, "potes", True, 5.50),
    ("Micrófonos Inalámbricos", "Audio", 4, "unidades", False, 120.00),
    ("Parlantes 1000W", "Audio", 2, "unidades", False, 450.00),
    ("Mesa de Sombrilla", "Mobiliario", 10, "unidades", False, 80.00),
    ("Paño / Manteles", "Decoración", 30, "unidades", False, 5.00),
    ("Pilas AA", "Accesorios", 200, "unidades", True, 0.50),
    ("Extintor", "Seguridad", 2, "unidades", False, 60.00),
]

# ==========================================
# EVENTOS
# ==========================================
EVENTS = [
    {
        "name": "Festival Invierno Vibe 2026",
        "days_from_today": 25,
        "time": "18:00",
        "target_audience": "Jóvenes 18-35",
        "guests_count": 350,
        "estimated_logistic_budget": 18500.00,
        "itinerary": [
            {"time": "18:00", "type": "generic", "name": "Acreditación y control de acceso"},
            {"time": "18:30", "type": "song", "name": "Let It Be", "song_id": 2},
            {"time": "19:00", "type": "file", "name": "Setlist Apertura", "file_id": 1},
            {"time": "21:30", "type": "generic", "name": "Show de luces"},
            {"time": "22:00", "type": "song", "name": "Dancing Queen", "song_id": 1},
        ],
        "staff": [
            {"email": "logistica@vibeplanner.com", "role": "Coordinador General"},
            {"email": "musico@vibeplanner.com", "role": "Sonido en Vivo"},
        ],
        "inventory": [
            ("Agua Mineral 500ml", 40),
            ("Cerveza Artesanal IPA", 24),
            ("Micrófonos Inalámbricos", 2),
        ],
        "groups": [
            {
                "name": "Staff Producción",
                "monetary_contribution": 1200.00,
                "contribution_status": "Pagado",
                "logistics_to_bring": [
                    {"id": "log-a1", "item": "Paño / Manteles", "quantity": 4, "entregado": True},
                    {"id": "log-a2", "item": "Extintor", "quantity": 1, "entregado": False},
                ],
                "members": [
                    {"name": "Sofía Ramírez", "email": "sofia.ramirez@correo.com", "monetary_contribution": 300.00},
                    {"name": "Diego Fernández", "email": "diego.fernandez@correo.com", "monetary_contribution": 300.00},
                ],
            },
            {
                "name": "Barra de Bebidas",
                "monetary_contribution": 800.00,
                "contribution_status": "Pendiente",
                "logistics_to_bring": [
                    {"id": "log-b1", "item": "Hielo en bolsa", "quantity": 10, "entregado": True},
                ],
                "members": [
                    {"name": "Martina López", "email": "martina.lopez@correo.com", "monetary_contribution": 200.00},
                ],
            },
        ],
        "solo_participants": [
            {"name": "Nicolás Duarte", "email": "nicolas.duarte@correo.com", "monetary_contribution": 150.00,
             "contribution_status": "Pagado"},
            {"name": "Camila Herrera", "email": None, "monetary_contribution": 0.00,
             "contribution_status": "Pendiente",
             "logistics_to_bring": [{"id": "log-c1", "item": "Pilas AA", "quantity": 20, "entregado": False}]},
        ],
    },
    {
        "name": "Boda Ana & Carlos",
        "days_from_today": 60,
        "time": "14:00",
        "target_audience": "Familia extendida",
        "guests_count": 120,
        "estimated_logistic_budget": 9200.00,
        "itinerary": [
            {"time": "14:00", "type": "generic", "name": "Recepción de invitados"},
            {"time": "15:00", "type": "song", "name": "La víctima", "song_id": 5},
            {"time": "18:00", "type": "song", "name": "Desde el Alma", "song_id": 9},
            {"time": "20:00", "type": "generic", "name": "Cena y brindis"},
            {"time": "22:00", "type": "song", "name": "Rosas", "song_id": 6},
        ],
        "staff": [
            {"email": "admin@vibeplanner.com", "role": "Coordinador General"},
        ],
        "inventory": [
            ("Vino Tinto Malbec", 18),
            ("Pizzas Congeladas", 10),
            ("Helado Artesanal", 12),
            ("Mesa de Sombrilla", 6),
        ],
        "groups": [
            {
                "name": "Los Novios",
                "monetary_contribution": 3000.00,
                "contribution_status": "Pagado",
                "logistics_to_bring": [
                    {"id": "log-d1", "item": "Paño / Manteles", "quantity": 10, "entregado": True},
                ],
                "members": [
                    {"name": "Ana Gómez", "email": "ana.gomez@correo.com", "monetary_contribution": 1500.00},
                    {"name": "Carlos Pérez", "email": "carlos.perez@correo.com", "monetary_contribution": 1500.00},
                ],
            },
        ],
        "solo_participants": [
            {"name": "Familia González", "email": "gonzalez.familia@correo.com", "monetary_contribution": 600.00,
             "contribution_status": "Pagado"},
            {"name": "Los primos", "email": None, "monetary_contribution": 450.00,
             "contribution_status": "Pendiente"},
        ],
    },
]

# ==========================================
# CANCIONEROS (files) con sus canciones
# ==========================================
FILES = [
    {
        "name": "Setlist Apertura Festival",
        "tematica": "Pop y Rock en vivo para abrir el festival",
        "songs": ["Let It Be", "Persiana Americana", "Rosas", "Dancing Queen"],
    },
    {
        "name": "Boda Acústico 2026",
        "tematica": "Bolero y Salsa para la recepción",
        "songs": ["La víctima", "Desde el alma", "Cuando seas mía"],
    },
    {
        "name": "Reggaetón Nocturno",
        "tematica": "Hits urbanos para el cierre",
        "songs": ["Tití Me Preguntó", "Around the World"],
    },
]


def _get_or_create(model, defaults, **filters):
    """Devuelve el registro si ya existe; si no, lo crea."""
    instance = model.query.filter_by(**filters).first()
    if instance:
        return instance, False

    instance = model(**defaults)
    db.session.add(instance)
    return instance, True


def _upsert(model, defaults, **filters):
    instance, created = _get_or_create(model, defaults, **filters)
    if created:
        db.session.commit()
    return instance, created


def reset_database():
    """Borra los datos de las tablas que usa el seed (deja el esquema intacto)."""
    db.session.query(VerificationCode).delete()
    db.session.query(TokenBlocklist).delete()
    db.session.query(Participant).delete()
    db.session.query(ParticipantGroup).delete()
    db.session.query(EventInventory).delete()
    db.session.query(EventStaff).delete()
    db.session.query(Event).delete()
    db.session.query(FileSong).delete()
    db.session.query(File).delete()
    db.session.query(Song).delete()
    db.session.query(InventoryItem).delete()
    db.session.query(Genre).delete()
    db.session.query(Author).delete()
    db.session.query(User).delete()
    db.session.query(RolePermission).delete()
    db.session.query(Role).delete()
    db.session.query(Permission).delete()
    db.session.commit()
    print("[SEED] Tabras vaciadas correctamente.")


def seed_users():
    created_count = 0
    for spec in USERS:
        user, created = _upsert(
            User,
            {
                "username": spec["username"],
                "email": spec["email"],
                "password_hash": generate_password_hash(DEFAULT_PASSWORD, method='pbkdf2:sha256'),
                "role": spec["role"],
                "is_active": True,
                "is_verified": spec["is_verified"],
                "created_at": datetime.utcnow(),
            },
            email=spec["email"]
        )
        if created:
            created_count += 1

    print(f"[SEED] Usuarios: {len(USERS)} totales ({created_count} nuevos).")
    print(f"[SEED] Contraseña de todas las cuentas sembradas: '{DEFAULT_PASSWORD}'")


def seed_taxonomy():
    for name in GENRES:
        _upsert(Genre, {"name": name}, name=name)

    for name in AUTHORS:
        _upsert(Author, {"name": name}, name=name)

    print(f"[SEED] Géneros: {len(GENRES)} · Autores: {len(AUTHORS)}")


def seed_songs():
    created = 0
    tuned = 0
    for name, author_name, genre_name, parts in SONGS:
        existing = Song.query.filter_by(name=name).first()

        # Las canciones que ya existían antes de la moderación no tienen tono.
        # Se lo deducimos de la estructura, pero sin pisar un tono ya cargado.
        if existing:
            if not existing.key and existing.structure:
                existing.key = detect_key(existing.structure)
                if existing.key:
                    tuned += 1
            continue

        author = Author.query.filter_by(name=author_name).first()
        genre = Genre.query.filter_by(name=genre_name).first()
        if not author or not genre:
            print(f"[SEED] ⚠ Canción '{name}' salteada: falta autor o género.")
            continue

        structure = {"parts": [{"title": t, "content": c} for t, c in parts]}

        db.session.add(Song(
            name=name,
            author_id=author.id,
            genre_id=genre.id,
            structure=structure,
            # Las canciones de demo van publicadas: si quedaran en borrador y sin
            # dueño, solo las vería un moderador y el catálogo se vería vacío.
            key=detect_key(structure),
            status=SONG_STATUS_PUBLICADA,
            created_at=datetime.utcnow()
        ))
        created += 1

    db.session.commit()
    print(
        f"[SEED] Canciones: {Song.query.count()} totales "
        f"({created} nuevas, {tuned} con tono deducido)."
    )


def seed_inventory():
    created = 0
    for name, category, stock, unit, consumable, price in INVENTORY:
        _, was_created = _upsert(
            InventoryItem,
            {
                "name": name,
                "category": category,
                "total_stock": stock,
                "unit_of_measure": unit,
                "is_consumable": consumable,
                "price_per_unit": price,
            },
            name=name
        )
        if was_created:
            created += 1

    print(f"[SEED] Inventario: {InventoryItem.query.count()} artículos ({created} nuevos).")


def seed_files():
    created = 0
    for spec in FILES:
        cancionero, was_created = _upsert(
            File,
            {"name": spec["name"], "tematica": spec["tematica"], "created_at": datetime.utcnow()},
            name=spec["name"]
        )
        if not was_created:
            continue

        for position, song_name in enumerate(spec["songs"], start=1):
            song = Song.query.filter_by(name=song_name).first()
            if not song:
                continue
            cancionero.songs_association.append(FileSong(song_id=song.id, position=position))

        db.session.commit()
        created += 1

    print(f"[SEED] Cancioneros: {File.query.count()} totales ({created} nuevos).")


def seed_events():
    created = 0
    for spec in EVENTS:
        existing = Event.query.filter_by(name=spec["name"]).first()
        if existing:
            continue

        owner = User.query.filter_by(role="admin").first()

        nuevo = Event(
            name=spec["name"],
            date=date.today() + timedelta(days=spec["days_from_today"]),
            time=time.fromisoformat(spec["time"]),
            target_audience=spec["target_audience"],
            guests_count=spec["guests_count"],
            estimated_logistic_budget=spec["estimated_logistic_budget"],
            itinerary=spec["itinerary"],
            user_id=owner.id if owner else None,
            created_at=datetime.utcnow()
        )

        for member in spec["staff"]:
            nuevo.staff.append(EventStaff(email=member["email"], role=member["role"]))

        for item_name, quantity in spec["inventory"]:
            item = InventoryItem.query.filter_by(name=item_name).first()
            if not item:
                print(f"[SEED] ⚠ Evento '{spec['name']}': ítem '{item_name}' no encontrado.")
                continue
            nuevo.inventory_assignments.append(
                EventInventory(item_id=item.id, quantity_used=quantity)
            )

        db.session.add(nuevo)
        db.session.commit()
        created += 1

        # Grupos y participantes se crean después porque necesitan el id del evento
        for group_spec in spec["groups"]:
            grupo = ParticipantGroup(
                event_id=nuevo.id,
                name=group_spec["name"],
                logistics_to_bring=group_spec.get("logistics_to_bring", []),
                monetary_contribution=group_spec["monetary_contribution"],
                contribution_status=group_spec["contribution_status"]
            )
            db.session.add(grupo)
            db.session.commit()

            for member in group_spec["members"]:
                db.session.add(Participant(
                    event_id=nuevo.id,
                    group_id=grupo.id,
                    name=member["name"],
                    email=member["email"],
                    logistics_to_bring=[],
                    monetary_contribution=member["monetary_contribution"],
                    contribution_status="Pagado" if member["monetary_contribution"] > 0 else "Pendiente"
                ))

        for solo in spec["solo_participants"]:
            db.session.add(Participant(
                event_id=nuevo.id,
                group_id=None,
                name=solo["name"],
                email=solo["email"],
                logistics_to_bring=solo.get("logistics_to_bring", []),
                monetary_contribution=solo["monetary_contribution"],
                contribution_status=solo["contribution_status"]
            ))

        db.session.commit()

    print(f"[SEED] Eventos: {Event.query.count()} totales ({created} nuevos) "
          f"· Participantes: {Participant.query.count()}")


def seed_roles_and_permissions():
    """Carga el catálogo de permisos y los 5 roles con su matriz de fábrica."""
    # 1. Catálogo de permisos (se agrega lo nuevo, no se toca lo existente)
    created_permissions = 0
    for spec in catalog_as_dicts():
        _, was_created = _upsert(
            Permission,
            {
                "key": spec["key"],
                "label": spec["label"],
                "module": spec["module"],
                "description": spec["description"],
            },
            key=spec["key"]
        )
        if was_created:
            created_permissions += 1

    # 2. Roles del sistema
    for name, description, is_protected in ROLE_DEFINITIONS:
        role, _ = _upsert(
            Role,
            {
                "name": name,
                "description": description,
                "is_system": True,
                "is_protected": is_protected,
                "created_at": datetime.utcnow(),
            },
            name=name
        )

        # Solo le cargamos la matriz si el rol todavía no tiene permisos:
        # si el admin los personalizó, el seed no le pisa el trabajo.
        if RolePermission.query.filter_by(role_id=role.id).count() > 0:
            continue

        for key in default_permissions_for(role.name):
            permission = Permission.query.filter_by(key=key).first()
            if permission:
                db.session.add(RolePermission(role_id=role.id, permission_id=permission.id))
        db.session.commit()

    # 3. Cualquier usuario con un rol que ya no existe vuelve a 'usuario'
    catalog_names = {r.name for r in Role.query.all()}
    for user in User.query.all():
        if user.role not in catalog_names:
            user.role = "usuario"
    db.session.commit()

    print(f"[SEED] Permisos: {Permission.query.count()} ({created_permissions} nuevos) "
          f"· Roles: {Role.query.count()}")


def seed_database(reset: bool = False):
    """Punto de entrada del seed."""
    print("[SEED] Cargando datos iniciales de VibePlanner...")
    if reset:
        reset_database()

    # Los roles van primero: los usuarios guardan el nombre del rol
    seed_roles_and_permissions()
    seed_users()
    seed_taxonomy()
    seed_songs()
    seed_inventory()
    seed_files()
    seed_events()

    print("[SEED] ✔ Listo.")


if __name__ == '__main__':
    from app import app

    with app.app_context():
        seed_database()