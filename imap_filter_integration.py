"""
REFERÊNCIA DE INTEGRAÇÃO — imap_sync_native.py
Este arquivo é apenas documentação/guia de patch para integrar filtros
dentro do loop de cópia do imap_sync_native.py. Não é um módulo executado.

Patch para imap_sync_native.py - Adicionar suporte a filtros GLOBAIS e ESPECÍFICOS

Adicione este código no início da função copy_folder(), logo após carregar o histórico:

    # ═══ CARREGAR FILTROS ATIVOS (GLOBAIS + ESPECÍFICOS) ═══════════════════════
    filtros = []
    if conta_origem_id and conta_principal_id:
        try:
            from filter_manager import FilterManager
            # Busca filtros globais da conta principal + filtros específicos da origem
            filtros = FilterManager.get_filtros_ativos_para_sincronizacao(
                conta_origem_id, 
                conta_principal_id
            )
            if filtros:
                globais = sum(1 for f in filtros if f.is_global)
                especificos = len(filtros) - globais
                logger.info(f"Carregados {len(filtros)} filtro(s): {globais} global(is), {especificos} específico(s)")
        except Exception as e:
            logger.warning(f"Erro ao carregar filtros: {e}")
    # ═══════════════════════════════════════════════════════════════════════════

IMPORTANTE: Você precisa ter o conta_principal_id disponível. 
Se não tiver, adicione-o como parâmetro da função copy_folder().

E substitua o bloco que copia para o destino pelo código abaixo.
"""

# Código a ser inserido na função copy_folder()
FILTER_INTEGRATION_CODE = '''
                # ═══ APLICAR FILTROS (GLOBAIS + ESPECÍFICOS) ════════════════
                # Preparar dados do email para os filtros
                email_data = {
                    'from': from_addr,
                    'to': to_addr,
                    'subject': subject,
                    'body': '',  # Corpo não é extraído por padrão por performance
                    'has_attachment': False  # Verificar se tem anexo
                }
                
                # Verificar se tem anexo
                if parsed_msg.is_multipart():
                    for part in parsed_msg.walk():
                        if part.get_content_maintype() == 'multipart':
                            continue
                        if part.get('Content-Disposition') is not None:
                            email_data['has_attachment'] = True
                            break
                
                # Aplicar filtros (globais primeiro, depois específicos)
                filter_actions = {
                    'pular_inbox': False,
                    'aplicar_label': None,
                    'marcar_lido': False,
                    'marcar_importante': False,
                    'deletar': False,
                    'encaminhar_para': None
                }
                
                for filtro in filtros:
                    if filtro.matches(email_data):
                        filtro_tipo = "GLOBAL" if filtro.is_global else "ESPECÍFICO"
                        logger.debug(f"Email corresponde ao filtro {filtro_tipo} '{filtro.nome}'")
                        actions = filtro.get_actions()
                        
                        # Combinar ações de múltiplos filtros
                        # Filtros específicos podem sobrescrever globais
                        if actions['pular_inbox']:
                            filter_actions['pular_inbox'] = True
                        if actions['aplicar_label']:
                            filter_actions['aplicar_label'] = actions['aplicar_label']
                        if actions['marcar_lido']:
                            filter_actions['marcar_lido'] = True
                        if actions['marcar_importante']:
                            filter_actions['marcar_importante'] = True
                        if actions['deletar']:
                            filter_actions['deletar'] = True
                        if actions['encaminhar_para']:
                            filter_actions['encaminhar_para'] = actions['encaminhar_para']
                
                # ═══ APLICAR AÇÕES DOS FILTROS ═══════════════════════════════
                
                # Se deve deletar, pular a cópia completamente
                if filter_actions['deletar']:
                    logger.debug(f"Email será deletado por filtro (UID {uid_str})")
                    stats['ignoradas'] += 1
                    # Marcar para deleção na origem
                    uids_para_deletar.append(uid_str)
                    continue
                
                # Determinar pastas de destino (respeitar "pular inbox" quando há label)
                pastas_destino = []
                if filter_actions['aplicar_label']:
                    label_name = filter_actions['aplicar_label'].strip()
                    label_folder = None
                    if ensure_folder_exists(dst_conn, label_name):
                        label_folder = label_name
                    elif '[Gmail]/' not in label_name and ensure_folder_exists(dst_conn, '[Gmail]/' + label_name):
                        label_folder = '[Gmail]/' + label_name
                    if label_folder:
                        if not filter_actions['pular_inbox']:
                            pastas_destino.append(dst_folder)
                            pastas_destino.append(label_folder)
                            logger.debug(f"Aplicando label '{label_folder}' ao email (mantendo na caixa de entrada)")
                        else:
                            pastas_destino.append(label_folder)
                            logger.debug(f"Aplicando label '{label_folder}' ao email (sem caixa de entrada)")
                if not pastas_destino and filter_actions['pular_inbox']:
                    archive_folders = ['Archive', 'Arquivo', '[Gmail]/All Mail']
                    for archive_name in archive_folders:
                        if ensure_folder_exists(dst_conn, archive_name):
                            pastas_destino.append(archive_name)
                            logger.debug(f"Pulando inbox, arquivando em '{archive_name}'")
                            break
                if not pastas_destino:
                    pastas_destino.append(dst_folder)
                
                # Modificar flags baseado nos filtros
                email_flags = list(flags) if flags else []
                
                # Marcar como lido
                if filter_actions['marcar_lido']:
                    if b'\\Seen' not in email_flags:
                        email_flags.append(b'\\Seen')
                    logger.debug(f"Marcando email como lido por filtro")
                
                # Marcar como importante
                if filter_actions['marcar_importante']:
                    if b'\\Flagged' not in email_flags:
                        email_flags.append(b'\\Flagged')
                    logger.debug(f"Marcando email como importante por filtro")
                
                # ═══ COPIAR PARA DESTINO(S) ═══════════════════════════════════
                copiou_alguma = False
                from imap_sync_native import (
                    _format_imap_append_flags,
                    _imap_append_datetime,
                    _imap_mailbox_arg,
                )
                append_flags = _format_imap_append_flags(email_flags, flags)
                append_date = _imap_append_datetime(internal_date)
                for target_folder in pastas_destino:
                    try:
                        dst_conn.append(
                            _imap_mailbox_arg(target_folder),
                            append_flags,
                            append_date,
                            raw_email
                        )
                        copiou_alguma = True
                        logger.debug(f"Email copiado para '{target_folder}'")
                    except Exception as e:
                        logger.error(f"Erro ao copiar email para '{target_folder}': {e}")
                if not copiou_alguma:
                    stats['erros'] += 1
                    continue
                stats['copiadas'] += 1
                
                # ═══ PÓS-PROCESSAMENTO ════════════════════════════════════════
                
                # Adicionar ao histórico persistente
                if message_id:
                    existing_ids.add(message_id)
                    message_ids_copiados.append(message_id)

                # Marcar para ações pós-cópia
                uids_para_marcar_lido.append(uid_str)

                # Verificar se deve ser deletada pela idade
                if cutoff_date and internal_date:
                    try:
                        import re
                        msg_date_header = parsed_msg.get('Date', '')
                        if msg_date_header:
                            from email.utils import parsedate_to_datetime
                            try:
                                msg_datetime = parsedate_to_datetime(msg_date_header)
                                if msg_datetime.tzinfo is None:
                                    msg_datetime = msg_datetime.replace(tzinfo=timezone.utc)
                                if msg_datetime < cutoff_date:
                                    uids_para_deletar.append(uid_str)
                            except Exception:
                                pass
                    except Exception:
                        pass
'''

# Instruções de instalação
INSTALLATION_INSTRUCTIONS = '''
INSTRUÇÕES PARA INTEGRAR OS FILTROS (GLOBAIS + ESPECÍFICOS):

1. Abra o arquivo imap_sync_native.py

2. Certifique-se de que a função copy_folder() recebe conta_principal_id como parâmetro.
   Se não recebe, adicione-o:
   
   def copy_folder(src_conn, dst_conn, src_folder, dst_folder, 
                   conta_origem_id=None, conta_principal_id=None, 
                   marcar_lido_origem=False, dias_manter_origem=0):

3. Na função copy_folder(), logo após a linha que carrega o histórico 
   (aproximadamente linha 354):
   
   existing_ids = EmailHistoryManager.get_message_ids_sincronizados(conta_origem_id)
   
   Adicione estas linhas:
   
   # ═══ CARREGAR FILTROS ATIVOS (GLOBAIS + ESPECÍFICOS) ═══
   filtros = []
   if conta_origem_id and conta_principal_id:
       try:
           from filter_manager import FilterManager
           filtros = FilterManager.get_filtros_ativos_para_sincronizacao(
               conta_origem_id, 
               conta_principal_id
           )
           if filtros:
               globais = sum(1 for f in filtros if f.is_global)
               especificos = len(filtros) - globais
               logger.info(f"Carregados {len(filtros)} filtro(s): {globais} global(is), {especificos} específico(s)")
       except Exception as e:
           logger.warning(f"Erro ao carregar filtros: {e}")

4. Substitua o bloco de código que copia para o destino (linhas 408-446 aproximadamente)
   pelo código fornecido acima em FILTER_INTEGRATION_CODE.

5. Certifique-se de que a função sincronizar_conta() passa o conta_principal_id 
   para a função copy_folder().

6. Salve o arquivo.

7. Reinicie o serviço Flask.

Pronto! Os filtros globais e específicos serão aplicados automaticamente durante a sincronização.
'''

if __name__ == '__main__':
    print(INSTALLATION_INSTRUCTIONS)
    print("\n" + "="*80)
    print("CÓDIGO PARA COPIAR:")
    print("="*80)
    print(FILTER_INTEGRATION_CODE)
