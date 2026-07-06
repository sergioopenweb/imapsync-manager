"""
Gerenciador de Filtros de Email - MailBridge
Suporta filtros globais (conta principal) e específicos (conta origem)
"""
import logging
import re
from typing import List, Dict, Optional
from db_manager import DatabaseManager

logger = logging.getLogger(__name__)


class EmailFilter:
    """Representa um filtro de email com critérios e ações"""
    
    def __init__(self, filtro_data: dict):
        self.id = filtro_data['id']
        self.conta_origem_id = filtro_data.get('conta_origem_id')
        self.conta_principal_id = filtro_data.get('conta_principal_id')
        self.nome = filtro_data['nome']
        self.ativo = filtro_data['ativo']
        
        # Tipo de filtro (global ou específico)
        self.is_global = self.conta_principal_id is not None and self.conta_origem_id is None
        
        # Critérios
        self.criterio_remetente = filtro_data.get('criterio_remetente')
        self.criterio_destinatario = filtro_data.get('criterio_destinatario')
        self.criterio_assunto = filtro_data.get('criterio_assunto')
        self.criterio_corpo = filtro_data.get('criterio_corpo')
        self.criterio_tem_anexo = filtro_data.get('criterio_tem_anexo')
        
        # Ações
        self.acao_pular_inbox = filtro_data.get('acao_pular_inbox', False)
        self.acao_aplicar_label = filtro_data.get('acao_aplicar_label')
        self.acao_marcar_lido = filtro_data.get('acao_marcar_lido', False)
        self.acao_marcar_importante = filtro_data.get('acao_marcar_importante', False)
        self.acao_deletar = filtro_data.get('acao_deletar', False)
        self.acao_encaminhar_para = filtro_data.get('acao_encaminhar_para')
    
    def matches(self, email_data: dict) -> bool:
        """
        Verifica se um email corresponde aos critérios deste filtro.
        
        Args:
            email_data: Dict com campos do email:
                - from: remetente
                - to: destinatário
                - subject: assunto
                - body: corpo (opcional)
                - has_attachment: bool
        
        Returns:
            True se o email corresponde aos critérios
        """
        if not self.ativo:
            return False
        
        # Todos os critérios definidos devem corresponder (AND lógico)
        # Ex: "De: X | Assunto: Y" = remetente X E assunto Y
        if self.criterio_remetente:
            sender = email_data.get('from', '').lower()
            criterio = self.criterio_remetente.lower()
            if not self._matches_text(sender, criterio):
                return False
        
        if self.criterio_destinatario:
            recipient = email_data.get('to', '').lower()
            criterio = self.criterio_destinatario.lower()
            if not self._matches_text(recipient, criterio):
                return False
        
        if self.criterio_assunto:
            subject = email_data.get('subject', '').lower()
            criterio = self.criterio_assunto.lower()
            if not self._matches_text(subject, criterio):
                return False
        
        if self.criterio_corpo:
            body = email_data.get('body', '').lower()
            criterio = self.criterio_corpo.lower()
            if not self._matches_text(body, criterio):
                return False
        
        if self.criterio_tem_anexo is not None:
            has_attachment = email_data.get('has_attachment', False)
            if has_attachment != self.criterio_tem_anexo:
                return False
        
        # Pelo menos um critério deve estar definido
        return bool(
            self.criterio_remetente or
            self.criterio_destinatario or
            self.criterio_assunto or
            self.criterio_corpo or
            self.criterio_tem_anexo is not None
        )
    
    def _matches_text(self, text: str, pattern: str) -> bool:
        """
        Verifica se o texto corresponde ao padrão.
        Suporta:
        - Correspondência parcial (substring)
        - Múltiplos termos separados por vírgula (OR)
        - Normalização de espaços (vários espaços tratados como um)
        """
        if not text or not pattern:
            return False

        # Normalizar espaços no texto (evita falha com "Backup  Banco" vs "Backup Banco")
        def norm(s):
            return ' '.join(str(s).split()) if s else ''

        text_norm = norm(text)
        # Suporta múltiplos critérios separados por vírgula
        patterns = [norm(p.strip()) for p in pattern.split(',') if p.strip()]

        for p in patterns:
            if p and p in text_norm:
                return True

        return False
    
    def get_actions(self) -> Dict[str, any]:
        """
        Retorna um dicionário com as ações que devem ser aplicadas.
        
        Returns:
            Dict com ações a serem aplicadas
        """
        return {
            'pular_inbox': self.acao_pular_inbox,
            'aplicar_label': self.acao_aplicar_label,
            'marcar_lido': self.acao_marcar_lido,
            'marcar_importante': self.acao_marcar_importante,
            'deletar': self.acao_deletar,
            'encaminhar_para': self.acao_encaminhar_para
        }


class FilterManager:
    """Gerenciador de filtros de email (globais e específicos)"""
    
    @staticmethod
    def criar_tabela_se_nao_existe():
        """Cria a tabela de filtros se não existir"""
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS filtros_email (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        conta_origem_id INT DEFAULT NULL,
                        conta_principal_id INT DEFAULT NULL,
                        nome VARCHAR(255) NOT NULL,
                        ativo BOOLEAN DEFAULT TRUE,
                        
                        criterio_remetente VARCHAR(500) DEFAULT NULL,
                        criterio_destinatario VARCHAR(500) DEFAULT NULL,
                        criterio_assunto VARCHAR(500) DEFAULT NULL,
                        criterio_corpo VARCHAR(500) DEFAULT NULL,
                        criterio_tem_anexo BOOLEAN DEFAULT NULL,
                        
                        acao_pular_inbox BOOLEAN DEFAULT FALSE,
                        acao_aplicar_label VARCHAR(255) DEFAULT NULL,
                        acao_marcar_lido BOOLEAN DEFAULT FALSE,
                        acao_marcar_importante BOOLEAN DEFAULT FALSE,
                        acao_deletar BOOLEAN DEFAULT FALSE,
                        acao_encaminhar_para VARCHAR(255) DEFAULT NULL,
                        gmail_filter_id VARCHAR(255) DEFAULT NULL,
                        
                        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        
                        FOREIGN KEY (conta_origem_id) REFERENCES contas_origem(id) ON DELETE CASCADE,
                        FOREIGN KEY (conta_principal_id) REFERENCES contas_principais(id) ON DELETE CASCADE,
                        INDEX idx_conta_origem_ativo (conta_origem_id, ativo),
                        INDEX idx_conta_principal_ativo (conta_principal_id, ativo),
                        INDEX idx_criado (criado_em),
                        CHECK (
                            (conta_origem_id IS NOT NULL AND conta_principal_id IS NULL) OR
                            (conta_origem_id IS NULL AND conta_principal_id IS NOT NULL)
                        )
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                ''')
                logger.info("Tabela filtros_email verificada/criada")
                FilterManager._ensure_gmail_filter_id_column(cursor)
        except Exception as e:
            logger.error(f"Erro ao criar tabela de filtros: {e}")
            raise
    
    @staticmethod
    def _ensure_gmail_filter_id_column(cursor):
        """Garante que a coluna gmail_filter_id existe (para importação do Gmail sem duplicar)."""
        try:
            cursor.execute('''
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'filtros_email' AND COLUMN_NAME = 'gmail_filter_id'
            ''')
            if cursor.fetchone()[0] > 0:
                return
            cursor.execute('''
                ALTER TABLE filtros_email
                ADD COLUMN gmail_filter_id VARCHAR(255) DEFAULT NULL,
                ADD INDEX idx_gmail_filter_id (gmail_filter_id)
            ''')
            logger.info("Coluna gmail_filter_id adicionada à tabela filtros_email")
        except Exception as e:
            if '1060' in str(e) or 'Duplicate column' in str(e):
                pass
            else:
                logger.warning(f"Coluna gmail_filter_id: {e}")
    
    @staticmethod
    def ensure_gmail_import_support():
        """Garante que a coluna gmail_filter_id existe. Chamar antes de importar filtros do Gmail."""
        try:
            with DatabaseManager.get_cursor() as cursor:
                FilterManager._ensure_gmail_filter_id_column(cursor)
        except Exception as e:
            logger.warning(f"ensure_gmail_import_support: {e}")
    
    @staticmethod
    def existe_filtro_por_gmail_id(conta_principal_id: int, gmail_filter_id: str) -> bool:
        """Verifica se já existe um filtro global com este ID do Gmail (evita duplicar na importação)."""
        if not gmail_filter_id:
            return False
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT 1 FROM filtros_email
                    WHERE conta_principal_id = %s AND gmail_filter_id = %s
                    LIMIT 1
                ''', (conta_principal_id, gmail_filter_id))
                return cursor.fetchone() is not None
        except Exception as e:
            logger.debug(f"existe_filtro_por_gmail_id: {e}")
            return False

    @staticmethod
    def existe_filtro_global_equivalente(conta_principal_id: int, filtro_data: dict) -> bool:
        """
        Verifica se já existe um filtro global (manual ou importado) com os mesmos critérios e ações.
        Usado na importação do Gmail para não duplicar regras que o usuário já criou manualmente.
        """
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT 1 FROM filtros_email
                    WHERE conta_principal_id = %s
                      AND COALESCE(criterio_remetente, '') = COALESCE(%s, '')
                      AND COALESCE(criterio_destinatario, '') = COALESCE(%s, '')
                      AND COALESCE(criterio_assunto, '') = COALESCE(%s, '')
                      AND COALESCE(criterio_corpo, '') = COALESCE(%s, '')
                      AND COALESCE(acao_aplicar_label, '') = COALESCE(%s, '')
                      AND COALESCE(acao_encaminhar_para, '') = COALESCE(%s, '')
                      AND IFNULL(acao_pular_inbox, 0) = IFNULL(%s, 0)
                      AND IFNULL(acao_marcar_lido, 0) = IFNULL(%s, 0)
                      AND IFNULL(acao_deletar, 0) = IFNULL(%s, 0)
                    LIMIT 1
                ''', (
                    conta_principal_id,
                    filtro_data.get('criterio_remetente'),
                    filtro_data.get('criterio_destinatario'),
                    filtro_data.get('criterio_assunto'),
                    filtro_data.get('criterio_corpo'),
                    filtro_data.get('acao_aplicar_label'),
                    filtro_data.get('acao_encaminhar_para'),
                    filtro_data.get('acao_pular_inbox', False),
                    filtro_data.get('acao_marcar_lido', False),
                    filtro_data.get('acao_deletar', False),
                ))
                return cursor.fetchone() is not None
        except Exception as e:
            logger.debug(f"existe_filtro_global_equivalente: {e}")
            return False

    @staticmethod
    def get_filtros_ativos_para_sincronizacao(conta_origem_id: int, conta_principal_id: int) -> List[EmailFilter]:
        """
        Busca TODOS os filtros ativos que devem ser aplicados durante a sincronização:
        - Filtros globais da conta principal
        - Filtros específicos da conta de origem
        
        Args:
            conta_origem_id: ID da conta de origem
            conta_principal_id: ID da conta principal
        
        Returns:
            Lista de objetos EmailFilter ativos (globais primeiro, depois específicos)
        """
        try:
            with DatabaseManager.get_cursor() as cursor:
                # Buscar filtros globais + específicos
                cursor.execute('''
                    SELECT * FROM filtros_email
                    WHERE ativo = TRUE AND (
                        conta_principal_id = %s OR
                        conta_origem_id = %s
                    )
                    ORDER BY 
                        CASE 
                            WHEN conta_principal_id IS NOT NULL THEN 0
                            ELSE 1
                        END,
                        id ASC
                ''', (conta_principal_id, conta_origem_id))
                
                filtros_data = cursor.fetchall()
                filtros = [EmailFilter(f) for f in filtros_data]
                
                globais = sum(1 for f in filtros if f.is_global)
                especificos = len(filtros) - globais
                
                logger.debug(f"Carregados {len(filtros)} filtro(s) ativo(s): {globais} global(is), {especificos} específico(s)")
                return filtros
        
        except Exception as e:
            logger.error(f"Erro ao buscar filtros para sincronização: {e}")
            return []
    
    @staticmethod
    def get_filtros_ativos(conta_origem_id: int) -> List[EmailFilter]:
        """
        Busca todos os filtros ativos para uma conta de origem (APENAS específicos).
        Usado para compatibilidade com código legado.
        
        Args:
            conta_origem_id: ID da conta de origem
        
        Returns:
            Lista de objetos EmailFilter ativos
        """
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT * FROM filtros_email
                    WHERE conta_origem_id = %s AND ativo = TRUE
                    ORDER BY id ASC
                ''', (conta_origem_id,))
                
                filtros_data = cursor.fetchall()
                filtros = [EmailFilter(f) for f in filtros_data]
                
                logger.debug(f"Carregados {len(filtros)} filtro(s) específico(s) ativo(s) para conta {conta_origem_id}")
                return filtros
        
        except Exception as e:
            logger.error(f"Erro ao buscar filtros: {e}")
            return []
    
    @staticmethod
    def get_filtros_globais(conta_principal_id: int) -> List[dict]:
        """
        Busca todos os filtros globais de uma conta principal.
        
        Args:
            conta_principal_id: ID da conta principal
        
        Returns:
            Lista de dicts com dados dos filtros globais
        """
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT * FROM filtros_email
                    WHERE conta_principal_id = %s
                    ORDER BY criado_em DESC
                ''', (conta_principal_id,))
                
                return cursor.fetchall()
        
        except Exception as e:
            logger.error(f"Erro ao buscar filtros globais: {e}")
            return []
    
    @staticmethod
    def get_filtros(conta_origem_id: int) -> List[dict]:
        """
        Busca todos os filtros específicos de uma conta de origem.
        
        Args:
            conta_origem_id: ID da conta de origem
        
        Returns:
            Lista de dicts com dados dos filtros
        """
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT * FROM filtros_email
                    WHERE conta_origem_id = %s
                    ORDER BY criado_em DESC
                ''', (conta_origem_id,))
                
                return cursor.fetchall()
        
        except Exception as e:
            logger.error(f"Erro ao buscar filtros: {e}")
            return []
    
    @staticmethod
    def criar_filtro_global(conta_principal_id: int, filtro_data: dict, gmail_filter_id: Optional[str] = None) -> Optional[int]:
        """
        Cria um novo filtro global (vinculado à conta principal).
        
        Args:
            conta_principal_id: ID da conta principal
            filtro_data: Dict com dados do filtro
            gmail_filter_id: ID do filtro no Gmail (opcional, para evitar duplicatas na importação)
        
        Returns:
            ID do filtro criado ou None em caso de erro
        """
        try:
            FilterManager.ensure_gmail_import_support()
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    INSERT INTO filtros_email (
                        conta_principal_id, nome, ativo,
                        criterio_remetente, criterio_destinatario, criterio_assunto,
                        criterio_corpo, criterio_tem_anexo,
                        acao_pular_inbox, acao_aplicar_label, acao_marcar_lido,
                        acao_marcar_importante, acao_deletar, acao_encaminhar_para,
                        gmail_filter_id
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                ''', (
                    conta_principal_id,
                    filtro_data['nome'],
                    filtro_data.get('ativo', True),
                    filtro_data.get('criterio_remetente'),
                    filtro_data.get('criterio_destinatario'),
                    filtro_data.get('criterio_assunto'),
                    filtro_data.get('criterio_corpo'),
                    filtro_data.get('criterio_tem_anexo'),
                    filtro_data.get('acao_pular_inbox', False),
                    filtro_data.get('acao_aplicar_label'),
                    filtro_data.get('acao_marcar_lido', False),
                    filtro_data.get('acao_marcar_importante', False),
                    filtro_data.get('acao_deletar', False),
                    filtro_data.get('acao_encaminhar_para'),
                    gmail_filter_id
                ))
                
                filtro_id = cursor.lastrowid
                logger.info(f"Filtro GLOBAL '{filtro_data['nome']}' criado com ID {filtro_id}")
                return filtro_id
        
        except Exception as e:
            logger.error(f"Erro ao criar filtro global: {e}")
            return None
    
    @staticmethod
    def criar_filtro(conta_origem_id: int, filtro_data: dict) -> Optional[int]:
        """
        Cria um novo filtro específico (vinculado à conta de origem).
        
        Args:
            conta_origem_id: ID da conta de origem
            filtro_data: Dict com dados do filtro
        
        Returns:
            ID do filtro criado ou None em caso de erro
        """
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    INSERT INTO filtros_email (
                        conta_origem_id, nome, ativo,
                        criterio_remetente, criterio_destinatario, criterio_assunto,
                        criterio_corpo, criterio_tem_anexo,
                        acao_pular_inbox, acao_aplicar_label, acao_marcar_lido,
                        acao_marcar_importante, acao_deletar, acao_encaminhar_para
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                ''', (
                    conta_origem_id,
                    filtro_data['nome'],
                    filtro_data.get('ativo', True),
                    filtro_data.get('criterio_remetente'),
                    filtro_data.get('criterio_destinatario'),
                    filtro_data.get('criterio_assunto'),
                    filtro_data.get('criterio_corpo'),
                    filtro_data.get('criterio_tem_anexo'),
                    filtro_data.get('acao_pular_inbox', False),
                    filtro_data.get('acao_aplicar_label'),
                    filtro_data.get('acao_marcar_lido', False),
                    filtro_data.get('acao_marcar_importante', False),
                    filtro_data.get('acao_deletar', False),
                    filtro_data.get('acao_encaminhar_para')
                ))
                
                filtro_id = cursor.lastrowid
                logger.info(f"Filtro ESPECÍFICO '{filtro_data['nome']}' criado com ID {filtro_id}")
                return filtro_id
        
        except Exception as e:
            logger.error(f"Erro ao criar filtro: {e}")
            return None
    
    @staticmethod
    def atualizar_filtro(filtro_id: int, filtro_data: dict) -> bool:
        """
        Atualiza um filtro existente.
        
        Args:
            filtro_id: ID do filtro
            filtro_data: Dict com dados do filtro
        
        Returns:
            True se atualizado com sucesso
        """
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    UPDATE filtros_email SET
                        nome = %s,
                        ativo = %s,
                        criterio_remetente = %s,
                        criterio_destinatario = %s,
                        criterio_assunto = %s,
                        criterio_corpo = %s,
                        criterio_tem_anexo = %s,
                        acao_pular_inbox = %s,
                        acao_aplicar_label = %s,
                        acao_marcar_lido = %s,
                        acao_marcar_importante = %s,
                        acao_deletar = %s,
                        acao_encaminhar_para = %s
                    WHERE id = %s
                ''', (
                    filtro_data['nome'],
                    filtro_data.get('ativo', True),
                    filtro_data.get('criterio_remetente'),
                    filtro_data.get('criterio_destinatario'),
                    filtro_data.get('criterio_assunto'),
                    filtro_data.get('criterio_corpo'),
                    filtro_data.get('criterio_tem_anexo'),
                    filtro_data.get('acao_pular_inbox', False),
                    filtro_data.get('acao_aplicar_label'),
                    filtro_data.get('acao_marcar_lido', False),
                    filtro_data.get('acao_marcar_importante', False),
                    filtro_data.get('acao_deletar', False),
                    filtro_data.get('acao_encaminhar_para'),
                    filtro_id
                ))
                
                logger.info(f"Filtro {filtro_id} atualizado")
                return True
        
        except Exception as e:
            logger.error(f"Erro ao atualizar filtro: {e}")
            return False
    
    @staticmethod
    def deletar_filtro(filtro_id: int) -> bool:
        """
        Deleta um filtro.
        
        Args:
            filtro_id: ID do filtro
        
        Returns:
            True se deletado com sucesso
        """
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('DELETE FROM filtros_email WHERE id = %s', (filtro_id,))
                logger.info(f"Filtro {filtro_id} deletado")
                return True
        
        except Exception as e:
            logger.error(f"Erro ao deletar filtro: {e}")
            return False
    
    @staticmethod
    def toggle_filtro(filtro_id: int) -> bool:
        """
        Ativa/desativa um filtro.
        
        Args:
            filtro_id: ID do filtro
        
        Returns:
            True se atualizado com sucesso
        """
        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    UPDATE filtros_email 
                    SET ativo = NOT ativo 
                    WHERE id = %s
                ''', (filtro_id,))
                logger.info(f"Status do filtro {filtro_id} alternado")
                return True
        
        except Exception as e:
            logger.error(f"Erro ao alternar filtro: {e}")
            return False
