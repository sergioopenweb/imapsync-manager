"""
Rotas de administração: usuários, impersonação, spam admin.
"""
import logging
from flask import render_template, request, redirect, url_for, session, flash, jsonify
from config import Config
from db_manager import DatabaseManager
from blueprints.utils import login_required, admin_required, is_admin

logger = logging.getLogger(__name__)


def register_routes(app):

    @app.route('/admin/usuarios')
    @login_required
    @admin_required
    def admin_usuarios():
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT u.id, u.nome, u.email, u.admin, u.ativo, u.criado_em,
                           u.api_key,
                           (SELECT COUNT(*) FROM contas_principais WHERE usuario_id = u.id) AS total_contas
                    FROM usuarios u
                    ORDER BY u.criado_em DESC
                ''')
                usuarios = cursor.fetchall()
            return render_template('admin_usuarios.html', usuarios=usuarios)
        except Exception as e:
            logger.error(f"Erro ao listar usuários (admin): {e}")
            flash('Erro ao carregar lista de usuários.', 'danger')
            return redirect(url_for('dashboard'))

    @app.route('/admin/usuarios/<int:user_id>/toggle', methods=['POST'])
    @login_required
    @admin_required
    def admin_toggle_usuario(user_id):
        if user_id == session.get('user_id'):
            return jsonify({'success': False, 'message': 'Você não pode desativar sua própria conta.'}), 400
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('SELECT id, ativo FROM usuarios WHERE id = %s', (user_id,))
                row = cursor.fetchone()
                if not row:
                    return jsonify({'success': False, 'message': 'Usuário não encontrado.'}), 404
                novo_ativo = 0 if row.get('ativo', 1) else 1
                cursor.execute('UPDATE usuarios SET ativo = %s WHERE id = %s', (novo_ativo, user_id))
            msg = 'Usuário ativado.' if novo_ativo else 'Usuário desativado.'
            flash(msg, 'success')
            return jsonify({'success': True, 'ativo': bool(novo_ativo), 'redirect': url_for('admin_usuarios')})
        except Exception as e:
            logger.error(f"Erro ao alternar usuário {user_id}: {e}")
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/admin/usuarios/<int:user_id>/deletar', methods=['POST'])
    @login_required
    @admin_required
    def admin_deletar_usuario(user_id):
        if user_id == session.get('user_id'):
            return jsonify({'success': False, 'message': 'Você não pode deletar sua própria conta.'}), 400
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('SELECT id FROM usuarios WHERE id = %s', (user_id,))
                if not cursor.fetchone():
                    return jsonify({'success': False, 'message': 'Usuário não encontrado.'}), 404
                cursor.execute('SELECT id FROM contas_principais WHERE usuario_id = %s', (user_id,))
                cp_ids = [row['id'] for row in cursor.fetchall()]
                if cp_ids:
                    placeholders = ','.join(['%s'] * len(cp_ids))
                    cursor.execute(
                        'DELETE FROM filtros_email WHERE conta_principal_id IN (' + placeholders + ')',
                        cp_ids
                    )
                cursor.execute('DELETE FROM usuarios WHERE id = %s', (user_id,))
            flash('Usuário removido com sucesso.', 'success')
            return jsonify({'success': True, 'redirect': url_for('admin_usuarios')})
        except Exception as e:
            logger.error(f"Erro ao deletar usuário {user_id}: {e}")
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/admin/usuarios/<int:user_id>/toggle-admin', methods=['POST'])
    @login_required
    @admin_required
    def admin_toggle_admin(user_id):
        """Concede ou remove a flag de admin de um usuário."""
        if user_id == session.get('user_id'):
            return jsonify({'success': False, 'message': 'Você não pode alterar sua própria flag de admin.'}), 400
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('SELECT id, admin, nome FROM usuarios WHERE id = %s', (user_id,))
                row = cursor.fetchone()
                if not row:
                    return jsonify({'success': False, 'message': 'Usuário não encontrado.'}), 404
                novo_admin = 0 if row.get('admin', 0) else 1
                cursor.execute('UPDATE usuarios SET admin = %s WHERE id = %s', (novo_admin, user_id))
            msg = f'{"Admin concedido" if novo_admin else "Admin removido"} para {row["nome"]}.'
            flash(msg, 'success')
            return jsonify({'success': True, 'admin': bool(novo_admin), 'redirect': url_for('admin_usuarios')})
        except Exception as e:
            logger.error(f"Erro ao alternar admin do usuário {user_id}: {e}")
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/admin/usuarios/<int:user_id>/entrar-como', methods=['POST'])
    @login_required
    @admin_required
    def admin_entrar_como(user_id):
        if user_id == session.get('user_id'):
            return redirect(url_for('dashboard'))
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('SELECT id, nome, email, ativo FROM usuarios WHERE id = %s', (user_id,))
                alvo = cursor.fetchone()
                if not alvo:
                    flash('Usuário não encontrado.', 'danger')
                    return redirect(url_for('admin_usuarios'))
                if not alvo.get('ativo', 1):
                    flash('Não é possível entrar como usuário desativado.', 'warning')
                    return redirect(url_for('admin_usuarios'))
            session['admin_original_id'] = session['user_id']
            session['user_id']           = alvo['id']
            session['user_nome']         = alvo['nome']
            session['user_email']        = alvo['email']
            session['is_admin']          = False
            flash(f'Entrando como {alvo["nome"]}. Use "Voltar ao admin" para retornar.', 'info')
            return redirect(url_for('dashboard'))
        except Exception as e:
            logger.error(f"Erro ao entrar como usuário {user_id}: {e}")
            flash('Erro ao assumir usuário.', 'danger')
            return redirect(url_for('admin_usuarios'))

    @app.route('/admin/voltar')
    @login_required
    def admin_voltar():
        admin_id = session.pop('admin_original_id', None)
        if not admin_id:
            return redirect(url_for('dashboard'))
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('SELECT id, nome, email, admin FROM usuarios WHERE id = %s', (admin_id,))
                admin = cursor.fetchone()
                if not admin:
                    session.clear()
                    flash('Sessão do admin não encontrada. Faça login novamente.', 'warning')
                    return redirect(url_for('login'))
            session['user_id']   = admin['id']
            session['user_nome'] = admin['nome']
            session['user_email'] = admin['email']
            session['is_admin']  = bool(admin.get('admin', 0))
            flash('Você voltou à sua conta de administrador.', 'success')
            return redirect(url_for('admin_usuarios'))
        except Exception as e:
            logger.error(f"Erro ao restaurar sessão admin: {e}")
            session.clear()
            return redirect(url_for('login'))

    @app.route('/admin/usuarios/<int:user_id>/gerar-api-key', methods=['POST'])
    @login_required
    @admin_required
    def admin_gerar_api_key(user_id):
        """Gera (ou regenera) a API key de um usuário."""
        import secrets
        try:
            nova_key = secrets.token_hex(Config.API_KEY_BYTES)
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('UPDATE usuarios SET api_key = %s WHERE id = %s', (nova_key, user_id))
            return jsonify({'success': True, 'api_key': nova_key})
        except Exception as e:
            logger.error(f"Erro ao gerar API key para usuário {user_id}: {e}")
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/admin/spam-global')
    @login_required
    @admin_required
    def admin_spam_palavras_genericas():
        """Config global de spam (admin). Mantém URL antiga para compatibilidade."""
        from spam_analyzer_config import (
            get_config_global, get_palavras_genericas,
        )
        return render_template(
            'admin_spam_global.html',
            global_cfg=get_config_global(force_reload=True),
            palavras_genericas=get_palavras_genericas(force_reload=True),
        )

    @app.route('/admin/spam-global/salvar', methods=['POST'])
    @login_required
    @admin_required
    def admin_spam_global_salvar():
        from spam_analyzer_config import salvar_config_global, ACAO_MARCAR_SPAM, ACAO_PULAR_INBOX
        ativo_por_defeito        = request.form.get('ativo_por_defeito') == 'on'
        acao_por_defeito         = request.form.get('acao_por_defeito') or ACAO_MARCAR_SPAM
        pasta_spam_padrao        = request.form.get('pasta_spam_padrao') or ''
        wordlist_global          = request.form.get('wordlist_global') or ''
        remetentes_bloqueados    = request.form.get('remetentes_bloqueados') or ''
        remetentes_permitidos    = request.form.get('remetentes_permitidos') or ''
        dominios_gratuitos       = request.form.get('dominios_gratuitos') or ''
        palavras_institucionais  = request.form.get('palavras_institucionais') or ''
        heuristica_reply_to      = request.form.get('heuristica_reply_to') == 'on'
        heuristica_display_name  = request.form.get('heuristica_display_name') == 'on'
        heuristica_dom_num       = request.form.get('heuristica_dominio_numerico') == 'on'

        if acao_por_defeito not in (ACAO_MARCAR_SPAM, ACAO_PULAR_INBOX):
            acao_por_defeito = ACAO_MARCAR_SPAM

        if salvar_config_global(
            ativo_por_defeito=ativo_por_defeito,
            acao_por_defeito=acao_por_defeito,
            pasta_spam_padrao=pasta_spam_padrao,
            wordlist_global=wordlist_global,
            remetentes_bloqueados=remetentes_bloqueados,
            remetentes_permitidos=remetentes_permitidos,
            dominios_gratuitos=dominios_gratuitos,
            palavras_institucionais=palavras_institucionais,
            heuristica_reply_to=heuristica_reply_to,
            heuristica_display_name=heuristica_display_name,
            heuristica_dominio_numerico=heuristica_dom_num,
        ):
            flash('Configuração global de spam salva.', 'success')
        else:
            flash('Erro ao salvar configuração global.', 'danger')
        return redirect(url_for('admin_spam_palavras_genericas'))

    @app.route('/admin/spam-global/limpar-redundancias', methods=['POST'])
    @login_required
    @admin_required
    def admin_spam_global_limpar_redundancias():
        """
        Re-salva a config global aplicando limpeza de redundâncias de wordlist
        e remove entradas já existentes no global do nível usuário (config_spam_usuario).
        """
        from spam_analyzer_config import (
            get_config_global,
            salvar_config_global,
            limpar_configs_usuario_redundantes_com_global,
        )
        cfg = get_config_global(force_reload=True)
        ok = salvar_config_global(
            ativo_por_defeito=bool(cfg.get('ativo_por_defeito', False)),
            acao_por_defeito=cfg.get('acao_por_defeito') or 'mark_spam',
            pasta_spam_padrao=cfg.get('pasta_spam_padrao') or None,
            wordlist_global=cfg.get('wordlist_global') or '',
            remetentes_bloqueados=cfg.get('remetentes_bloqueados') or '',
            remetentes_permitidos=cfg.get('remetentes_permitidos') or '',
            dominios_gratuitos=cfg.get('dominios_gratuitos') or '',
            palavras_institucionais=cfg.get('palavras_institucionais') or '',
            heuristica_reply_to=bool(cfg.get('heuristica_reply_to', True)),
            heuristica_display_name=bool(cfg.get('heuristica_display_name', True)),
            heuristica_dominio_numerico=bool(cfg.get('heuristica_dominio_numerico', True)),
        )
        atualizados = limpar_configs_usuario_redundantes_com_global()
        return jsonify({'success': bool(ok), 'usuarios_atualizados': atualizados})

    def _admin_spam_endpoint(fn_add, fn_rem, campo='valor'):
        data  = request.get_json(silent=True) or {}
        valor = (data.get(campo) or '').strip().lower()
        if not valor:
            return jsonify({'success': False, 'message': 'Valor não informado.'}), 400
        if data.get('_acao') == 'remover':
            ok = fn_rem(valor)
            if not ok:
                return jsonify({'success': False, 'message': f'"{valor}" não encontrado.'}), 404
        else:
            ok = fn_add(valor)
            if not ok:
                return jsonify({'success': False, 'message': f'"{valor}" já existe na lista.'}), 409
        return jsonify({'success': True, campo: valor})

    @app.route('/admin/spam-palavras-genericas/genericas', methods=['POST'])
    @login_required
    @admin_required
    def admin_spam_genericas_crud():
        from spam_analyzer_config import adicionar_palavra_generica, remover_palavra_generica
        return _admin_spam_endpoint(adicionar_palavra_generica, remover_palavra_generica, 'palavra')
