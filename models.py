from database import db
from datetime import datetime

# alembic revision --autogenerate -m "tablas base y repertorios"
# alembic upgrade head

class Author(db.Model):
    __tablename__ = 'authors'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    
    # Relación: Un autor puede tener muchas canciones
    songs = db.relationship('Song', backref='author', lazy=True)


class Genre(db.Model):
    __tablename__ = 'genres'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    
    # Relación: Un género puede estar en muchas canciones
    songs = db.relationship('Song', backref='genre', lazy=True)


class Song(db.Model):
    __tablename__ = 'songs'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    
    genre_id = db.Column(db.Integer, db.ForeignKey('genres.id'), nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey('authors.id'), nullable=False)
    
    structure = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # REPARACIÓN AQUÍ: Conecta de vuelta con la tabla intermedia
    files_association = db.relationship('FileSong', back_populates='song', cascade="all, delete-orphan")

# ==========================================
# TABLA INTERMEDIA PARA EL ORDEN NUMÉRICO
# ==========================================
class FileSong(db.Model):
    __tablename__ = 'file_songs'
    
    file_id = db.Column(db.Integer, db.ForeignKey('files.id', ondelete='CASCADE'), primary_key=True)
    song_id = db.Column(db.Integer, db.ForeignKey('songs.id', ondelete='CASCADE'), primary_key=True)
    
    # Manejo del orden directo por números
    position = db.Column(db.Integer, nullable=False, default=1)

    # REPARACIÓN AQUÍ: Relación directa para poder hacer `assoc.song`
    song = db.relationship('Song', back_populates='files_association')
# ==========================================
# MODELO FILE (REPERTORIOS / LISTAS)
# ==========================================
class File(db.Model):
    __tablename__ = 'files'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)              # Nombre de la lista/repertorio
    tematica = db.Column(db.String(150), nullable=True)           # Temática del evento o playlist
    created_at = db.Column(db.DateTime, default=datetime.utcnow)   # Fecha de creación
    
    # Relación directa con la tabla intermedia
    songs_association = db.relationship(
        'FileSong', 
        backref='file', 
        lazy='dynamic', 
        cascade="all, delete-orphan"
    )

    @property
    def ordered_songs(self):
        """
        Retorna los objetos Song ordenados numéricamente por la posición
        """
        return [assoc.song for assoc in self.songs_association.order_by(FileSong.position).all()]
    
class Event(db.Model):
    __tablename__ = 'events'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    date = db.Column(db.Date, nullable=False)
    time = db.Column(db.Time, nullable=False)
    target_audience = db.Column(db.String(50), nullable=False, default="General")
    
    # --- ÚNICOS CAMPOS NUEVOS EN LA BD ---
    # Para guardar el aforo y el presupuesto estimado del evento como histórico informativo
    guests_count = db.Column(db.Integer, nullable=True, default=0)
    estimated_logistic_budget = db.Column(db.Numeric(10, 2), nullable=True, default=0.00)
    
    itinerary = db.Column(db.JSON, nullable=True, default=list)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    staff = db.relationship('EventStaff', back_populates='event', cascade="all, delete-orphan")
    inventory_assignments = db.relationship('EventInventory', back_populates='event', cascade="all, delete-orphan")

# EventInventory vuelve a ser el pivote rígido original que amarra SOLO items reales de la DB:
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
    
    # Unidad de medida e indicadores de consumo
    unit_of_measure = db.Column(db.String(20), nullable=False, default="N/A")
    is_consumable = db.Column(db.Boolean, nullable=False, default=False)
    
    # --- NUEVO APARTADO DE PRECIO ---
    # Precio unitario en USD
    price_per_unit = db.Column(db.Numeric(10, 2), nullable=False, default=0.00)
    
    event_assignments = db.relationship('EventInventory', back_populates='item', cascade="all, delete-orphan")

