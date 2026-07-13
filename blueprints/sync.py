"""
Rotas de logs, histórico de emails, status de sync em tempo real e pré-visualização.
"""
import re
import json
import time
import logging
from flask import render_template, request, redirect, url_for, session, flash, jsonify, Response
from db_manager import DatabaseManager, EmailHistoryManager, SyncErrorManager
from blueprints.utils import login_required, is_ajax, extrair_email_do_remetente
from config import Config

logger = logging.getLogger(__name__)


def _validar_conta_origem_usuario(conta_origem_id, usuario_id):
    """Retorna dict da conta ou None se não pertence ao usuário."""
    with DatabaseManager.get_cursor() as cursor:
        cursor.execute('''
            SELECT co.*, cp.usuario_id, cp.nome AS conta_principal_nome
            FROM contas_origem co
            JOIN contas_principais cp ON co.conta_principal_id = cp.id
            WHERE co.id = %s AND cp.usuario_id = %s
        ''', (conta_origem_id, usuario_id))
        return cursor.fetchone()


def _motivo_spam_por_emails(emails, conta_principal_id):
    """Retorna dict {id: {'motivo': str, 'detalhe': str|None}} para emails detectados como spam."""
    if not emails:
        return {}
    resultado = {}
    for e in emails:
        if not e.get('detectado_spam_pelo_filtro'):
            continue
        motivo = e.get('detectado_spam_motivo')
        detalhe = e.get('detectado_spam_detalhe')
        if motivo:
            resultado[e.get('id')] = {'motivo': motivo, 'detalhe': detalhe}

    emails_sem_motivo = [
        e for e in emails
        if e.get('detectado_spam_pelo_filtro') and e.get('id') not in resultado
    ]
    if emails_sem_motivo and conta_principal_id:
        try:
            from spam_analyzer_config import get_config
            cfg = get_config(conta_principal_id)
            bloqueados = set(
                ln.strip().lower()
                for ln in (cfg.get('remetentes_bloqueados') or '').splitlines()
                if ln.strip() and '@' in ln
            ) if cfg else set()
            for e in emails_sem_motivo:
                email_rem = extrair_email_do_remetente(e.get('remetente'))
                motivo = 'remetente' if email_rem and email_rem in bloqueados else 'conteudo'
                resultado[e.get('id')] = {'motivo': motivo, 'detalhe': None}
        except Exception:
            for e in emails_sem_motivo:
                resultado[e.get('id')] = {'motivo': 'conteudo', 'detalhe': None}
    return resultado


def register_routes(app):

    # ── Logs ─────────────────────────────────────────────────────────────────

    @app.route('/logs/<int:conta_origem_id>')
    @login_required
    def logs_sincronizacao(conta_origem_id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT co.*, cp.usuario_id
                    FROM contas_origem co
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE co.id = %s AND cp.usuario_id = %s
                ''', (conta_origem_id, session['user_id']))
                conta = cursor.fetchone()

                if not conta:
                    flash('Conta não encontrada', 'danger')
                    return redirect(url_for('dashboard'))

                cursor.execute('''
                    SELECT * FROM logs_sincronizacao
                    WHERE conta_origem_id = %s
                    ORDER BY criado_em DESC
                    LIMIT %s
                ''', (conta_origem_id, Config.LOGS_UI_LIMIT))
                logs = cursor.fetchall()

            erros_ativos = 0
            try:
                SyncErrorManager.criar_tabelas_se_nao_existem()
                erros_ativos = SyncErrorManager.contar_erros_ativos(conta_origem_id)
            except Exception:
                pass

            return render_template('logs.html', conta=conta, logs=logs, erros_ativos=erros_ativos)
        except Exception as e:
            logger.error(f"Erro ao buscar logs: {e}")
            flash('Erro ao carregar logs', 'danger')
            return redirect(url_for('dashboard'))

    # ── Status em tempo real (polling JSON) ───────────────────────────────────

    @app.route('/conta-origem/<int:conta_origem_id>/sync-status')
    @login_required
    def sync_status(conta_origem_id):
        """Retorna o status da última (ou em andamento) sincronização para polling."""
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT co.id, cp.usuario_id
                    FROM contas_origem co
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE co.id = %s AND cp.usuario_id = %s
                ''', (conta_origem_id, session['user_id']))
                if not cursor.fetchone():
                    return jsonify({'error': 'Conta não encontrada'}), 404

                cursor.execute('''
                    SELECT status, criado_em, finalizado_em, mensagem
                    FROM logs_sincronizacao
                    WHERE conta_origem_id = %s
                    ORDER BY criado_em DESC
                    LIMIT 1
                ''', (conta_origem_id,))
                log = cursor.fetchone()

            if not log:
                return jsonify({'status': 'idle', 'message': 'Nenhuma sincronização registrada'})

            msg_limit = (
                Config.LOG_MENSAGEM_DB_MAX_LEN
                if log['status'] == 'executando'
                else Config.SYNC_STATUS_MESSAGE_MAX_LEN
            )
            return jsonify({
                'status':        log['status'],
                'criado_em':     log['criado_em'].isoformat() if log['criado_em'] else None,
                'finalizado_em': log['finalizado_em'].isoformat() if log['finalizado_em'] else None,
                'message':       (log.get('mensagem') or '')[:msg_limit],
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/conta-origem/<int:conta_origem_id>/parar-sincronizacao', methods=['POST'])
    @login_required
    def parar_sincronizacao(conta_origem_id):
        """Solicita parada da sync em andamento (flag + fecha log executando na UI)."""
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT co.id, co.nome
                    FROM contas_origem co
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE co.id = %s AND cp.usuario_id = %s
                ''', (conta_origem_id, session['user_id']))
                conta = cursor.fetchone()
            if not conta:
                return jsonify({'success': False, 'message': 'Conta não encontrada'}), 404

            from sync_cancel import solicitar_cancelamento
            from db_manager import SyncLockManager

            solicitar_cancelamento(conta_origem_id)
            fechados = SyncLockManager.liberar_executando_forcado(
                conta_origem_id,
                mensagem='Cancelado pelo usuário na interface',
            )

            if fechados:
                msg = f'Sincronização de "{conta["nome"]}" sendo interrompida ({fechados} registro(s) fechado(s)).'
            else:
                msg = (
                    f'Sinal de parada enviado para "{conta["nome"]}". '
                    'Se ainda houver processo IMAP ativo, ele deve encerrar em alguns segundos.'
                )

            logger.info(f"Parar sync solicitado: conta {conta_origem_id} ({conta['nome']}), logs fechados={fechados}")
            return jsonify({'success': True, 'message': msg, 'logs_fechados': fechados})
        except Exception as e:
            logger.exception(f"Erro ao parar sincronização da conta {conta_origem_id}")
            return jsonify({'success': False, 'message': str(e)}), 500

    # ── Emails sincronizados (histórico) ──────────────────────────────────────

    @app.route('/conta-origem/<int:conta_origem_id>/emails-sincronizados')
    @login_required
    def emails_sincronizados(conta_origem_id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT co.*, cp.nome AS conta_principal_nome
                    FROM contas_origem co
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE co.id = %s AND cp.usuario_id = %s
                ''', (conta_origem_id, session['user_id']))
                conta = cursor.fetchone()
                if not conta:
                    flash('Conta não encontrada', 'danger')
                    return redirect(url_for('dashboard'))

            EmailHistoryManager.criar_tabela_se_nao_existe()
            apenas_spam                   = request.args.get('spam') == '1'
            apenas_detectados_pelo_filtro = request.args.get('filtro_spam') == '1'
            apenas_filtros_email          = request.args.get('filtros_email') == '1'
            pagina     = max(1, int(request.args.get('pagina', 1)))
            por_pagina = Config.EMAILS_POR_PAGINA
            offset     = (pagina - 1) * por_pagina

            emails = EmailHistoryManager.listar_emails_sincronizados(
                conta_origem_id, limite=por_pagina + 1, offset=offset,
                apenas_spam=apenas_spam,
                apenas_detectados_pelo_filtro=apenas_detectados_pelo_filtro,
                apenas_filtros_email=apenas_filtros_email,
            )
            has_next = len(emails) > por_pagina
            if has_next:
                emails = emails[:por_pagina]
            message_ids = [e.get('message_id') or '' for e in emails if e.get('message_id')]
            filtros_por_email = (
                EmailHistoryManager.listar_filtros_aplicados_por_message_ids(conta_origem_id, message_ids)
                if message_ids else {}
            )
            motivo_spam_por_id = _motivo_spam_por_emails(emails, conta.get('conta_principal_id'))

            return render_template(
                'emails_sincronizados.html',
                conta=conta, emails=emails, filtros_por_email=filtros_por_email,
                motivo_spam_por_id=motivo_spam_por_id,
                apenas_spam=apenas_spam,
                apenas_detectados_pelo_filtro=apenas_detectados_pelo_filtro,
                apenas_filtros_email=apenas_filtros_email,
                pagina=pagina,
                has_next=has_next,
            )
        except Exception as e:
            logger.error(f"Erro ao listar emails sincronizados: {e}")
            flash('Erro ao carregar lista', 'danger')
            return redirect(url_for('dashboard'))

    @app.route('/conta-principal/<int:conta_principal_id>/emails-sincronizados')
    @login_required
    def emails_sincronizados_conta_principal(conta_principal_id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT id, nome, email
                    FROM contas_principais
                    WHERE id = %s AND usuario_id = %s
                ''', (conta_principal_id, session['user_id']))
                conta = cursor.fetchone()
                if not conta:
                    flash('Conta não encontrada', 'danger')
                    return redirect(url_for('dashboard'))

            EmailHistoryManager.criar_tabela_se_nao_existe()
            apenas_spam                   = request.args.get('spam') == '1'
            apenas_detectados_pelo_filtro = request.args.get('filtro_spam') == '1'
            apenas_filtros_email          = request.args.get('filtros_email') == '1'
            pagina     = max(1, int(request.args.get('pagina', 1)))
            por_pagina = Config.EMAILS_POR_PAGINA
            offset     = (pagina - 1) * por_pagina

            emails = EmailHistoryManager.listar_emails_sincronizados_conta_principal(
                conta_principal_id, limite=por_pagina + 1, offset=offset,
                apenas_spam=apenas_spam,
                apenas_detectados_pelo_filtro=apenas_detectados_pelo_filtro,
                apenas_filtros_email=apenas_filtros_email,
            )
            has_next = len(emails) > por_pagina
            if has_next:
                emails = emails[:por_pagina]
            filtros_por_email = {}
            for co_id in set(e.get('conta_origem_id') for e in emails if e.get('conta_origem_id')):
                mids = [e.get('message_id') or '' for e in emails if e.get('conta_origem_id') == co_id]
                if mids:
                    for mid, filtros in EmailHistoryManager.listar_filtros_aplicados_por_message_ids(co_id, mids).items():
                        filtros_por_email[(co_id, mid)] = filtros

            motivo_spam_por_id = _motivo_spam_por_emails(emails, conta_principal_id)

            return render_template(
                'emails_sincronizados_conta_principal.html',
                conta=conta, emails=emails, filtros_por_email=filtros_por_email,
                motivo_spam_por_id=motivo_spam_por_id,
                apenas_spam=apenas_spam,
                apenas_detectados_pelo_filtro=apenas_detectados_pelo_filtro,
                apenas_filtros_email=apenas_filtros_email,
                pagina=pagina,
                has_next=has_next,
            )
        except Exception as e:
            logger.error(f"Erro ao listar emails sincronizados da conta principal: {e}")
            flash('Erro ao carregar lista', 'danger')
            return redirect(url_for('dashboard'))

    # ── Ações sobre emails (spam, whitelist, etc.) ────────────────────────────

    @app.route('/conta-origem/<int:conta_origem_id>/email-histórico/<int:historico_id>/marcar-spam',
               methods=['POST'])
    @login_required
    def marcar_email_historico_spam(conta_origem_id, historico_id):
        from spam_analyzer_config import adicionar_palavras_spam_do_email
        ok = EmailHistoryManager.marcar_email_spam(historico_id, conta_origem_id, session['user_id'])
        if ok:
            adicionar_palavras_spam_do_email(historico_id, conta_origem_id, session['user_id'])
        msg_ok  = 'Marcado como spam. Remetente e frases adicionados ao filtro.'
        msg_err = 'Não foi possível marcar ou email não encontrado.'
        if is_ajax():
            return jsonify({'ok': ok, 'message': msg_ok if ok else msg_err})
        flash(msg_ok if ok else msg_err, 'success' if ok else 'warning')
        return redirect(request.referrer or url_for('emails_sincronizados', conta_origem_id=conta_origem_id))

    @app.route('/conta-origem/<int:conta_origem_id>/email-histórico/<int:historico_id>/adicionar-whitelist',
               methods=['POST'])
    @login_required
    def adicionar_remetente_whitelist_route(conta_origem_id, historico_id):
        from spam_analyzer_config import adicionar_remetente_whitelist
        ok = adicionar_remetente_whitelist(historico_id, conta_origem_id, session['user_id'])
        msg_ok  = 'Remetente adicionado à whitelist. Não será tratado como spam.'
        msg_err = 'Não foi possível adicionar à whitelist ou remetente não encontrado.'
        if is_ajax():
            return jsonify({'ok': ok, 'message': msg_ok if ok else msg_err})
        flash(msg_ok if ok else msg_err, 'success' if ok else 'warning')
        return redirect(request.referrer or url_for('emails_sincronizados', conta_origem_id=conta_origem_id))

    @app.route('/conta-origem/<int:conta_origem_id>/email-histórico/<int:historico_id>/remover-do-filtro-spam',
               methods=['POST'])
    @login_required
    def remover_do_filtro_spam_route(conta_origem_id, historico_id):
        from spam_analyzer_config import remover_remetente_do_filtro_spam
        ok = remover_remetente_do_filtro_spam(historico_id, conta_origem_id, session['user_id'])
        msg_ok  = 'Remetente removido do filtro e adicionado à whitelist.'
        msg_err = 'Não foi possível remover do filtro ou remetente não encontrado.'
        if is_ajax():
            return jsonify({'ok': ok, 'message': msg_ok if ok else msg_err})
        flash(msg_ok if ok else msg_err, 'success' if ok else 'warning')
        return redirect(request.referrer or url_for('emails_sincronizados', conta_origem_id=conta_origem_id))

    @app.route('/conta-origem/<int:conta_origem_id>/email-histórico/<int:historico_id>/desmarcar-spam',
               methods=['POST'])
    @login_required
    def desmarcar_email_historico_spam(conta_origem_id, historico_id):
        from spam_analyzer_config import remover_palavras_spam_do_email
        ok = EmailHistoryManager.desmarcar_email_spam(historico_id, conta_origem_id, session['user_id'])
        if ok:
            remover_palavras_spam_do_email(historico_id, conta_origem_id, session['user_id'])
        msg_ok  = 'Marca de spam removida. Remetente e frases retirados do filtro.'
        msg_err = 'Não foi possível desmarcar ou email não encontrado.'
        if is_ajax():
            return jsonify({'ok': ok, 'message': msg_ok if ok else msg_err})
        flash(msg_ok if ok else msg_err, 'success' if ok else 'warning')
        return redirect(request.referrer or url_for('emails_sincronizados', conta_origem_id=conta_origem_id))

    # ── Pré-visualização de email ─────────────────────────────────────────────

    @app.route('/conta-origem/<int:conta_origem_id>/email-histórico/<int:historico_id>/preview')
    @login_required
    def email_preview(conta_origem_id, historico_id):
        """Retorna JSON com metadados do email para pré-visualização no modal."""
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT co.id, cp.usuario_id
                    FROM contas_origem co
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE co.id = %s AND cp.usuario_id = %s
                ''', (conta_origem_id, session['user_id']))
                if not cursor.fetchone():
                    return jsonify({'error': 'Acesso negado'}), 403

                cursor.execute('''
                    SELECT id, assunto, remetente, data_email, sincronizado_em,
                           marcado_spam, detectado_spam_pelo_filtro, detectado_spam_motivo,
                           detectado_spam_detalhe
                    FROM historico_emails_sincronizados
                    WHERE id = %s AND conta_origem_id = %s
                ''', (historico_id, conta_origem_id))
                email = cursor.fetchone()

            if not email:
                return jsonify({'error': 'Email não encontrado'}), 404

            from app import formatar_data_email
            return jsonify({
                'id':            email['id'],
                'assunto':       email.get('assunto') or '(sem assunto)',
                'remetente':     email.get('remetente') or '(desconhecido)',
                'data_email':    formatar_data_email(email.get('data_email') or ''),
                'sincronizado_em': (
                    email['sincronizado_em'].strftime('%d/%m/%Y %H:%M')
                    if email.get('sincronizado_em') else '-'
                ),
                'marcado_spam':  bool(email.get('marcado_spam')),
                'spam_filtro':   bool(email.get('detectado_spam_pelo_filtro')),
                'spam_motivo':   email.get('detectado_spam_motivo') or '',
                'spam_detalhe':  email.get('detectado_spam_detalhe') or '',
            })
        except Exception as e:
            logger.error(f"Erro no preview do email {historico_id}: {e}")
            return jsonify({'error': str(e)}), 500

    # ── Mensagens problemáticas (erros de sync + exclusão manual) ─────────────

    @app.route('/conta-origem/<int:conta_origem_id>/mensagens-problematicas')
    @login_required
    def mensagens_problematicas(conta_origem_id):
        try:
            conta = _validar_conta_origem_usuario(conta_origem_id, session['user_id'])
            if not conta:
                flash('Conta não encontrada', 'danger')
                return redirect(url_for('dashboard'))

            SyncErrorManager.criar_tabelas_se_nao_existem()
            log_id = request.args.get('log_id', type=int)
            erros = SyncErrorManager.listar_erros_conta(conta_origem_id, log_id=log_id)
            pendentes_exclusao = SyncErrorManager.contar_pendentes_exclusao(conta_origem_id)

            return render_template(
                'mensagens_problematicas.html',
                conta=conta,
                erros=erros,
                log_id=log_id,
                pendentes_exclusao=pendentes_exclusao,
            )
        except Exception as e:
            logger.error(f"Erro ao listar mensagens problemáticas: {e}")
            flash('Erro ao carregar mensagens problemáticas', 'danger')
            return redirect(url_for('dashboard'))

    @app.route('/conta-origem/<int:conta_origem_id>/mensagens-exclusao/marcar', methods=['POST'])
    @login_required
    def marcar_mensagens_exclusao(conta_origem_id):
        try:
            conta = _validar_conta_origem_usuario(conta_origem_id, session['user_id'])
            if not conta:
                return jsonify({'ok': False, 'message': 'Conta não encontrada'}), 404

            data = request.get_json(silent=True) or {}
            uids = data.get('uids') or request.form.getlist('uids') or []
            if isinstance(uids, str):
                uids = [uids]
            if not uids:
                return jsonify({'ok': False, 'message': 'Nenhuma mensagem selecionada'}), 400

            SyncErrorManager.criar_tabelas_se_nao_existem()
            n = SyncErrorManager.marcar_para_exclusao(
                conta_origem_id, uids, session['user_id'], motivo='manual'
            )
            msg = f'{n} mensagem(ns) marcada(s) para exclusão na origem.'
            if is_ajax():
                return jsonify({'ok': True, 'message': msg, 'marcadas': n})
            flash(msg, 'success')
            return redirect(url_for('mensagens_problematicas', conta_origem_id=conta_origem_id))
        except Exception as e:
            logger.error(f"Erro ao marcar exclusão: {e}")
            return jsonify({'ok': False, 'message': str(e)}), 500

    @app.route('/conta-origem/<int:conta_origem_id>/mensagens-exclusao/marcar-resolvido', methods=['POST'])
    @login_required
    def marcar_mensagens_resolvidas(conta_origem_id):
        """Remove do painel mensagens que já estão no destino (sem tocar na origem)."""
        try:
            conta = _validar_conta_origem_usuario(conta_origem_id, session['user_id'])
            if not conta:
                return jsonify({'ok': False, 'message': 'Conta não encontrada'}), 404

            data = request.get_json(silent=True) or {}
            uids = data.get('uids') or request.form.getlist('uids') or []
            if isinstance(uids, str):
                uids = [uids]
            if not uids:
                return jsonify({'ok': False, 'message': 'Nenhuma mensagem selecionada'}), 400

            SyncErrorManager.criar_tabelas_se_nao_existem()
            resolvidas, sem_mid = SyncErrorManager.marcar_resolvido(conta_origem_id, uids)
            msg = f'{resolvidas} mensagem(ns) marcada(s) como resolvida(s) e removida(s) do painel.'
            if sem_mid:
                msg += (f' Atenção: {sem_mid} sem Message-ID identificável podem ser copiada(s) '
                        'novamente na próxima sincronização.')
            if is_ajax():
                return jsonify({'ok': True, 'message': msg, 'resolvidas': resolvidas})
            flash(msg, 'success')
            return redirect(url_for('mensagens_problematicas', conta_origem_id=conta_origem_id))
        except Exception as e:
            logger.error(f"Erro ao marcar mensagens como resolvidas: {e}")
            return jsonify({'ok': False, 'message': str(e)}), 500

    @app.route('/conta-origem/<int:conta_origem_id>/mensagens-exclusao/desmarcar', methods=['POST'])
    @login_required
    def desmarcar_mensagens_exclusao(conta_origem_id):
        try:
            conta = _validar_conta_origem_usuario(conta_origem_id, session['user_id'])
            if not conta:
                return jsonify({'ok': False, 'message': 'Conta não encontrada'}), 404

            data = request.get_json(silent=True) or {}
            uids = data.get('uids') or request.form.getlist('uids') or []
            if isinstance(uids, str):
                uids = [uids]
            n = SyncErrorManager.desmarcar_exclusao(conta_origem_id, uids)
            msg = f'{n} mensagem(ns) desmarcada(s).'
            if is_ajax():
                return jsonify({'ok': True, 'message': msg, 'desmarcadas': n})
            flash(msg, 'info')
            return redirect(url_for('mensagens_problematicas', conta_origem_id=conta_origem_id))
        except Exception as e:
            return jsonify({'ok': False, 'message': str(e)}), 500

    def _excluir_uids_na_origem(conta, conta_origem_id, uids):
        """Marca e exclui UIDs na caixa de origem. Retorna (excluidas, msg, ok)."""
        from imap_sync_native import connect_imap, deletar_uids_origem

        uids = [str(u).strip() for u in uids if str(u).strip()]
        if not uids:
            return 0, 'Nenhuma mensagem selecionada.', False

        SyncErrorManager.marcar_para_exclusao(
            conta_origem_id, uids, session['user_id'], motivo='manual'
        )

        conn = None
        falhas = []
        excluidas = 0
        limpas_painel = 0
        try:
            conn = connect_imap(
                conta['servidor'], conta['porta'],
                conta['email'], conta['senha'],
                bool(conta.get('ssl', True)),
            )
            erros_db = SyncErrorManager.buscar_erros_por_uids(conta_origem_id, uids)
            mensagens = [
                {
                    'uid_origem': e['uid_origem'],
                    'message_id': e.get('message_id'),
                    'assunto': e.get('assunto'),
                    'remetente': e.get('remetente'),
                }
                for e in erros_db
            ]
            if not mensagens:
                mensagens = [{'uid_origem': u, 'message_id': None, 'assunto': None, 'remetente': None} for u in uids]

            resultado = deletar_uids_origem(conn, 'INBOX', uids, mensagens=mensagens)
            nao_encontradas = resultado.get('nao_encontradas') or []

            if resultado['ok']:
                for uid in uids:
                    if str(uid) in nao_encontradas:
                        continue
                    SyncErrorManager.atualizar_status_exclusao(conta_origem_id, uid, 'excluida')
                excluidas = resultado['deletadas']
                aviso = resultado.get('aviso')
                if aviso:
                    falhas.append(aviso)
            else:
                excluidas = resultado.get('deletadas') or 0

            if nao_encontradas:
                limpas_painel = SyncErrorManager.remover_erros_uids(conta_origem_id, nao_encontradas)
                for uid in nao_encontradas:
                    SyncErrorManager.desmarcar_exclusao(conta_origem_id, [uid])
        except Exception as e:
            logger.exception(f"Erro IMAP ao excluir mensagens da conta {conta_origem_id}")
            for uid in uids:
                SyncErrorManager.atualizar_status_exclusao(
                    conta_origem_id, uid, 'falhou', erro_exclusao=str(e)
                )
            falhas.append(str(e))
        finally:
            if conn:
                try:
                    conn.logout()
                except Exception:
                    pass

        if excluidas or limpas_painel:
            partes = []
            if excluidas:
                partes.append(f'{excluidas} excluída(s) na origem ({conta["email"]})')
            if limpas_painel:
                partes.append(
                    f'{limpas_painel} removida(s) do painel (já não existiam na caixa de origem)'
                )
            msg = '. '.join(partes) + '.'
            if falhas:
                msg += ' ' + '; '.join(falhas)
            return excluidas, msg, True
        return 0, f'Não foi possível excluir: {"; ".join(falhas) or "erro desconhecido"}', False

    @app.route('/conta-origem/<int:conta_origem_id>/mensagens-exclusao/excluir', methods=['POST'])
    @login_required
    def excluir_mensagens_selecionadas(conta_origem_id):
        """Marca e exclui na origem as mensagens selecionadas (ação única)."""
        try:
            conta = _validar_conta_origem_usuario(conta_origem_id, session['user_id'])
            if not conta:
                return jsonify({'ok': False, 'message': 'Conta não encontrada'}), 404

            SyncErrorManager.criar_tabelas_se_nao_existem()
            data = request.get_json(silent=True) or {}
            uids = data.get('uids') or request.form.getlist('uids') or []
            if isinstance(uids, str):
                uids = [uids]

            excluidas, msg, ok = _excluir_uids_na_origem(conta, conta_origem_id, uids)
            if is_ajax():
                status = 200 if ok else 400
                return jsonify({'ok': ok, 'message': msg, 'excluidas': excluidas}), status
            flash(msg, 'success' if ok else 'danger')
            return redirect(url_for('mensagens_problematicas', conta_origem_id=conta_origem_id))
        except Exception as e:
            logger.exception(f"Erro ao excluir mensagens selecionadas: {e}")
            return jsonify({'ok': False, 'message': str(e)}), 500

    @app.route('/conta-origem/<int:conta_origem_id>/mensagens-exclusao/executar', methods=['POST'])
    @login_required
    def executar_mensagens_exclusao(conta_origem_id):
        """Compatibilidade: exclui pendentes já marcadas (fluxo antigo em 2 passos)."""
        try:
            conta = _validar_conta_origem_usuario(conta_origem_id, session['user_id'])
            if not conta:
                return jsonify({'ok': False, 'message': 'Conta não encontrada'}), 404

            SyncErrorManager.criar_tabelas_se_nao_existem()
            pendentes = SyncErrorManager.listar_pendentes_exclusao(conta_origem_id)
            uids = [p['uid_origem'] for p in pendentes]
            excluidas, msg, ok = _excluir_uids_na_origem(conta, conta_origem_id, uids)

            if is_ajax():
                status = 200 if ok else 400
                return jsonify({'ok': ok, 'message': msg, 'excluidas': excluidas}), status
            flash(msg, 'success' if ok else 'danger')
            return redirect(url_for('mensagens_problematicas', conta_origem_id=conta_origem_id))
        except Exception as e:
            logger.exception(f"Erro ao executar exclusão na origem: {e}")
            return jsonify({'ok': False, 'message': str(e)}), 500
