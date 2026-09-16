#!/usr/bin/env python3
"""Servidor web do lean4net.

Serve uma interface com 3 areas (YAML de entrada, Lean 4 gerado e
resultado da verificacao) e uma API que reaproveita o mesmo pipeline de
main.py (spec_loader -> lean_codegen -> runner), sem duplicar logica.

A API espelha as duas etapas da CLI: `POST /api/generate` (YAML -> Lean,
chama o LLM) e `POST /api/verify` (Lean -> compilador). Sao endpoints
independentes e sem estado no servidor -- o Lean gerado volta para o
navegador, onde pode ser revisado/editado, e e o texto do cliente que
sera de fato verificado. `POST /api/prompt` complementa a etapa 1: devolve
o prompt que *seria* enviado ao modelo, para inspecao previa, sem chamar a
API da Anthropic.

Uso:
    python3 backend/webapp/server.py
    # abre http://127.0.0.1:5000
"""

from __future__ import annotations

import os
import secrets
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from flask import Flask, jsonify, redirect, request, send_from_directory, url_for
from flask_login import current_user, login_required, login_user, logout_user

from backend.core.lean_codegen import (
    DEFAULT_PROPERTIES,
    LeanCodegenError,
    build_prompt,
    extract_sensitive_ports,
    generate,
    parse_properties,
)
from backend.core.model import SpecError
from backend.core.runner import find_lean_binary, run_lean
from backend.core.spec_loader import load_custom_properties, load_spec
from backend.webapp.auth import login_manager, verify_credentials

DEFAULT_SENSITIVE_PORTS = "22,3389"

app = Flask(__name__, static_folder="../../frontend", static_url_path="")

app.secret_key = os.environ.get("LEAN4NET_SECRET_KEY") or secrets.token_hex(32)
if not os.environ.get("LEAN4NET_SECRET_KEY"):
    print(
        "aviso: LEAN4NET_SECRET_KEY nao definida -- usando uma chave "
        "temporaria (sessoes serao invalidadas a cada reinicio do servidor).",
        file=sys.stderr,
    )

login_manager.init_app(app)


@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith("/api/"):
        return jsonify({"error": "nao autenticado"}), 401
    return redirect(url_for("login"))


_lean_bin_cache: str | None = None


def _get_lean_bin() -> str:
    global _lean_bin_cache
    if _lean_bin_cache is None:
        _lean_bin_cache = find_lean_binary(None)
    return _lean_bin_cache


@app.get("/login")
def login():
    return send_from_directory(app.static_folder, "login.html")


@app.post("/login")
def login_submit():
    body = request.get_json(force=True, silent=True) or {}
    user = verify_credentials(body.get("username", ""), body.get("password", ""))
    if not user:
        return jsonify({"error": "usuario ou senha invalidos"}), 401
    login_user(user, remember=bool(body.get("remember")))
    return jsonify({"ok": True})


@app.get("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.get("/api/whoami")
@login_required
def api_whoami():
    return jsonify({"username": current_user.id})


@app.get("/")
@login_required
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/example")
@login_required
def example():
    example_path = os.path.join(PROJECT_ROOT, "data", "spec", "example.yaml")
    try:
        with open(example_path, "r", encoding="utf-8") as f:
            return jsonify({"yaml": f.read()})
    except OSError as e:
        return jsonify({"error": f"nao foi possivel ler o exemplo: {e}"}), 500


@app.get("/api/default-properties")
@login_required
def default_properties():
    """Propriedades usadas para pre-popular o editor ao carregar a pagina.

    Mesmo texto de `DEFAULT_PROPERTIES` (backend/core/lean_codegen.py) --
    as quatro propriedades originais do projeto, agora so mais um ponto de
    partida editavel como qualquer outra propriedade customizada.
    """
    return jsonify(
        {"properties": [{"id": c.id, "description": c.description} for c in DEFAULT_PROPERTIES]}
    )


def _spec_from_request(body: dict):
    """Valida o corpo de uma requisicao da etapa 1 e devolve (spec, portas, propriedades).

    Em caso de erro devolve `(None, (resposta_json, status))` -- o mesmo
    tratamento vale para `/api/prompt` e `/api/generate`, que recebem
    exatamente o mesmo corpo.
    """
    yaml_text = body.get("yaml", "")
    ports_text = body.get("sensitive_ports") or DEFAULT_SENSITIVE_PORTS

    if not yaml_text.strip():
        return None, (jsonify({"error": "YAML vazio."}), 400)

    try:
        sensitive_ports = [int(p) for p in ports_text.split(",") if p.strip()]
    except ValueError:
        return None, (
            jsonify({"error": "Lista de portas sensiveis invalida (use algo como 22,3389)."}),
            400,
        )

    try:
        custom_properties = load_custom_properties(body.get("custom_properties"))
    except SpecError as e:
        return None, (jsonify({"error": f"erro nas propriedades customizadas: {e}"}), 400)

    with tempfile.TemporaryDirectory(prefix="lean4net_") as tmp_dir:
        spec_path = os.path.join(tmp_dir, "spec.yaml")
        with open(spec_path, "w", encoding="utf-8") as f:
            f.write(yaml_text)

        try:
            spec = load_spec(spec_path)
        except SpecError as e:
            return None, (jsonify({"error": f"erro na especificacao: {e}"}), 400)
        except Exception as e:  # yaml malformado
            return None, (jsonify({"error": f"erro ao ler o YAML: {e}"}), 400)

    return (spec, sensitive_ports, custom_properties), None


@app.post("/api/prompt")
@login_required
def api_prompt():
    """Previa da etapa 1: o prompt que sera enviado ao modelo, sem envia-lo.

    Nao chama a API da Anthropic (e portanto nao custa nada nem demora): o
    frontend usa esta rota para mostrar o prompt no painel antes de disparar
    a geracao. Como `build_prompt()` e a mesma funcao que `generate()` usa
    para montar a requisicao, o texto devolvido aqui e literalmente o que
    sera enviado.
    """
    ok, err = _spec_from_request(request.get_json(force=True, silent=True) or {})
    if err:
        return err
    spec, sensitive_ports, custom_properties = ok

    p = build_prompt(spec, sensitive_ports, tuple(custom_properties))
    return jsonify(
        {
            "model": p.model,
            "max_tokens": p.max_tokens,
            "thinking": p.thinking,
            "output_config": p.output_config,
            "system": p.system,
            "user": p.user,
        }
    )


@app.post("/api/generate")
@login_required
def api_generate():
    """Etapa 1: YAML -> Lean 4 (chama o LLM, nao chama o compilador)."""
    ok, err = _spec_from_request(request.get_json(force=True, silent=True) or {})
    if err:
        return err
    spec, sensitive_ports, custom_properties = ok

    try:
        lean_source, properties = generate(spec, sensitive_ports, tuple(custom_properties))
    except LeanCodegenError as e:
        return jsonify({"error": f"erro ao gerar o Lean 4 via LLM: {e}"}), 502

    return jsonify(
        {
            "lean_source": lean_source,
            "properties": [
                {
                    "id": p.id,
                    "theorem_name": p.theorem_name,
                    "description": p.description,
                    "line_start": p.line_start,
                    "line_end": p.line_end,
                }
                for p in properties
            ],
        }
    )


@app.post("/api/verify")
@login_required
def api_verify():
    """Etapa 2: Lean 4 -> compilador (nao chama o LLM).

    Recebe o texto Lean vindo do cliente -- que pode te-lo editado depois da
    etapa 1 -- e recalcula os intervalos de linha das propriedades sobre
    esse texto, nunca sobre o que foi gerado antes.
    """
    body = request.get_json(force=True, silent=True) or {}
    lean_source = body.get("lean_source", "")

    if not lean_source.strip():
        return jsonify({"error": "Nenhum codigo Lean para verificar: gere o Lean primeiro."}), 400

    try:
        lean_bin = _get_lean_bin()
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 500

    # As portas sensiveis so alimentam a descricao de P3 no relatorio; ler do
    # proprio Lean mantem o texto coerente com o arquivo verificado.
    sensitive_ports = extract_sensitive_ports(lean_source)
    if sensitive_ports is None:
        sensitive_ports = [int(p) for p in DEFAULT_SENSITIVE_PORTS.split(",")]

    # As propriedades customizadas, ao contrario das portas sensiveis, nao
    # tem como ser recuperadas do proprio .lean (so o id/nome do teorema
    # sao reconstruiveis a partir dos marcadores -- a descricao em
    # linguagem natural nao fica embutida no arquivo). Se o cliente ainda
    # tem a lista do editor em maos (fluxo comum: gerar e verificar na
    # mesma sessao), reenvia-la aqui garante que o relatorio mostre a
    # descricao original em vez do texto de fallback.
    try:
        custom_properties = load_custom_properties(body.get("custom_properties"))
    except SpecError as e:
        return jsonify({"error": f"erro nas propriedades customizadas: {e}"}), 400

    try:
        properties = parse_properties(lean_source, sensitive_ports, tuple(custom_properties))
    except LeanCodegenError as e:
        return jsonify({"error": f"codigo Lean invalido para esta ferramenta: {e}"}), 400

    with tempfile.TemporaryDirectory(prefix="lean4net_") as tmp_dir:
        lean_path = os.path.join(tmp_dir, "spec.lean")
        with open(lean_path, "w", encoding="utf-8") as f:
            f.write(lean_source)

        try:
            report = run_lean(lean_path, properties, lean_bin)
        except Exception as e:
            return jsonify({"error": f"erro ao executar o lean: {e}"}), 500

    results = [
        {
            "id": r.prop.id,
            "theorem_name": r.prop.theorem_name,
            "description": r.prop.description,
            "ok": r.ok,
            "messages": r.messages,
        }
        for r in report.results
    ]

    return jsonify(
        {
            "report": {
                "success": report.success,
                "results": results,
                "raw_stdout": report.raw_stdout,
                "raw_stderr": report.raw_stderr,
                "returncode": report.returncode,
            }
        }
    )


if __name__ == "__main__":
    # HOST default (127.0.0.1) mantem o comportamento local de sempre --
    # Docker/gunicorn nao passa por este bloco (ver Dockerfile), mas quem
    # quiser rodar `python3 backend/webapp/server.py` dentro do container
    # (ex.: para depurar) precisa de 0.0.0.0 para a porta publicada
    # alcancar o processo.
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    print(f"lean4net web em http://{host}:{port}")
    app.run(host=host, port=port, debug=True)
