from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import qrcode
import resend
import base64
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

# --- Email (Resend) ---
resend.api_key = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM", "onboarding@resend.dev")

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

    # Parsear JSON de forma segura
    try:
        data = json.loads(payload_bytes) if payload_bytes else {}
    except (json.JSONDecodeError, Exception):
        # WooCommerce a veces envía un ping de prueba que no es JSON válido
        print("⚠️ Payload no es JSON válido, ignorando (posible ping de prueba)")
        return jsonify({"status": "ok", "message": "Ping recibido"}), 200

    print("📩 Webhook WooCommerce recibido:", data.get("id", "sin id"))

    # Ignorar pings de prueba de WooCommerce (payload vacío o sin campos de pedido)
    if not data or ("id" not in data and "status" not in data):
        print("⚠️ Ping de prueba de WooCommerce, ignorando")
        return jsonify({"status": "ok", "message": "Ping recibido"}), 200

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
        url_qr = f"https://manyoffers.shop/validar?codigo={codigo_unico}"

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
        import traceback
        print("❌ Error procesando pedido:", str(e))
        print("📋 Traceback completo:")
        traceback.print_exc()


# =========================
# 📧 ENVIAR EMAIL CON QR (Resend - HTML)
# =========================

# Ruta al logo (relativa al directorio del script)
LOGO_PATH = os.path.join(os.path.dirname(__file__), "Logo-Zippy-Blanco-total.png")
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "email_cupon.html")

# Pre-cargar el logo en base64 una sola vez al arrancar
try:
    with open(LOGO_PATH, "rb") as f:
        LOGO_B64 = base64.b64encode(f.read()).decode()
    print(f"✅ Logo cargado correctamente desde: {LOGO_PATH}")
except Exception as e:
    LOGO_B64 = ""
    print(f"⚠️ No se pudo cargar el logo: {e}")

# Verificar que el template existe
try:
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        _ = f.read()
    print(f"✅ Template HTML cargado correctamente desde: {TEMPLATE_PATH}")
except Exception as e:
    print(f"❌ ERROR CRÍTICO: No se pudo cargar el template HTML: {e}")


def send_email_with_qr(to_email, nombre, producto, qr_path, codigo_unico, monto, fecha, url_qr):
    subject = f"🎉 Tu cupón de Many Offers: {producto}"

    # URL pública del QR generada por qrserver.com (compatible con todos los clientes de email)
    qr_url_publica = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={url_qr}&color=221c5b&bgcolor=ffffff&margin=10"

    # URL pública del logo en WordPress
    logo_url = "https://manyoffers.shop/wp-content/uploads/2026/08/Logo-escogido-Many-Offers-v.png"

    # Cargar template HTML y reemplazar placeholders
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        html_body = f.read()

    html_body = (html_body
        .replace("{{NOMBRE}}", nombre)
        .replace("{{PRODUCTO}}", producto)
        .replace("{{MONTO}}", str(monto))
        .replace("{{FECHA}}", fecha)
        .replace("{{CODIGO}}", codigo_unico)
        .replace("cid:cupon_qr", qr_url_publica)
        .replace("cid:logo_many_offers", logo_url)
    )

    # Texto plano como fallback
    body_text = (
        f"Hola {nombre},\n\n"
        f"¡Gracias por tu compra en Many Offers!\n\n"
        f"Cupón: {producto}\n"
        f"Código único: {codigo_unico}\n"
        f"Monto: ${monto}\n"
        f"Fecha: {fecha}\n\n"
        f"Presenta este código en el establecimiento para canjear tu descuento.\n"
        f"Cada código es único y válido solo una vez.\n\n"
        f"¡Disfruta tu oferta!\n"
        f"El equipo de Many Offers — https://manyoffers.shop"
    )

    params = {
        "from": RESEND_FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
        "html": html_body,
        "text": body_text,
    }

    response = resend.Emails.send(params)
    print(f"📧 Email HTML enviado a {to_email} — ID: {response.get('id', 'N/A')}")


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
