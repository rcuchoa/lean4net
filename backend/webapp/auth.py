#!/usr/bin/env python3
"""Autenticacao da interface web do lean4net.

Usuario unico (admin), configurado via variavel de ambiente -- mesmo
padrao ja usado para ANTHROPIC_API_KEY (ver backend/core/lean_codegen.py).
Sem banco de dados: LEAN4NET_ADMIN_USER guarda o usuario e
LEAN4NET_ADMIN_PASSWORD_HASH guarda a senha ja em hash (gerado com
`python3 backend/webapp/hash_password.py`), nunca a senha em texto puro.
"""

from __future__ import annotations

import os
import sys

from flask_login import LoginManager, UserMixin
from werkzeug.security import check_password_hash

ADMIN_USER = os.environ.get("LEAN4NET_ADMIN_USER")
ADMIN_PASSWORD_HASH = os.environ.get("LEAN4NET_ADMIN_PASSWORD_HASH")

if not ADMIN_USER or not ADMIN_PASSWORD_HASH:
    print(
        "erro: defina LEAN4NET_ADMIN_USER e LEAN4NET_ADMIN_PASSWORD_HASH "
        "antes de iniciar o servidor web (veja README.md -- gere o hash "
        "com `python3 backend/webapp/hash_password.py`).",
        file=sys.stderr,
    )
    sys.exit(1)


class User(UserMixin):
    def __init__(self, username: str) -> None:
        self.id = username


login_manager = LoginManager()


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    return User(user_id) if user_id == ADMIN_USER else None


def verify_credentials(username: str, password: str) -> User | None:
    if username == ADMIN_USER and check_password_hash(ADMIN_PASSWORD_HASH, password):
        return User(username)
    return None
