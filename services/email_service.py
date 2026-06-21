# services/email_service import os
import os
import yagmail
from dotenv import load_dotenv

load_dotenv()

class EmailNotifierService:
    def __init__(self):
        self.user = os.getenv("GMAIL_USER")
        self.password = os.getenv("GMAIL_APP_PASSWORD")
        
    def send_production_sheet(self, recipient_list: list, event_data: dict):
        """
        Envía la hoja de producción completa (Itinerario + Inventario) al Staff.
        """
        if not recipient_list or not self.user or not self.password:
            return False

        # Inicializar cliente yagmail
        yag = yagmail.SMTP(self.user, self.password)

        # 1. Armar filas del Itinerario
        itinerary_rows = ""
        for block in event_data.get('itinerary', []):
            badge_color = "#6366f1" if block.get('type') == 'song' else "#10b981" if block.get('type') == 'file' else "#f59e0b"
            itinerary_rows += f"""
            <tr style="border-bottom: 1px solid #334155;">
                <td style="padding: 10px; font-family: monospace; color: #a5b4fc;">{block.get('time')}</td>
                <td style="padding: 10px;">
                    <span style="background-color: {badge_color}20; color: {badge_color}; border: 1px solid {badge_color}40; padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: bold; text-transform: uppercase;">
                        {block.get('type')}
                    </span>
                </td>
                <td style="padding: 10px; color: #f1f5f9; font-weight: bold;">{block.get('name')}</td>
            </tr>
            """
        if not itinerary_rows:
            itinerary_rows = "<tr><td colspan='3' style='padding: 15px; text-align: center; color: #64748b;'>No se asignaron bloques aún.</td></tr>"

        # 2. Armar filas del Inventario
        inventory_rows = ""
        for item in event_data.get('inventory', []):
            inventory_rows += f"""
            <tr style="border-bottom: 1px solid #334155;">
                <td style="padding: 10px; color: #f1f5f9;">{item.get('name', f'ID Item: {item.get("item_id")}')}</td>
                <td style="padding: 10px; text-align: right; font-family: monospace; color: #38bdf8; font-weight: bold;">
                    {item.get('quantity')} {item.get('unit', 'uds')}
                </td>
            </tr>
            """
        if not inventory_rows:
            inventory_rows = "<tr><td colspan='2' style='padding: 15px; text-align: center; color: #64748b;'>No se solicitaron recursos de bodega.</td></tr>"

        # 3. Diseño Maquetado en HTML (Estilo VibePlanner)
        html_content = f"""
        <html>
            <body style="font-family: sans-serif; background-color: #0f172a; color: #e2e8f0; padding: 20px; margin: 0;">
                <div style="max-width: 650px; margin: 0 auto; background-color: #1e293b; border: 1px solid #334155; padding: 30px; border-radius: 16px; box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3);">
                    
                    <div style="border-b: 2px solid #334155; padding-bottom: 15px; margin-bottom: 20px;">
                        <span style="font-size: 10px; font-weight: bold; color: #6366f1; text-transform: uppercase; tracking-wider: 0.1em;">VibePlanner Production</span>
                        <h1 style="font-size: 24px; font-weight: 900; color: #ffffff; margin: 5px 0 0 0; text-transform: uppercase;">Hoja de Ruta Logística</h1>
                    </div>

                    # Datos Generales
                    <div style="background-color: #0f172a; border: 1px solid #1e293b; padding: 15px; border-radius: 10px; margin-bottom: 25px;">
                        <h3 style="margin: 0 0 10px 0; font-size: 16px; color: #f1f5f9; text-transform: uppercase;">{event_data.get('name')}</h3>
                        <p style="margin: 4px 0; font-size: 13px; color: #94a3b8;">📅 <strong>Fecha:</strong> {event_data.get('date')}</p>
                        <p style="margin: 4px 0; font-size: 13px; color: #94a3b8;">⏰ <strong>Inicio:</strong> {event_data.get('time')}</p>
                        <p style="margin: 4px 0; font-size: 13px; color: #94a3b8;">👥 <strong>Target:</strong> {event_data.get('target_audience')}</p>
                    </div>

                    # Tabla Itinerario
                    <h4 style="color: #6366f1; text-transform: uppercase; font-size: 12px; margin-bottom: 10px; letter-spacing: 1px;">📍 Cronograma del Show</h4>
                    <table style="w: 100%; width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 25px; text-align: left;">
                        <thead>
                            <tr style="background-color: #0f172a; color: #94a3b8; font-size: 11px; text-transform: uppercase;">
                                <th style="padding: 8px 10px;">Hora</th>
                                <th style="padding: 8px 10px;">Tipo</th>
                                <th style="padding: 8px 10px;">Bloque / Canción</th>
                            </tr>
                        </thead>
                        <tbody>
                            {itinerary_rows}
                        </tbody>
                    </table>

                    # Tabla Inventario
                    <h4 style="color: #38bdf8; text-transform: uppercase; font-size: 12px; margin-bottom: 10px; letter-spacing: 1px;">📦 Insumos y Equipos de Bodega</h4>
                    <table style="w: 100%; width: 100%; border-collapse: collapse; font-size: 13px; text-align: left;">
                        <thead>
                            <tr style="background-color: #0f172a; color: #94a3b8; font-size: 11px; text-transform: uppercase;">
                                <th style="padding: 8px 10px;">Descripción del Recurso</th>
                                <th style="padding: 8px 10px; text-align: right;">Cantidad</th>
                            </tr>
                        </thead>
                        <tbody>
                            {inventory_rows}
                        </tbody>
                    </table>

                    <div style="margin-top: 30px; border-top: 1px solid #334155; padding-top: 15px; text-align: center;">
                        <p style="font-size: 11px; color: #64748b; margin: 0;">Este es un despacho automatizado de asignación táctica para VibePlanner.</p>
                    </div>
                </div>
            </body>
        </html>
        """

        asunto = f"📢 Orden de Producción: {event_data.get('name')}"

        # Enviar en copia oculta (Bcc) usando yagmail para cuidar la privacidad de correos
        yag.send(
            bcc=recipient_list,
            subject=asunto,
            contents=html_content
        )
        return True