from flask import Flask, request, jsonify, render_template
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
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib


# SendGrid
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

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


def send_email_with_qr(to_email, nombre, producto, qr_path, codigo_unico, monto, fecha, url_qr):
    subject = f"Tu cupón de Many Offers: {producto}"

    cuerpo_texto = f"""
    Hola {nombre},

    ¡Gracias por tu compra en Many Offers!

    Aquí tienes tu código QR para tu cupón de **{producto}**.
    Cada código es único y válido solo una vez.

    Detalles de tu compra:
    - Producto: {producto}
    - Monto: ${monto}
    - Fecha: {fecha}
    - Código: {codigo_unico}

    Presenta este código QR en el establecimiento para validar tu descuento.

    ¡Disfruta tu oferta!
    """

    try:
        # Crear el mensaje de correo
        msg = MIMEMultipart()
        msg["From"] = SENDGRID_FROM
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(cuerpo_texto, "plain"))

        # Adjuntar la imagen del QR
        with open(qr_path, "rb") as f:
            img = MIMEImage(f.read())
            img.add_header("Content-ID", "<qr>")
            msg.attach(img)

        # Agregar la URL al cuerpo del mensaje
        html_content = f"""
        <html>
        <body>
            <p>{cuerpo_texto}</p>
            <img src="cid:qr" alt="QR del cupón" />
            <p>O usar este enlace directo:</p>
            <a href="{url_qr}">{url_qr}</a>
        </body>
        </html>
        """
        msg.attach(MIMEText(html_content, "html"))


        sg = SendGridAPIClient(SENDGRID_KEY)
        response = sg.send(message)
        print(f"✅ Email enviado a {to_email} con código {codigo_unico}. Status SendGrid: {response.status_code}")

    except Exception as e:
        print("❌ Error enviando correo con SendGrid:", e)


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

    # 5️⃣ Enviar email al cliente (con manejo de errores interno)
    send_email_with_qr(
        to_email=correo,
        nombre=nombre,
        producto=productos,
        qr_path=qr_path,
        codigo_unico=codigo_unico,
        monto=total_str,
        fecha=fecha,
        url_qr=url_qr
    )

    # 6️⃣ Limpiar archivo QR local
    try:
        os.remove(qr_path)
    except Exception as e:
        print("No se pudo eliminar el archivo QR local:", e)

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


if __name__ == "__main__":
    print("Rutas registradas en Flask:")
    print(app.url_map)

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
