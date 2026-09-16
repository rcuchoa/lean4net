"""Invoca o compilador `lean` sobre o arquivo gerado e interpreta a saida.

Estrategia de diagnostico: como cada propriedade vira um `theorem`
independente, uma falha em `decide` para uma propriedade nao impede o Lean
de continuar elaborando os teoremas seguintes. Entao rodamos o compilador
uma unica vez sobre o arquivo inteiro e usamos o numero de linha de cada
mensagem de erro para descobrir a qual propriedade ela pertence (usando os
intervalos de linha registrados pelo codegen).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass

from backend.core.lean_codegen import Property

_ERROR_RE = re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+):\s*error:\s*(?P<msg>.*)$")


@dataclass
class PropertyResult:
    prop: Property
    ok: bool
    messages: list[str]


@dataclass
class VerificationReport:
    success: bool
    results: list[PropertyResult]
    raw_stdout: str
    raw_stderr: str
    returncode: int


def find_lean_binary(explicit: str | None = None) -> str:
    candidate = explicit or "lean"
    resolved = shutil.which(candidate)
    if resolved is None:
        raise FileNotFoundError(
            f"binario 'lean' nao encontrado no PATH (procurado: {candidate!r}). "
            "Instale o Lean 4 (via elan) ou passe --lean-bin com o caminho completo."
        )
    return resolved


def run_lean(lean_file: str, properties: list[Property], lean_bin: str) -> VerificationReport:
    proc = subprocess.run(
        [lean_bin, lean_file],
        capture_output=True,
        text=True,
        timeout=300,
    )

    errors_by_line: dict[int, list[str]] = {}
    for stream in (proc.stdout, proc.stderr):
        lines = stream.splitlines()
        i = 0
        while i < len(lines):
            m = _ERROR_RE.match(lines[i].strip())
            if not m:
                i += 1
                continue
            msg_parts = [m.group("msg")]
            i += 1
            # linhas de continuacao: pertencem ao mesmo erro ate a proxima
            # mensagem "arquivo:linha:coluna: ..." ou o fim da saida.
            while i < len(lines) and not _ERROR_RE.match(lines[i].strip()):
                if lines[i].strip():
                    msg_parts.append(lines[i])
                i += 1
            errors_by_line.setdefault(int(m.group("line")), []).append("\n".join(msg_parts))

    results: list[PropertyResult] = []
    for prop in properties:
        msgs: list[str] = []
        for line_no, msgs_at_line in errors_by_line.items():
            if prop.line_start <= line_no <= prop.line_end:
                msgs.extend(msgs_at_line)
        results.append(PropertyResult(prop=prop, ok=(len(msgs) == 0), messages=msgs))

    # Erros que nao caem em nenhum intervalo de propriedade (ex: erro de
    # sintaxe no preludio) -- se houver, o veredito geral nao pode ser "sucesso"
    # mesmo que todas as propriedades individualmente pareçam ok.
    unmatched = {
        line: msgs
        for line, msgs in errors_by_line.items()
        if not any(p.line_start <= line <= p.line_end for p in properties)
    }

    overall_ok = proc.returncode == 0 and not unmatched
    if unmatched:
        for line, msgs in sorted(unmatched.items()):
            for msg in msgs:
                results.append(
                    PropertyResult(
                        prop=Property("?", f"linha {line}", "erro fora de uma propriedade conhecida", line, line),
                        ok=False,
                        messages=[msg],
                    )
                )

    return VerificationReport(
        success=overall_ok,
        results=results,
        raw_stdout=proc.stdout,
        raw_stderr=proc.stderr,
        returncode=proc.returncode,
    )
