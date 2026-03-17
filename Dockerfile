FROM python:3.11-slim

# Instala fontes para renderização dos slides
RUN apt-get update && apt-get install -y \
    fonts-dejavu-core \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instala dependências primeiro (cache eficiente)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o projeto
COPY . .

# Porta padrão (Railway injeta $PORT em runtime)
EXPOSE 8080

# sh -c garante que $PORT é expandido pelo shell antes de passar ao uvicorn
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
