"""
ImapSync Manager — Aplicação Flask principal.
As rotas estão organizadas em blueprints/ para manutenibilidade.
Este arquivo cria o app, registra as rotas e inicia o servidor em modo dev.
"""
import os
import sys
import logging
import logging.handlers

if '/opt/imapsync-manager' not in sys.path:
    sys.path.insert(0, '/opt/imapsync-manager')

from flask import Flask, session
from config import Config
from db_manager import DatabaseManager, EmailHistoryManager, SyncLockManager, SyncErrorManager

# ── Logging ──────────────────────────────────────────────────────────────────

os.makedirs(Config.LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.handlers.RotatingFileHandler(
            Config.LOG_FILE,
            maxBytes=Config.LOG_MAX_BYTES,
            backupCount=Config.LOG_BACKUP_COUNT,
            encoding='utf-8',
        ),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ── Aplicação Flask ──────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key                = Config.SECRET_KEY
app.permanent_session_lifetime = Config.PERMANENT_SESSION_LIFETIME

# Respeita os headers X-Forwarded-* do nginx/proxy reverso
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)


# ── Template filters ─────────────────────────────────────────────────────────

def formatar_data_email(s):
    """Formata string de data do header do email para dd/mm/yyyy HH:MM (Brasília)."""
    s = (s or '').strip()
    if not s:
        return '-'
    try:
        from email.utils import parsedate_to_datetime
        import datetime
        dt     = parsedate_to_datetime(s)
        brt    = datetime.timezone(datetime.timedelta(hours=Config.APP_TIMEZONE_OFFSET))
        dt_brt = dt.astimezone(brt)
        return dt_brt.strftime('%d/%m/%Y %H:%M')
    except Exception:
        fallback = s.replace(' -0300', '').replace(' +0000', '').strip()
        return fallback[:16] if len(fallback) > 16 else (fallback or '-')


app.template_filter('formatar_data_email')(formatar_data_email)


# ── Context processor ─────────────────────────────────────────────────────────

@app.context_processor
def inject_template_helpers():
    from blueprints.utils import is_admin
    out = {'is_admin': is_admin}
    if session.get('user_id'):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute(
                    'SELECT id, nome, ativa FROM contas_principais WHERE usuario_id = %s ORDER BY nome',
                    (session['user_id'],)
                )
                out['sidebar_contas_principais'] = cursor.fetchall()
        except Exception as e:
            logger.debug(f"Context processor contas_principais: {e}")
            out['sidebar_contas_principais'] = []
    else:
        out['sidebar_contas_principais'] = []
    return out


# ── Error handlers ────────────────────────────────────────────────────────────

@app.errorhandler(500)
def internal_error(error):
    from flask import flash, redirect, url_for
    logger.error(f"Erro 500: {error}")
    flash('Erro interno do servidor', 'danger')
    return redirect(url_for('dashboard'))


@app.errorhandler(404)
def not_found(error):
    from flask import flash, redirect, url_for
    flash('Página não encontrada', 'warning')
    return redirect(url_for('dashboard'))


# ── Registro de rotas (blueprints) ────────────────────────────────────────────

from blueprints import auth, admin, dashboard, accounts, sync, filters, spam, api

for module in [auth, admin, dashboard, accounts, sync, filters, spam, api]:
    module.register_routes(app)


# ── Inicialização do banco ────────────────────────────────────────────────────

DatabaseManager.initialize_pool()

try:
    SyncLockManager.criar_tabela_se_nao_existe()
    EmailHistoryManager.criar_tabela_se_nao_existe()
    EmailHistoryManager.criar_tabela_filtro_aplicado_se_nao_existe()
    EmailHistoryManager.limpar_historico_filtro_antigo(dias_manter=Config.HISTORICO_FILTRO_RETENTION_DAYS)
    EmailHistoryManager.limpar_historico_antigo(None, dias_manter=Config.HISTORICO_EMAILS_RETENTION_DAYS)
    DatabaseManager.ensure_ativacao_columns()
    DatabaseManager.ensure_sync_intervalo_column()
    DatabaseManager.ensure_api_key_column()
    DatabaseManager.ensure_alertas_table()
    SyncErrorManager.criar_tabelas_se_nao_existem()
    SyncErrorManager.limpar_erros_antigos()
except Exception as e:
    logger.warning(f"Verificação de tabelas na inicialização: {e}")


if __name__ == '__main__':
    app.run(debug=Config.FLASK_DEBUG, host=Config.APP_HOST, port=Config.APP_PORT)
