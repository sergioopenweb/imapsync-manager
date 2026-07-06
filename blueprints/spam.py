"""
Rotas do Spam Analyzer — nível conta e nível utilizador.
"""
import logging
from flask import render_template, request, redirect, url_for, session, flash
from db_manager import DatabaseManager
from blueprints.utils import login_required

logger = logging.getLogger(__name__)


def register_routes(app):

    @app.route('/conta-principal/<int:conta_principal_id>/spam-analyzer')
    @login_required
    def spam_analyzer(conta_principal_id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute(
                    'SELECT * FROM contas_principais WHERE id = %s AND usuario_id = %s',
                    (conta_principal_id, session['user_id'])
                )
                conta = cursor.fetchone()
                if not conta:
                    flash('Conta não encontrada', 'danger')
                    return redirect(url_for('dashboard'))

            spam_config = None
            spam_analyzer_disponivel = False
            palavras_genericas = []
            global_cfg = {}
            spam_stats = {}
            try:
                from spam_analyzer_config import (
                    criar_tabela_se_nao_existe, get_config, get_palavras_genericas,
                    aplicar_defaults, get_config_global, get_estatisticas_spam_conta_principal,
                )
                from spam_analyzer import is_available as spam_analyzer_is_available
                criar_tabela_se_nao_existe()
                spam_config = aplicar_defaults(get_config(conta_principal_id))
                spam_analyzer_disponivel = spam_analyzer_is_available()
                palavras_genericas = get_palavras_genericas()
                global_cfg = get_config_global()
                spam_stats = get_estatisticas_spam_conta_principal(conta_principal_id, dias=30)
            except Exception:
                pass

            return render_template(
                'spam_analyzer.html',
                conta=conta,
                spam_config=spam_config,
                spam_analyzer_disponivel=spam_analyzer_disponivel,
                palavras_genericas=palavras_genericas,
                global_cfg=global_cfg,
                spam_stats=spam_stats,
            )
        except Exception as e:
            logger.error(f"Erro ao abrir Spam Analyzer: {e}")
            flash('Erro ao carregar página.', 'danger')
            return redirect(url_for('dashboard'))

    @app.route('/conta-principal/<int:conta_principal_id>/spam-analyzer/salvar', methods=['POST'])
    @login_required
    def salvar_spam_analyzer(conta_principal_id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute(
                    'SELECT id FROM contas_principais WHERE id = %s AND usuario_id = %s',
                    (conta_principal_id, session['user_id'])
                )
                if not cursor.fetchone():
                    flash('Conta não encontrada', 'danger')
                    return redirect(url_for('dashboard'))

            from spam_analyzer_config import salvar_config, ACAO_MARCAR_SPAM, ACAO_PULAR_INBOX
            ativo                 = request.form.get('spam_analyzer_ativo') == 'on'
            acao                  = request.form.get('spam_analyzer_acao', ACAO_MARCAR_SPAM)
            wordlist_extra        = request.form.get('spam_analyzer_wordlist') or ''
            model_path            = request.form.get('spam_analyzer_model_path') or ''
            remetentes_bloqueados = request.form.get('spam_analyzer_remetentes_bloqueados') or ''
            remetentes_permitidos = request.form.get('spam_analyzer_remetentes_permitidos') or ''
            pasta_spam            = request.form.get('spam_analyzer_pasta_spam') or ''
            # dominios_gratuitos e palavras_institucionais são geridos apenas no nível global (admin)

            def _parse_h(campo):
                val = request.form.get(campo)
                if val == 'herdar' or val is None:
                    return None
                return val == '1'

            bloqueio_prefixo = _parse_h('bloqueio_prefixo_estrito')

            if acao not in (ACAO_MARCAR_SPAM, ACAO_PULAR_INBOX):
                acao = ACAO_MARCAR_SPAM

            if salvar_config(
                conta_principal_id, ativo, acao, wordlist_extra=wordlist_extra,
                model_path=model_path, remetentes_bloqueados=remetentes_bloqueados,
                remetentes_permitidos=remetentes_permitidos, pasta_spam=pasta_spam,
                heuristica_dominio_numerico=_parse_h('heuristica_dominio_numerico'),
                heuristica_reply_to=_parse_h('heuristica_reply_to'),
                heuristica_display_name=_parse_h('heuristica_display_name'),
                bloqueio_prefixo_estrito=bloqueio_prefixo,
            ):
                flash('Configuração do Spam Analyzer salva.', 'success')
            else:
                flash('Erro ao salvar configuração.', 'danger')
        except Exception:
            logger.exception('Erro ao salvar Spam Analyzer')
            flash('Erro ao salvar configuração.', 'danger')
        return redirect(url_for('spam_analyzer', conta_principal_id=conta_principal_id))

