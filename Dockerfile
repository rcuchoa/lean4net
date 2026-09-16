# Imagem unica, compartilhada pelos dois servicos do projeto (backend/webapp
# e backend/lean-api) e pela CLI (main.py) -- ver docker-compose.yml para
# como cada um usa esta mesma imagem com um comando/porta diferente.
#
# Precisa do toolchain do Lean 4 (nao so das dependencias Python) porque
# tanto o webapp (`POST /api/verify`) quanto o lean-api (`POST /check`)
# chamam o binario `lean` de verdade -- nao ha como simular isso so com
# codigo Python.
FROM python:3.12-slim

# curl: instala o elan. git: o Lake usa para resolver dependencias (nenhuma
# neste projeto -- so `Init` -- mas o Lake ainda invoca git internamente).
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
      ca-certificates \
      git \
    && rm -rf /var/lib/apt/lists/*

# Lean 4 via elan, com a mesma versao usada em desenvolvimento. Sem
# Mathlib -- so a biblioteca `Init`, que ja vem com qualquer toolchain.
ENV ELAN_HOME=/usr/local/elan
ENV PATH="${ELAN_HOME}/bin:${PATH}"
RUN curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf \
      | sh -s -- -y --no-modify-path --default-toolchain leanprover/lean4:v4.34.0 \
    && elan --version && lean --version && lake --version

WORKDIR /app

# Dependencias Python: as duas requirements.txt do projeto (core+webapp e
# lean-api) mais o gunicorn, que so existe para rodar o webapp em Docker
# (troca o servidor de desenvolvimento do Flask, com debug=True, por um
# servidor WSGI de producao -- ver README "Docker").
COPY requirements.txt requirements.txt
COPY backend/lean-api/requirements.txt backend/lean-api/requirements.txt
RUN pip install --no-cache-dir \
      -r requirements.txt \
      -r backend/lean-api/requirements.txt \
      "gunicorn>=21.2"

# Codigo da aplicacao. `data/` inclui os specs de exemplo (usados por
# `GET /api/example`) -- o docker-compose.yml monta um volume por cima para
# persistir o que for gerado depois.
COPY backend/ backend/
COPY frontend/ frontend/
COPY data/ data/
COPY main.py main.py

EXPOSE 5000 8000
