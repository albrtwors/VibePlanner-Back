# routes/routes_event.py
import threading
from flask import Blueprint, request, jsonify
from datetime import datetime
from database import db
from models import Event, EventStaff, EventInventory, InventoryItem
from services.ai_event_service import AssistantService
from services.email_service import EmailNotifierService
event_bp = Blueprint('event_bp', __name__)
assistant_service = AssistantService() # <-- 2. Instancias el asistente global

# ... (Tus rutas previas: GET /api/events, POST /api/events, DELETE /api/events/<id>, GET /api/events/<id>) ...


# ==========================================
# POST - CHAT ASISTENTE VIRTUAL (EXTRACCIÓN)
# ==========================================
@event_bp.route('/api/assistant/chat', methods=['POST'])
def assistant_chat():
    data = request.get_json() or {}
    user_input = data.get('message', '')
    
    if not user_input.strip():
        return jsonify({"error": "No enviaste ningún mensaje, varón."}), 400
        
    try:
        # El servicio ahora devuelve un diccionario con 'extracted_data' y 'message'
        result = assistant_service.process_prompt(user_input)
        
        # Desestructuramos directamente en la respuesta HTTP
        return jsonify({
            "message": result["message"],
            "extracted_data": result["extracted_data"]
        }), 200
        
    except Exception as e:
        print(f"Error crítico en el asistente de eventos: {str(e)}")
        return jsonify({"error": "Fallo interno al procesar el dictado por IA."}), 500
# ==========================================
# GET - LISTAR EVENTOS (CON QUERY PARAMS)
# ==========================================
@event_bp.route('/api/events', methods=['GET'])
def get_events():
    query = Event.query
    
    # Filtros por Query Params
    start_date = request.args.get('start_date') # YYYY-MM-DD
    end_date = request.args.get('end_date')
    audience = request.args.get('audience')
    
    if start_date:
        query = query.filter(Event.date >= datetime.strptime(start_date, '%Y-%m-%d').date())
    if end_date:
        query = query.filter(Event.date <= datetime.strptime(end_date, '%Y-%m-%d').date())
    if audience:
        query = query.filter(Event.target_audience.ilike(f"%{audience}%"))
        
    events = query.order_by(Event.date.asc()).all()
    
    payload = []
    for e in events:
        payload.append({
            "id": e.id,
            "name": e.name,
            "date": e.date.isoformat(),
            "time": e.time.strftime('%H:%M:%S'),
            "target_audience": e.target_audience,
            "itinerary": e.itinerary,
            "staff": [{"email": s.email, "role": s.role} for s in e.staff],
            "inventory": [{
                "item_id": inv.item_id,
                "name": inv.item.name,
                "quantity_used": float(inv.quantity_used),
                "unit": inv.item.unit_of_measure
            } for inv in e.inventory_assignments]
        })
        
    return jsonify(payload), 200

# ==========================================
# POST - CREAR EVENTO CON VALIDACIÓN DE STOCK
# ==========================================
notifier = EmailNotifierService()

@event_bp.route('/api/events', methods=['POST'])
def create_event():
    data = request.get_json() or {}
    
    # Validaciones básicas de tu ruta original
    required = ['name', 'date', 'time']
    if not all(k in data for k in required):
        return jsonify({"error": "Faltan campos mandatorios (name, date, time), varón."}), 400
        
    try:
        fecha_obj = datetime.strptime(data['date'], '%Y-%m-%d').date()
        hora_obj = datetime.strptime(data['time'], '%H:%M').time()
    except ValueError:
        return jsonify({"error": "Formatos inválidos. Use YYYY-MM-DD para date y HH:MM para time."}), 400

    # 1. Instanciar la cabecera del Evento (Con el JSON directo como lo tienes)
    nuevo_evento = Event(
        name=data['name'],
        date=fecha_obj,
        time=hora_obj,
        target_audience=data.get('target_audience', 'General'),
        itinerary=data.get('itinerary', []) # <--- JSON directo a la columna, sin inventar modelos raros
    )

    # 2. Procesar e Inyectar Staff + Recopilar correos para el Mail
    staff_list = data.get('staff', [])
    emails_a_notificar = []
    for member in staff_list:
        if 'email' in member and 'role' in member:
            email_limpio = member['email'].strip()
            staff_obj = EventStaff(email=email_limpio, role=member['role'])
            nuevo_evento.staff.append(staff_obj) 
            emails_a_notificar.append(email_limpio)

    # 3. Procesar, Validar y Amarrar el Inventario Relacional
    inventory_requests = data.get('inventory', [])
    items_hidratados_para_mail = [] 
    
    for req in inventory_requests:
        item_id = req.get('item_id')
        qty_requested = req.get('quantity', 1.0)
        
        item = InventoryItem.query.get(item_id)
        if not item:
            return jsonify({"error": f"El artículo con ID {item_id} no existe en la DB."}), 404
            
        # Validación atómica de stock original
        if qty_requested > item.total_stock:
            return jsonify({
                "error": f"Falta de stock para '{item.name}'. Pediste {qty_requested} {item.unit_of_measure} pero solo hay {item.total_stock} disponibles."
            }), 400
            
        # Fila pivote usando tu relación original
        asignacion = EventInventory(item_id=item_id, quantity_used=qty_requested)
        nuevo_evento.inventory_assignments.append(asignacion)

        # Hidratación para el Mail
        items_hidratados_para_mail.append({
            "name": item.name,
            "quantity": qty_requested,
            "unit": item.unit_of_measure
        })

    # 4. Impactar Base de Datos
    try:
        db.session.add(nuevo_evento)
        db.session.commit() 

        # 5. DISPARAR CORREO EN SEGUNDO PLANO
        lista_final_correos = list(set(emails_a_notificar))
        
        if lista_final_correos:
            payload_mail = {
                "name": nuevo_evento.name,
                "date": data['date'], 
                "time": data['time'],
                "target_audience": nuevo_evento.target_audience,
                "itinerary": data.get('itinerary', []), # Pasamos el mismo array plano JSON
                "inventory": items_hidratados_para_mail
            }
            
            # El hilo asíncrono para que ruede transparente
            email_thread = threading.Thread(
                target=notifier.send_production_sheet,
                args=(lista_final_correos, payload_mail)
            )
            email_thread.start()

        return jsonify({
            "message": "¡Evento agendado con éxito, varón!", 
            "event_id": nuevo_evento.id,
            "notificados_count": len(lista_final_correos)
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Fallo al guardar en base de datos: {str(e)}"}), 500
    data = request.get_json() or {}
    
    # Validaciones básicas de metadata de tu ruta original
    required = ['name', 'date', 'time']
    if not all(k in data for k in required):
        return jsonify({"error": "Faltan campos mandatorios (name, date, time), varón."}), 400
        
    try:
        fecha_obj = datetime.strptime(data['date'], '%Y-%m-%d').date()
        hora_obj = datetime.strptime(data['time'], '%H:%M').time()
    except ValueError:
        return jsonify({"error": "Formatos inválidos. Use YYYY-MM-DD para date y HH:MM para time."}), 400

    # 1. Instanciar la cabecera del Evento (Tu lógica ORM intacta)
    nuevo_evento = Event(
        name=data['name'],
        date=fecha_obj,
        time=hora_obj,
        target_audience=data.get('target_audience', 'General'),
        itinerary=data.get('itinerary', []) # Recibe el array híbrido de Next.js
    )

    # 2. Procesar e Inyectar Staff + Recopilar correos para el hilo
    staff_list = data.get('staff', [])
    emails_a_notificar = []
    for member in staff_list:
        if 'email' in member and 'role' in member:
            email_limpio = member['email'].strip()
            staff_obj = EventStaff(email=email_limpio, role=member['role'])
            nuevo_evento.staff.append(staff_obj) # Relación nativa
            emails_a_notificar.append(email_limpio)

    # 3. Procesar, Validar y Amarrar el Inventario Relacional
    inventory_requests = data.get('inventory', [])
    items_hidratados_para_mail = [] # <-- Para mandarle los nombres reales al yagmail
    
    for req in inventory_requests:
        item_id = req.get('item_id')
        qty_requested = req.get('quantity', 1.0)
        
        item = InventoryItem.query.get(item_id)
        if not item:
            return jsonify({"error": f"El artículo con ID {item_id} no existe en la DB."}), 404
            
        # Tu validación atómica de stock original
        if qty_requested > item.total_stock:
            return jsonify({
                "error": f"Falta de stock para '{item.name}'. Pediste {qty_requested} {item.unit_of_measure} pero solo hay {item.total_stock} disponibles."
            }), 400
            
        # Creamos la fila pivote usando tu relación original
        asignacion = EventInventory(item_id=item_id, quantity_used=qty_requested)
        nuevo_evento.inventory_assignments.append(asignacion)

        # Guardamos los strings legibles para el HTML del correo
        items_hidratados_para_mail.append({
            "name": item.name,
            "quantity": qty_requested,
            "unit": item.unit_of_measure
        })

    # 4. Impactar Base de Datos de manera limpia
    try:
        db.session.add(nuevo_evento)
        db.session.commit() # Aquí SQLAlchemy guarda todo en cascada y genera el ID

        # 5. DISPARAR CORREO EN SEGUNDO PLANO (THREADING ASÍNCRONO)
        lista_final_correos = list(set(emails_a_notificar))
        
        if lista_final_correos:
            payload_mail = {
                "name": nuevo_evento.name,
                "date": data['date'], # Pasamos el string limpio para el HTML
                "time": data['time'],
                "target_audience": nuevo_evento.target_audience,
                "itinerary": data.get('itinerary', []),
                "inventory": items_hidratados_para_mail
            }
            
            # Levantar el hilo de fondo para que Next.js no se quede esperando a yagmail
            email_thread = threading.Thread(
                target=notifier.send_production_sheet,
                args=(lista_final_correos, payload_mail)
            )
            email_thread.start()

        return jsonify({
            "message": "¡Evento agendado con éxito, varón!", 
            "event_id": nuevo_evento.id,
            "notificados_count": len(lista_final_correos)
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Fallo al guardar en base de datos: {str(e)}"}), 500
# ==========================================
# DELETE - ELIMINAR EVENTO (LIMPIA EN CASCADA)
# ==========================================
@event_bp.route('/api/events/<int:event_id>', methods=['DELETE'])
def delete_event(event_id):
    evento = Event.query.get(event_id)
    if not evento:
        return jsonify({"error": "Evento no encontrado."}), 404
        
    db.session.delete(evento)
    db.session.commit() # El CASCADE de la DB limpia automáticamente event_staff y event_inventory
    return jsonify({"message": "Evento eliminado del sistema correctamente."}), 200

# ==========================================
# GET - DETALLE DE UN EVENTO ESPECÍFICO
# ==========================================
@event_bp.route('/api/events/<int:event_id>', methods=['GET'])
def get_event_detail(event_id):
    evento = Event.query.get(event_id)
    if not evento:
        return jsonify({"error": "El evento solicitado no existe, varón."}), 404

    # Mapeamos estructuradamente respetando los tipos de datos
    payload = {
        "id": evento.id,
        "name": evento.name,
        "date": evento.date.isoformat(), # YYYY-MM-DD
        "time": evento.time.strftime('%H:%M:%S'),
        "target_audience": evento.target_audience,
        "itinerary": evento.itinerary, # Carga el JSON directo
        "staff": [
            {
                "id": s.id,
                "email": s.email,
                "role": s.role
            } for s in evento.staff
        ],
        # Compaginamos con la interfaz del Front mapeando desde inventory_assignments
        "inventory_assignments": [
            {
                "item_id": inv.item_id,
                "item_name": inv.item.name,
                "category": inv.item.category,
                "quantity_used": float(inv.quantity_used), # Asegura compatibilidad con JSON decimal
                "unit_of_measure": inv.item.unit_of_measure
            } for inv in evento.inventory_assignments
        ]
    }

    return jsonify(payload), 200