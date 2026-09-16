"""Rotas HTTP da lean-api.

Este modulo cuida exclusivamente da camada HTTP: validacao de request,
codigos de status, logging de acesso. Toda a execucao do Lean (e seu
parsing) vive em `lean_runner.py`, acessada aqui apenas atraves do
protocolo `LeanExecutor`.
"""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException

from app.lean_runner import (
    LeanExecutionError,
    LeanExecutor,
    LeanTimeoutError,
    LeanUnavailableError,
    get_lean_executor,
    settings,
)
from app.models import CheckRequest, CheckResponse, HealthResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("lean_api")

app = FastAPI(
    title="lean-api",
    description=(
        "API REST local para verificar codigo Lean 4 via subprocesso "
        "('lake env lean'). Ver README.md para limitacoes de seguranca "
        "e o plano de evolucao para um backend LSP/RPC."
    ),
    version="0.1.0",
)


@app.get("/health", response_model=HealthResponse)
def health(executor: LeanExecutor = Depends(get_lean_executor)) -> HealthResponse:
    """Verifica se o Lean (e o Lake) estao disponiveis para a API."""
    return executor.health()


@app.post(
    "/check",
    response_model=CheckResponse,
    responses={
        413: {"description": "Codigo Lean maior que LEAN_MAX_CODE_SIZE."},
        503: {"description": "Lean/Lake indisponivel neste servidor."},
        504: {"description": "Tempo limite (LEAN_TIMEOUT) excedido ao verificar o codigo."},
    },
)
def check(request: CheckRequest, executor: LeanExecutor = Depends(get_lean_executor)) -> CheckResponse:
    """Verifica um trecho de codigo Lean 4 e retorna erros/warnings.

    Retorna sempre HTTP 200 quando o Lean roda normalmente -- inclusive
    quando o codigo tem erros de compilacao ou uma prova invalida
    (nesse caso, `valid: false`). HTTP != 200 e reservado para
    problemas na propria requisicao ou na infraestrutura de verificacao
    (ver `responses` acima e o README).
    """
    code_length = len(request.code)
    if code_length > settings.max_code_size:
        raise HTTPException(
            status_code=413,
            detail=(
                f"codigo possui {code_length} caracteres; "
                f"o limite configurado (LEAN_MAX_CODE_SIZE) e {settings.max_code_size}."
            ),
        )

    logger.info("recebida requisicao /check (tamanho=%d caracteres)", code_length)

    try:
        return executor.check(request.code)
    except LeanUnavailableError as exc:
        logger.warning("lean indisponivel: %s", exc)
        raise HTTPException(status_code=503, detail="Lean/Lake indisponivel neste servidor.") from exc
    except LeanTimeoutError as exc:
        logger.warning("timeout ao verificar codigo (limite=%.1fs)", settings.timeout)
        raise HTTPException(
            status_code=504,
            detail=f"tempo limite de {settings.timeout}s excedido ao executar o Lean.",
        ) from exc
    except LeanExecutionError as exc:
        logger.exception("falha ao executar o Lean")
        raise HTTPException(status_code=500, detail="erro interno ao executar o Lean.") from exc
    except Exception as exc:  # noqa: BLE001 - ultima linha de defesa
        logger.exception("falha inesperada ao verificar codigo lean")
        raise HTTPException(status_code=500, detail="erro interno ao verificar o codigo.") from exc
