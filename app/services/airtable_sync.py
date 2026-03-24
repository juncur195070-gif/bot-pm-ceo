"""
Airtable Sync — Push unidireccional PostgreSQL → Airtable.

Cada vez que se crea o actualiza un item en PostgreSQL,
este servicio replica el cambio en Airtable para que
los devs vean la tarjeta en el Kanban.

Si Airtable falla, el bot sigue funcionando.
Los errores se loguean pero no bloquean el flujo.
"""

import httpx
from app.config.settings import settings


class AirtableSyncService:
    """Push data from PostgreSQL to Airtable."""

    def __init__(self):
        self.api_key = settings.AIRTABLE_API_KEY
        self.base_id = settings.AIRTABLE_BASE_ID
        self.enabled = bool(self.api_key and self.base_id)

    @property
    def base_url(self) -> str:
        return f"https://api.airtable.com/v0/{self.base_id}"

    @property
    def headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    async def sync_backlog_item(self, item: dict) -> str | None:
        """
        Crea o actualiza un item en la tabla BACKLOG_MAESTRO de Airtable.

        Si el item ya tiene airtable_record_id → UPDATE
        Si no → CREATE

        Retorna el record_id de Airtable o None si fallo.
        """
        if not self.enabled:
            return None

        # Solo enviar campos que tienen valor real
        fields = {}

        def _add(airtable_field, value):
            if value is not None and str(value).strip() != "":
                fields[airtable_field] = str(value) if not isinstance(value, (int, float)) else value

        _add("codigo_backlog", item.get("codigo"))
        _add("titulo", item.get("titulo"))
        _add("descripcion", item.get("descripcion"))
        _add("cliente", item.get("cliente_nombre"))
        _add("dev_asignado", item.get("dev_nombre"))
        _add("tipo", item.get("tipo"))
        _add("estado", item.get("estado"))
        _add("urgencia", item.get("urgencia_declarada"))
        _add("esfuerzo_talla", item.get("esfuerzo_talla"))
        fields["score_wsjf"] = float(item.get("score_wsjf", 0) or 0)

        # Adjuntar imagenes como Attachments de Airtable
        adjuntos = item.get("adjuntos_urls", [])
        if adjuntos:
            fields["adjuntos"] = [{"url": url} for url in adjuntos if url]

        # Agregar deadlines si existen
        if item.get("deadline_interno"):
            fields["deadline_interno"] = str(item["deadline_interno"])
        if item.get("deadline_cliente"):
            fields["deadline_cliente"] = str(item["deadline_cliente"])

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                existing_id = item.get("airtable_record_id")
                codigo = fields.get("codigo_backlog", "")

                # Si no tenemos airtable_record_id, buscar por codigo_backlog
                if not existing_id and codigo:
                    search_resp = await client.get(
                        f"{self.base_url}/BACKLOG_MAESTRO",
                        headers=self.headers,
                        params={
                            "filterByFormula": f'{{codigo_backlog}}="{codigo}"',
                            "maxRecords": 1
                        }
                    )
                    if search_resp.status_code == 200:
                        records = search_resp.json().get("records", [])
                        if records:
                            existing_id = records[0]["id"]

                if existing_id:
                    # UPDATE existente
                    resp = await client.patch(
                        f"{self.base_url}/BACKLOG_MAESTRO/{existing_id}",
                        headers=self.headers,
                        json={"fields": fields}
                    )
                else:
                    # CREATE nuevo (solo si no existe)
                    resp = await client.post(
                        f"{self.base_url}/BACKLOG_MAESTRO",
                        headers=self.headers,
                        json={"fields": fields}
                    )

                if resp.status_code in (200, 201):
                    record_id = resp.json().get("id", "")
                    return record_id
                elif resp.status_code == 422 and "INVALID_MULTIPLE_CHOICE" in resp.text:
                    # Single Select tiene opcion que no existe en Airtable
                    # Reintentar SIN los campos Single Select problematicos
                    select_fields = ["tipo", "estado", "urgencia", "esfuerzo_talla"]
                    fields_sin_select = {k: v for k, v in fields.items() if k not in select_fields}
                    print(f"  ⚠ Airtable: opcion de Select no existe, reintentando sin campos Select")
                    if existing_id:
                        resp2 = await client.patch(f"{self.base_url}/BACKLOG_MAESTRO/{existing_id}",
                            headers=self.headers, json={"fields": fields_sin_select})
                    else:
                        resp2 = await client.post(f"{self.base_url}/BACKLOG_MAESTRO",
                            headers=self.headers, json={"fields": fields_sin_select})
                    if resp2.status_code in (200, 201):
                        return resp2.json().get("id", "")
                    print(f"  ⚠ Airtable retry failed: {resp2.status_code}")
                    return None
                else:
                    print(f"  ⚠ Airtable sync error: {resp.status_code} {resp.text[:200]}")
                    return None

        except Exception as e:
            print(f"  ⚠ Airtable sync failed: {e}")
            return None


# Instancia global
airtable_sync = AirtableSyncService()
