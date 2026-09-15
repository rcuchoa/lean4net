"""Modelo semantico da especificacao de infraestrutura de nuvem.

A especificacao YAML e traduzida para estas estruturas em Python (o
"front-end"), que ja fazem parsing/normalizacao de CIDRs para inteiros.
O codegen Lean (lean_codegen.py) consome apenas este modelo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CIDR_RE = re.compile(
    r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})/(\d{1,2})$"
)


class SpecError(Exception):
    """Erro de especificacao (estrutura invalida ou referencia quebrada)."""


@dataclass(frozen=True)
class Cidr:
    addr: int  # endereco de rede como inteiro de 32 bits
    plen: int  # tamanho do prefixo, 0..32

    @staticmethod
    def parse(text: str, where: str) -> "Cidr":
        m = CIDR_RE.match(text.strip())
        if not m:
            raise SpecError(f"{where}: CIDR invalido: {text!r}")
        octets = [int(x) for x in m.groups()[:4]]
        plen = int(m.group(5))
        if any(o > 255 for o in octets):
            raise SpecError(f"{where}: octeto fora de faixa em {text!r}")
        if not (0 <= plen <= 32):
            raise SpecError(f"{where}: prefixo fora de faixa (0-32) em {text!r}")
        addr = (octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]
        return Cidr(addr=addr, plen=plen)


@dataclass(frozen=True)
class Rule:
    proto: str
    port_from: int
    port_to: int
    src: Cidr


@dataclass(frozen=True)
class Vpc:
    name: str
    cidr: Cidr


@dataclass(frozen=True)
class Subnet:
    name: str
    vpc: str
    cidr: Cidr


@dataclass(frozen=True)
class SecurityGroup:
    name: str
    rules: list[Rule] = field(default_factory=list)


@dataclass(frozen=True)
class Instance:
    name: str
    subnet: str
    security_groups: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class InfraSpec:
    vpcs: list[Vpc]
    subnets: list[Subnet]
    security_groups: list[SecurityGroup]
    instances: list[Instance]
