from database import db
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

# ==========================================
# MÓDULO DE USUARIOS Y CONTROL DE ACCESO
# ==========================================

# Nombres de los roles que maneja el sistema. El admin es el único protegido
# (no se puede borrar ni renombrar) porque es el que administra todo lo demás.
ROLE_ADMIN = 'admin'
ROLE_MUSICO = 'musico'
ROLE_CANTANTE = 'cantante'
ROLE_APOYO_LOGISTICO = 'apoyo_logistico'
ROLE_USUARIO = 'usuario'

# Roles que existían antes del módulo de roles y permisos.
# La migración los traduce a los nuevos para no dejar cuentas sin acceso.
LEGACY_ROLE_MAP = {
    'coordinator': ROLE_APOYO_LOGISTICO,
    'operator': ROLE_USUARIO,
}


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    # Guarda el NOMBRE del rol (ver tabla roles). Es lo que viaja en el JWT.
    role = db.Column(db.String(30), nullable=False, default=ROLE_USUARIO)
    is_active = db.Column(db.Boolean, default=True)

    # El usuario recién registrado debe confirmar su correo con un código
    # antes de poder iniciar sesión.
    is_verified = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relaciones de auditoría para saber quién creó qué
    events = db.relationship('Event', backref='creator', lazy=True)
    files = db.relationship('File', backref='creator', lazy=True)
    songs = db.relationship('Song', backref='creator', lazy=True)

    auth_codes = db.relationship(
        'VerificationCode',
        back_populates='user',
        cascade='all, delete-orphan',
        lazy='dynamic'
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class VerificationCode(db.Model):
    """
    Códigos numéricos de un solo uso enviados por email.

    Se usan para dos flujos distintos, differentiation por 'purpose':
      - 'register'       -> confirmar el correo al registrarse
      - 'password_reset' -> autorizar el cambio de contraseña

    Solo queda activo el último código emitido por usuario y propósito
    (los anteriores se invalidan), y expira a los minutos indicados en
    la columna expires_at.
    """
    __tablename__ = 'verification_codes'

    PURPOSES = ('register', 'password_reset')

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)

    # 'register' o 'password_reset'
    purpose = db.Column(db.String(20), nullable=False)

    # Código de 6 dígitos enviados al correo
    code = db.Column(db.String(6), nullable=False)

    # Intentos fallidos restantes antes de bloquear el código
    attempts_left = db.Column(db.Integer, nullable=False, default=5)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)

    user = db.relationship('User', back_populates='auth_codes')

    @property
    def is_expired(self):
        return datetime.utcnow() >= self.expires_at

    @property
    def is_usable(self):
        return self.attempts_left > 0 and not self.is_expired


class TokenBlocklist(db.Model):
    """
    Tabla requerida por Flask-JWT-Extended para almacenar tokens revocados (Logout).
    Mantiene un índice rápido sobre el identificador único del JWT (jti).
    """
    __tablename__ = 'token_blocklist'

    id = db.Column(db.Integer, primary_key=True)
    jti = db.Column(db.String(36), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ==========================================
# MÓDULO DE ROLES Y PERMISOS (RBAC)
# ==========================================

class Role(db.Model):
    """
    Catálogo de roles del sistema.

    'users.role' guarda el nombre de un registro de esta tabla, así que el
    nombre es la clave de negocio. El rol admin viene con is_protected=True:
    no se puede borrar ni renombrar y siempre conserva todos los permisos,
    porque es el que administra el resto del sistema.
    """
    __tablename__ = 'roles'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30), unique=True, nullable=False, index=True)
    description = db.Column(db.String(200), nullable=True)

    # Roles base del proyecto: no se pueden eliminar (sigue el CRUD)
    is_system = db.Column(db.Boolean, nullable=False, default=False)

    # El admin no se puede tocar ni borrar
    is_protected = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    permission_links = db.relationship(
        'RolePermission',
        back_populates='role',
        cascade='all, delete-orphan'
    )

    @property
    def permissions(self):
        """Permisos del rol como lista de claves."""
        return [link.permission.key for link in self.permission_links]


class Permission(db.Model):
    """
    Catálogo de permisos. Son las acciones atómicas que se pueden asignar a un
    rol (ej: 'events.create'). El catálogo se carga desde seeds.py; el admin
    administra qué permisos tiene cada rol, no el catálogo en sí.
    """
    __tablename__ = 'permissions'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(60), unique=True, nullable=False, index=True)
    label = db.Column(db.String(80), nullable=False)

    # Módulo al que pertenece, para agrupar la matriz (Eventos, Canciones, ...)
    module = db.Column(db.String(40), nullable=False)
    description = db.Column(db.String(200), nullable=True)

    role_links = db.relationship(
        'RolePermission',
        back_populates='permission',
        cascade='all, delete-orphan'
    )


class RolePermission(db.Model):
    """Tabla puente N a N: qué permisos tiene cada rol."""
    __tablename__ = 'role_permissions'

    role_id = db.Column(
        db.Integer,
        db.ForeignKey('roles.id', ondelete='CASCADE'),
        primary_key=True
    )
    permission_id = db.Column(
        db.Integer,
        db.ForeignKey('permissions.id', ondelete='CASCADE'),
        primary_key=True
    )

    role = db.relationship('Role', back_populates='permission_links')
    permission = db.relationship('Permission', back_populates='role_links')


# ==========================================
# MÓDULOS DEL CORE (CANCIONES Y REPERTORIO)
# ==========================================

class Author(db.Model):
    __tablename__ = 'authors'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    songs = db.relationship('Song', backref='author', lazy=True)


class Genre(db.Model):
    __tablename__ = 'genres'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    songs = db.relationship('Song', backref='genre', lazy=True)


class Song(db.Model):
    __tablename__ = 'songs'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    
    genre_id = db.Column(db.Integer, db.ForeignKey('genres.id'), nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey('authors.id'), nullable=False)
    
    # CORRECCIÓN: Clave foránea para la relación User.songs
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    structure = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    files_association = db.relationship('FileSong', back_populates='song', cascade="all, delete-orphan")


class FileSong(db.Model):
    __tablename__ = 'file_songs'
    
    file_id = db.Column(db.Integer, db.ForeignKey('files.id', ondelete='CASCADE'), primary_key=True)
    song_id = db.Column(db.Integer, db.ForeignKey('songs.id', ondelete='CASCADE'), primary_key=True)
    position = db.Column(db.Integer, nullable=False, default=1)

    song = db.relationship('Song', back_populates='files_association')


class File(db.Model):
    __tablename__ = 'files'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    tematica = db.Column(db.String(150), nullable=True)
    
    # CORRECCIÓN: Clave foránea para la relación User.files
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    songs_association = db.relationship(
        'FileSong', 
        backref='file', 
        lazy='dynamic', 
        cascade="all, delete-orphan"
    )

    @property
    def ordered_songs(self):
        return [assoc.song for assoc in self.songs_association.order_by(FileSong.position).all()]
    

# ==========================================
# MÓDULOS DE LOGÍSTICA Y EVENTOS
# ==========================================

class Event(db.Model):
    __tablename__ = 'events'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    date = db.Column(db.Date, nullable=False)
    time = db.Column(db.Time, nullable=False)
    target_audience = db.Column(db.String(50), nullable=False, default="General")
    
    # CORRECCIÓN: Clave foránea para la relación User.events
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    guests_count = db.Column(db.Integer, nullable=True, default=0)
    estimated_logistic_budget = db.Column(db.Numeric(10, 2), nullable=True, default=0.00)
    
    itinerary = db.Column(db.JSON, nullable=True, default=list)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    staff = db.relationship('EventStaff', back_populates='event', cascade="all, delete-orphan")
    inventory_assignments = db.relationship('EventInventory', back_populates='event', cascade="all, delete-orphan")
    groups = db.relationship('ParticipantGroup', backref='event', cascade="all, delete-orphan")
    participants = db.relationship('Participant', backref='event', cascade="all, delete-orphan")


class EventInventory(db.Model):
    __tablename__ = 'event_inventory'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id', ondelete="CASCADE"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey('inventory_items.id', ondelete="CASCADE"), nullable=False)
    quantity_used = db.Column(db.Numeric(10, 2), nullable=False, default=1.0) 

    event = db.relationship('Event', back_populates='inventory_assignments')
    item = db.relationship('InventoryItem', back_populates='event_assignments')


class EventStaff(db.Model):
    __tablename__ = 'event_staff'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id', ondelete="CASCADE"), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(80), nullable=False)

    event = db.relationship('Event', back_populates='staff')


class InventoryItem(db.Model):
    __tablename__ = 'inventory_items'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    category = db.Column(db.String(50), nullable=True)
    total_stock = db.Column(db.Numeric(10, 2), nullable=False, default=0.0)
    unit_of_measure = db.Column(db.String(20), nullable=False, default="N/A")
    is_consumable = db.Column(db.Boolean, nullable=False, default=False)
    price_per_unit = db.Column(db.Numeric(10, 2), nullable=False, default=0.00)
    
    event_assignments = db.relationship('EventInventory', back_populates='item', cascade="all, delete-orphan")


class ParticipantGroup(db.Model):
    __tablename__ = 'participant_groups'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id', ondelete="CASCADE"), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    
    logistics_to_bring = db.Column(db.JSON, nullable=True, default=list)
    monetary_contribution = db.Column(db.Numeric(10, 2), nullable=False, default=0.00)
    contribution_status = db.Column(db.String(20), nullable=False, default="Pendiente")

    participants = db.relationship('Participant', back_populates='group', cascade="all, delete-orphan")


class Participant(db.Model):
    __tablename__ = 'participants'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id', ondelete="CASCADE"), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey('participant_groups.id', ondelete="SET NULL"), nullable=True)
    
    name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(120), nullable=True)
    
    logistics_to_bring = db.Column(db.JSON, nullable=True, default=list)
    monetary_contribution = db.Column(db.Numeric(10, 2), nullable=False, default=0.00)
    contribution_status = db.Column(db.String(20), nullable=False, default="Pendiente") 
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    group = db.relationship('ParticipantGroup', back_populates='participants')