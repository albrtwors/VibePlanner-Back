# routes/routes_inventory.py
from flask import Blueprint, request, jsonify
from database import db
from models import InventoryItem
from services.ai_inventory_service import procesar_extraccion_inventario_ia
inventory_bp = Blueprint('inventory_bp', __name__)

# ==========================================
# 1. GET - LISTAR ARTÍCULOS (CON QUERY PARAMS)
# ==========================================
@inventory_bp.route('/api/inventory/upload-ia', methods=['POST'])
def bulk_upload_inventory_ia():
    data = request.get_json() or {}
    user_prompt = data.get('prompt')
    
    if not user_prompt:
        return jsonify({"error": "No se ingresó ninguna instrucción para procesar."}), 400
        
    # Invocar al servicio extractor de IA
    resultado_ia = procesar_extraccion_inventario_ia(user_prompt)
    
    if not resultado_ia["success"]:
        return jsonify({"error": resultado_ia["error"]}), 422
        
    items_creados = []
    items_ignorados = []
    
    # Iterar e inyectar en la base de datos de manera segura
    for item_data in resultado_ia["items"]:
        # Evitar colisión de nombres duplicados
        existente = InventoryItem.query.filter_by(name=item_data['name']).first()
        if existente:
            items_ignorados.append(item_data['name'])
            continue
            
        nuevo_item = InventoryItem(
            name=item_data['name'],
            category=item_data['category'],
            total_stock=item_data['total_stock'],
            unit_of_measure=item_data['unit_of_measure'],
            is_consumable=item_data['is_consumable']
        )
        db.session.add(nuevo_item)
        items_creados.append(item_data['name'])
        
    if items_creados:
        db.session.commit()
        
    return jsonify({
        "message": f"Procesamiento logístico completado. Se crearon {len(items_creados)} artículos.",
        "added_items": items_creados,
        "skipped_items": items_ignorados,
        "ai_summary": resultado_ia["summary"]
    }), 201

@inventory_bp.route('/api/inventory', methods=['GET'])
def get_inventory():
    query = InventoryItem.query
    
    category = request.args.get('category')
    is_consumable = request.args.get('is_consumable')
    search = request.args.get('search')
    
    if category:
        query = query.filter(InventoryItem.category.ilike(f"%{category}%"))
    if is_consumable is not None:
        query = query.filter(InventoryItem.is_consumable == (is_consumable.lower() == 'true'))
    if search:
        query = query.filter(InventoryItem.name.ilike(f"%{search}%"))
        
    items = query.all()
    
    payload = [{
        "id": i.id,
        "name": i.name,
        "category": i.category,
        "total_stock": float(i.total_stock),
        "unit_of_measure": i.unit_of_measure,
        "is_consumable": i.is_consumable
    } for i in items]
    
    return jsonify(payload), 200


# ==========================================
# 2. GET - DETALLE DE UN ARTÍCULO (NUEVA)
# ==========================================
@inventory_bp.route('/api/inventory/<int:item_id>', methods=['GET'])
def get_inventory_item(item_id):
    item = InventoryItem.query.get(item_id)
    if not item:
        return jsonify({"error": "El artículo no existe en la bodega, varón."}), 404
        
    return jsonify({
        "id": item.id,
        "name": item.name,
        "category": item.category,
        "total_stock": float(item.total_stock),
        "unit_of_measure": item.unit_of_measure,
        "is_consumable": item.is_consumable
    }), 200


# ==========================================
# 3. POST - CREAR NUEVO ARTÍCULO
# ==========================================
@inventory_bp.route('/api/inventory', methods=['POST'])
def create_inventory_item():
    data = request.get_json() or {}
    
    if not data.get('name') or data.get('total_stock') is None:
        return jsonify({"error": "Faltan campos obligatorios: name y total_stock, varón."}), 400
        
    if InventoryItem.query.filter_by(name=data['name']).first():
        return jsonify({"error": "Ese artículo ya existe en el inventario."}), 400

    nuevo_item = InventoryItem(
        name=data['name'],
        category=data.get('category'),
        total_stock=data['total_stock'],
        unit_of_measure=data.get('unit_of_measure', 'N/A'),
        is_consumable=data.get('is_consumable', False)
    )
    
    db.session.add(nuevo_item)
    db.session.commit()
    return jsonify({"message": "Artículo registrado en el almacén.", "id": nuevo_item.id}), 201


# ==========================================
# 4. PUT - ACTUALIZAR ARTÍCULO (NUEVA)
# ==========================================
@inventory_bp.route('/api/inventory/<int:item_id>', methods=['PUT'])
def update_inventory_item(item_id):
    item = InventoryItem.query.get(item_id)
    if not item:
        return jsonify({"error": "No puedes actualizar un artículo inexistente."}), 404
        
    data = request.get_json() or {}
    
    # Validar que si cambia el nombre, no choque con otro existente
    nuevo_nombre = data.get('name')
    if nuevo_nombre and nuevo_nombre != item.name:
        if InventoryItem.query.filter_by(name=nuevo_nombre).first():
            return jsonify({"error": "Ya existe otro artículo con ese nombre en la base de datos."}), 400
        item.name = nuevo_nombre

    # Modificación flexible de campos remanentes
    if 'category' in data: item.category = data['category']
    if 'total_stock' in data: item.total_stock = data['total_stock']
    if 'unit_of_measure' in data: item.unit_of_measure = data['unit_of_measure']
    if 'is_consumable' in data: item.is_consumable = data['is_consumable']

    db.session.commit()
    return jsonify({"message": "Insumo de inventario actualizado correctamente."}), 200


# ==========================================
# 5. DELETE - ELIMINAR ARTÍCULO (NUEVA)
# ==========================================
@inventory_bp.route('/api/inventory/<int:item_id>', methods=['DELETE'])
def delete_inventory_item(item_id):
    item = InventoryItem.query.get(item_id)
    if not item:
        return jsonify({"error": "El artículo que intentas borrar no existe."}), 404
        
    try:
        db.session.delete(item)
        db.session.commit()
        return jsonify({"message": f"Artículo '{item.name}' removido de la bodega con éxito."}), 200
    except Exception as e:
        # Esto previene caídas si el ítem está amarrado por clave foránea a una orden de un evento
        db.session.rollback()
        return jsonify({
            "error": "No se puede eliminar el artículo porque está asignado a eventos activos. Desvincúlalo primero, varón."
        }), 400