"""
Gerenciador de conexões com o banco de dados
"""
import mysql.connector
from mysql.connector import pooling
from contextlib import contextmanager
import logging
from config import Config

logger = logging.getLogger(__name__)

class DatabaseManager:
    """Gerenciador de conexões com pool"""
    
    _pool = None
    
    @classmethod
    def initialize_pool(cls):
        """Inicializa o pool de conexões"""
        if cls._pool is None:
            try:
                cls._pool = pooling.MySQLConnectionPool(**Config.DB_CONFIG)
                logger.info("Pool de conexões inicializado com sucesso")
            except mysql.connector.Error as e:
                logger.error(f"Erro ao criar pool de conexões: {e}")
                raise
    
    @classmethod
    @contextmanager
    def get_connection(cls):
        """Context manager para obter conexão do pool"""
        if cls._pool is None:
            cls.initialize_pool()
        
        connection = None
        try:
            connection = cls._pool.get_connection()
            yield connection
            connection.commit()
        except Exception as e:
            if connection:
                connection.rollback()
            logger.error(f"Erro no banco de dados: {e}")
            raise
        finally:
            if connection and connection.is_connected():
                connection.close()
    
    @classmethod
    def ensure_ativacao_columns(cls):
        """Adiciona colunas ativo (usuarios) e ativa (contas_principais) se não existirem."""
        try:
            with cls.get_cursor() as cursor:
                cursor.execute('''
                    SELECT COUNT(*) AS cnt FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'usuarios' AND COLUMN_NAME = 'ativo'
                ''')
                if cursor.fetchone()['cnt'] == 0:
                    cursor.execute('ALTER TABLE usuarios ADD COLUMN ativo TINYINT(1) NOT NULL DEFAULT 1')
                    logger.info("Coluna usuarios.ativo adicionada")
                cursor.execute('''
                    SELECT COUNT(*) AS cnt FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'contas_principais' AND COLUMN_NAME = 'ativa'
                ''')
                if cursor.fetchone()['cnt'] == 0:
                    cursor.execute('ALTER TABLE contas_principais ADD COLUMN ativa TINYINT(1) NOT NULL DEFAULT 1')
                    logger.info("Coluna contas_principais.ativa adicionada")
        except Exception as e:
            logger.warning(f"Verificação colunas ativo/ativa: {e}")

    @classmethod
    @contextmanager
    def get_cursor(cls, dictionary=True):
        """Context manager para obter cursor"""
        with cls.get_connection() as conn:
            cursor = conn.cursor(dictionary=dictionary)
            try:
                yield cursor
            finally:
                cursor.close()

    # ── Migrações ─────────────────────────────────────────────────────────────

    @classmethod
    def ensure_sync_intervalo_column(cls):
        """Adiciona coluna sync_intervalo_minutos em contas_origem se não existir."""
        try:
            with cls.get_cursor() as cursor:
                cursor.execute('''
                    SELECT COUNT(*) AS cnt FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'contas_origem'
                      AND COLUMN_NAME = 'sync_intervalo_minutos'
                ''')
                if cursor.fetchone()['cnt'] == 0:
                    cursor.execute(
                        'ALTER TABLE contas_origem ADD COLUMN sync_intervalo_minutos INT NOT NULL DEFAULT 0'
                    )
                    logger.info("Coluna contas_origem.sync_intervalo_minutos adicionada")
        except Exception as e:
            logger.warning(f"Verificação coluna sync_intervalo_minutos: {e}")

    @classmethod
    def ensure_api_key_column(cls):
        """Adiciona coluna api_key em usuarios se não existir."""
        try:
            with cls.get_cursor() as cursor:
                cursor.execute('''
                    SELECT COUNT(*) AS cnt FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'usuarios'
                      AND COLUMN_NAME = 'api_key'
                ''')
                if cursor.fetchone()['cnt'] == 0:
                    cursor.execute(
                        'ALTER TABLE usuarios ADD COLUMN api_key VARCHAR(64) NULL UNIQUE'
                    )
                    logger.info("Coluna usuarios.api_key adicionada")
        except Exception as e:
            logger.warning(f"Verificação coluna api_key: {e}")

    @classmethod
    def ensure_alertas_table(cls):
        """Cria tabela alertas_config se não existir."""
        try:
            with cls.get_cursor() as cursor:
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS alertas_config (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        conta_principal_id INT NOT NULL,
                        email_destino VARCHAR(255) NOT NULL,
                        falhas_consecutivas INT NOT NULL DEFAULT 3,
                        ativo TINYINT(1) NOT NULL DEFAULT 1,
                        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE KEY uk_conta (conta_principal_id),
                        FOREIGN KEY (conta_principal_id) REFERENCES contas_principais(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                ''')
                # Coluna de contagem de falhas consecutivas em contas_origem
                cursor.execute('''
                    SELECT COUNT(*) AS cnt FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'contas_origem'
                      AND COLUMN_NAME = 'falhas_consecutivas'
                ''')
                if cursor.fetchone()['cnt'] == 0:
                    cursor.execute(
                        'ALTER TABLE contas_origem ADD COLUMN falhas_consecutivas INT NOT NULL DEFAULT 0'
                    )
                    logger.info("Coluna contas_origem.falhas_consecutivas adicionada")
        except Exception as e:
            logger.warning(f"Verificação tabela alertas_config: {e}")

    # ── Dashboard stats ───────────────────────────────────────────────────────

    @classmethod
    def get_dashboard_stats(cls, usuario_id):
        """Retorna estatísticas agregadas para o dashboard do usuário."""
        try:
            with cls.get_cursor() as cursor:
                # Emails sincronizados hoje
                cursor.execute('''
                    SELECT COUNT(*) AS n
                    FROM historico_emails_sincronizados h
                    JOIN contas_origem co ON h.conta_origem_id = co.id
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE cp.usuario_id = %s
                      AND DATE(h.sincronizado_em) = CURDATE()
                ''', (usuario_id,))
                emails_hoje = cursor.fetchone()['n']

                # Spam recebido hoje (marcado manualmente OU detectado pelo filtro)
                cursor.execute('''
                    SELECT COUNT(*) AS n
                    FROM historico_emails_sincronizados h
                    JOIN contas_origem co ON h.conta_origem_id = co.id
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE cp.usuario_id = %s
                      AND DATE(h.sincronizado_em) = CURDATE()
                      AND (h.marcado_spam = 1 OR h.detectado_spam_pelo_filtro = 1)
                ''', (usuario_id,))
                spam_recebidos_hoje = cursor.fetchone()['n']

                # Spam bloqueado hoje (detectado pelo Spam Analyzer na sync)
                cursor.execute('''
                    SELECT COUNT(*) AS n
                    FROM historico_emails_sincronizados h
                    JOIN contas_origem co ON h.conta_origem_id = co.id
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE cp.usuario_id = %s
                      AND DATE(h.sincronizado_em) = CURDATE()
                      AND h.detectado_spam_pelo_filtro = 1
                ''', (usuario_id,))
                spam_bloqueados_hoje = cursor.fetchone()['n']

                # Erros nas últimas 24h
                cursor.execute('''
                    SELECT COUNT(*) AS n
                    FROM logs_sincronizacao l
                    JOIN contas_origem co ON l.conta_origem_id = co.id
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE cp.usuario_id = %s
                      AND l.status = 'erro'
                      AND l.criado_em >= DATE_SUB(NOW(), INTERVAL %s HOUR)
                ''', (usuario_id, Config.DASHBOARD_ERROS_HORAS))
                erros_24h = cursor.fetchone()['n']

                # Última sincronização bem-sucedida
                cursor.execute('''
                    SELECT MAX(l.finalizado_em) AS ts
                    FROM logs_sincronizacao l
                    JOIN contas_origem co ON l.conta_origem_id = co.id
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE cp.usuario_id = %s AND l.status = 'sucesso'
                ''', (usuario_id,))
                ultima_sync = cursor.fetchone()['ts']

                # Contas com último status = erro
                cursor.execute('''
                    SELECT COUNT(DISTINCT l.conta_origem_id) AS n
                    FROM logs_sincronizacao l
                    JOIN contas_origem co ON l.conta_origem_id = co.id
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE cp.usuario_id = %s
                      AND l.status = 'erro'
                      AND l.id = (
                          SELECT MAX(l2.id) FROM logs_sincronizacao l2
                          WHERE l2.conta_origem_id = l.conta_origem_id
                      )
                ''', (usuario_id,))
                contas_com_erro = cursor.fetchone()['n']

            return {
                'emails_hoje':    emails_hoje,
                'spam_recebidos_hoje': spam_recebidos_hoje,
                'spam_bloqueados_hoje': spam_bloqueados_hoje,
                'erros_24h':      erros_24h,
                'ultima_sync':    ultima_sync.strftime('%d/%m/%Y %H:%M') if ultima_sync else None,
                'contas_com_erro': contas_com_erro,
            }
        except Exception as e:
            logger.warning(f"Erro ao buscar dashboard stats: {e}")
            return {
                'emails_hoje': 0,
                'spam_recebidos_hoje': 0,
                'spam_bloqueados_hoje': 0,
                'erros_24h': 0,
                'ultima_sync': None,
                'contas_com_erro': 0,
            }

    @classmethod
    def get_atividade_semanal(cls, usuario_id):
        """Retorna a contagem de emails sincronizados por dia nos últimos 7 dias.

        Inclui também a contagem de spam (marcado manualmente ou detectado pelo Spam Analyzer)
        para permitir gráficos comparativos.
        """
        try:
            with cls.get_cursor() as cursor:
                cursor.execute('''
                    SELECT
                        DATE(h.sincronizado_em) AS dia,
                        COUNT(*) AS total,
                        SUM(CASE WHEN (h.marcado_spam = 1 OR h.detectado_spam_pelo_filtro = 1) THEN 1 ELSE 0 END) AS spam
                    FROM historico_emails_sincronizados h
                    JOIN contas_origem co ON h.conta_origem_id = co.id
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE cp.usuario_id = %s
                      AND h.sincronizado_em >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
                    GROUP BY DATE(h.sincronizado_em)
                    ORDER BY dia
                ''', (usuario_id, Config.DASHBOARD_ATIVIDADE_DIAS))
                rows = cursor.fetchall()
            return [{'dia': str(r['dia']), 'total': r['total'], 'spam': int(r.get('spam') or 0)} for r in rows]
        except Exception as e:
            logger.warning(f"Erro ao buscar atividade semanal: {e}")
            return []

    # ── API key ───────────────────────────────────────────────────────────────

    @classmethod
    def get_usuario_por_api_key(cls, api_key):
        """Busca um usuário ativo pela API key."""
        try:
            with cls.get_cursor() as cursor:
                cursor.execute(
                    'SELECT id, nome, email, ativo FROM usuarios WHERE api_key = %s',
                    (api_key,)
                )
                return cursor.fetchone()
        except Exception as e:
            logger.error(f"Erro ao buscar usuário por API key: {e}")
            return None

    # ── Alertas ───────────────────────────────────────────────────────────────

    @classmethod
    def incrementar_falhas_consecutivas(cls, conta_origem_id):
        """Incrementa contador de falhas e retorna o novo total."""
        try:
            with cls.get_cursor() as cursor:
                cursor.execute(
                    'UPDATE contas_origem SET falhas_consecutivas = falhas_consecutivas + 1 WHERE id = %s',
                    (conta_origem_id,)
                )
                cursor.execute(
                    'SELECT falhas_consecutivas FROM contas_origem WHERE id = %s',
                    (conta_origem_id,)
                )
                row = cursor.fetchone()
                return row['falhas_consecutivas'] if row else 0
        except Exception as e:
            logger.warning(f"Erro ao incrementar falhas {conta_origem_id}: {e}")
            return 0

    @classmethod
    def resetar_falhas_consecutivas(cls, conta_origem_id):
        """Zera o contador de falhas consecutivas após sync bem-sucedido."""
        try:
            with cls.get_cursor() as cursor:
                cursor.execute(
                    'UPDATE contas_origem SET falhas_consecutivas = 0 WHERE id = %s',
                    (conta_origem_id,)
                )
        except Exception as e:
            logger.warning(f"Erro ao resetar falhas {conta_origem_id}: {e}")

    @classmethod
    def get_alerta_config(cls, conta_principal_id):
        """Retorna a config de alerta de uma conta principal, ou None."""
        try:
            with cls.get_cursor() as cursor:
                cursor.execute(
                    'SELECT * FROM alertas_config WHERE conta_principal_id = %s AND ativo = 1',
                    (conta_principal_id,)
                )
                return cursor.fetchone()
        except Exception as e:
            logger.warning(f"Erro ao buscar alerta config: {e}")
            return None


class SyncLockManager:
    """Gerenciador de locks para sincronização"""

    @staticmethod
    def criar_tabela_se_nao_existe():
        """Cria a tabela de logs de sincronização se não existir (necessária para locks e histórico de execução)."""
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS logs_sincronizacao (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        conta_origem_id INT NOT NULL,
                        status VARCHAR(50) NOT NULL DEFAULT 'executando',
                        mensagem VARCHAR(1000) DEFAULT NULL,
                        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        finalizado_em TIMESTAMP NULL DEFAULT NULL,
                        INDEX idx_conta_status (conta_origem_id, status),
                        INDEX idx_criado_em (criado_em),
                        FOREIGN KEY (conta_origem_id) REFERENCES contas_origem(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                ''')
                logger.info("Tabela logs_sincronizacao verificada/criada")
        except Exception as e:
            logger.error(f"Erro ao criar tabela logs_sincronizacao: {e}")
            raise

    @staticmethod
    def limpar_logs_executando_orfaos(segundos: int = None, forcar: bool = False) -> int:
        """
        Marca como erro logs 'executando' antigos (processo morreu sem finalizar).
        forcar=True: fecha todos os 'executando' (uso após kill manual do processo).
        """
        from config import Config
        if segundos is None:
            segundos = Config.SYNC_ORPHAN_LOG_SEC
        try:
            with DatabaseManager.get_cursor() as cursor:
                if forcar:
                    cursor.execute('''
                        UPDATE logs_sincronizacao
                        SET status = 'erro',
                            mensagem = 'Processo não finalizou (log órfão liberado)',
                            finalizado_em = NOW()
                        WHERE status = 'executando'
                    ''')
                else:
                    cursor.execute('''
                        UPDATE logs_sincronizacao
                        SET status = 'erro',
                            mensagem = 'Processo não finalizou (log órfão liberado)',
                            finalizado_em = NOW()
                        WHERE status = 'executando'
                        AND criado_em < DATE_SUB(NOW(), INTERVAL %s SECOND)
                    ''', (segundos,))
                return cursor.rowcount or 0
        except Exception as e:
            logger.error(f"Erro ao limpar logs órfãos: {e}")
            return 0

    @staticmethod
    def liberar_executando_forcado(conta_origem_id: int = None, mensagem: str = None) -> int:
        """Fecha logs 'executando' imediatamente (conta específica ou todas)."""
        msg = (mensagem or '').strip() or 'Sincronização interrompida (liberação manual)'
        try:
            with DatabaseManager.get_cursor() as cursor:
                if conta_origem_id is not None:
                    cursor.execute('''
                        UPDATE logs_sincronizacao
                        SET status = 'erro',
                            mensagem = %s,
                            finalizado_em = NOW()
                        WHERE status = 'executando' AND conta_origem_id = %s
                    ''', (msg, conta_origem_id))
                else:
                    cursor.execute('''
                        UPDATE logs_sincronizacao
                        SET status = 'erro',
                            mensagem = %s,
                            finalizado_em = NOW()
                        WHERE status = 'executando'
                    ''', (msg,))
                return cursor.rowcount or 0
        except Exception as e:
            logger.error(f"Erro ao liberar executando: {e}")
            return 0

    @staticmethod
    def acquire_lock(conta_origem_id, timeout=30):
        """
        Tenta adquirir lock para sincronização
        Retorna True se conseguiu, False se outra sincronização está em andamento
        """
        from config import Config
        try:
            with DatabaseManager.get_cursor() as cursor:
                # Logs órfãos desta conta (processo morto, ex. kill do auto_sync)
                cursor.execute('''
                    UPDATE logs_sincronizacao
                    SET status = 'erro',
                        mensagem = 'Processo não finalizou (log órfão liberado)',
                        finalizado_em = NOW()
                    WHERE conta_origem_id = %s
                    AND status = 'executando'
                    AND criado_em < DATE_SUB(NOW(), INTERVAL %s SECOND)
                ''', (conta_origem_id, Config.SYNC_ORPHAN_LOG_SEC))

                # Verificar se ainda existe sync recente em andamento
                cursor.execute('''
                    SELECT id, criado_em 
                    FROM logs_sincronizacao 
                    WHERE conta_origem_id = %s 
                    AND status = 'executando'
                ''', (conta_origem_id,))
                
                em_andamento = cursor.fetchone()
                
                if em_andamento:
                    logger.warning(f"Sincronização já em andamento para conta {conta_origem_id}")
                    return False
                
                # Limpar locks muito antigos (fallback legado, timeout em minutos)
                cursor.execute('''
                    UPDATE logs_sincronizacao 
                    SET status = 'erro', 
                        mensagem = 'Timeout - processo travado',
                        finalizado_em = NOW()
                    WHERE conta_origem_id = %s 
                    AND status = 'executando'
                    AND criado_em < DATE_SUB(NOW(), INTERVAL %s SECOND)
                ''', (conta_origem_id, timeout * 60))
                
                return True
                
        except Exception as e:
            logger.error(f"Erro ao verificar lock: {e}")
            return False
    
    @staticmethod
    def create_log_entry(conta_origem_id):
        """Cria entrada de log para sincronização"""
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    INSERT INTO logs_sincronizacao (conta_origem_id, status)
                    VALUES (%s, 'executando')
                ''', (conta_origem_id,))
                return cursor.lastrowid
        except Exception as e:
            logger.error(f"Erro ao criar log: {e}")
            raise
    
    @staticmethod
    def update_log_progress(log_id, mensagem):
        """Atualiza mensagem de um log em andamento (progresso na interface)."""
        if not log_id:
            return
        try:
            from config import Config
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    UPDATE logs_sincronizacao
                    SET mensagem = %s
                    WHERE id = %s AND status = 'executando'
                ''', (mensagem[:Config.LOG_MENSAGEM_DB_MAX_LEN], log_id))
        except Exception as e:
            logger.debug(f"Erro ao atualizar progresso do log {log_id}: {e}")

    @staticmethod
    def update_log_success(log_id, mensagem, conta_origem_id):
        """Atualiza log com sucesso"""
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    UPDATE logs_sincronizacao 
                    SET status = 'sucesso', 
                        mensagem = %s,
                        finalizado_em = NOW()
                    WHERE id = %s
                ''', (mensagem[:Config.LOG_MENSAGEM_DB_MAX_LEN], log_id))
                
                cursor.execute('''
                    UPDATE contas_origem 
                    SET ultima_sincronizacao = NOW()
                    WHERE id = %s
                ''', (conta_origem_id,))
            
        except Exception as e:
            logger.error(f"Erro ao atualizar log de sucesso: {e}")
    
    @staticmethod
    def update_log_error(log_id, mensagem):
        """Atualiza log com erro"""
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    UPDATE logs_sincronizacao 
                    SET status = 'erro', 
                        mensagem = %s,
                        finalizado_em = NOW()
                    WHERE id = %s
                ''', (mensagem[:Config.LOG_MENSAGEM_DB_MAX_LEN], log_id))
        except Exception as e:
            logger.error(f"Erro ao atualizar log de erro: {e}")
    
    @staticmethod
    def limpar_logs_antigos(conta_origem_id, max_logs):
        """
        Remove logs antigos, mantendo apenas os N mais recentes
        
        Args:
            conta_origem_id: ID da conta de origem
            max_logs: Número máximo de logs a manter
        """
        try:
            with DatabaseManager.get_cursor() as cursor:
                # Contar quantos logs existem
                cursor.execute('''
                    SELECT COUNT(*) as total 
                    FROM logs_sincronizacao 
                    WHERE conta_origem_id = %s
                ''', (conta_origem_id,))
                
                total_logs = cursor.fetchone()['total']
                
                if total_logs > max_logs:
                    # Deletar os logs mais antigos, mantendo apenas os N mais recentes
                    cursor.execute('''
                        DELETE FROM logs_sincronizacao
                        WHERE conta_origem_id = %s
                        AND id NOT IN (
                            SELECT id FROM (
                                SELECT id 
                                FROM logs_sincronizacao 
                                WHERE conta_origem_id = %s
                                ORDER BY criado_em DESC
                                LIMIT %s
                            ) as keep_logs
                        )
                    ''', (conta_origem_id, conta_origem_id, max_logs))
                    
                    logs_removidos = total_logs - max_logs
                    logger.info(f"Removidos {logs_removidos} log(s) antigo(s) da conta {conta_origem_id}")
                    return logs_removidos
                
                return 0
                
        except Exception as e:
            logger.error(f"Erro ao limpar logs antigos: {e}")
            return 0


class EmailHistoryManager:
    """Gerenciador de histórico de emails sincronizados"""

    MESSAGE_ID_MAX_LEN = 500

    @staticmethod
    def normalize_message_id(message_id) -> str:
        """
        Normaliza Message-ID para comparação e armazenamento.
        Garante formato com <...> e tamanho máximo consistente com a coluna VARCHAR(500).
        """
        if not message_id:
            return ''
        mid = ''.join(str(message_id).split())
        if not mid:
            return ''
        if not mid.startswith('<'):
            mid = '<' + mid
        if not mid.endswith('>'):
            mid = mid + '>'
        if len(mid) > EmailHistoryManager.MESSAGE_ID_MAX_LEN:
            mid = mid[: EmailHistoryManager.MESSAGE_ID_MAX_LEN]
        return mid

    @staticmethod
    def _ensure_historico_columns(cursor):
        """Adiciona colunas assunto, remetente, data_email, marcado_spam se não existirem (migração por coluna)."""
        colunas = [
            ('assunto', 'ADD COLUMN assunto VARCHAR(500) DEFAULT NULL'),
            ('remetente', 'ADD COLUMN remetente VARCHAR(500) DEFAULT NULL'),
            ('data_email', 'ADD COLUMN data_email VARCHAR(100) DEFAULT NULL'),
            ('marcado_spam', 'ADD COLUMN marcado_spam TINYINT(1) DEFAULT 0'),
            ('detectado_spam_pelo_filtro', 'ADD COLUMN detectado_spam_pelo_filtro TINYINT(1) DEFAULT 0'),
            ('aplicado_filtro_email', 'ADD COLUMN aplicado_filtro_email TINYINT(1) DEFAULT 0'),
            ('spam_wordlist_adicionada', 'ADD COLUMN spam_wordlist_adicionada TEXT DEFAULT NULL'),
            ('detectado_spam_motivo', 'ADD COLUMN detectado_spam_motivo VARCHAR(50) DEFAULT NULL'),
            # Guarda o valor concreto que disparou a detecção (entrada na blacklist, palavra, reply-to…)
            ('detectado_spam_detalhe', 'ADD COLUMN detectado_spam_detalhe VARCHAR(500) DEFAULT NULL'),
        ]
        for col_name, add_sql in colunas:
            try:
                cursor.execute('''
                    SELECT COUNT(*) AS cnt FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'historico_emails_sincronizados' AND COLUMN_NAME = %s
                ''', (col_name,))
                if cursor.fetchone()['cnt'] > 0:
                    continue
                cursor.execute(f'ALTER TABLE historico_emails_sincronizados {add_sql}')
                logger.info(f"Coluna {col_name} adicionada ao historico_emails_sincronizados")
            except Exception as e:
                if '1060' not in str(e) and 'Duplicate column' not in str(e):
                    logger.warning(f"Coluna histórico {col_name}: {e}")
        try:
            cursor.execute('''
                SELECT COUNT(*) AS cnt FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'historico_emails_sincronizados' AND INDEX_NAME = 'idx_marcado_spam'
            ''')
            if cursor.fetchone()['cnt'] == 0:
                cursor.execute('ALTER TABLE historico_emails_sincronizados ADD INDEX idx_marcado_spam (marcado_spam)')
                logger.info("Índice idx_marcado_spam adicionado ao histórico")
        except Exception as e:
            if '1061' not in str(e) and 'Duplicate key' not in str(e):
                logger.warning(f"Índice histórico: {e}")
        EmailHistoryManager._ensure_unique_conta_message_id(cursor)

    @staticmethod
    def _ensure_unique_conta_message_id(cursor):
        """Índice UNIQUE (conta, message_id) para INSERT IGNORE e deduplicação confiável."""
        try:
            cursor.execute('''
                SELECT COUNT(*) AS cnt FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'historico_emails_sincronizados'
                  AND INDEX_NAME = 'uk_conta_message_id'
            ''')
            if cursor.fetchone()['cnt'] > 0:
                return
            cursor.execute('''
                DELETE h1 FROM historico_emails_sincronizados h1
                INNER JOIN historico_emails_sincronizados h2
                  ON h1.conta_origem_id = h2.conta_origem_id
                 AND h1.message_id = h2.message_id
                 AND h1.id > h2.id
            ''')
            cursor.execute('''
                ALTER TABLE historico_emails_sincronizados
                ADD UNIQUE KEY uk_conta_message_id (conta_origem_id, message_id)
            ''')
            logger.info('Índice uk_conta_message_id adicionado ao histórico de emails')
        except Exception as e:
            err = str(e)
            if '1061' in err or 'Duplicate key' in err or '1062' in err:
                return
            logger.warning(f"Índice uk_conta_message_id: {e}")

    @staticmethod
    def criar_tabela_se_nao_existe():
        """Cria a tabela de histórico de emails se não existir"""
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS historico_emails_sincronizados (
                        id BIGINT AUTO_INCREMENT PRIMARY KEY,
                        conta_origem_id INT NOT NULL,
                        message_id VARCHAR(500) NOT NULL,
                        sincronizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        assunto VARCHAR(500) DEFAULT NULL,
                        remetente VARCHAR(500) DEFAULT NULL,
                        data_email VARCHAR(100) DEFAULT NULL,
                        marcado_spam TINYINT(1) DEFAULT 0,
                        detectado_spam_pelo_filtro TINYINT(1) DEFAULT 0,
                        aplicado_filtro_email TINYINT(1) DEFAULT 0,
                        INDEX idx_conta_message (conta_origem_id, message_id),
                        INDEX idx_sincronizado (sincronizado_em),
                        INDEX idx_marcado_spam (marcado_spam),
                        FOREIGN KEY (conta_origem_id) REFERENCES contas_origem(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                ''')
                EmailHistoryManager._ensure_historico_columns(cursor)
                logger.info("Tabela historico_emails_sincronizados verificada/criada")
        except Exception as e:
            logger.error(f"Erro ao criar tabela de histórico: {e}")
            raise
    
    @staticmethod
    def get_remetentes_emails_conhecidos(conta_origem_id):
        """
        E-mails de remetentes que já apareceram no histórico de sincronização desta conta.
        Usado para pular Spam Analyzer e filtros em mensagens novas do mesmo remetente.
        """
        try:
            from spam_analyzer_config import _extrair_email_remetente
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT DISTINCT remetente
                    FROM historico_emails_sincronizados
                    WHERE conta_origem_id = %s
                      AND remetente IS NOT NULL
                      AND remetente != ''
                ''', (conta_origem_id,))
                emails = set()
                for row in cursor.fetchall():
                    email = _extrair_email_remetente(row.get('remetente'))
                    if email:
                        emails.add(email.lower())
                logger.info(
                    f"Carregados {len(emails)} remetente(s) conhecido(s) do histórico "
                    f"(conta {conta_origem_id})"
                )
                return emails
        except Exception as e:
            logger.error(f"Erro ao buscar remetentes conhecidos: {e}")
            return set()

    @staticmethod
    def get_message_ids_sincronizados(conta_origem_id):
        """
        Busca todos os Message-IDs já sincronizados para uma conta
        
        Args:
            conta_origem_id: ID da conta de origem
            
        Returns:
            set: Conjunto de Message-IDs já sincronizados
        """
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT message_id 
                    FROM historico_emails_sincronizados 
                    WHERE conta_origem_id = %s
                ''', (conta_origem_id,))
                
                results = cursor.fetchall()
                message_ids = set()
                for row in results:
                    mid = EmailHistoryManager.normalize_message_id(row.get('message_id'))
                    if mid:
                        message_ids.add(mid)

                logger.debug(f"Carregados {len(message_ids)} Message-IDs do histórico para conta {conta_origem_id}")
                return message_ids
                
        except Exception as e:
            logger.error(f"Erro ao buscar histórico de emails: {e}")
            return set()
    
    @staticmethod
    def adicionar_message_ids(conta_origem_id, message_ids):
        """
        Adiciona Message-IDs ao histórico.
        message_ids pode ser:
        - Lista de str (message_id) -> assunto/remetente/data_email ficam NULL
        - Lista de dict com keys: message_id, assunto?, remetente?, data_email?
        """
        if not message_ids:
            return
        try:
            with DatabaseManager.get_cursor() as cursor:
                EmailHistoryManager._ensure_historico_columns(cursor)
                rows = []
                for item in message_ids:
                    if isinstance(item, dict):
                        mid = EmailHistoryManager.normalize_message_id(item.get('message_id'))
                        if not mid:
                            continue
                        assunto = (item.get('assunto') or '')[:500]
                        remetente = (item.get('remetente') or '')[:500]
                        data_email = (item.get('data_email') or '')[:100]
                        detectado = 1 if item.get('detectado_spam_pelo_filtro') else 0
                        aplicado_filtro = 1 if item.get('aplicado_filtro_email') else 0
                        motivo = (item.get('detectado_spam_motivo') or None)
                        detalhe = (item.get('detectado_spam_detalhe') or None)
                        if detalhe:
                            detalhe = detalhe[:500]
                        rows.append((conta_origem_id, mid, assunto or None, remetente or None, data_email or None, detectado, aplicado_filtro, motivo, detalhe))
                    else:
                        mid = EmailHistoryManager.normalize_message_id(item)
                        if not mid:
                            continue
                        rows.append((conta_origem_id, mid, None, None, None, 0, 0, None, None))
                if not rows:
                    return
                cursor.executemany('''
                    INSERT IGNORE INTO historico_emails_sincronizados 
                    (conta_origem_id, message_id, assunto, remetente, data_email,
                     detectado_spam_pelo_filtro, aplicado_filtro_email, detectado_spam_motivo,
                     detectado_spam_detalhe)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', rows)
                logger.debug(f"Adicionados {len(rows)} Message-IDs ao histórico da conta {conta_origem_id}")
        except Exception as e:
            logger.error(f"Erro ao adicionar Message-IDs ao histórico: {e}")
    
    @staticmethod
    def limpar_historico_antigo(conta_origem_id, dias_manter=90):
        """
        Remove histórico de emails sincronizados há mais de X dias
        
        Args:
            conta_origem_id: ID da conta de origem (None = todas as contas)
            dias_manter: Número de dias de histórico para manter
        """
        try:
            with DatabaseManager.get_cursor() as cursor:
                if conta_origem_id:
                    cursor.execute('''
                        DELETE FROM historico_emails_sincronizados 
                        WHERE conta_origem_id = %s 
                        AND sincronizado_em < DATE_SUB(NOW(), INTERVAL %s DAY)
                    ''', (conta_origem_id, dias_manter))
                else:
                    cursor.execute('''
                        DELETE FROM historico_emails_sincronizados 
                        WHERE sincronizado_em < DATE_SUB(NOW(), INTERVAL %s DAY)
                    ''', (dias_manter,))
                
                removidos = cursor.rowcount
                # Também limpar histórico de "filtro aplicado" além de X dias (evita crescimento infinito)
                try:
                    cursor.execute('''
                        DELETE FROM historico_filtro_aplicado 
                        WHERE aplicado_em < DATE_SUB(NOW(), INTERVAL %s DAY)
                    ''', (dias_manter,))
                    removidos_filtro = cursor.rowcount
                    if removidos_filtro > 0:
                        logger.info(f"Removidos {removidos_filtro} registros antigos do historico_filtro_aplicado")
                except Exception as e2:
                    if 'exist' not in str(e2).lower():
                        logger.warning(f"Limpeza historico_filtro_aplicado: {e2}")
                if removidos > 0:
                    logger.info(f"Removidos {removidos} registros antigos do histórico")
                return removidos
                
        except Exception as e:
            logger.error(f"Erro ao limpar histórico antigo: {e}")
            return 0
    
    @staticmethod
    def limpar_historico_filtro_antigo(dias_manter=90):
        """
        Remove registros de "últimos emails que o filtro pegou" mais antigos que X dias.
        Evita que a tabela historico_filtro_aplicado cresça indefinidamente.
        """
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    DELETE FROM historico_filtro_aplicado 
                    WHERE aplicado_em < DATE_SUB(NOW(), INTERVAL %s DAY)
                ''', (dias_manter,))
                removidos = cursor.rowcount
                if removidos > 0:
                    logger.info(f"Removidos {removidos} registros antigos do historico_filtro_aplicado (> {dias_manter} dias)")
                return removidos
        except Exception as e:
            logger.error(f"Erro ao limpar histórico filtro antigo: {e}")
            return 0

    @staticmethod
    def listar_emails_sincronizados(conta_origem_id, limite=None, offset=0, apenas_spam=False,
                                    apenas_detectados_pelo_filtro=False, apenas_filtros_email=False):
        """
        Lista emails do histórico com assunto, remetente, data.
        apenas_spam: se True, retorna só os marcados como spam (pelo usuário).
        apenas_detectados_pelo_filtro: se True, retorna só os que o Spam Analyzer classificou como spam na sync.
        apenas_filtros_email: se True, retorna só os que bateram em algum filtro global ou específico na sync.
        """
        if limite is None:
            limite = Config.HISTORICO_EMAILS_DEFAULT_LIMIT
        try:
            with DatabaseManager.get_cursor() as cursor:
                EmailHistoryManager._ensure_historico_columns(cursor)
                sel = '''
                    SELECT id, conta_origem_id, message_id, assunto, remetente, data_email,
                           sincronizado_em, marcado_spam, detectado_spam_pelo_filtro,
                           aplicado_filtro_email, detectado_spam_motivo, detectado_spam_detalhe
                    FROM historico_emails_sincronizados
                    WHERE conta_origem_id = %s
                '''
                params = [conta_origem_id]
                if apenas_spam:
                    sel += ' AND marcado_spam = 1'
                if apenas_detectados_pelo_filtro:
                    sel += ' AND detectado_spam_pelo_filtro = 1'
                if apenas_filtros_email:
                    sel += ' AND aplicado_filtro_email = 1'
                sel += ' ORDER BY sincronizado_em DESC LIMIT %s OFFSET %s'
                params.extend([limite, offset])
                cursor.execute(sel, params)
                return cursor.fetchall()
        except Exception as e:
            logger.error(f"Erro ao listar histórico: {e}")
            return []

    @staticmethod
    def listar_emails_sincronizados_conta_principal(conta_principal_id, limite=None, offset=0, apenas_spam=False,
                                                    apenas_detectados_pelo_filtro=False, apenas_filtros_email=False):
        """
        Lista emails do histórico de TODAS as contas de origem da conta principal.
        Retorna cada registro com campos do histórico mais conta_origem_nome e conta_origem_email.
        """
        if limite is None:
            limite = Config.HISTORICO_EMAILS_DEFAULT_LIMIT
        try:
            with DatabaseManager.get_cursor() as cursor:
                EmailHistoryManager._ensure_historico_columns(cursor)
                sel = '''
                    SELECT h.id, h.conta_origem_id, h.message_id, h.assunto, h.remetente, h.data_email,
                           h.sincronizado_em, h.marcado_spam, h.detectado_spam_pelo_filtro,
                           h.aplicado_filtro_email, h.detectado_spam_motivo, h.detectado_spam_detalhe,
                           co.nome AS conta_origem_nome, co.email AS conta_origem_email
                    FROM historico_emails_sincronizados h
                    INNER JOIN contas_origem co ON co.id = h.conta_origem_id AND co.conta_principal_id = %s
                    WHERE 1=1
                '''
                params = [conta_principal_id]
                if apenas_spam:
                    sel += ' AND h.marcado_spam = 1'
                if apenas_detectados_pelo_filtro:
                    sel += ' AND h.detectado_spam_pelo_filtro = 1'
                if apenas_filtros_email:
                    sel += ' AND h.aplicado_filtro_email = 1'
                sel += ' ORDER BY h.sincronizado_em DESC LIMIT %s OFFSET %s'
                params.extend([limite, offset])
                cursor.execute(sel, params)
                return cursor.fetchall()
        except Exception as e:
            logger.error(f"Erro ao listar histórico da conta principal: {e}")
            return []

    @staticmethod
    def marcar_email_spam(historico_id, conta_origem_id, usuario_id):
        """Marca um email do histórico como spam. Verifica se a conta pertence ao usuário."""
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    UPDATE historico_emails_sincronizados h
                    INNER JOIN contas_origem co ON co.id = h.conta_origem_id
                    INNER JOIN contas_principais cp ON cp.id = co.conta_principal_id AND cp.usuario_id = %s
                    SET h.marcado_spam = 1
                    WHERE h.id = %s AND h.conta_origem_id = %s
                ''', (usuario_id, historico_id, conta_origem_id))
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Erro ao marcar spam: {e}")
            return False

    @staticmethod
    def desmarcar_email_spam(historico_id, conta_origem_id, usuario_id):
        """Remove a marca de spam de um email do histórico."""
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    UPDATE historico_emails_sincronizados h
                    INNER JOIN contas_origem co ON co.id = h.conta_origem_id
                    INNER JOIN contas_principais cp ON cp.id = co.conta_principal_id AND cp.usuario_id = %s
                    SET h.marcado_spam = 0
                    WHERE h.id = %s AND h.conta_origem_id = %s
                ''', (usuario_id, historico_id, conta_origem_id))
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Erro ao desmarcar spam: {e}")
            return False

    @staticmethod
    def limpar_historico_conta(conta_origem_id):
        """
        Remove todo o histórico de uma conta específica
        (emails sincronizados e registros de "filtro aplicado" dessa conta).
        
        Args:
            conta_origem_id: ID da conta de origem
        """
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    DELETE FROM historico_filtro_aplicado 
                    WHERE conta_origem_id = %s
                ''', (conta_origem_id,))
                cursor.execute('''
                    DELETE FROM historico_emails_sincronizados 
                    WHERE conta_origem_id = %s
                ''', (conta_origem_id,))
                
                removidos = cursor.rowcount
                logger.info(f"Removidos {removidos} registros do histórico da conta {conta_origem_id}")
                return removidos
                
        except Exception as e:
            logger.error(f"Erro ao limpar histórico da conta: {e}")
            return 0

    @staticmethod
    def criar_tabela_filtro_aplicado_se_nao_existe():
        """Cria a tabela que registra quais filtros foram aplicados a cada email (para exibir na interna do filtro)."""
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS historico_filtro_aplicado (
                        id BIGINT AUTO_INCREMENT PRIMARY KEY,
                        conta_origem_id INT NOT NULL,
                        message_id VARCHAR(500) NOT NULL,
                        filtro_id INT NOT NULL,
                        assunto VARCHAR(500) DEFAULT NULL,
                        remetente VARCHAR(500) DEFAULT NULL,
                        data_email VARCHAR(100) DEFAULT NULL,
                        aplicado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        INDEX idx_filtro_aplicado_em (filtro_id, aplicado_em),
                        INDEX idx_conta_message (conta_origem_id, message_id),
                        FOREIGN KEY (conta_origem_id) REFERENCES contas_origem(id) ON DELETE CASCADE,
                        FOREIGN KEY (filtro_id) REFERENCES filtros_email(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                ''')
                logger.info("Tabela historico_filtro_aplicado verificada/criada")
        except Exception as e:
            logger.error(f"Erro ao criar tabela historico_filtro_aplicado: {e}")
            raise

    @staticmethod
    def registrar_filtros_aplicados(conta_origem_id, itens):
        """
        Registra quais filtros foram aplicados a cada email.
        itens: lista de dict com message_id, assunto, remetente, data_email, filtro_ids (lista de int)
        """
        if not itens or not conta_origem_id:
            return
        try:
            EmailHistoryManager.criar_tabela_filtro_aplicado_se_nao_existe()
            with DatabaseManager.get_cursor() as cursor:
                rows = []
                for item in itens:
                    mid = EmailHistoryManager.normalize_message_id(item.get('message_id'))
                    if not mid:
                        continue
                    filtro_ids = item.get('filtro_ids') or []
                    for fid in filtro_ids:
                        rows.append((
                            conta_origem_id,
                            mid,
                            fid,
                            (item.get('assunto') or '')[:500] or None,
                            (item.get('remetente') or '')[:500] or None,
                            (item.get('data_email') or '')[:100] or None,
                        ))
                if not rows:
                    return
                cursor.executemany('''
                    INSERT INTO historico_filtro_aplicado
                    (conta_origem_id, message_id, filtro_id, assunto, remetente, data_email)
                    VALUES (%s, %s, %s, %s, %s, %s)
                ''', rows)
                logger.debug(f"Registrados {len(rows)} aplicações de filtro(s) no histórico")
        except Exception as e:
            logger.error(f"Erro ao registrar filtros aplicados: {e}")

    @staticmethod
    def listar_filtros_aplicados_por_message_ids(conta_origem_id, message_ids):
        """
        Para cada message_id, retorna a lista de filtros que foram aplicados (id e nome).
        Útil para exibir na lista de emails sincronizados qual filtro bateu em cada email.
        Retorna: dict[message_id] = [{'id': int, 'nome': str}, ...]
        """
        if not message_ids:
            return {}
        try:
            with DatabaseManager.get_cursor() as cursor:
                placeholders = ','.join(['%s'] * len(message_ids))
                cursor.execute('''
                    SELECT h.message_id, f.id AS filtro_id, f.nome AS filtro_nome
                    FROM historico_filtro_aplicado h
                    INNER JOIN filtros_email f ON f.id = h.filtro_id
                    WHERE h.conta_origem_id = %s AND h.message_id IN (''' + placeholders + ''')
                ''', [conta_origem_id] + list(message_ids))
                rows = cursor.fetchall()
            result = {}
            for r in rows:
                mid = r.get('message_id') or ''
                if mid not in result:
                    result[mid] = []
                result[mid].append({'id': r['filtro_id'], 'nome': (r.get('filtro_nome') or '').strip() or 'Filtro'})
            return result
        except Exception as e:
            logger.error(f"Erro ao listar filtros aplicados por message_id: {e}")
            return {}

    @staticmethod
    def listar_ultimos_emails_por_filtro(filtro_id, limite=None):
        """Lista os últimos emails aos quais este filtro foi aplicado (para exibir na página do filtro)."""
        if limite is None:
            limite = Config.FILTRO_ULTIMOS_EMAILS_LIMIT
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT id, conta_origem_id, message_id, assunto, remetente, data_email, aplicado_em
                    FROM historico_filtro_aplicado
                    WHERE filtro_id = %s
                    ORDER BY aplicado_em DESC
                    LIMIT %s
                ''', (filtro_id, limite))
                return cursor.fetchall()
        except Exception as e:
            logger.error(f"Erro ao listar emails por filtro: {e}")
            return {}


class SyncErrorManager:
    """Erros por mensagem na sincronização e fila de exclusão manual na origem."""

    @staticmethod
    def criar_tabelas_se_nao_existem():
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS sync_erros_mensagem (
                        id BIGINT AUTO_INCREMENT PRIMARY KEY,
                        log_sincronizacao_id INT NULL,
                        conta_origem_id INT NOT NULL,
                        uid_origem VARCHAR(32) NOT NULL,
                        message_id VARCHAR(500) NULL,
                        assunto VARCHAR(500) NULL,
                        remetente VARCHAR(500) NULL,
                        pasta_origem VARCHAR(255) NOT NULL DEFAULT 'INBOX',
                        fase VARCHAR(50) NOT NULL,
                        erro_codigo VARCHAR(80) NULL,
                        erro_mensagem TEXT,
                        tentativas INT NOT NULL DEFAULT 1,
                        ultimo_erro_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE KEY uk_conta_uid (conta_origem_id, uid_origem),
                        INDEX idx_conta_ultimo (conta_origem_id, ultimo_erro_em),
                        INDEX idx_log (log_sincronizacao_id),
                        FOREIGN KEY (conta_origem_id) REFERENCES contas_origem(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                ''')
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS mensagens_marcadas_exclusao (
                        id BIGINT AUTO_INCREMENT PRIMARY KEY,
                        conta_origem_id INT NOT NULL,
                        uid_origem VARCHAR(32) NOT NULL,
                        message_id VARCHAR(500) NULL,
                        assunto VARCHAR(500) NULL,
                        remetente VARCHAR(500) NULL,
                        motivo VARCHAR(50) NOT NULL DEFAULT 'erro_sync',
                        status VARCHAR(20) NOT NULL DEFAULT 'pendente',
                        marcado_por_usuario_id INT NULL,
                        marcado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        excluida_em TIMESTAMP NULL,
                        erro_exclusao TEXT NULL,
                        UNIQUE KEY uk_conta_uid (conta_origem_id, uid_origem),
                        INDEX idx_conta_status (conta_origem_id, status),
                        FOREIGN KEY (conta_origem_id) REFERENCES contas_origem(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                ''')
                logger.info("Tabelas sync_erros_mensagem e mensagens_marcadas_exclusao verificadas/criadas")
        except Exception as e:
            logger.error(f"Erro ao criar tabelas de erros de sync: {e}")
            raise

    @staticmethod
    def _truncar(texto, limite):
        if not texto:
            return None
        s = str(texto).strip()
        return s[:limite] if s else None

    @staticmethod
    def classificar_erro(erro_mensagem: str) -> str:
        msg = (erro_mensagem or '').upper()
        if 'TOOBIG' in msg or 'TOO LARGE' in msg or 'MESSAGE TOO LARGE' in msg:
            return 'TOOBIG'
        if 'FETCH' in msg:
            return 'FETCH'
        if 'APPEND' in msg:
            return 'APPEND'
        if 'SSL' in msg or 'SOCKET' in msg:
            return 'CONEXAO'
        return 'OUTRO'

    @staticmethod
    def registrar_erro(
        conta_origem_id,
        uid_origem,
        fase,
        erro_mensagem,
        log_sincronizacao_id=None,
        message_id=None,
        assunto=None,
        remetente=None,
        pasta_origem='INBOX',
    ):
        if not conta_origem_id or not uid_origem:
            return
        try:
            from config import Config
            codigo = SyncErrorManager.classificar_erro(erro_mensagem)
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    INSERT INTO sync_erros_mensagem (
                        log_sincronizacao_id, conta_origem_id, uid_origem, message_id,
                        assunto, remetente, pasta_origem, fase, erro_codigo, erro_mensagem
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        log_sincronizacao_id = VALUES(log_sincronizacao_id),
                        message_id = COALESCE(VALUES(message_id), message_id),
                        assunto = COALESCE(VALUES(assunto), assunto),
                        remetente = COALESCE(VALUES(remetente), remetente),
                        fase = VALUES(fase),
                        erro_codigo = VALUES(erro_codigo),
                        erro_mensagem = VALUES(erro_mensagem),
                        tentativas = tentativas + 1,
                        ultimo_erro_em = NOW()
                ''', (
                    log_sincronizacao_id,
                    conta_origem_id,
                    str(uid_origem),
                    SyncErrorManager._truncar(message_id, 500),
                    SyncErrorManager._truncar(assunto, 500),
                    SyncErrorManager._truncar(remetente, 500),
                    pasta_origem or 'INBOX',
                    fase,
                    codigo,
                    (erro_mensagem or '')[:2000],
                ))
        except Exception as e:
            logger.debug(f"Erro ao registrar falha de mensagem UID {uid_origem}: {e}")

    @staticmethod
    def listar_erros_conta(conta_origem_id, log_id=None, limite=None):
        if limite is None:
            limite = Config.SYNC_ERROS_UI_LIMIT
        try:
            with DatabaseManager.get_cursor() as cursor:
                if log_id:
                    cursor.execute('''
                        SELECT e.*, m.status AS exclusao_status, m.marcado_em AS exclusao_marcado_em
                        FROM sync_erros_mensagem e
                        LEFT JOIN mensagens_marcadas_exclusao m
                          ON m.conta_origem_id = e.conta_origem_id AND m.uid_origem = e.uid_origem
                        WHERE e.conta_origem_id = %s AND e.log_sincronizacao_id = %s
                        ORDER BY e.ultimo_erro_em DESC
                        LIMIT %s
                    ''', (conta_origem_id, log_id, limite))
                else:
                    cursor.execute('''
                        SELECT e.*, m.status AS exclusao_status, m.marcado_em AS exclusao_marcado_em
                        FROM sync_erros_mensagem e
                        LEFT JOIN mensagens_marcadas_exclusao m
                          ON m.conta_origem_id = e.conta_origem_id AND m.uid_origem = e.uid_origem
                        WHERE e.conta_origem_id = %s
                        ORDER BY e.ultimo_erro_em DESC
                        LIMIT %s
                    ''', (conta_origem_id, limite))
                return cursor.fetchall()
        except Exception as e:
            logger.error(f"Erro ao listar erros da conta {conta_origem_id}: {e}")
            return []

    @staticmethod
    def contar_erros_ativos(conta_origem_id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT COUNT(*) AS total
                    FROM sync_erros_mensagem
                    WHERE conta_origem_id = %s
                ''', (conta_origem_id,))
                row = cursor.fetchone()
                return row['total'] if row else 0
        except Exception as e:
            logger.debug(f"Erro ao contar erros ativos: {e}")
            return 0

    @staticmethod
    def contar_pendentes_exclusao(conta_origem_id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT COUNT(*) AS total
                    FROM mensagens_marcadas_exclusao
                    WHERE conta_origem_id = %s AND status = 'pendente'
                ''', (conta_origem_id,))
                row = cursor.fetchone()
                return row['total'] if row else 0
        except Exception as e:
            logger.debug(f"Erro ao contar pendentes exclusão: {e}")
            return 0

    @staticmethod
    def remover_erros_uids(conta_origem_id, uids):
        uids = [str(u).strip() for u in (uids or []) if str(u).strip()]
        if not uids:
            return 0
        try:
            placeholders = ','.join(['%s'] * len(uids))
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute(f'''
                    DELETE FROM mensagens_marcadas_exclusao
                    WHERE conta_origem_id = %s AND uid_origem IN ({placeholders})
                ''', [conta_origem_id] + uids)
                cursor.execute(f'''
                    DELETE FROM sync_erros_mensagem
                    WHERE conta_origem_id = %s AND uid_origem IN ({placeholders})
                ''', [conta_origem_id] + uids)
                removidos = cursor.rowcount or 0
                return removidos if removidos else len(uids)
        except Exception as e:
            logger.error(f"Erro ao remover erros UIDs: {e}")
            return 0

    @staticmethod
    def buscar_erros_por_uids(conta_origem_id, uids):
        uids = [str(u).strip() for u in (uids or []) if str(u).strip()]
        if not uids:
            return []
        try:
            placeholders = ','.join(['%s'] * len(uids))
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute(f'''
                    SELECT uid_origem, message_id, assunto, remetente
                    FROM sync_erros_mensagem
                    WHERE conta_origem_id = %s AND uid_origem IN ({placeholders})
                ''', [conta_origem_id] + uids)
                return cursor.fetchall()
        except Exception as e:
            logger.error(f"Erro ao buscar erros por UID: {e}")
            return []

    @staticmethod
    def marcar_para_exclusao(conta_origem_id, uids, usuario_id, motivo='manual'):
        if not uids:
            return 0
        marcados = 0
        try:
            with DatabaseManager.get_cursor() as cursor:
                for uid in uids:
                    uid_str = str(uid).strip()
                    if not uid_str:
                        continue
                    cursor.execute('''
                        SELECT assunto, remetente, message_id
                        FROM sync_erros_mensagem
                        WHERE conta_origem_id = %s AND uid_origem = %s
                    ''', (conta_origem_id, uid_str))
                    erro = cursor.fetchone()
                    cursor.execute('''
                        INSERT INTO mensagens_marcadas_exclusao (
                            conta_origem_id, uid_origem, message_id, assunto, remetente,
                            motivo, status, marcado_por_usuario_id
                        ) VALUES (%s, %s, %s, %s, %s, %s, 'pendente', %s)
                        ON DUPLICATE KEY UPDATE
                            assunto = COALESCE(VALUES(assunto), assunto),
                            remetente = COALESCE(VALUES(remetente), remetente),
                            message_id = COALESCE(VALUES(message_id), message_id),
                            motivo = VALUES(motivo),
                            status = 'pendente',
                            marcado_por_usuario_id = VALUES(marcado_por_usuario_id),
                            marcado_em = NOW(),
                            erro_exclusao = NULL
                    ''', (
                        conta_origem_id,
                        uid_str,
                        erro.get('message_id') if erro else None,
                        erro.get('assunto') if erro else None,
                        erro.get('remetente') if erro else None,
                        motivo,
                        usuario_id,
                    ))
                    marcados += 1
            return marcados
        except Exception as e:
            logger.error(f"Erro ao marcar mensagens para exclusão: {e}")
            return 0

    @staticmethod
    def desmarcar_exclusao(conta_origem_id, uids):
        if not uids:
            return 0
        try:
            placeholders = ','.join(['%s'] * len(uids))
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute(f'''
                    DELETE FROM mensagens_marcadas_exclusao
                    WHERE conta_origem_id = %s AND status = 'pendente'
                    AND uid_origem IN ({placeholders})
                ''', [conta_origem_id] + [str(u) for u in uids])
                return cursor.rowcount or 0
        except Exception as e:
            logger.error(f"Erro ao desmarcar exclusão: {e}")
            return 0

    @staticmethod
    def listar_pendentes_exclusao(conta_origem_id):
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT * FROM mensagens_marcadas_exclusao
                    WHERE conta_origem_id = %s AND status = 'pendente'
                    ORDER BY marcado_em ASC
                ''', (conta_origem_id,))
                return cursor.fetchall()
        except Exception as e:
            logger.error(f"Erro ao listar pendentes exclusão: {e}")
            return []

    @staticmethod
    def atualizar_status_exclusao(conta_origem_id, uid_origem, status, erro_exclusao=None):
        try:
            with DatabaseManager.get_cursor() as cursor:
                if status == 'excluida':
                    cursor.execute('''
                        UPDATE mensagens_marcadas_exclusao
                        SET status = 'excluida', excluida_em = NOW(), erro_exclusao = NULL
                        WHERE conta_origem_id = %s AND uid_origem = %s
                    ''', (conta_origem_id, str(uid_origem)))
                    cursor.execute('''
                        DELETE FROM sync_erros_mensagem
                        WHERE conta_origem_id = %s AND uid_origem = %s
                    ''', (conta_origem_id, str(uid_origem)))
                else:
                    cursor.execute('''
                        UPDATE mensagens_marcadas_exclusao
                        SET status = %s, erro_exclusao = %s
                        WHERE conta_origem_id = %s AND uid_origem = %s
                    ''', (status, (erro_exclusao or '')[:2000], conta_origem_id, str(uid_origem)))
        except Exception as e:
            logger.error(f"Erro ao atualizar status exclusão UID {uid_origem}: {e}")

    @staticmethod
    def limpar_erros_antigos(dias_manter=None):
        if dias_manter is None:
            dias_manter = Config.SYNC_ERROS_RETENTION_DAYS
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    DELETE FROM sync_erros_mensagem
                    WHERE ultimo_erro_em < DATE_SUB(NOW(), INTERVAL %s DAY)
                ''', (dias_manter,))
                return cursor.rowcount or 0
        except Exception as e:
            logger.debug(f"Erro ao limpar erros antigos: {e}")
            return 0
