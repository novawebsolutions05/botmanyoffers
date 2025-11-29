from flask import Flask, request, jsonify, render_template, send_file
from flask_cors import CORS
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import qrcode
import string
import random
import os
import uuid
import json
import base64

# SendGrid
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Attachment, TrackingSettings, ClickTracking, OpenTracking

app = Flask(__name__)
CORS(app)

# --- Configuración de Google Sheets ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

# Cargar credenciales desde la variable de entorno en Render
json_creds = os.environ.get("GOOGLE_CREDENTIALS")
creds_dict = json.loads(json_creds)

creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
SPREADSHEET_ID = "1huOU__jhatsGiP7RZ4zxDeevbYmI8fgh83B4fIJJNew"
sheet = client.open_by_key(SPREADSHEET_ID).sheet1

# Índices de columnas (basados en el orden de append_row)
CODE_COL = 4       # columna del código único
CANJEADO_COL = 8   # columna "Canjeado"

# --- Configuración de SendGrid ---
SENDGRID_KEY = os.getenv("SENDGRID_KEY")
SENDGRID_FROM = os.getenv("SENDGRID_FROM")  # ej: tu gmail verificado en SendGrid


# --- Generar código único ---
def generar_codigo_unico(longitud=8):
    caracteres = string.ascii_uppercase + string.digits
    return ''.join(random.choice(caracteres) for _ in range(longitud))


def send_email_with_qr(to_email, nombre, producto, qr_path, codigo_unico, monto, fecha, url_qr, qr_image_url):
    # Asunto sin caracteres especiales para evitar spam
    subject = f"Confirmacion de compra - Many Offers - {producto}"
    
    print(f"🔍 DEBUG: qr_path={qr_path}, qr_image_url={qr_image_url}")

    cuerpo_texto = f"""
Hola {nombre},

¡Gracias por tu compra en Many Offers!

Aquí tienes tu código QR para tu cupón de {producto}.
Cada código es único y válido solo una vez.

Detalles de tu compra:
- Producto: {producto}
- Monto: ${monto}
- Fecha: {fecha}
- Código: {codigo_unico}

Puedes presentar este código QR en el establecimiento para validar tu descuento.

¡Disfruta tu oferta!
"""

    try:
        # Leer la imagen QR y convertirla a base64 para attachment inline
        with open(qr_path, "rb") as f:
            qr_image_data = base64.b64encode(f.read()).decode('utf-8')
        
        # Crear el contenido HTML usando Content-ID para attachment inline
        # Este es el método más confiable para Gmail
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #ffffff;">
    <div style="background-color: #ffffff; padding: 20px;">
        <h2 style="color: #2A0066; margin-top: 0;">¡Gracias por tu compra en Many Offers!</h2>
        
        <p>Hola {nombre},</p>
        
        <p>Aquí tienes tu código QR para tu cupón de <strong>{producto}</strong>.</p>
        <p style="color: #666; font-size: 14px;">Cada código es único y válido solo una vez.</p>
        
        <div style="background-color: #f5f5f5; padding: 20px; border-radius: 10px; margin: 20px 0;">
            <h3 style="margin-top: 0; color: #2A0066;">Detalles de tu compra:</h3>
            <ul style="list-style: none; padding: 0; margin: 0;">
                <li style="margin: 10px 0;"><strong>Producto:</strong> {producto}</li>
                <li style="margin: 10px 0;"><strong>Monto:</strong> ${monto}</li>
                <li style="margin: 10px 0;"><strong>Fecha:</strong> {fecha}</li>
                <li style="margin: 10px 0;"><strong>Código:</strong> {codigo_unico}</li>
            </ul>
        </div>
        
        <div style="text-align: center; margin: 30px 0;">
            <p style="margin-bottom: 15px; font-weight: bold;">Puedes presentar este código QR en el establecimiento para validar tu descuento:</p>
            <!-- Usar URL directa como fuente principal -->
            <img src="{qr_image_url}" alt="QR del cupón" style="max-width: 300px; height: auto; border: 2px solid #2A0066; border-radius: 10px; padding: 10px; background-color: white; display: block; margin: 0 auto;" />
        </div>
        
        <div style="margin-top: 30px; padding: 15px; background-color: #f0f0f0; border-radius: 5px;">
            <p style="margin: 0; font-size: 14px;">O usar este enlace directo:</p>
            <p style="margin: 10px 0 0 0;"><a href="{url_qr}" style="color: #2A0066; word-break: break-all; text-decoration: none;">{url_qr}</a></p>
        </div>
        
        <p style="margin-top: 30px; color: #666; font-size: 14px;">¡Disfruta tu oferta!</p>
        
        <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">
        <p style="font-size: 12px; color: #999; text-align: center;">Este es un correo de confirmación de compra. Si no realizaste esta compra, por favor ignora este mensaje.</p>
    </div>
</body>
</html>
"""

        # Crear el mensaje usando SendGrid con URL directa
        # La URL es más confiable que attachments inline en muchos clientes
        message = Mail(
            from_email=SENDGRID_FROM,
            to_emails=to_email,
            subject=subject,
            plain_text_content=cuerpo_texto,
            html_content=html_content
        )
        
        # Configurar reply-to para evitar spam
        if SENDGRID_FROM:
            message.reply_to = SENDGRID_FROM
        
        # Agregar attachment inline como respaldo (aunque usamos URL principal)
        # Algunos clientes de correo prefieren attachments inline
        try:
            attachment = Attachment()
            attachment.file_content = qr_image_data
            attachment.file_type = "image/png"
            attachment.file_name = f"qr_{codigo_unico}.png"
            attachment.disposition = "inline"
            attachment.content_id = "qr_cupon"
            message.add_attachment(attachment)
            print(f"✅ Attachment QR agregado como respaldo con Content-ID: qr_cupon")
        except Exception as attach_error:
            print(f"⚠️ No se pudo agregar attachment (continuando con URL): {attach_error}")
            # No es crítico, la URL debería funcionar
        
        # Configurar tracking settings para mejor deliverability
        # Comentar temporalmente para evitar errores 400
        # try:
        #     tracking_settings = TrackingSettings()
        #     tracking_settings.click_tracking = ClickTracking(enable=True)
        #     tracking_settings.open_tracking = OpenTracking(enable=True)
        #     message.tracking_settings = tracking_settings
        # except Exception as tracking_error:
        #     print(f"⚠️ Advertencia: No se pudo configurar tracking settings: {tracking_error}")

        # Validar que SENDGRID_FROM esté configurado
        if not SENDGRID_FROM:
            raise ValueError("SENDGRID_FROM no está configurado")
        
        # Validar que SENDGRID_KEY esté configurado
        if not SENDGRID_KEY:
            raise ValueError("SENDGRID_KEY no está configurado")
        
        # Enviar el correo
        print(f"📧 Intentando enviar correo a {to_email} desde {SENDGRID_FROM}")
        sg = SendGridAPIClient(SENDGRID_KEY)
        response = sg.send(message)
        print(f"✅ Email enviado a {to_email} con código {codigo_unico}. Status SendGrid: {response.status_code}")

    except Exception as e:
        print("❌ Error enviando correo con SendGrid:", e)
        import traceback
        traceback.print_exc()


@app.route("/webhook", methods=["POST"])
def webhook():
    import re
    from datetime import datetime

    def clean_wix_value(value):
        """Limpia los valores que llegan desde Wix con funciones como JOIN(), TEXT(), etc."""
        if not isinstance(value, str):
            return value

        # Si es algo como JOIN(Nombre del ítem, ", ")
        if value.startswith("JOIN("):
            inner = re.findall(r"JOIN\((.*)\)", value)
            if inner:
                inner = inner[0]
                # eliminar comillas y paréntesis internos
                inner = inner.replace("'", "").replace('"', "").replace(",", ", ")
                return inner.strip()

        # Si es algo como TEXT(Valor total) o TEXT(Fecha...)
        if value.startswith("TEXT("):
            inner = re.findall(r"TEXT\((.*)\)", value)
            if inner:
                return inner[0].replace("'", "").replace('"', "").strip()

        return value.strip()

    # --- Recibir datos del webhook ---
    data = request.get_json(force=True, silent=True)
    if not data:
        data = request.form.to_dict()
    if "data" in data:
        data = data["data"]

    print("Datos recibidos:", data)
    print("Datos recibidos desde Wix:", data)

    nombre = clean_wix_value(data.get("nombre", ""))
    correo = clean_wix_value(data.get("correo", ""))
    productos = clean_wix_value(data.get("productos", ""))
    total = clean_wix_value(data.get("total", ""))
    fecha = clean_wix_value(data.get("fecha", ""))

    # --- Normalizar total (intentar convertirlo a número) ---
    try:
        total_num = float(re.sub(r"[^\d\.]", "", total))
    except:
        total_num = 0.0

    total_str = f"{total_num:,.2f}"

    # --- Normalizar fecha ---
    try:
        if "T" in fecha:
            fecha_obj = datetime.fromisoformat(fecha.replace("Z", ""))
            fecha = fecha_obj.strftime("%Y-%m-%d %H:%M:%S")
    except:
        pass

    print(f"\n🧾 Datos finales limpiados:\nNombre: {nombre}\nCorreo: {correo}\nProductos: {productos}\nTotal: {total_str}\nFecha: {fecha}\n")

    # 1️⃣ Generar código único
    codigo_unico = str(uuid.uuid4())[:8]  # genera un código único corto

    # 2️⃣ Crear URL personalizada
    url_qr = f"https://botmanyoffers.onrender.com/validar?codigo={codigo_unico}"

    # 3️⃣ Generar el código QR
    qr_img = qrcode.make(url_qr)
    qr_path = f"qr_{codigo_unico}.png"
    qr_img.save(qr_path)

    # 4️⃣ Guardar datos en Google Sheets
    sheet.append_row([nombre, correo, productos, codigo_unico, total_str, "-", fecha, "NO"])

    # 5️⃣ Crear URL pública para la imagen QR
    qr_url = f"https://botmanyoffers.onrender.com/qr/{codigo_unico}"

    # 6️⃣ Enviar email al cliente (con manejo de errores interno)
    send_email_with_qr(
        to_email=correo,
        nombre=nombre,
        producto=productos,
        qr_path=qr_path,
        codigo_unico=codigo_unico,
        monto=total_str,
        fecha=fecha,
        url_qr=url_qr,
        qr_image_url=qr_url
    )

    # 7️⃣ NO eliminar el archivo QR - se necesita para servir desde la URL pública

    return jsonify({"status": "success", "message": "Datos guardados, QR generado y correo procesado"}), 200


@app.route("/validar", methods=["POST"])
def validar():
    """
    Recibe un JSON con {"codigo": "ABC123"}.
    Si existe y no está canjeado -> marca "SI" y responde válido.
    Si ya estaba canjeado -> responde inválido.
    Si no existe -> 404.
    """
    payload = request.get_json(force=True, silent=True) or {}
    codigo = payload.get("codigo", "").strip()

    if not codigo:
        return jsonify({"status": "error", "message": "Código no enviado"}), 400

    try:
        # Traemos toda la columna de códigos
        codigos = sheet.col_values(CODE_COL)  # incluye encabezado

        if codigo not in codigos:
            return jsonify({"status": "error", "message": "Código no encontrado"}), 404

        # Fila exacta del código
        row = codigos.index(codigo) + 1  # +1 por encabezado

        # Estado actual del cupón
        estado_actual = (sheet.cell(row, CANJEADO_COL).value or "").strip().upper()

        if estado_actual == "SI":
            return jsonify({"status": "invalid", "message": "Este código ya fue canjeado"}), 403

        # Marcar como canjeado
        sheet.update_cell(row, CANJEADO_COL, "SI")

        return jsonify({"status": "valid", "message": "Código válido y marcado como canjeado"}), 200

    except Exception as e:
        print("Error en /validar:", e)
        return jsonify({"status": "error", "message": "Error interno"}), 500


@app.route("/web")
def web():
    return render_template("validador.html")


@app.route("/qr/<codigo>")
def servir_qr(codigo):
    """Sirve la imagen QR desde el servidor con headers optimizados para Gmail"""
    qr_path = f"qr_{codigo}.png"
    print(f"🔍 Intentando servir QR: {qr_path}, existe: {os.path.exists(qr_path)}")
    
    if os.path.exists(qr_path):
        response = send_file(qr_path, mimetype='image/png')
        # Headers críticos para que Gmail pueda cargar la imagen
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        response.headers['Content-Type'] = 'image/png'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        # Asegurar que la imagen sea accesible
        response.headers['Content-Disposition'] = 'inline'
        print(f"✅ QR servido correctamente: {qr_path}")
        return response
    else:
        print(f"❌ QR no encontrado: {qr_path}")
        return jsonify({"error": "QR no encontrado"}), 404


if __name__ == "__main__":
    print("Rutas registradas en Flask:")
    print(app.url_map)

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
