from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import qrcode
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
import string
import random
import os
import uuid
import json
from dotenv import load_dotenv
import threading

# Cargar variables de entorno
load_dotenv()

app = Flask(__name__)
CORS(app)

# --- Configuración de Google Sheets ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
json_creds = os.environ.get("GOOGLE_CREDENTIALS")
creds_dict = json.loads(json_creds)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)

SPREADSHEET_ID = "1huOU__jhatsGiP7RZ4zxDeevbYmI8fgh83B4fIJJNew"
sheet = client.open_by_key(SPREADSHEET_ID).sheet1

CODE_COL = 4
CANJEADO_COL = 8

# --- Configuración del correo ---
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
EMAIL_USER = "novawebsolutions05@gmail.com"
EMAIL_PASS = "aupc pzqx wybo tndn"

# --- Generar código único ---
def generar_codigo_unico(longitud=8):
    caracteres = string.ascii_uppercase + string.digits
    return ''.join(random.choice(caracteres) for _ in range(longitud))


# =========================
# 🔥 WEBHOOK PRINCIPAL
# =========================
@app.route("/webhook", methods=["POST", "GET"])
def webhook():

    # 👉 Permitir verificación de WooCommerce
    if request.method == "GET":
        return "OK", 200

    data = request.get_json(force=True, silent=True) or {}

    print("📩 Datos recibidos WooCommerce:", data)

    # 👉 Procesar en segundo plano (evita timeout)
    threading.Thread(target=procesar_pedido, args=(data,)).start()

    return jsonify({"status": "ok"}), 200


def procesar_pedido(data):
    try:
        # 🔹 EXTRAER DATOS DE WOOCOMMERCE
        nombre = data.get("billing", {}).get("first_name", "Cliente")
        correo = data.get("billing", {}).get("email", "")
        productos = ", ".join([item.get("name", "") for item in data.get("line_items", [])])
        total = data.get("total", "0")
        fecha = data.get("date_created", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        print(f"\n🧾 Pedido:\nNombre: {nombre}\nCorreo: {correo}\nProductos: {productos}\nTotal: {total}\nFecha: {fecha}\n")

        # 🔹 GENERAR CÓDIGO
        codigo_unico = str(uuid.uuid4())[:8].upper()

        # 🔹 URL QR
        url_qr = f"https://www.manyoffers.net/validar?codigo={codigo_unico}"

        # 🔹 GENERAR QR
        qr_img = qrcode.make(url_qr)
        qr_path = f"qr_{codigo_unico}.png"
        qr_img.save(qr_path)

        # 🔹 GUARDAR EN GOOGLE SHEETS
        sheet.append_row([nombre, correo, productos, codigo_unico, total, "-", fecha, "NO"])

        # 🔹 ENVIAR EMAIL
        send_email_with_qr(
            to_email=correo,
            nombre=nombre,
            producto=productos,
            qr_path=qr_path,
            codigo_unico=codigo_unico,
            monto=total,
            fecha=fecha,
            url_qr=url_qr
        )

        # 🔹 BORRAR QR
        os.remove(qr_path)

        print(f"✅ Pedido procesado correctamente: {codigo_unico}")

    except Exception as e:
        print("❌ Error procesando pedido:", str(e))


# =========================
# 📧 EMAIL
# =========================
def send_email_with_qr(to_email, nombre, producto, qr_path, codigo_unico, monto, fecha, url_qr):
    subject = f"Tu cupón de Many Offers: {producto}"

    body = f"""
Hola {nombre},

¡Gracias por tu compra en Many Offers!

Aquí tienes tu código QR para tu cupón de {producto}.
Cada código es único y válido solo una vez.

Detalles:
- Producto: {producto}
- Monto: ${monto}
- Fecha: {fecha}
- Código: {codigo_unico}

Presenta este QR en el establecimiento.

¡Disfruta tu oferta!
"""

    msg = MIMEMultipart()
    msg["From"] = EMAIL_USER
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with open(qr_path, "rb") as f:
        img = MIMEImage(f.read())
        msg.attach(img)

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        server.send_message(msg)

    print(f"📧 Email enviado a {to_email}")


# =========================
# ✅ VALIDAR CUPÓN
# =========================
@app.route("/validar", methods=["POST"])
def validar():
    payload = request.get_json(force=True, silent=True) or {}
    codigo = payload.get("codigo", "").strip()

    if not codigo:
        return jsonify({"status": "error", "message": "Código no enviado"}), 400

    try:
        codigos = sheet.col_values(CODE_COL)

        if codigo not in codigos:
            return jsonify({"status": "error", "message": "Código no encontrado"}), 404

        row = codigos.index(codigo) + 1
        estado_actual = (sheet.cell(row, CANJEADO_COL).value or "").strip().upper()

        if estado_actual == "SI":
            return jsonify({"status": "invalid", "message": "Este código ya fue canjeado"}), 403

        sheet.update_cell(row, CANJEADO_COL, "SI")

        return jsonify({"status": "valid", "message": "Código válido y canjeado"}), 200

    except Exception as e:
        print("Error en /validar:", e)
        return jsonify({"status": "error", "message": "Error interno"}), 500


# =========================
# 🌐 WEB INTERFAZ
# =========================
@app.route("/web")
def web():
    return render_template("validador.html")


# =========================
# 🚀 RUN
# =========================
if __name__ == "__main__":
    print("Rutas registradas:")
    print(app.url_map)

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
