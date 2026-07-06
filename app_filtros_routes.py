"""
Rotas adicionais para gerenciamento de filtros - adicionar em app.py
Suporta filtros globais (conta principal) e específicos (conta origem)
"""

# ─── Filtros GLOBAIS (Conta Principal) ──────────────────────────────────────

@app.route('/conta-principal/<int:conta_principal_id>/filtros-globais')
@login_required
def filtros_globais(conta_principal_id):
    """Lista todos os filtros globais de uma conta principal"""
    from filter_manager import FilterManager
    
    try:
        with DatabaseManager.get_cursor() as cursor:
            # Verificar se a conta pertence ao usuário
            cursor.execute('''
                SELECT * FROM contas_principais
                WHERE id = %s AND usuario_id = %s
            ''', (conta_principal_id, session['user_id']))
            conta = cursor.fetchone()
            
            if not conta:
                flash('Conta não encontrada', 'danger')
                return redirect(url_for('dashboard'))
        
        # Buscar filtros globais da conta
        filtros = FilterManager.get_filtros_globais(conta_principal_id)
        
        return render_template('filtros_globais.html', conta=conta, filtros=filtros)
    
    except Exception as e:
        logger.error(f"Erro ao listar filtros globais: {e}")
        flash('Erro ao carregar filtros', 'danger')
        return redirect(url_for('dashboard'))


@app.route('/conta-principal/<int:conta_principal_id>/filtro-global/adicionar', methods=['GET', 'POST'])
@login_required
def adicionar_filtro_global(conta_principal_id):
    """Adiciona um novo filtro global"""
    from filter_manager import FilterManager
    
    try:
        with DatabaseManager.get_cursor() as cursor:
            # Verificar se a conta pertence ao usuário
            cursor.execute('''
                SELECT * FROM contas_principais
                WHERE id = %s AND usuario_id = %s
            ''', (conta_principal_id, session['user_id']))
            conta = cursor.fetchone()
            
            if not conta:
                flash('Conta não encontrada', 'danger')
                return redirect(url_for('dashboard'))
        
        if request.method == 'POST':
            # Coletar dados do formulário
            filtro_data = {
                'nome': request.form.get('nome'),
                'ativo': request.form.get('ativo') == 'on',
                'criterio_remetente': request.form.get('criterio_remetente') or None,
                'criterio_destinatario': request.form.get('criterio_destinatario') or None,
                'criterio_assunto': request.form.get('criterio_assunto') or None,
                'criterio_corpo': request.form.get('criterio_corpo') or None,
                'criterio_tem_anexo': None,
                'acao_pular_inbox': request.form.get('acao_pular_inbox') == 'on',
                'acao_aplicar_label': request.form.get('acao_aplicar_label') or None,
                'acao_marcar_lido': request.form.get('acao_marcar_lido') == 'on',
                'acao_marcar_importante': request.form.get('acao_marcar_importante') == 'on',
                'acao_deletar': request.form.get('acao_deletar') == 'on',
                'acao_encaminhar_para': request.form.get('acao_encaminhar_para') or None
            }
            
            # Processar criterio_tem_anexo
            tem_anexo = request.form.get('criterio_tem_anexo')
            if tem_anexo == 'true':
                filtro_data['criterio_tem_anexo'] = True
            elif tem_anexo == 'false':
                filtro_data['criterio_tem_anexo'] = False
            
            # Validar: pelo menos um critério
            criterios = [
                filtro_data['criterio_remetente'],
                filtro_data['criterio_destinatario'],
                filtro_data['criterio_assunto'],
                filtro_data['criterio_corpo'],
                filtro_data['criterio_tem_anexo']
            ]
            if not any(c is not None and c != '' for c in criterios):
                flash('Defina pelo menos um critério de correspondência', 'warning')
                return render_template('adicionar_filtro_global.html', conta=conta)
            
            # Validar: pelo menos uma ação
            acoes = [
                filtro_data['acao_pular_inbox'],
                filtro_data['acao_marcar_lido'],
                filtro_data['acao_marcar_importante'],
                filtro_data['acao_deletar'],
                filtro_data['acao_aplicar_label'],
                filtro_data['acao_encaminhar_para']
            ]
            if not any(a for a in acoes):
                flash('Defina pelo menos uma ação', 'warning')
                return render_template('adicionar_filtro_global.html', conta=conta)
            
            # Criar filtro global
            filtro_id = FilterManager.criar_filtro_global(conta_principal_id, filtro_data)
            
            if filtro_id:
                flash(f'Filtro global "{filtro_data["nome"]}" criado com sucesso!', 'success')
                return redirect(url_for('filtros_globais', conta_principal_id=conta_principal_id))
            else:
                flash('Erro ao criar filtro', 'danger')
        
        return render_template('adicionar_filtro_global.html', conta=conta)
    
    except Exception as e:
        logger.error(f"Erro ao adicionar filtro global: {e}")
        flash('Erro ao processar solicitação', 'danger')
        return redirect(url_for('dashboard'))


# ─── Filtros ESPECÍFICOS (Conta Origem) ─────────────────────────────────────

@app.route('/conta-origem/<int:conta_origem_id>/filtros')
@login_required
def filtros_conta(conta_origem_id):
    """Lista todos os filtros específicos de uma conta de origem"""
    from filter_manager import FilterManager
    
    try:
        with DatabaseManager.get_cursor() as cursor:
            # Verificar se a conta pertence ao usuário
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
        
        # Buscar filtros específicos da conta
        filtros = FilterManager.get_filtros(conta_origem_id)
        
        return render_template('filtros.html', conta=conta, filtros=filtros)
    
    except Exception as e:
        logger.error(f"Erro ao listar filtros: {e}")
        flash('Erro ao carregar filtros', 'danger')
        return redirect(url_for('dashboard'))


@app.route('/conta-origem/<int:conta_origem_id>/filtro/adicionar', methods=['GET', 'POST'])
@login_required
def adicionar_filtro(conta_origem_id):
    """Adiciona um novo filtro específico"""
    from filter_manager import FilterManager
    
    try:
        with DatabaseManager.get_cursor() as cursor:
            # Verificar se a conta pertence ao usuário
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
            # Coletar dados do formulário
            filtro_data = {
                'nome': request.form.get('nome'),
                'ativo': request.form.get('ativo') == 'on',
                'criterio_remetente': request.form.get('criterio_remetente') or None,
                'criterio_destinatario': request.form.get('criterio_destinatario') or None,
                'criterio_assunto': request.form.get('criterio_assunto') or None,
                'criterio_corpo': request.form.get('criterio_corpo') or None,
                'criterio_tem_anexo': None,
                'acao_pular_inbox': request.form.get('acao_pular_inbox') == 'on',
                'acao_aplicar_label': request.form.get('acao_aplicar_label') or None,
                'acao_marcar_lido': request.form.get('acao_marcar_lido') == 'on',
                'acao_marcar_importante': request.form.get('acao_marcar_importante') == 'on',
                'acao_deletar': request.form.get('acao_deletar') == 'on',
                'acao_encaminhar_para': request.form.get('acao_encaminhar_para') or None
            }
            
            # Processar criterio_tem_anexo
            tem_anexo = request.form.get('criterio_tem_anexo')
            if tem_anexo == 'true':
                filtro_data['criterio_tem_anexo'] = True
            elif tem_anexo == 'false':
                filtro_data['criterio_tem_anexo'] = False
            
            # Validar: pelo menos um critério
            criterios = [
                filtro_data['criterio_remetente'],
                filtro_data['criterio_destinatario'],
                filtro_data['criterio_assunto'],
                filtro_data['criterio_corpo'],
                filtro_data['criterio_tem_anexo']
            ]
            if not any(c is not None and c != '' for c in criterios):
                flash('Defina pelo menos um critério de correspondência', 'warning')
                return render_template('adicionar_filtro.html', conta=conta)
            
            # Validar: pelo menos uma ação
            acoes = [
                filtro_data['acao_pular_inbox'],
                filtro_data['acao_marcar_lido'],
                filtro_data['acao_marcar_importante'],
                filtro_data['acao_deletar'],
                filtro_data['acao_aplicar_label'],
                filtro_data['acao_encaminhar_para']
            ]
            if not any(a for a in acoes):
                flash('Defina pelo menos uma ação', 'warning')
                return render_template('adicionar_filtro.html', conta=conta)
            
            # Criar filtro
            filtro_id = FilterManager.criar_filtro(conta_origem_id, filtro_data)
            
            if filtro_id:
                flash(f'Filtro "{filtro_data["nome"]}" criado com sucesso!', 'success')
                return redirect(url_for('filtros_conta', conta_origem_id=conta_origem_id))
            else:
                flash('Erro ao criar filtro', 'danger')
        
        return render_template('adicionar_filtro.html', conta=conta)
    
    except Exception as e:
        logger.error(f"Erro ao adicionar filtro: {e}")
        flash('Erro ao processar solicitação', 'danger')
        return redirect(url_for('dashboard'))


# ─── Edição de Filtros (Global ou Específico) ───────────────────────────────

@app.route('/filtro/<int:id>/editar', methods=['GET', 'POST'])
@login_required
def editar_filtro(id):
    """Edita um filtro existente (global ou específico)"""
    from filter_manager import FilterManager
    
    try:
        with DatabaseManager.get_cursor() as cursor:
            # Buscar filtro e verificar permissão
            cursor.execute('''
                SELECT f.*, 
                       co.id as conta_origem_id, 
                       co.nome as conta_nome,
                       cp.usuario_id, 
                       cp.id as conta_principal_id,
                       cp.nome as conta_principal_nome
                FROM filtros_email f
                LEFT JOIN contas_origem co ON f.conta_origem_id = co.id
                LEFT JOIN contas_principais cp ON (f.conta_principal_id = cp.id OR co.conta_principal_id = cp.id)
                WHERE f.id = %s AND cp.usuario_id = %s
            ''', (id, session['user_id']))
            filtro = cursor.fetchone()
            
            if not filtro:
                flash('Filtro não encontrado', 'danger')
                return redirect(url_for('dashboard'))
            
            # Determinar se é filtro global ou específico
            is_global = filtro['conta_origem_id'] is None
        
        if request.method == 'POST':
            # Coletar dados do formulário
            filtro_data = {
                'nome': request.form.get('nome'),
                'ativo': request.form.get('ativo') == 'on',
                'criterio_remetente': request.form.get('criterio_remetente') or None,
                'criterio_destinatario': request.form.get('criterio_destinatario') or None,
                'criterio_assunto': request.form.get('criterio_assunto') or None,
                'criterio_corpo': request.form.get('criterio_corpo') or None,
                'criterio_tem_anexo': None,
                'acao_pular_inbox': request.form.get('acao_pular_inbox') == 'on',
                'acao_aplicar_label': request.form.get('acao_aplicar_label') or None,
                'acao_marcar_lido': request.form.get('acao_marcar_lido') == 'on',
                'acao_marcar_importante': request.form.get('acao_marcar_importante') == 'on',
                'acao_deletar': request.form.get('acao_deletar') == 'on',
                'acao_encaminhar_para': request.form.get('acao_encaminhar_para') or None
            }
            
            # Processar criterio_tem_anexo
            tem_anexo = request.form.get('criterio_tem_anexo')
            if tem_anexo == 'true':
                filtro_data['criterio_tem_anexo'] = True
            elif tem_anexo == 'false':
                filtro_data['criterio_tem_anexo'] = False
            
            # Atualizar filtro
            if FilterManager.atualizar_filtro(id, filtro_data):
                flash(f'Filtro "{filtro_data["nome"]}" atualizado com sucesso!', 'success')
                
                # Redirecionar para a página correta
                if is_global:
                    return redirect(url_for('filtros_globais', conta_principal_id=filtro['conta_principal_id']))
                else:
                    return redirect(url_for('filtros_conta', conta_origem_id=filtro['conta_origem_id']))
            else:
                flash('Erro ao atualizar filtro', 'danger')
        
        return render_template('editar_filtro.html', filtro=filtro, is_global=is_global)
    
    except Exception as e:
        logger.error(f"Erro ao editar filtro: {e}")
        flash('Erro ao processar solicitação', 'danger')
        return redirect(url_for('dashboard'))


@app.route('/filtro/<int:id>/toggle', methods=['POST'])
@login_required
def toggle_filtro(id):
    """Ativa/desativa um filtro"""
    from filter_manager import FilterManager
    
    try:
        with DatabaseManager.get_cursor() as cursor:
            # Verificar permissão
            cursor.execute('''
                SELECT f.*, cp.usuario_id
                FROM filtros_email f
                LEFT JOIN contas_origem co ON f.conta_origem_id = co.id
                LEFT JOIN contas_principais cp ON (f.conta_principal_id = cp.id OR co.conta_principal_id = cp.id)
                WHERE f.id = %s AND cp.usuario_id = %s
            ''', (id, session['user_id']))
            filtro = cursor.fetchone()
            
            if not filtro:
                return jsonify({'success': False, 'message': 'Filtro não encontrado'}), 404
        
        if FilterManager.toggle_filtro(id):
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'message': 'Erro ao alternar filtro'}), 500
    
    except Exception as e:
        logger.error(f"Erro ao alternar filtro: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/filtro/<int:id>/deletar', methods=['POST'])
@login_required
def deletar_filtro(id):
    """Deleta um filtro"""
    from filter_manager import FilterManager
    
    try:
        with DatabaseManager.get_cursor() as cursor:
            # Verificar permissão
            cursor.execute('''
                SELECT f.*, cp.usuario_id
                FROM filtros_email f
                LEFT JOIN contas_origem co ON f.conta_origem_id = co.id
                LEFT JOIN contas_principais cp ON (f.conta_principal_id = cp.id OR co.conta_principal_id = cp.id)
                WHERE f.id = %s AND cp.usuario_id = %s
            ''', (id, session['user_id']))
            filtro = cursor.fetchone()
            
            if not filtro:
                return jsonify({'success': False, 'message': 'Filtro não encontrado'}), 404
        
        if FilterManager.deletar_filtro(id):
            flash('Filtro deletado com sucesso!', 'success')
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'message': 'Erro ao deletar filtro'}), 500
    
    except Exception as e:
        logger.error(f"Erro ao deletar filtro: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500
