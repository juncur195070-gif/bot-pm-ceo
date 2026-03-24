# Imagen base: Python 3.12 version liviana (sin extras innecesarios)
FROM python:3.12-slim

# Directorio de trabajo dentro del container
WORKDIR /code

# Copiar SOLO requirements primero (para cache de Docker)
# Si no cambias las librerias, Docker reutiliza esta capa
# y no reinstala todo cada vez que cambias codigo
COPY requirements.txt .

# Instalar librerias de Python
RUN pip install --no-cache-dir -r requirements.txt

# Copiar todo el codigo de la app
COPY . .

# Puerto donde escucha FastAPI
EXPOSE 8000

# Comando para arrancar el servidor
# --host 0.0.0.0 = acepta conexiones de cualquier IP (necesario en Docker)
# --port 8000 = puerto donde escucha
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
