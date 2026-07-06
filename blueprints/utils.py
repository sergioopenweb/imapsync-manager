"""
Utilitários compartilhados entre todos os módulos de rotas.
"""
import re
import logging
from functools import wraps
from flask import session, redirect, url_for, flash, request

logger = logging.getLogger(__name__)


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Você precisa fazer login primeiro', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Você precisa fazer login primeiro', 'warning')
            return redirect(url_for('login'))
        if not is_admin():
            flash('Acesso restrito a administradores.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function


def is_admin():
    return session.get('is_admin', False)


def is_ajax():
    xrw = (request.headers.get('X-Requested-With') or '').lower()
    return xrw in ('fetch', 'xmlhttprequest')


def extrair_email_do_remetente(remetente):
    """Extrai o endereço de email do campo From (ex: 'Nome <user@domain.com>' -> 'user@domain.com')."""
    if not remetente or not str(remetente).strip():
        return None
    match = re.search(r'<([^>]+)>', str(remetente))
    email = (match.group(1) if match else remetente).strip()
    return email.lower() if '@' in email else None
