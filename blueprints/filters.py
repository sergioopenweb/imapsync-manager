"""
Rotas de filtros de email (globais e específicos por conta de origem).
"""
import logging
from flask import render_template, request, redirect, url_for, session, flash, jsonify
from config import Config
from db_manager import DatabaseManager, EmailHistoryManager
from filter_manager import FilterManager
from blueprints.utils import login_required

logger = logging.getLogger(__name__)


def _coletar_filtro_data(form):
    """Extrai e normaliza os dados de filtro do formulário."""
    def _opt(val):
        return (val or '').strip() or None

    filtro_data = {
        'nome':                   form.get('nome'),
        'ativo':                  form.get('ativo') == 'on',
        'criterio_remetente':     form.get('criterio_remetente') or None,
        'criterio_destinatario':  form.get('criterio_destinatario') or None,
        'criterio_assunto':       form.get('criterio_assunto') or None,
        'criterio_corpo':         form.get('criterio_corpo') or None,
        'criterio_tem_anexo':     None,
        'acao_pular_inbox':       form.get('acao_pular_inbox') == 'on',
        'acao_aplicar_label':     _opt(form.get('acao_aplicar_label')),
        'acao_marcar_lido':       form.get('acao_marcar_lido') == 'on',
        'acao_marcar_importante': form.get('acao_marcar_importante') == 'on',
        'acao_deletar':           form.get('acao_deletar') == 'on',
        'acao_encaminhar_para':   _opt(form.get('acao_encaminhar_para')),
    }
    tem_anexo = form.get('criterio_tem_anexo')
    if tem_anexo == 'true':
        filtro_data['criterio_tem_anexo'] = True
    elif tem_anexo == 'false':
        filtro_data['criterio_tem_anexo'] = False
    return filtro_data


def _validar_filtro(filtro_data, template, **ctx):
    """Retorna None se válido, ou uma Response de erro para renderizar o formulário."""
    from flask import render_template as rt
    criterios = [
        filtro_data['criterio_remetente'],
        filtro_data['criterio_destinatario'],
        filtro_data['criterio_assunto'],
        filtro_data['criterio_corpo'],
        filtro_data['criterio_tem_anexo'],
    ]
    if not any(c is not None and c != '' for c in criterios):
        flash('Defina pelo menos um critério de correspondência', 'warning')
        return rt(template, **ctx)
    acoes = [
        filtro_data['acao_pular_inbox'],
        filtro_data['acao_marcar_lido'],
        filtro_data['acao_marcar_importante'],
        filtro_data['acao_deletar'],
        filtro_data['acao_aplicar_label'],
        filtro_data['acao_encaminhar_para'],
    ]
    if not any(a for a in acoes):
        flash('Defina pelo menos uma ação', 'warning')
        return rt(template, **ctx)
    return None


def register_routes(app):

    # ── Filtros GLOBAIS (Conta Principal) ────────────────────────────────────

    @app.route('/conta-principal/<int:conta_principal_id>/filtros-globais')
    @login_required
    def filtros_globais(conta_principal_id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT * FROM contas_principais
                    WHERE id = %s AND usuario_id = %s
                ''', (conta_principal_id, session['user_id']))
                conta = cursor.fetchone()
                if not conta:
                    flash('Conta não encontrada', 'danger')
                    return redirect(url_for('dashboard'))

            filtros = FilterManager.get_filtros_globais(conta_principal_id)
            return render_template('filtros_globais.html', conta=conta, filtros=filtros)
        except Exception as e:
            logger.error(f"Erro ao listar filtros globais: {e}")
            flash('Erro ao carregar filtros', 'danger')
            return redirect(url_for('dashboard'))

    @app.route('/conta-principal/<int:conta_principal_id>/filtro-global/adicionar', methods=['GET', 'POST'])
    @login_required
    def adicionar_filtro_global(conta_principal_id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT * FROM contas_principais
                    WHERE id = %s AND usuario_id = %s
                ''', (conta_principal_id, session['user_id']))
                conta = cursor.fetchone()
                if not conta:
                    flash('Conta não encontrada', 'danger')
                    return redirect(url_for('dashboard'))

            if request.method == 'POST':
                filtro_data = _coletar_filtro_data(request.form)
                erro = _validar_filtro(filtro_data, 'adicionar_filtro_global.html', conta=conta)
                if erro:
                    return erro
                filtro_id = FilterManager.criar_filtro_global(conta_principal_id, filtro_data)
                if filtro_id:
                    flash(f'Filtro global "{filtro_data["nome"]}" criado com sucesso!', 'success')
                    return redirect(url_for('filtros_globais', conta_principal_id=conta_principal_id))
                flash('Erro ao criar filtro', 'danger')

            return render_template('adicionar_filtro_global.html', conta=conta)
        except Exception as e:
            logger.error(f"Erro ao adicionar filtro global: {e}")
            flash('Erro ao processar solicitação', 'danger')
            return redirect(url_for('dashboard'))

    @app.route('/conta-principal/<int:conta_principal_id>/filtros-globais/importar-gmail',
               methods=['GET', 'POST'])
    @login_required
    def importar_filtros_gmail_route(conta_principal_id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT * FROM contas_principais
                    WHERE id = %s AND usuario_id = %s
                ''', (conta_principal_id, session['user_id']))
                conta = cursor.fetchone()
                if not conta:
                    flash('Conta não encontrada', 'danger')
                    return redirect(url_for('dashboard'))
        except Exception as e:
            logger.error(f"Erro ao verificar conta: {e}")
            flash('Erro ao carregar', 'danger')
            return redirect(url_for('dashboard'))

        if request.method == 'POST':
            xml_content = None
            if request.files.get('arquivo_xml'):
                f = request.files['arquivo_xml']
                if f.filename and f.filename.lower().endswith(('.xml', '.xml.gz')):
                    raw = f.read()
                    xml_content = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else raw
            if not xml_content and request.form.get('xml_texto'):
                xml_content = request.form.get('xml_texto', '').strip()
            if not xml_content:
                flash('Envie um arquivo XML ou cole o conteúdo do arquivo exportado do Gmail.', 'warning')
                return render_template('importar_filtros_gmail.html', conta=conta)
            try:
                from gmail_filters_import import importar_filtros_gmail
                stats = importar_filtros_gmail(xml_content, conta_principal_id)
            except Exception as e:
                logger.exception("Importação Gmail")
                flash(f'Erro ao importar: {e}', 'danger')
                return render_template('importar_filtros_gmail.html', conta=conta)
            msg = (
                f"Importados: {stats['importados']}; já existentes: {stats['ja_existentes']}; "
                f"sem critério: {stats['ignorados']}."
            )
            if stats.get('possiveis_duplicatas', 0) > 0:
                msg += f" Possíveis duplicatas ignoradas: {stats['possiveis_duplicatas']}."
            if stats['erros']:
                msg += f" Erros: {len(stats['erros'])}."
            flash(msg, 'success' if stats['importados'] else 'info')
            return redirect(url_for('filtros_globais', conta_principal_id=conta_principal_id))

        return render_template('importar_filtros_gmail.html', conta=conta)

    # ── Filtros ESPECÍFICOS (Conta Origem) ───────────────────────────────────

    @app.route('/conta-origem/<int:conta_origem_id>/filtros')
    @login_required
    def filtros_conta(conta_origem_id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT co.*, cp.usuario_id, cp.id as conta_principal_id
                    FROM contas_origem co
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE co.id = %s AND cp.usuario_id = %s
                ''', (conta_origem_id, session['user_id']))
                conta = cursor.fetchone()
                if not conta:
                    flash('Conta não encontrada', 'danger')
                    return redirect(url_for('dashboard'))

            filtros = FilterManager.get_filtros(conta_origem_id)
            return render_template('filtros.html', conta=conta, filtros=filtros)
        except Exception as e:
            logger.error(f"Erro ao listar filtros: {e}")
            flash('Erro ao carregar filtros', 'danger')
            return redirect(url_for('dashboard'))

    @app.route('/conta-origem/<int:conta_origem_id>/filtro/adicionar', methods=['GET', 'POST'])
    @login_required
    def adicionar_filtro(conta_origem_id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT co.*, cp.usuario_id, cp.id as conta_principal_id
                    FROM contas_origem co
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE co.id = %s AND cp.usuario_id = %s
                ''', (conta_origem_id, session['user_id']))
                conta = cursor.fetchone()
                if not conta:
                    flash('Conta não encontrada', 'danger')
                    return redirect(url_for('dashboard'))

            if request.method == 'POST':
                filtro_data = _coletar_filtro_data(request.form)
                erro = _validar_filtro(filtro_data, 'adicionar_filtro.html', conta=conta)
                if erro:
                    return erro
                filtro_id = FilterManager.criar_filtro(conta_origem_id, filtro_data)
                if filtro_id:
                    flash(f'Filtro "{filtro_data["nome"]}" criado com sucesso!', 'success')
                    return redirect(url_for('filtros_conta', conta_origem_id=conta_origem_id))
                flash('Erro ao criar filtro', 'danger')

            return render_template('adicionar_filtro.html', conta=conta)
        except Exception as e:
            logger.error(f"Erro ao adicionar filtro: {e}")
            flash('Erro ao processar solicitação', 'danger')
            return redirect(url_for('dashboard'))

    # ── Edição / toggle / exclusão (global ou específico) ────────────────────

    @app.route('/filtro/<int:id>/editar', methods=['GET', 'POST'])
    @login_required
    def editar_filtro(id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT f.*,
                           co.id as conta_origem_id,
                           co.nome as conta_nome,
                           cp.usuario_id,
                           cp.id as conta_principal_id,
                           cp.nome as conta_principal_nome
                    FROM filtros_email f
                    LEFT JOIN contas_origem co ON f.conta_origem_id = co.id
                    LEFT JOIN contas_principais cp ON (
                        f.conta_principal_id = cp.id OR co.conta_principal_id = cp.id
                    )
                    WHERE f.id = %s AND cp.usuario_id = %s
                ''', (id, session['user_id']))
                filtro = cursor.fetchone()
                if not filtro:
                    flash('Filtro não encontrado', 'danger')
                    return redirect(url_for('dashboard'))
                is_global = filtro['conta_origem_id'] is None

            if request.method == 'POST':
                filtro_data = _coletar_filtro_data(request.form)
                if FilterManager.atualizar_filtro(id, filtro_data):
                    flash(f'Filtro "{filtro_data["nome"]}" atualizado com sucesso!', 'success')
                    if is_global:
                        return redirect(url_for('filtros_globais', conta_principal_id=filtro['conta_principal_id']))
                    return redirect(url_for('filtros_conta', conta_origem_id=filtro['conta_origem_id']))
                flash('Erro ao atualizar filtro', 'danger')

            ultimos_emails = EmailHistoryManager.listar_ultimos_emails_por_filtro(filtro['id'], limite=Config.FILTRO_ULTIMOS_EMAILS_LIMIT)
            return render_template('editar_filtro.html', filtro=filtro, is_global=is_global,
                                   ultimos_emails=ultimos_emails)
        except Exception as e:
            logger.error(f"Erro ao editar filtro: {e}")
            flash('Erro ao processar solicitação', 'danger')
            return redirect(url_for('dashboard'))

    @app.route('/filtro/<int:id>/toggle', methods=['POST'])
    @login_required
    def toggle_filtro(id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT f.*, cp.usuario_id
                    FROM filtros_email f
                    LEFT JOIN contas_origem co ON f.conta_origem_id = co.id
                    LEFT JOIN contas_principais cp ON (
                        f.conta_principal_id = cp.id OR co.conta_principal_id = cp.id
                    )
                    WHERE f.id = %s AND cp.usuario_id = %s
                ''', (id, session['user_id']))
                if not cursor.fetchone():
                    return jsonify({'success': False, 'message': 'Filtro não encontrado'}), 404
            if FilterManager.toggle_filtro(id):
                return jsonify({'success': True})
            return jsonify({'success': False, 'message': 'Erro ao alternar filtro'}), 500
        except Exception as e:
            logger.error(f"Erro ao alternar filtro: {e}")
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/filtro/<int:id>/deletar', methods=['POST'])
    @login_required
    def deletar_filtro(id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT f.*, cp.usuario_id
                    FROM filtros_email f
                    LEFT JOIN contas_origem co ON f.conta_origem_id = co.id
                    LEFT JOIN contas_principais cp ON (
                        f.conta_principal_id = cp.id OR co.conta_principal_id = cp.id
                    )
                    WHERE f.id = %s AND cp.usuario_id = %s
                ''', (id, session['user_id']))
                if not cursor.fetchone():
                    return jsonify({'success': False, 'message': 'Filtro não encontrado'}), 404
            if FilterManager.deletar_filtro(id):
                flash('Filtro deletado com sucesso!', 'success')
                return jsonify({'success': True})
            return jsonify({'success': False, 'message': 'Erro ao deletar filtro'}), 500
        except Exception as e:
            logger.error(f"Erro ao deletar filtro: {e}")
            return jsonify({'success': False, 'message': str(e)}), 500
