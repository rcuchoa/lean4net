"""Testes de integracao da lean-api.

Rodam a API real via `TestClient` contra o ambiente Lean ja configurado
no sistema (nenhum mock do Lean em si). Para os cenarios de
indisponibilidade e timeout, o `LeanExecutor` usado pela API e trocado,
via `app.dependency_overrides`, por um executor apontando para um
comando invalido ou para um timeout bem curto -- sem depender de
desinstalar nada do sistema operacional.

Execucao: `cd backend/lean-api && pytest` (requer Lean 4 + Lake disponiveis no
PATH, como para rodar a propria API).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.lean_runner import Settings, SubprocessLeanExecutor, get_lean_executor, settings
from app.main import app

VALID_DECIDE_CODE = "theorem trivial_example : 1 + 1 = 2 := by decide\n"

VALID_INDUCTION_CODE = (
    "theorem zero_add (n : Nat) : 0 + n = n := by\n"
    "  induction n with\n"
    "  | zero => rfl\n"
    "  | succ n ih => simp [Nat.add, ih]\n"
)

INVALID_PROOF_CODE = "theorem broken_proof : 1 + 1 = 3 := by decide\n"

UNAVAILABLE_COMMAND = "definitely-not-a-real-lean-binary-xyz"


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _executor_with(**overrides) -> SubprocessLeanExecutor:
    base = dict(
        project_dir=settings.project_dir,
        command=settings.command,
        timeout=settings.timeout,
        max_code_size=settings.max_code_size,
    )
    base.update(overrides)
    return SubprocessLeanExecutor(Settings(**base))


# --------------------------------------------------------------------------
# /health
# --------------------------------------------------------------------------


def test_health_reports_lean_available(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert "status" in body
    assert "lean_available" in body
    assert "lean_version" in body
    assert body["status"] == "ok"
    assert body["lean_available"] is True
    assert body["lean_version"]


def test_health_when_lean_unavailable(client: TestClient) -> None:
    app.dependency_overrides[get_lean_executor] = lambda: _executor_with(command=UNAVAILABLE_COMMAND)

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["lean_available"] is False


# --------------------------------------------------------------------------
# /check -- casos validos
# --------------------------------------------------------------------------


def test_check_valid_decide_proof(client: TestClient) -> None:
    response = client.post("/check", json={"code": VALID_DECIDE_CODE})

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["exit_code"] == 0
    assert body["errors"] == []


def test_check_valid_induction_proof(client: TestClient) -> None:
    response = client.post("/check", json={"code": VALID_INDUCTION_CODE})

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["exit_code"] == 0
    assert body["errors"] == []


def test_check_empty_code_is_trivially_valid(client: TestClient) -> None:
    response = client.post("/check", json={"code": ""})

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["exit_code"] == 0


# --------------------------------------------------------------------------
# /check -- prova invalida
# --------------------------------------------------------------------------


def test_check_invalid_proof_returns_200_with_valid_false(client: TestClient) -> None:
    response = client.post("/check", json={"code": INVALID_PROOF_CODE})

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["exit_code"] != 0
    assert len(body["errors"]) >= 1

    error = body["errors"][0]
    assert error["severity"] == "error"
    assert error["line"] == 1
    assert error["column"] is not None
    assert "false" in error["message"].lower()

    # o caminho local do arquivo temporario nao deve vazar na resposta
    assert "/tmp" not in body["stdout"]
    assert "Main.lean" in body["stdout"]


# --------------------------------------------------------------------------
# /check -- validacao de request / limites
# --------------------------------------------------------------------------


def test_check_missing_code_field_returns_422(client: TestClient) -> None:
    response = client.post("/check", json={})

    assert response.status_code == 422


def test_check_code_too_large_returns_413(client: TestClient) -> None:
    chunk = "-- padding\n"
    huge_code = chunk * ((settings.max_code_size // len(chunk)) + 10)
    assert len(huge_code) > settings.max_code_size

    response = client.post("/check", json={"code": huge_code})

    assert response.status_code == 413


# --------------------------------------------------------------------------
# /check -- timeout e indisponibilidade
# --------------------------------------------------------------------------


def test_check_timeout_returns_504(client: TestClient) -> None:
    # timeout curto, mas com folga sobre o overhead de start-up do
    # 'lake env lean' observado no ambiente de testes; o codigo dorme
    # bem alem disso para que o timeout seja sempre o fator decisivo.
    app.dependency_overrides[get_lean_executor] = lambda: _executor_with(timeout=3.0)

    sleeping_code = "#eval IO.sleep 20000\n"
    response = client.post("/check", json={"code": sleeping_code})

    assert response.status_code == 504


def test_check_when_lean_unavailable_returns_503(client: TestClient) -> None:
    app.dependency_overrides[get_lean_executor] = lambda: _executor_with(command=UNAVAILABLE_COMMAND)

    response = client.post("/check", json={"code": VALID_DECIDE_CODE})

    assert response.status_code == 503
