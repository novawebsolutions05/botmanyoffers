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
import os
import uuid
import json
import hashlib
import hmac
import threading
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# --- Google Sheets ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
json_creds = os.environ.get("GOOGLE_CREDENTIALS")
creds_dict = json.loads(json_creds)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)

SPREADSHEET_ID = "1huOU__jhatsGiP7RZ4zxDeevbYmI8fgh83B4fIJJNew"
sheet = client.open_by_key(SPREADSHEET_ID).sheet1

CODE_COL = 4
CANJEADO_COL = 8

# --- Email ---
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
EMAIL_USER = os.environ.get("EMAIL_USER", "novawebsolutions05@gmail.com")
EMAIL_PASS = os.environ.get("EMAIL_PASS", "aupc pzqx wybo tndn")

# --- WooCommerce Secret (para verificar firma) ---
# Debes poner este mismo valor en WooCommerce > Webhooks > Secret
WC_SECRET = os.environ.get("WC_SECRET", "")


# =========================
# 🔐 Verificar firma WooCommerce
# =========================
def verificar_firma_wc(payload_bytes, signature_header):
    """WooCommerce firma el body con HMAC-SHA256 usando el secret del webhook."""
    if not WC_SECRET:
        return True  # Si no hay secret configurado, se omite la verificación
    mac = hmac.new(WC_SECRET.encode("utf-8"), payload_bytes, hashlib.sha256)
    expected = mac.digest().hex()
    # WooCommerce envía la firma en base64
    import base64
    expected_b64 = base64.b64encode(mac.digest()).decode()
    return hmac.compare_digest(expected_b64, signature_header or "")


# =========================
# 🔥 WEBHOOK WOOCOMMERCE
# =========================
@app.route("/webhook", methods=["POST"])
def webhook():
    payload_bytes = request.get_data()
    signature = request.headers.get("X-Wc-Webhook-Signature", "")

    # Verificar firma si hay secret configurado
    if WC_SECRET and not verificar_firma_wc(payload_bytes, signature):
        print("❌ Firma inválida")
        return jsonify({"status": "error", "message": "Firma inválida"}), 401

    data = json.loads(payload_bytes) if payload_bytes else {}
    print("📩 Webhook WooCommerce recibido:", data.get("id", "sin id"))

    # Solo procesar pedidos completados o procesando
    estado = data.get("status", "")
    if estado not in ("completed", "processing"):
        print(f"⏭️ Pedido ignorado, estado: {estado}")
        return jsonify({"status": "ignored", "message": f"Estado '{estado}' no procesado"}), 200

    # Procesar en hilo para no hacer timeout
    threading.Thread(target=procesar_pedido, args=(data,)).start()

    return jsonify({"status": "ok"}), 200


# =========================
# ⚙️ PROCESAR PEDIDO
# =========================
def procesar_pedido(data):
    try:
        billing = data.get("billing", {})
        nombre = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip() or "Cliente"
        correo = billing.get("email", "")

        line_items = data.get("line_items", [])
        productos = ", ".join([
            f"{item.get('name', '')} x{item.get('quantity', 1)}"
            for item in line_items
        ])

        total = data.get("total", "0")
        fecha_raw = data.get("date_created", "")
        try:
            fecha = datetime.fromisoformat(fecha_raw.replace("Z", "")).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        print(f"\n🧾 Pedido WooCommerce:\nNombre: {nombre}\nCorreo: {correo}\nProductos: {productos}\nTotal: {total}\nFecha: {fecha}\n")

        if not correo:
            print("❌ No hay correo, se omite el pedido")
            return

        # Generar código único
        codigo_unico = str(uuid.uuid4())[:8].upper()
        url_qr = f"https://www.manyoffers.net/validar?codigo={codigo_unico}"

        # Generar QR
        qr_img = qrcode.make(url_qr)
        qr_path = f"qr_{codigo_unico}.png"
        qr_img.save(qr_path)

        # Guardar en Google Sheets
        sheet.append_row([nombre, correo, productos, codigo_unico, total, "-", fecha, "NO"])

        # Enviar email
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

        os.remove(qr_path)
        print(f"✅ Pedido procesado: {codigo_unico}")

    except Exception as e:
        print("❌ Error procesando pedido:", str(e))


# =========================
# 📧 ENVIAR EMAIL CON QR
# =========================
def send_email_with_qr(to_email, nombre, producto, qr_path, codigo_unico, monto, fecha, url_qr):
    subject = f"Tu cupón de Many Offers: {producto}"
    body = f"""Hola {nombre},

¡Gracias por tu compra en Many Offers!

Aquí tienes tu código QR para tu cupón de {producto}.
Cada código es único y válido solo una vez.

Detalles:
- Producto: {producto}
- Monto: ${monto}
- Fecha: {fecha}
- Código: {codigo_unico}

Presenta este QR en el establecimiento para validar tu descuento.

¡Disfruta tu oferta!
"""
    msg = MIMEMultipart()
    msg["From"] = EMAIL_USER
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with open(qr_path, "rb") as f:
        img = MIMEImage(f.read())
        img.add_header("Content-Disposition", "attachment", filename="cupon_qr.png")
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
    codigo = payload.get("codigo", "").strip().upper()

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
        return jsonify({"status": "valid", "message": "Código válido y marcado como canjeado"}), 200

    except Exception as e:
        print("Error en /validar:", e)
        return jsonify({"status": "error", "message": "Error interno"}), 500


# =========================
# 🌐 INTERFAZ WEB
# =========================
@app.route("/web")
def web():
    return render_template("validador.html")


# =========================
# 🚀 ARRANQUE
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
