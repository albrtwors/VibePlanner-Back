# services/email_service import os
import os
import yagmail
from dotenv import load_dotenv

load_dotenv()

class EmailNotifierService:
    def __init__(self):
        self.user = os.getenv("GMAIL_USER")
        self.password = os.getenv("GMAIL_APP_PASSWORD")
        
    # ------------------------------------------------------------------
    # UTILIDADES PARA AUTENTICACIÓN (VERIFICACIÓN DE EMAIL / RESET)
    # ------------------------------------------------------------------
    @property
    def is_configured(self) -> bool:
        """True si hay credenciales de Gmail cargadas."""
        return bool(self.user and self.password)

    def send_auth_code(self, recipient: str, code: str, purpose: str, username: str = "") -> bool:
        """
        Envía el código numérico de un solo uso para dos flujos:
          - purpose='register'       -> verificación del correo al registrarse
          - purpose='password_reset' -> autorización para cambiar la contraseña

        Si no hay credenciales configuradas devuelve False y deja el código
        únicamente en la consola (modo desarrollo).
        """
        if not self.is_configured:
            print(f"[MAIL-DEV] Sin credenciales de Gmail. No se pudo enviar el código a {recipient}.")
            return False

        if purpose == 'password_reset':
            asunto = f"🔐 Restablecé tu contraseña de VibePlanner (código: {code})"
            titulo = "Recuperación de Acceso"
            intro = "Recibimos un pedido para restablecer la contraseña de tu cuenta."
            cierre = "Si no solicitaste este cambio, ignorá este mensaje y tu contraseña seguirá siendo la misma."
        else:
            asunto = f"✉️ Confirmá tu correo en VibePlanner (código: {code})"
            titulo = "Verificación de Correo"
            intro = "Confirmá este correo para activar tu cuenta de VibePlanner."
            cierre = "Si no creaste esta cuenta, podés ignorar este mensaje sin consecuencias."

        saludo = f"Hola {username}," if username else "Hola,"

        html_content = f"""
        <html>
            <body style="font-family: sans-serif; background-color: #0f172a; color: #e2e8f0; padding: 20px; margin: 0;">
                <div style="max-width: 650px; margin: 0 auto; background-color: #1e293b; border: 1px solid #334155; padding: 30px; border-radius: 16px;">
                    <div style="border-bottom: 2px solid #334155; padding-bottom: 15px; margin-bottom: 20px;">
                        <span style="font-size: 10px; font-weight: bold; color: #6366f1; text-transform: uppercase; letter-spacing: 0.1em;">VibePlanner</span>
                        <h1 style="font-size: 24px; font-weight: 900; color: #ffffff; margin: 5px 0 0 0; text-transform: uppercase;">{titulo}</h1>
                    </div>

                    <p style="font-size: 14px; color: #cbd5e1; margin: 0 0 10px 0;">{saludo}</p>
                    <p style="font-size: 14px; color: #94a3b8; margin: 0 0 25px 0; line-height: 1.6;">{intro}</p>

                    <div style="background-color: #0f172a; border: 1px solid #334155; border-radius: 12px; padding: 25px; text-align: center;">
                        <p style="font-size: 10px; font-weight: bold; color: #64748b; text-transform: uppercase; letter-spacing: 0.2em; margin: 0 0 12px 0;">Tu código de seguridad</p>
                        <p style="font-family: monospace; font-size: 42px; font-weight: bold; color: #ffffff; letter-spacing: 12px; margin: 0;">{code}</p>
                    </div>

                    <p style="font-size: 12px; color: #64748b; margin: 25px 0 0 0; line-height: 1.6;">
                        El código expira en breve y solo puede usarse una vez. {cierre}
                    </p>

                    <div style="margin-top: 30px; border-top: 1px solid #334155; padding-top: 15px; text-align: center;">
                        <p style="font-size: 11px; color: #64748b; margin: 0;">Este es un mensaje automatizado de seguridad de VibePlanner.</p>
                    </div>
                </div>
            </body>
        </html>
        """

        yag = yagmail.SMTP(self.user, self.password)
        yag.send(to=recipient, subject=asunto, contents=html_content)
        return True

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