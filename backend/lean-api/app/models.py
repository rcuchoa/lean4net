"""Modelos Pydantic de request/response da lean-api.

Este modulo nao conhece subprocessos nem detalhes de execucao do Lean --
apenas o formato dos dados que entram e saem pela camada HTTP (main.py).
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class CheckRequest(BaseModel):
    """Corpo esperado por POST /check."""

    code: str = Field(..., description="Codigo-fonte Lean 4 a ser verificado.")


class Diagnostic(BaseModel):
    """Uma mensagem de erro ou warning extraida da saida do Lean.

    `line`/`column` ficam `None` quando a mensagem nao segue o formato
    `arquivo:linha:coluna: severidade: ...` (o parser e best-effort -- a
    saida bruta original sempre fica disponivel em `stdout`/`stderr`).
    """

    line: Optional[int] = Field(None, description="Linha (1-indexed) reportada pelo Lean.")
    column: Optional[int] = Field(None, description="Coluna (0-indexed) reportada pelo Lean.")
    severity: Literal["error", "warning"] = Field(..., description="Severidade da mensagem.")
    message: str = Field(..., description="Texto da mensagem (pode ter multiplas linhas).")


class CheckResponse(BaseModel):
    """Resposta de POST /check.

    `valid` reflete apenas o exit code do processo Lean (True quando o
    Lean aceitou o arquivo, ou seja, exit code 0 -- mesmo que existam
    warnings). Erros de compilacao ou provas invalidas resultam em
    `valid: false` com HTTP 200; a requisicao em si foi bem-sucedida.
    """

    valid: bool = Field(..., description="True se o Lean aceitou o codigo (exit code 0).")
    exit_code: int = Field(..., description="Codigo de saida do processo Lean.")
    errors: List[Diagnostic] = Field(default_factory=list, description="Diagnosticos de severidade error.")
    warnings: List[Diagnostic] = Field(default_factory=list, description="Diagnosticos de severidade warning.")
    stdout: str = Field(..., description="Saida padrao bruta do comando Lean (caminhos locais sanitizados).")
    stderr: str = Field(..., description="Saida de erro bruta do comando Lean (caminhos locais sanitizados).")


class HealthResponse(BaseModel):
    """Resposta de GET /health."""

    status: Literal["ok", "unavailable"] = Field(..., description="'ok' se o Lean respondeu; 'unavailable' caso contrario.")
    lean_available: bool = Field(..., description="True se o comando Lean configurado pode ser executado.")
    lean_version: Optional[str] = Field(None, description="Saida de 'lean --version' via o comando configurado.")
    lake_available: bool = Field(..., description="True se o binario 'lake' foi encontrado no PATH.")
    lake_version: Optional[str] = Field(None, description="Saida de 'lake --version', se disponivel.")
    detail: Optional[str] = Field(None, description="Detalhe adicional quando o Lean nao esta disponivel.")
