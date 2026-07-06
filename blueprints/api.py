"""
API REST (/api/v1/) e endpoint de health check (/health).
Autenticação via header X-API-Key.
"""
import logging
import threading
from functools import wraps
from flask import jsonify, request, session
from config import Config
from db_manager import DatabaseManager
from blueprints.utils import login_required

logger = logging.getLogger(__name__)


def api_key_required(f):
    """Decorator: autentica pela sessão web OU pelo header X-API-Key."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('user_id'):
            return f(*args, **kwargs)
        api_key = request.headers.get(Config.API_KEY_HEADER, '').strip()
        if not api_key:
            return jsonify({'error': f'Autenticação necessária. Use {Config.API_KEY_HEADER} ou faça login.'}), 401
        try:
            usuario = DatabaseManager.get_usuario_por_api_key(api_key)
            if not usuario:
                return jsonify({'error': 'API key inválida ou expirada.'}), 401
            if not usuario.get('ativo', 1):
                return jsonify({'error': 'Conta desativada.'}), 403
            # Injeta user_id temporariamente no contexto via g
            from flask import g
            g.api_user_id = usuario['id']
        except Exception as e:
            logger.error(f"Erro na autenticação por API key: {e}")
            return jsonify({'error': 'Erro interno de autenticação.'}), 500
        return f(*args, **kwargs)
    return decorated


def get_current_user_id():
    """Retorna o user_id do usuário atual (sessão web ou API key)."""
    if session.get('user_id'):
        return session['user_id']
    from flask import g
    return getattr(g, 'api_user_id', None)


def register_routes(app):

    # ── Health check ─────────────────────────────────────────────────────────

    @app.route('/health')
    def health():
        """Status do sistema para monitoramento externo."""
        db_ok   = False
        db_msg  = 'ok'
        contas  = 0
        ultima  = None
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('SELECT 1')
                db_ok = True
                cursor.execute('SELECT COUNT(*) as n FROM contas_origem WHERE ativa = 1')
                contas = cursor.fetchone()['n']
                cursor.execute(
                    'SELECT MAX(finalizado_em) as ts FROM logs_sincronizacao WHERE status = %s',
                    ('sucesso',)
                )
                row = cursor.fetchone()
                ultima = row['ts'].isoformat() if row and row['ts'] else None
        except Exception as e:
            db_msg = str(e)

        status = 'ok' if db_ok else 'degraded'
        return jsonify({
            'status':       status,
            'db':           'ok' if db_ok else db_msg,
            'contas_ativas': contas,
            'ultima_sync':  ultima,
        }), 200 if db_ok else 503

    # ── API v1 ────────────────────────────────────────────────────────────────

    @app.route('/api/v1/accounts')
    @api_key_required
    def api_list_accounts():
        """Lista contas principais e origens do usuário autenticado."""
        user_id = get_current_user_id()
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT cp.id, cp.nome, cp.email, cp.servidor, cp.porta,
                           cp.ssl, cp.ativa, cp.criado_em,
                           (SELECT COUNT(*) FROM contas_origem WHERE conta_principal_id = cp.id) AS total_origens
                    FROM contas_principais cp
                    WHERE cp.usuario_id = %s
                    ORDER BY cp.nome
                ''', (user_id,))
                contas = cursor.fetchall()

            return jsonify({
                'success': True,
                'accounts': [
                    {
                        'id':            c['id'],
                        'nome':          c['nome'],
                        'email':         c['email'],
                        'servidor':      c['servidor'],
                        'porta':         c['porta'],
                        'ssl':           bool(c['ssl']),
                        'ativa':         bool(c['ativa']),
                        'total_origens': c['total_origens'],
                        'criado_em':     c['criado_em'].isoformat() if c['criado_em'] else None,
                    }
                    for c in contas
                ],
            })
        except Exception as e:
            logger.error(f"Erro na API list_accounts: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/v1/sync/<int:conta_origem_id>', methods=['POST'])
    @api_key_required
    def api_sync_account(conta_origem_id):
        """Inicia sincronização manual de uma conta de origem."""
        from sync_executor import ImapSyncExecutor
        user_id = get_current_user_id()
        try:
            dados_conta = ImapSyncExecutor.get_dados_conta(conta_origem_id, user_id)
            if not dados_conta:
                return jsonify({'success': False, 'error': 'Conta não encontrada'}), 404
            if not dados_conta.get('ativa'):
                return jsonify({'success': False, 'error': 'Conta desativada'}), 400
            if not dados_conta.get('dest_ativa', True):
                return jsonify({'success': False, 'error': 'Conta principal desativada'}), 400

            def run_sync():
                ImapSyncExecutor.executar_sincronizacao(conta_origem_id, dados_conta)

            threading.Thread(target=run_sync, daemon=True).start()
            return jsonify({'success': True, 'message': 'Sincronização iniciada.'})
        except Exception as e:
            logger.error(f"Erro na API sync_account {conta_origem_id}: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/v1/sync/<int:conta_origem_id>/status')
    @api_key_required
    def api_sync_status(conta_origem_id):
        """Retorna o status da última sincronização de uma conta."""
        user_id = get_current_user_id()
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT co.id, cp.usuario_id
                    FROM contas_origem co
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE co.id = %s AND cp.usuario_id = %s
                ''', (conta_origem_id, user_id))
                if not cursor.fetchone():
                    return jsonify({'success': False, 'error': 'Conta não encontrada'}), 404

                cursor.execute('''
                    SELECT status, criado_em, finalizado_em, mensagem
                    FROM logs_sincronizacao
                    WHERE conta_origem_id = %s
                    ORDER BY criado_em DESC LIMIT 1
                ''', (conta_origem_id,))
                log = cursor.fetchone()

            if not log:
                return jsonify({'success': True, 'status': 'idle', 'message': 'Sem sincronizações registradas'})

            return jsonify({
                'success':       True,
                'status':        log['status'],
                'criado_em':     log['criado_em'].isoformat() if log['criado_em'] else None,
                'finalizado_em': log['finalizado_em'].isoformat() if log['finalizado_em'] else None,
                'message':       (log.get('mensagem') or '')[:Config.API_SYNC_STATUS_MESSAGE_MAX_LEN],
            })
        except Exception as e:
            logger.error(f"Erro na API sync_status {conta_origem_id}: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500
