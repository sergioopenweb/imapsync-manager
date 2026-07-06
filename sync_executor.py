"""
Módulo de sincronização - MailBridge
Usa IMAP nativo Python (sem dependência de imapsync externo).
Compatível com a mesma interface do sync_executor.py anterior.
"""
import logging
from datetime import datetime
from config import Config
from db_manager import DatabaseManager, SyncLockManager
from imap_sync_native import sincronizar_conta

logger = logging.getLogger(__name__)


class ImapSyncExecutor:
    """Executor de sincronizações via IMAP nativo"""

    @staticmethod
    def executar_sincronizacao(conta_origem_id, dados_conta):
        """
        Executa sincronização para uma conta.

        Args:
            conta_origem_id: ID da conta de origem
            dados_conta: Dict com dados completos da conta (origem e destino)

        Returns:
            dict: {'success': bool, 'message': str, 'log_id': int}
        """
        if not dados_conta.get('ativa'):
            return {'success': False, 'message': 'Conta está desativada', 'log_id': None}

        from sync_cancel import cancelamento_solicitado, limpar_cancelamento

        if cancelamento_solicitado(conta_origem_id):
            return {
                'success': False,
                'message': 'Parada da sincronização anterior em andamento. Aguarde alguns segundos.',
                'log_id': None,
            }

        if not SyncLockManager.acquire_lock(conta_origem_id, Config.IMAPSYNC_LOCK_TIMEOUT):
            return {
                'success': False,
                'message': 'Sincronização já em andamento para esta conta',
                'log_id': None
            }

        limpar_cancelamento(conta_origem_id)

        try:
            log_id = SyncLockManager.create_log_entry(conta_origem_id)
            SyncLockManager.update_log_progress(
                log_id,
                f"Iniciando sincronização: {dados_conta.get('email', '')} → {dados_conta.get('dest_email', '')}",
            )
        except Exception as e:
            logger.error(f"Erro ao criar log: {e}")
            return {'success': False, 'message': f'Erro ao criar log: {str(e)}', 'log_id': None}

        try:
            dados_conta['sync_log_id'] = log_id
            resultado = sincronizar_conta(dados_conta)

            if resultado['success']:
                SyncLockManager.update_log_success(log_id, resultado['output'], conta_origem_id)
                SyncLockManager.limpar_logs_antigos(conta_origem_id, Config.MAX_LOGS_POR_CONTA)
                logger.info(f"Sincronização bem-sucedida para conta {conta_origem_id}")
            else:
                SyncLockManager.update_log_error(log_id, resultado['output'])
                logger.error(f"Erro na sincronização da conta {conta_origem_id}")

            # Atualiza contadores de falha e dispara alerta se necessário
            try:
                from alert_manager import verificar_e_alertar
                verificar_e_alertar(
                    conta_origem_id=conta_origem_id,
                    conta_nome=dados_conta.get('nome', ''),
                    conta_email=dados_conta.get('email', ''),
                    conta_principal_id=dados_conta.get('conta_principal_id'),
                    sucesso=resultado['success'],
                )
            except Exception:
                pass

            return {
                'success': resultado['success'],
                'message': resultado['output'][:Config.LOG_MENSAGEM_DB_MAX_LEN],
                'log_id': log_id
            }

        except Exception as e:
            SyncLockManager.update_log_error(log_id, str(e))
            logger.exception(f"Exceção durante sincronização da conta {conta_origem_id}")
            try:
                from alert_manager import verificar_e_alertar
                verificar_e_alertar(
                    conta_origem_id=conta_origem_id,
                    conta_nome=dados_conta.get('nome', ''),
                    conta_email=dados_conta.get('email', ''),
                    conta_principal_id=dados_conta.get('conta_principal_id'),
                    sucesso=False,
                )
            except Exception:
                pass
            return {'success': False, 'message': f'Erro: {str(e)}', 'log_id': log_id}
        finally:
            limpar_cancelamento(conta_origem_id)

    @staticmethod
    def get_contas_ativas():
        """Busca todas as contas ativas para sincronização."""
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT co.*,
                           cp.servidor   AS dest_servidor,
                           cp.email      AS dest_email,
                           cp.senha      AS dest_senha,
                           cp.porta      AS dest_porta,
                           cp.`ssl`      AS dest_ssl,
                           cp.nome       AS dest_nome,
                           cp.ativa      AS dest_ativa
                    FROM contas_origem co
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE co.ativa = 1 AND cp.ativa = 1
                    ORDER BY co.id
                ''')
                return cursor.fetchall()
        except Exception as e:
            logger.error(f"Erro ao buscar contas ativas: {e}")
            return []

    @staticmethod
    def get_dados_conta(conta_origem_id, usuario_id=None):
        """Busca dados completos de uma conta para sincronização."""
        try:
            with DatabaseManager.get_cursor() as cursor:
                query = '''
                    SELECT co.*,
                           cp.servidor   AS dest_servidor,
                           cp.email      AS dest_email,
                           cp.senha      AS dest_senha,
                           cp.porta      AS dest_porta,
                           cp.`ssl`      AS dest_ssl,
                           cp.usuario_id,
                           cp.ativa      AS dest_ativa
                    FROM contas_origem co
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE co.id = %s
                '''
                params = [conta_origem_id]
                if usuario_id is not None:
                    query += ' AND cp.usuario_id = %s'
                    params.append(usuario_id)

                cursor.execute(query, params)
                return cursor.fetchone()
        except Exception as e:
            logger.error(f"Erro ao buscar dados da conta {conta_origem_id}: {e}")
            return None
