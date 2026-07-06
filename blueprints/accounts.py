"""
Rotas de contas principais e contas de origem.
"""
import logging
from flask import render_template, request, redirect, url_for, session, flash, jsonify
from config import Config
from db_manager import DatabaseManager, EmailHistoryManager
from blueprints.utils import login_required

logger = logging.getLogger(__name__)


def register_routes(app):

    # ── Contas Principais ────────────────────────────────────────────────────

    @app.route('/conta-principal/adicionar', methods=['GET', 'POST'])
    @login_required
    def adicionar_conta_principal():
        if request.method == 'POST':
            nome       = request.form.get('nome')
            servidor   = request.form.get('servidor')
            email_addr = request.form.get('email')
            senha      = request.form.get('senha')
            porta      = request.form.get('porta', Config.DEFAULT_IMAP_SSL_PORT)
            ssl        = request.form.get('ssl') == 'on'

            try:
                with DatabaseManager.get_cursor() as cursor:
                    cursor.execute('''
                        INSERT INTO contas_principais
                        (usuario_id, nome, servidor, email, senha, porta, `ssl`, ativa)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 1)
                    ''', (session['user_id'], nome, servidor, email_addr, senha, porta, ssl))
                flash('Conta principal adicionada com sucesso!', 'success')
                return redirect(url_for('dashboard'))
            except Exception as e:
                logger.error(f"Erro ao adicionar conta principal: {e}")
                flash('Erro ao adicionar conta. Tente novamente.', 'danger')

        return render_template('adicionar_conta_principal.html')

    @app.route('/conta-principal/<int:id>')
    @login_required
    def detalhes_conta_principal(id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT * FROM contas_principais
                    WHERE id = %s AND usuario_id = %s
                ''', (id, session['user_id']))
                conta = cursor.fetchone()

                if not conta:
                    flash('Conta não encontrada', 'danger')
                    return redirect(url_for('dashboard'))

                cursor.execute('''
                    SELECT * FROM contas_origem
                    WHERE conta_principal_id = %s
                    ORDER BY criado_em DESC
                ''', (id,))
                contas_origem = cursor.fetchall()

            erros_por_conta = {}
            pendentes_por_conta = {}
            try:
                from db_manager import SyncErrorManager
                SyncErrorManager.criar_tabelas_se_nao_existem()
                for origem in contas_origem:
                    erros_por_conta[origem['id']] = SyncErrorManager.contar_erros_ativos(origem['id'])
                    pendentes_por_conta[origem['id']] = SyncErrorManager.contar_pendentes_exclusao(origem['id'])
            except Exception:
                pass

            return render_template(
                'detalhes_conta_principal.html',
                conta=conta,
                contas_origem=contas_origem,
                erros_por_conta=erros_por_conta,
                pendentes_por_conta=pendentes_por_conta,
            )
        except Exception as e:
            logger.error(f"Erro ao buscar detalhes da conta: {e}")
            flash('Erro ao carregar conta', 'danger')
            return redirect(url_for('dashboard'))

    @app.route('/conta-principal/<int:id>/toggle', methods=['POST'])
    @login_required
    def toggle_conta_principal(id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT id, ativa FROM contas_principais
                    WHERE id = %s AND usuario_id = %s
                ''', (id, session['user_id']))
                conta = cursor.fetchone()
                if not conta:
                    return jsonify({'success': False, 'message': 'Conta não encontrada'}), 404
                nova_ativa = 0 if conta.get('ativa', 1) else 1
                cursor.execute('UPDATE contas_principais SET ativa = %s WHERE id = %s', (nova_ativa, id))
            flash('Conta principal ' + ('ativada' if nova_ativa else 'desativada') + '.', 'success')
            return jsonify({
                'success': True,
                'ativa': bool(nova_ativa),
                'redirect': url_for('detalhes_conta_principal', id=id),
            })
        except Exception as e:
            logger.error(f"Erro ao alternar conta principal {id}: {e}")
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/conta-principal/<int:id>/deletar', methods=['POST'])
    @login_required
    def deletar_conta_principal(id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT id FROM contas_principais
                    WHERE id = %s AND usuario_id = %s
                ''', (id, session['user_id']))
                if not cursor.fetchone():
                    return jsonify({'success': False, 'message': 'Conta não encontrada'}), 404

                cursor.execute('SELECT id FROM contas_origem WHERE conta_principal_id = %s', (id,))
                for co in cursor.fetchall():
                    cursor.execute('DELETE FROM logs_sincronizacao WHERE conta_origem_id = %s', (co['id'],))

                cursor.execute('DELETE FROM contas_origem WHERE conta_principal_id = %s', (id,))
                cursor.execute('DELETE FROM contas_principais WHERE id = %s', (id,))

            flash('Conta principal e todas as contas de origem relacionadas foram deletadas!', 'success')
            return jsonify({'success': True, 'redirect': url_for('dashboard')})
        except Exception as e:
            logger.error(f"Erro ao deletar conta principal: {e}")
            return jsonify({'success': False, 'message': f'Erro: {str(e)}'}), 500

    # ── Contas de Origem ─────────────────────────────────────────────────────

    @app.route('/conta-origem/adicionar/<int:conta_principal_id>', methods=['GET', 'POST'])
    @login_required
    def adicionar_conta_origem(conta_principal_id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT * FROM contas_principais
                    WHERE id = %s AND usuario_id = %s
                ''', (conta_principal_id, session['user_id']))
                conta_principal = cursor.fetchone()

                if not conta_principal:
                    flash('Conta principal não encontrada', 'danger')
                    return redirect(url_for('dashboard'))

            if request.method == 'POST':
                nome               = request.form.get('nome')
                servidor           = request.form.get('servidor')
                email_addr         = request.form.get('email')
                senha              = request.form.get('senha')
                porta              = request.form.get('porta', Config.DEFAULT_IMAP_SSL_PORT)
                ssl                = request.form.get('ssl') == 'on'
                ativa              = request.form.get('ativa') == 'on'
                marcar_lido        = request.form.get('marcar_lido_origem') == 'on'
                dias_manter        = request.form.get('dias_manter_origem', 0)
                label_destino      = request.form.get('label_destino', '').strip() or None
                sync_intervalo_min = int(request.form.get('sync_intervalo_minutos', 0))

                try:
                    with DatabaseManager.get_cursor() as cursor:
                        cursor.execute('''
                            INSERT INTO contas_origem
                            (conta_principal_id, nome, servidor, email, senha, porta, ssl, ativa,
                             marcar_lido_origem, dias_manter_origem, label_destino, sync_intervalo_minutos)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ''', (conta_principal_id, nome, servidor, email_addr, senha, porta, ssl,
                              ativa, marcar_lido, dias_manter, label_destino, sync_intervalo_min))
                    flash('Conta de origem adicionada com sucesso!', 'success')
                    return redirect(url_for('detalhes_conta_principal', id=conta_principal_id))
                except Exception as e:
                    logger.error(f"Erro ao adicionar conta de origem: {e}")
                    flash('Erro ao adicionar conta. Tente novamente.', 'danger')

            return render_template('adicionar_conta_origem.html', conta_principal=conta_principal)
        except Exception as e:
            logger.error(f"Erro ao adicionar conta de origem: {e}")
            flash('Erro ao carregar página', 'danger')
            return redirect(url_for('dashboard'))

    @app.route('/conta-origem/<int:id>')
    @login_required
    def detalhes_conta_origem(id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT co.*, cp.usuario_id
                    FROM contas_origem co
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE co.id = %s AND cp.usuario_id = %s
                ''', (id, session['user_id']))
                conta = cursor.fetchone()

                if not conta:
                    flash('Conta não encontrada', 'danger')
                    return redirect(url_for('dashboard'))

                cursor.execute('''
                    SELECT * FROM logs_sincronizacao
                    WHERE conta_origem_id = %s
                    ORDER BY criado_em DESC
                    LIMIT %s
                ''', (id, Config.LOGS_CONTA_DETALHE_LIMIT))
                logs = cursor.fetchall()

            return render_template('detalhes_conta_origem.html', conta=conta, logs=logs)
        except Exception as e:
            logger.error(f"Erro ao buscar detalhes da conta de origem: {e}")
            flash('Erro ao carregar conta', 'danger')
            return redirect(url_for('dashboard'))

    @app.route('/conta-origem/<int:id>/editar', methods=['GET', 'POST'])
    @login_required
    def editar_conta_origem(id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT co.*, cp.usuario_id,
                           cp.nome  AS conta_principal_nome,
                           cp.email AS conta_principal_email
                    FROM contas_origem co
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE co.id = %s AND cp.usuario_id = %s
                ''', (id, session['user_id']))
                conta = cursor.fetchone()

                if not conta:
                    flash('Conta não encontrada', 'danger')
                    return redirect(url_for('dashboard'))

            if request.method == 'POST':
                nome               = request.form.get('nome')
                servidor           = request.form.get('servidor')
                email_addr         = request.form.get('email')
                senha              = request.form.get('senha')
                porta              = request.form.get('porta', Config.DEFAULT_IMAP_SSL_PORT)
                ssl                = request.form.get('ssl') == 'on'
                dias_manter        = int(request.form.get('dias_manter', 0))
                marcar_lido        = request.form.get('marcar_lido') == 'on'
                ativa              = request.form.get('ativa') == 'on'
                label_destino      = request.form.get('label_destino', '').strip() or None
                sync_intervalo_min = int(request.form.get('sync_intervalo_minutos', 0))

                try:
                    with DatabaseManager.get_cursor() as cursor:
                        if senha:
                            cursor.execute('''
                                UPDATE contas_origem
                                SET nome=%s, servidor=%s, email=%s, senha=%s,
                                    porta=%s, `ssl`=%s, dias_manter_origem=%s,
                                    marcar_lido_origem=%s, ativa=%s, label_destino=%s,
                                    sync_intervalo_minutos=%s
                                WHERE id=%s
                            ''', (nome, servidor, email_addr, senha,
                                  porta, ssl, dias_manter, marcar_lido, ativa, label_destino,
                                  sync_intervalo_min, id))
                        else:
                            cursor.execute('''
                                UPDATE contas_origem
                                SET nome=%s, servidor=%s, email=%s,
                                    porta=%s, `ssl`=%s, dias_manter_origem=%s,
                                    marcar_lido_origem=%s, ativa=%s, label_destino=%s,
                                    sync_intervalo_minutos=%s
                                WHERE id=%s
                            ''', (nome, servidor, email_addr,
                                  porta, ssl, dias_manter, marcar_lido, ativa, label_destino,
                                  sync_intervalo_min, id))
                    flash('Conta atualizada com sucesso!', 'success')
                    return redirect(url_for('detalhes_conta_principal', id=conta['conta_principal_id']))
                except Exception as e:
                    logger.error(f"Erro ao atualizar conta de origem: {e}")
                    flash('Erro ao atualizar conta. Tente novamente.', 'danger')

            return render_template('editar_conta_origem.html', conta=conta)
        except Exception as e:
            logger.error(f"Erro ao processar edição: {e}")
            flash('Erro ao processar solicitação', 'danger')
            return redirect(url_for('dashboard'))

    @app.route('/conta-origem/<int:id>/toggle', methods=['POST'])
    @login_required
    def toggle_conta_origem(id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT co.id, co.ativa, cp.usuario_id
                    FROM contas_origem co
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE co.id = %s AND cp.usuario_id = %s
                ''', (id, session['user_id']))
                conta = cursor.fetchone()
                if not conta:
                    return jsonify({'success': False, 'message': 'Conta não encontrada'}), 404
                nova_ativa = not conta['ativa']
                cursor.execute('UPDATE contas_origem SET ativa = %s WHERE id = %s', (nova_ativa, id))
            return jsonify({'success': True, 'ativa': nova_ativa})
        except Exception as e:
            logger.error(f"Erro ao alternar conta: {e}")
            return jsonify({'success': False, 'message': f'Erro: {str(e)}'}), 500

    @app.route('/conta-origem/<int:id>/deletar', methods=['POST'])
    @login_required
    def deletar_conta_origem(id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT co.id, cp.usuario_id
                    FROM contas_origem co
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE co.id = %s AND cp.usuario_id = %s
                ''', (id, session['user_id']))
                conta = cursor.fetchone()
                if not conta:
                    return jsonify({'success': False, 'message': 'Conta não encontrada'}), 404
                cursor.execute('DELETE FROM logs_sincronizacao WHERE conta_origem_id = %s', (id,))
                cursor.execute('DELETE FROM contas_origem WHERE id = %s', (id,))
            flash('Conta de origem deletada com sucesso!', 'success')
            return jsonify({'success': True})
        except Exception as e:
            logger.error(f"Erro ao deletar conta de origem: {e}")
            return jsonify({'success': False, 'message': f'Erro: {str(e)}'}), 500

    @app.route('/conta-origem/<int:id>/sincronizar', methods=['POST'])
    @login_required
    def sincronizar_conta_origem(id):
        import threading
        from sync_executor import ImapSyncExecutor

        try:
            dados_conta = ImapSyncExecutor.get_dados_conta(id, session['user_id'])
            if not dados_conta:
                return jsonify({'success': False, 'message': 'Conta não encontrada'}), 404
            if not dados_conta.get('ativa'):
                return jsonify({'success': False, 'message': 'Conta de origem está desativada'}), 400
            if not dados_conta.get('dest_ativa', True):
                return jsonify({'success': False, 'message': 'Conta principal está desativada'}), 400

            def run_sync():
                resultado = ImapSyncExecutor.executar_sincronizacao(id, dados_conta)
                logger.info(f"Sincronização manual concluída para conta {id}: {resultado['success']}")

            thread = threading.Thread(target=run_sync, daemon=True)
            thread.start()
            return jsonify({'success': True, 'message': 'Sincronização iniciada. Acompanhe o progresso nos logs.'})
        except Exception as e:
            logger.error(f"Erro ao sincronizar conta: {e}")
            return jsonify({'success': False, 'message': f'Erro: {str(e)}'}), 500

    @app.route('/conta-origem/<int:id>/limpar-historico', methods=['POST'])
    @login_required
    def limpar_historico_conta(id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT co.id, cp.usuario_id
                    FROM contas_origem co
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE co.id = %s AND cp.usuario_id = %s
                ''', (id, session['user_id']))
                if not cursor.fetchone():
                    return jsonify({'success': False, 'message': 'Conta não encontrada'}), 404
            removidos = EmailHistoryManager.limpar_historico_conta(id)
            return jsonify({'success': True, 'message': f'{removidos} registros removidos do histórico'})
        except Exception as e:
            logger.error(f"Erro ao limpar histórico: {e}")
            return jsonify({'success': False, 'message': f'Erro: {str(e)}'}), 500
