"""
Servicio Kapso — Puente entre WhatsApp y el bot.

Responsabilidades:
  1. Verificar firma HMAC-SHA256 del webhook
  2. Extraer contenido del mensaje (texto, audio transcrito, imagen)
  3. Enviar mensajes de vuelta por WhatsApp

Kapso API docs: https://docs.kapso.ai
"""

import hmac
import hashlib
import httpx
from app.config.settings import settings


class KapsoService:
    """Cliente para interactuar con Kapso WhatsApp API."""

    def __init__(self):
        self.api_key = settings.KAPSO_API_KEY
        self.phone_number_id = settings.KAPSO_PHONE_NUMBER_ID
        self.webhook_secret = settings.KAPSO_WEBHOOK_SECRET
        self.base_url = f"https://api.kapso.ai/meta/whatsapp/{settings.KAPSO_API_VERSION}/{self.phone_number_id}"

    # ── RECIBIR ──

    def verificar_firma(self, payload_bytes: bytes, signature: str) -> bool:
        """
        Verifica que el webhook realmente viene de Kapso.

        Kapso firma cada webhook con HMAC-SHA256 usando tu webhook_secret.
        Si la firma no coincide, alguien esta intentando enviarte datos falsos.

        Args:
            payload_bytes: El body crudo del request (bytes)
            signature: El header X-Webhook-Signature del request

        Returns:
            True si la firma es valida
        """
        # En desarrollo local, si no hay secret configurado, aceptar todo
        if settings.SETUP == "LOCAL" and not self.webhook_secret:
            return True

        if not self.webhook_secret or not signature:
            return False

        expected = hmac.new(
            self.webhook_secret.encode(),
            payload_bytes,
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def extraer_mensaje(self, payload: dict) -> dict:
        """
        Extrae el contenido relevante del payload de Kapso.

        Kapso envia 2 formatos:
        - Formato batch (v2): { "data": [{ "message": {...}, "conversation": {...} }], "batch": true }
        - Formato simple: { "message": {...} }

        Retorna un dict normalizado con:
        {
            "from": "51987654321",
            "message_id": "wamid.xxx",
            "type": "texto|audio|imagen",
            "contenido": "texto...",
            "media_url": "https://...",
            "contact_name": "Tatiana",
        }
        """
        # Manejar formato batch v2 de Kapso
        # Kapso envia: { "data": [{ "message": {...}, "conversation": {...} }], "batch": true }
        if payload.get("batch") and payload.get("data"):
            first_item = payload["data"][0]
            msg = first_item.get("message", {})
            conversation = first_item.get("conversation", {})
            # El contact_name viene de conversation en batch mode
            if not msg.get("kapso", {}).get("contact_name"):
                if "kapso" not in msg:
                    msg["kapso"] = {}
                msg["kapso"]["contact_name"] = conversation.get("contact_name", "")
        else:
            msg = payload.get("message", {})

        kapso = msg.get("kapso", {})
        msg_type = msg.get("type", "text")

        # Extraer contenido segun tipo
        if msg_type == "text":
            contenido = msg.get("text", {}).get("body", "")
            tipo = "texto"
            media_url = None

        elif msg_type == "audio":
            # Kapso transcribe automaticamente los audios
            transcript = kapso.get("transcript", {})
            contenido = transcript.get("text", "")
            if not contenido:
                contenido = "[Audio recibido sin transcripcion]"
            tipo = "audio"
            media_url = kapso.get("media_url")

        elif msg_type == "image":
            # Imagen: el caption es el texto, media_url es la imagen
            contenido = msg.get("image", {}).get("caption", "")
            tipo = "imagen"
            media_url = kapso.get("media_url")

        else:
            # Otros tipos (video, document, etc.) — tratar como texto
            contenido = f"[Mensaje tipo {msg_type} no soportado]"
            tipo = "otro"
            media_url = kapso.get("media_url")

        return {
            "from": msg.get("from", ""),
            "message_id": msg.get("id", ""),
            "type": tipo,
            "contenido": contenido.strip(),
            "media_url": media_url,
            "contact_name": kapso.get("contact_name", ""),
        }

    # ── ENVIAR ──

    async def enviar_texto(self, to: str, mensaje: str) -> dict:
        """
        Envia un mensaje de texto por WhatsApp via Kapso API.

        Args:
            to: Numero destino sin '+' (ej: "51987654321")
            mensaje: Texto del mensaje (max ~4096 chars para WhatsApp)

        Returns:
            Respuesta de Kapso con el message_id
        """
        # Limpiar numero: quitar '+' si lo tiene
        to_clean = to.replace("+", "")

        async with httpx.AsyncClient(timeout=settings.KAPSO_TIMEOUT) as client:
            resp = await client.post(
                f"{self.base_url}/messages",
                headers={
                    "X-API-Key": self.api_key,
                    "Content-Type": "application/json"
                },
                json={
                    "messaging_product": "whatsapp",
                    "to": to_clean,
                    "type": "text",
                    "text": {"body": mensaje}
                }
            )
            if resp.status_code >= 400:
                print(f"  ⚠ Kapso API error: {resp.status_code} {resp.text[:200]}")
            return resp.json()

    async def enviar_texto_seguro(self, to: str, mensaje: str) -> dict | None:
        """
        Igual que enviar_texto pero no lanza excepcion si falla.
        Util para notificaciones que no deben bloquear el flujo principal.
        """
        try:
            return await self.enviar_texto(to, mensaje)
        except Exception as e:
            print(f"Error enviando WhatsApp a {to}: {e}")
            return None


# Instancia global
kapso_service = KapsoService()
