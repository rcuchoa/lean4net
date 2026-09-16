"""Execucao do Lean e parsing da sua saida.

Esta e a UNICA camada da aplicacao que sabe como verificar codigo Lean.
`main.py` (HTTP) nao inicia subprocessos nem faz parsing de mensagens --
ele apenas chama `LeanExecutor.check()`/`.health()` e traduz excecoes em
respostas HTTP.

A implementacao atual (`SubprocessLeanExecutor`) roda `lake env lean
<arquivo>` uma vez por requisicao. O protocolo `LeanExecutor` existe para
que essa implementacao possa ser trocada, no futuro, por um backend
baseado em um processo persistente do Lean Language Server (LSP/RPC) --
ver README.md -- sem qualquer mudanca em `main.py` ou `models.py`.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Protocol, Tuple

from app.models import CheckResponse, Diagnostic, HealthResponse

logger = logging.getLogger("lean_api.lean_runner")

_SUBMITTED_FILENAME = "Main.lean"
_VERSION_PROBE_TIMEOUT = 5.0


# --------------------------------------------------------------------------
# Configuracao
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Settings:
    """Configuracao centralizada, lida das variaveis de ambiente.

    Nenhum caminho absoluto e fixado no codigo: `project_dir` cai no
    diretorio de trabalho atual quando `LEAN_PROJECT_DIR` nao e definida.
    """

    project_dir: str
    command: str
    timeout: float
    max_code_size: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            project_dir=os.environ.get("LEAN_PROJECT_DIR", os.getcwd()),
            command=os.environ.get("LEAN_COMMAND", "lake env lean"),
            timeout=float(os.environ.get("LEAN_TIMEOUT", "10")),
            max_code_size=int(os.environ.get("LEAN_MAX_CODE_SIZE", "100000")),
        )

    @property
    def command_parts(self) -> List[str]:
        """O comando configurado, tokenizado (nunca via shell)."""
        parts = shlex.split(self.command)
        if not parts:
            raise ValueError("LEAN_COMMAND nao pode ser vazio")
        return parts


settings = Settings.from_env()


# --------------------------------------------------------------------------
# Excecoes explicitas
# --------------------------------------------------------------------------


class LeanRunnerError(Exception):
    """Base para erros da camada de execucao do Lean."""


class LeanUnavailableError(LeanRunnerError):
    """O comando Lean/Lake configurado nao pode ser executado."""


class LeanTimeoutError(LeanRunnerError):
    """O processo Lean excedeu o tempo limite configurado."""

    def __init__(self, timeout: float):
        self.timeout = timeout
        super().__init__(f"lean excedeu o tempo limite de {timeout}s")


class LeanExecutionError(LeanRunnerError):
    """Falha inesperada ao preparar ou executar o processo Lean."""


# --------------------------------------------------------------------------
# Parsing da saida do Lean
# --------------------------------------------------------------------------

# Ex.: "Main.lean:4:8: error: unknown identifier 'foo'"
#      "Main.lean:4:8: error(lean.someCode): mensagem"
#      "Main.lean:4:8: warning: mensagem"
_DIAGNOSTIC_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?P<column>\d+):\s*"
    r"(?P<severity>error|warning)(?:\s*\([^)]*\))?:\s*(?P<message>.*)$"
)


def parse_diagnostics(output: str) -> Tuple[List[Diagnostic], List[Diagnostic]]:
    """Extrai diagnosticos de erro/warning de uma saida do Lean.

    O formato reconhecido e `arquivo:linha:coluna: severidade: mensagem`,
    com linhas de continuacao (contexto extra) agregadas na mesma
    mensagem ate a proxima linha reconhecida. Mensagens que nao seguem
    esse formato sao ignoradas aqui -- elas continuam disponiveis na
    saida bruta (`stdout`/`stderr`) da resposta, entao nenhuma
    informacao e perdida mesmo quando o parser nao reconhece algo.
    """
    errors: List[Diagnostic] = []
    warnings: List[Diagnostic] = []

    lines = output.splitlines()
    i = 0
    while i < len(lines):
        match = _DIAGNOSTIC_RE.match(lines[i])
        if not match:
            i += 1
            continue

        severity = match.group("severity")
        message_lines = [match.group("message")]
        i += 1
        while i < len(lines) and not _DIAGNOSTIC_RE.match(lines[i]):
            message_lines.append(lines[i])
            i += 1

        while message_lines and message_lines[-1].strip() == "":
            message_lines.pop()

        diagnostic = Diagnostic(
            line=int(match.group("line")),
            column=int(match.group("column")),
            severity=severity,  # type: ignore[arg-type]
            message="\n".join(message_lines).strip(),
        )
        (errors if severity == "error" else warnings).append(diagnostic)

    return errors, warnings


def _sanitize(text: str, submitted_path: str, tmp_dir: str) -> str:
    """Remove caminhos locais (arquivo temporario e diretorio) da saida.

    O Lean reporta o caminho absoluto do arquivo verificado nas suas
    mensagens; como esse caminho e um arquivo temporario interno da API,
    ele e substituido pelo nome logico `Main.lean` antes de sair para o
    cliente, para nao expor detalhes do sistema de arquivos do servidor.
    """
    sanitized = text.replace(submitted_path, _SUBMITTED_FILENAME)
    sanitized = sanitized.replace(tmp_dir, "")
    return sanitized


# --------------------------------------------------------------------------
# Interface de execucao (permite trocar subprocess por LSP/RPC no futuro)
# --------------------------------------------------------------------------


class LeanExecutor(Protocol):
    """Contrato entre a camada HTTP e o mecanismo de verificacao do Lean.

    Qualquer implementacao (subprocesso hoje, um cliente LSP/RPC amanha)
    precisa apenas satisfazer esta interface para ser usada por
    `main.py` sem nenhuma mudanca nas rotas.
    """

    def check(self, code: str) -> CheckResponse: ...

    def health(self) -> HealthResponse: ...


class SubprocessLeanExecutor:
    """Implementacao atual: um processo `lean` novo por requisicao.

    Fluxo de `check()`:
      1. valida que o comando configurado existe;
      2. grava o codigo recebido em um arquivo temporario isolado
         (`Main.lean` dentro de um diretorio temporario exclusivo);
      3. executa `<LEAN_COMMAND> <arquivo>` com cwd em `LEAN_PROJECT_DIR`,
         argumentos em lista (nunca `shell=True`), timeout e interrupcao
         explicita do grupo de processos quando o timeout e atingido;
      4. sanitiza e faz o parsing da saida;
      5. remove o diretorio temporario, sempre.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # -- health -------------------------------------------------------

    def health(self) -> HealthResponse:
        lake_path = shutil.which("lake")
        lake_available = lake_path is not None
        lake_version = self._probe_version(["lake", "--version"]) if lake_available else None

        lean_version: Optional[str] = None
        lean_available = False
        detail: Optional[str] = None

        try:
            command = self._settings.command_parts
        except ValueError as exc:
            return HealthResponse(
                status="unavailable",
                lean_available=False,
                lean_version=None,
                lake_available=lake_available,
                lake_version=lake_version,
                detail=str(exc),
            )

        lean_version = self._probe_version([*command, "--version"])
        lean_available = lean_version is not None
        if not lean_available:
            detail = "nao foi possivel executar o comando Lean configurado (LEAN_COMMAND)."

        return HealthResponse(
            status="ok" if lean_available else "unavailable",
            lean_available=lean_available,
            lean_version=lean_version,
            lake_available=lake_available,
            lake_version=lake_version,
            detail=detail,
        )

    def _probe_version(self, argv: List[str]) -> Optional[str]:
        try:
            proc = subprocess.run(
                argv,
                cwd=self._settings.project_dir,
                capture_output=True,
                text=True,
                timeout=_VERSION_PROBE_TIMEOUT,
            )
        except (FileNotFoundError, PermissionError, NotADirectoryError):
            return None
        except subprocess.TimeoutExpired:
            return None
        except OSError:
            return None

        if proc.returncode != 0:
            return None
        output = (proc.stdout or proc.stderr or "").strip()
        return output or None

    # -- check ----------------------------------------------------------

    def check(self, code: str) -> CheckResponse:
        try:
            command = self._settings.command_parts
        except ValueError as exc:
            raise LeanUnavailableError(str(exc)) from exc

        tmp_dir = tempfile.mkdtemp(prefix="lean_api_")
        submitted_path = str(Path(tmp_dir) / _SUBMITTED_FILENAME)
        try:
            with open(submitted_path, "w", encoding="utf-8") as f:
                f.write(code)

            argv = [*command, submitted_path]
            exit_code, stdout, stderr = self._run(argv)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        stdout = _sanitize(stdout, submitted_path, tmp_dir)
        stderr = _sanitize(stderr, submitted_path, tmp_dir)

        out_errors, out_warnings = parse_diagnostics(stdout)
        err_errors, err_warnings = parse_diagnostics(stderr)

        return CheckResponse(
            valid=(exit_code == 0),
            exit_code=exit_code,
            errors=[*out_errors, *err_errors],
            warnings=[*out_warnings, *err_warnings],
            stdout=stdout,
            stderr=stderr,
        )

    def _run(self, argv: List[str]) -> Tuple[int, str, str]:
        """Executa `argv`, com timeout e interrupcao explicita do processo.

        Codigo nao confiavel: sem `shell=True`, argumentos em lista,
        `start_new_session=True` para poder matar todo o grupo de
        processos (nao apenas o processo imediato) se o timeout for
        atingido.
        """
        try:
            proc = subprocess.Popen(
                argv,
                cwd=self._settings.project_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except (FileNotFoundError, PermissionError, NotADirectoryError) as exc:
            raise LeanUnavailableError(f"nao foi possivel executar o comando Lean: {exc}") from exc
        except OSError as exc:
            raise LeanExecutionError(f"falha ao iniciar o processo Lean: {exc}") from exc

        try:
            stdout, stderr = proc.communicate(timeout=self._settings.timeout)
        except subprocess.TimeoutExpired:
            self._kill_process_group(proc)
            try:
                proc.communicate(timeout=_VERSION_PROBE_TIMEOUT)
            except subprocess.TimeoutExpired:
                pass
            raise LeanTimeoutError(self._settings.timeout)
        except OSError as exc:
            self._kill_process_group(proc)
            raise LeanExecutionError(f"falha ao ler a saida do processo Lean: {exc}") from exc

        return proc.returncode, stdout, stderr

    @staticmethod
    def _kill_process_group(proc: "subprocess.Popen[str]") -> None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except OSError:
                pass


_default_executor = SubprocessLeanExecutor(settings)


def get_lean_executor() -> LeanExecutor:
    """Dependency-injection point usado por main.py (`Depends(...)`).

    Centraliza qual implementacao de `LeanExecutor` a API usa hoje; a
    troca para um executor baseado em LSP/RPC no futuro se resume a
    mudar o que esta funcao retorna.
    """
    return _default_executor
