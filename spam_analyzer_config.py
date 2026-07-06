"""
Configuração do Spam Analyzer por conta principal.
Permite: marcar como spam (pasta Spam) ou mover e ignorar inbox (arquivar).
Quando o usuário marca um email como spam na lista "Emails sincronizados",
as palavras do assunto e do remetente são adicionadas à wordlist para barrar
mensagens parecidas na próxima sincronização.
"""
import re
import logging
import unicodedata
from typing import Optional, Dict, Any, List
from db_manager import DatabaseManager
from spam_analyzer import _STOPWORDS_WORDLIST, _MIN_LEN_WORDLIST

logger = logging.getLogger(__name__)

# Frases = apenas trigramas (3 palavras) — mais específicos, menos ruído que bigramas
MIN_PALAVRAS_FRASE = 3
MAX_PALAVRAS_FRASE = 3
# Máximo de frases novas adicionadas por email marcado como spam
MAX_FRASES_POR_EMAIL = 5
# Tamanho mínimo de cada palavra dentro da frase (caracteres)
MIN_TAMANHO_PALAVRA = 4
# Prefixo guardado no histórico para remover o remetente ao desmarcar
PREFIXO_REMETENTE_GUARDADO = 'REMETENTE:'

# Ações disponíveis
ACAO_MARCAR_SPAM = 'mark_spam'      # Enviar para pasta Spam no destino
ACAO_PULAR_INBOX = 'skip_inbox'     # Arquivar (não colocar na caixa de entrada)

_palavras_genericas_cache: Optional[List[str]] = None
_dominios_gratuitos_default_cache: Optional[List[str]] = None
_palavras_institucionais_default_cache: Optional[List[str]] = None


# ─── Helpers genéricos para tabelas de defaults ───────────────────────────────

def _get_default_list(tabela: str, coluna: str, cache_attr: str, force_reload: bool) -> List[str]:
    import spam_analyzer_config as _self
    cache = getattr(_self, cache_attr)
    if cache is not None and not force_reload:
        return cache
    try:
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute(f'SELECT {coluna} FROM {tabela} ORDER BY {coluna}')
            rows = cursor.fetchall()
        resultado = [r[coluna] for r in rows]
        setattr(_self, cache_attr, resultado)
        return resultado
    except Exception as e:
        logger.warning(f"Erro ao buscar {tabela}: {e}")
        return cache or []


def _adicionar_default(tabela: str, coluna: str, cache_attr: str, valor: str) -> bool:
    import spam_analyzer_config as _self
    valor = valor.strip().lower()
    if not valor:
        return False
    try:
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute(f'INSERT IGNORE INTO {tabela} ({coluna}) VALUES (%s)', (valor,))
            inserido = cursor.rowcount > 0
        setattr(_self, cache_attr, None)
        return inserido
    except Exception as e:
        logger.error(f"Erro ao adicionar em {tabela} '{valor}': {e}")
        return False


def _remover_default(tabela: str, coluna: str, cache_attr: str, valor: str) -> bool:
    import spam_analyzer_config as _self
    valor = valor.strip().lower()
    if not valor:
        return False
    try:
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute(f'DELETE FROM {tabela} WHERE {coluna} = %s', (valor,))
            removido = cursor.rowcount > 0
        setattr(_self, cache_attr, None)
        return removido
    except Exception as e:
        logger.error(f"Erro ao remover de {tabela} '{valor}': {e}")
        return False


# ─── Domínios gratuitos padrão ────────────────────────────────────────────────

def get_dominios_gratuitos_default(force_reload: bool = False) -> List[str]:
    """Domínios gratuitos padrão (gmail.com, hotmail.com…) para novas contas."""
    return _get_default_list(
        'spam_dominios_gratuitos_default', 'dominio',
        '_dominios_gratuitos_default_cache', force_reload
    )


def adicionar_dominio_gratuito_default(dominio: str) -> bool:
    return _adicionar_default(
        'spam_dominios_gratuitos_default', 'dominio',
        '_dominios_gratuitos_default_cache', dominio
    )


def remover_dominio_gratuito_default(dominio: str) -> bool:
    return _remover_default(
        'spam_dominios_gratuitos_default', 'dominio',
        '_dominios_gratuitos_default_cache', dominio
    )


# ─── Palavras institucionais padrão ──────────────────────────────────────────

def get_palavras_institucionais_default(force_reload: bool = False) -> List[str]:
    """Palavras institucionais padrão (banco, receita…) para novas contas."""
    return _get_default_list(
        'spam_palavras_institucionais_default', 'palavra',
        '_palavras_institucionais_default_cache', force_reload
    )


def adicionar_palavra_institucional_default(palavra: str) -> bool:
    return _adicionar_default(
        'spam_palavras_institucionais_default', 'palavra',
        '_palavras_institucionais_default_cache', palavra
    )


def remover_palavra_institucional_default(palavra: str) -> bool:
    return _remover_default(
        'spam_palavras_institucionais_default', 'palavra',
        '_palavras_institucionais_default_cache', palavra
    )


def get_palavras_genericas(force_reload: bool = False) -> List[str]:
    """
    Retorna a lista de palavras genéricas armazenadas na tabela spam_palavras_genericas.
    Usa cache em memória para evitar consulta ao banco a cada requisição.
    """
    global _palavras_genericas_cache
    if _palavras_genericas_cache is not None and not force_reload:
        return _palavras_genericas_cache
    try:
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute('SELECT palavra FROM spam_palavras_genericas ORDER BY palavra')
            rows = cursor.fetchall()
        _palavras_genericas_cache = [r['palavra'] for r in rows]
        return _palavras_genericas_cache
    except Exception as e:
        logger.warning(f"Erro ao buscar palavras genéricas do banco: {e}")
        return _palavras_genericas_cache or []


def adicionar_palavra_generica(palavra: str) -> bool:
    """Adiciona uma palavra à lista de genéricas. Invalida o cache. Retorna True se inserida."""
    global _palavras_genericas_cache
    palavra = palavra.strip().lower()
    if not palavra:
        return False
    try:
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute(
                'INSERT IGNORE INTO spam_palavras_genericas (palavra) VALUES (%s)', (palavra,)
            )
            inserida = cursor.rowcount > 0
        _palavras_genericas_cache = None
        return inserida
    except Exception as e:
        logger.error(f"Erro ao adicionar palavra genérica '{palavra}': {e}")
        return False


def remover_palavra_generica(palavra: str) -> bool:
    """Remove uma palavra da lista de genéricas. Invalida o cache. Retorna True se removida."""
    global _palavras_genericas_cache
    palavra = palavra.strip().lower()
    if not palavra:
        return False
    try:
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute(
                'DELETE FROM spam_palavras_genericas WHERE palavra = %s', (palavra,)
            )
            removida = cursor.rowcount > 0
        _palavras_genericas_cache = None
        return removida
    except Exception as e:
        logger.error(f"Erro ao remover palavra genérica '{palavra}': {e}")
        return False


def criar_tabela_se_nao_existe():
    """Cria a tabela de configuração do Spam Analyzer se não existir."""
    try:
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS config_spam_analyzer (
                    conta_principal_id INT PRIMARY KEY,
                    ativo BOOLEAN DEFAULT FALSE,
                    acao ENUM('mark_spam', 'skip_inbox') DEFAULT 'mark_spam',
                    wordlist_extra TEXT DEFAULT NULL,
                    model_path VARCHAR(500) DEFAULT NULL,
                    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (conta_principal_id) REFERENCES contas_principais(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')
            _ensure_wordlist_model_columns(cursor)
            _ensure_remetentes_bloqueados_column(cursor)
            _ensure_remetentes_permitidos_column(cursor)
            _ensure_pasta_spam_column(cursor)
            _ensure_dominios_gratuitos_column(cursor)
            _ensure_palavras_institucionais_column(cursor)
            _ensure_heuristica_dominio_numerico_column(cursor)
            _ensure_heuristica_reply_to_column(cursor)
            _ensure_heuristica_display_name_column(cursor)
            _ensure_mediumtext_columns(cursor)
            logger.info("Tabela config_spam_analyzer verificada/criada")
    except Exception as e:
        logger.error(f"Erro ao criar tabela config_spam_analyzer: {e}")
        raise


def _ensure_remetentes_bloqueados_column(cursor):
    """Garante que a coluna remetentes_bloqueados existe. Usa ALTER direto para evitar problemas com information_schema."""
    try:
        cursor.execute('ALTER TABLE config_spam_analyzer ADD COLUMN remetentes_bloqueados TEXT DEFAULT NULL')
        logger.info("Coluna remetentes_bloqueados adicionada à config_spam_analyzer")
    except Exception as e:
        err = str(e).lower()
        if '1060' in str(e) or 'duplicate column' in err:
            pass  # coluna já existe
        else:
            logger.warning(f"Coluna remetentes_bloqueados: {e}")


def _ensure_remetentes_permitidos_column(cursor):
    """Garante que a coluna remetentes_permitidos existe. Usa ALTER direto para evitar problemas com information_schema."""
    try:
        cursor.execute('ALTER TABLE config_spam_analyzer ADD COLUMN remetentes_permitidos TEXT DEFAULT NULL')
        logger.info("Coluna remetentes_permitidos adicionada à config_spam_analyzer")
    except Exception as e:
        err = str(e).lower()
        if '1060' in str(e) or 'duplicate column' in err:
            pass  # coluna já existe
        else:
            logger.warning(f"Coluna remetentes_permitidos: {e}")


def _ensure_pasta_spam_column(cursor):
    """Garante que a coluna pasta_spam existe (pasta de destino para emails detectados como spam)."""
    try:
        cursor.execute('ALTER TABLE config_spam_analyzer ADD COLUMN pasta_spam VARCHAR(255) DEFAULT NULL')
        logger.info("Coluna pasta_spam adicionada à config_spam_analyzer")
    except Exception as e:
        err = str(e).lower()
        if '1060' in str(e) or 'duplicate column' in err:
            pass
        else:
            logger.warning(f"Coluna pasta_spam: {e}")


def _ensure_dominios_gratuitos_column(cursor):
    """Garante que a coluna dominios_gratuitos existe (lista de domínios de email gratuitos para detecção de spoofing)."""
    try:
        cursor.execute('ALTER TABLE config_spam_analyzer ADD COLUMN dominios_gratuitos TEXT DEFAULT NULL')
        logger.info("Coluna dominios_gratuitos adicionada à config_spam_analyzer")
    except Exception as e:
        if '1060' in str(e) or 'duplicate column' in str(e).lower():
            pass
        else:
            logger.warning(f"Coluna dominios_gratuitos: {e}")


def _ensure_palavras_institucionais_column(cursor):
    """Garante que a coluna palavras_institucionais existe (palavras que indicam entidade institucional no display name)."""
    try:
        cursor.execute('ALTER TABLE config_spam_analyzer ADD COLUMN palavras_institucionais TEXT DEFAULT NULL')
        logger.info("Coluna palavras_institucionais adicionada à config_spam_analyzer")
    except Exception as e:
        if '1060' in str(e) or 'duplicate column' in str(e).lower():
            pass
        else:
            logger.warning(f"Coluna palavras_institucionais: {e}")


def _ensure_heuristica_dominio_numerico_column(cursor):
    """Garante que a coluna heuristica_dominio_numerico existe (heurística de domínios com padrões numéricos suspeitos)."""
    try:
        cursor.execute('ALTER TABLE config_spam_analyzer ADD COLUMN heuristica_dominio_numerico TINYINT(1) DEFAULT NULL')
        logger.info("Coluna heuristica_dominio_numerico adicionada à config_spam_analyzer")
    except Exception as e:
        if '1060' in str(e) or 'duplicate column' in str(e).lower():
            pass
        else:
            logger.warning(f"Coluna heuristica_dominio_numerico: {e}")


def _ensure_heuristica_reply_to_column(cursor):
    """Garante a coluna heuristica_reply_to (NULL = herdar do nível superior)."""
    try:
        cursor.execute('ALTER TABLE config_spam_analyzer ADD COLUMN heuristica_reply_to TINYINT(1) DEFAULT NULL')
        logger.info("Coluna heuristica_reply_to adicionada à config_spam_analyzer")
    except Exception as e:
        if '1060' in str(e) or 'duplicate column' in str(e).lower():
            pass
        else:
            logger.warning(f"Coluna heuristica_reply_to: {e}")


def _ensure_heuristica_display_name_column(cursor):
    """Garante a coluna heuristica_display_name (NULL = herdar do nível superior)."""
    try:
        cursor.execute('ALTER TABLE config_spam_analyzer ADD COLUMN heuristica_display_name TINYINT(1) DEFAULT NULL')
        logger.info("Coluna heuristica_display_name adicionada à config_spam_analyzer")
    except Exception as e:
        if '1060' in str(e) or 'duplicate column' in str(e).lower():
            pass
        else:
            logger.warning(f"Coluna heuristica_display_name: {e}")


def _ensure_mediumtext_columns(cursor):
    """Migra colunas TEXT para MEDIUMTEXT para suportar listas grandes."""
    for col in ('wordlist_extra', 'remetentes_bloqueados', 'remetentes_permitidos',
                'dominios_gratuitos', 'palavras_institucionais'):
        try:
            cursor.execute(
                f'ALTER TABLE config_spam_analyzer MODIFY COLUMN {col} MEDIUMTEXT DEFAULT NULL'
            )
        except Exception:
            pass


def _ensure_wordlist_model_columns(cursor):
    """Garante que as colunas wordlist_extra e model_path existem (migração)."""
    try:
        cursor.execute('''
            SELECT COUNT(*) AS cnt FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'config_spam_analyzer' AND COLUMN_NAME = 'wordlist_extra'
        ''')
        if (cursor.fetchone() or {}).get('cnt', 0) > 0:
            return
        cursor.execute('''
            ALTER TABLE config_spam_analyzer
            ADD COLUMN wordlist_extra TEXT DEFAULT NULL,
            ADD COLUMN model_path VARCHAR(500) DEFAULT NULL
        ''')
        logger.info("Colunas wordlist_extra e model_path adicionadas à config_spam_analyzer")
    except Exception as e:
        if '1060' not in str(e) and 'Duplicate column' not in str(e):
            logger.warning(f"Colunas wordlist/model: {e}")


def get_config(conta_principal_id: int) -> Optional[Dict[str, Any]]:
    """
    Retorna a configuração do Spam Analyzer para a conta principal.
    Se não existir linha, retorna None (desativado).
    Heurísticas NULL = herdar do nível superior (utilizador ou global).
    """
    try:
        criar_tabela_se_nao_existe()
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute('''
                SELECT conta_principal_id, ativo, acao, wordlist_extra, model_path,
                       remetentes_bloqueados, remetentes_permitidos, pasta_spam,
                       dominios_gratuitos, palavras_institucionais,
                       heuristica_dominio_numerico,
                       heuristica_reply_to,
                       heuristica_display_name
                FROM config_spam_analyzer
                WHERE conta_principal_id = %s
            ''', (conta_principal_id,))
            row = cursor.fetchone()
            if row:
                return {
                    'conta_principal_id': row['conta_principal_id'],
                    'ativo': bool(row['ativo']),
                    'acao': row['acao'] or ACAO_MARCAR_SPAM,
                    'wordlist_extra': row.get('wordlist_extra') or '',
                    'model_path': row.get('model_path') or '',
                    'remetentes_bloqueados': row.get('remetentes_bloqueados') or '',
                    'remetentes_permitidos': row.get('remetentes_permitidos') or '',
                    'pasta_spam': (row.get('pasta_spam') or '').strip() or None,
                    'dominios_gratuitos': row.get('dominios_gratuitos') or '',
                    'palavras_institucionais': row.get('palavras_institucionais') or '',
                    # NULL significa "herdar do nível superior"
                    'heuristica_dominio_numerico': row.get('heuristica_dominio_numerico'),
                    'heuristica_reply_to': row.get('heuristica_reply_to'),
                    'heuristica_display_name': row.get('heuristica_display_name'),
                }
            return None
    except Exception as e:
        logger.warning(f"Erro ao buscar config spam analyzer: {e}")
        return None


def aplicar_defaults(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Preenche campos vazios com valores padrão do nível global.
    Se cfg for None (conta nova), cria um dict base.
    Domínios gratuitos e palavras institucionais são geridos apenas globalmente
    pelo admin — não são editáveis por conta.
    """
    global_cfg = get_config_global()
    if cfg is None:
        cfg = {
            'ativo': bool(global_cfg.get('ativo_por_defeito', False)),
            'acao': global_cfg.get('acao_por_defeito') or ACAO_MARCAR_SPAM,
            'wordlist_extra': '',
            'model_path': '',
            'remetentes_bloqueados': '',
            'remetentes_permitidos': '',
            'pasta_spam': global_cfg.get('pasta_spam_padrao') or None,
            'heuristica_dominio_numerico': None,
            'heuristica_reply_to': None,
            'heuristica_display_name': None,
        }
    return cfg


def get_config_para_sync(conta_principal_id: int) -> Optional[Dict[str, Any]]:
    """Mantido para compatibilidade. Preferir get_config_merged_para_sync quando usuario_id disponível."""
    return get_config_merged_para_sync(conta_principal_id, usuario_id=None)


def _extrair_email_remetente(remetente: Optional[str]) -> Optional[str]:
    """Extrai o endereço de email do campo From (ex: 'Nome <user@domain.com>' -> 'user@domain.com')."""
    if not remetente or not remetente.strip():
        return None
    match = re.search(r'<([^>]+)>', remetente)
    email = (match.group(1) if match else remetente).strip()
    if '@' in email:
        return email.lower()
    return None


def _extrair_display_name(remetente: Optional[str]) -> Optional[str]:
    """Extrai o nome de exibição do campo From (ex: 'Pedagio Digital <x@y.com>' -> 'pedagio digital')."""
    if not remetente or not remetente.strip():
        return None
    match = re.match(r'^(.+?)\s*<[^>]+>', remetente.strip())
    if match:
        name = match.group(1).strip().strip('"\'').strip()
        return name.lower() if name else None
    return None


def _normalizar_acentos(texto: str) -> str:
    """Remove acentos/diacríticos: 'Pedágio' → 'Pedagio'."""
    return unicodedata.normalize('NFKD', texto).encode('ASCII', 'ignore').decode('ASCII')


def remetente_bloqueado_entrada(remetente_raw: Optional[str], lista_bloqueados: List[str]) -> Optional[str]:
    """
    Verifica se o remetente está na lista de bloqueados.
    Retorna a entrada que correspondeu (ex: '@spam.com', 'user@evil.com', 'Nome Suspeito')
    ou None se não bloqueado.
    Mesmos formatos suportados que remetente_bloqueado().
    """
    if not remetente_raw or not lista_bloqueados:
        return None

    from_email = (_extrair_email_remetente(remetente_raw) or '').lower()
    display_name = (_extrair_display_name(remetente_raw) or '').lower()
    display_name_norm = _normalizar_acentos(display_name) if display_name else ''
    from_domain = ('@' + from_email.split('@')[1]) if from_email and '@' in from_email else ''
    from_domain_bare = from_email.split('@')[1] if from_email and '@' in from_email else ''
    from_user = from_email.split('@')[0] if from_email and '@' in from_email else ''

    for entrada in lista_bloqueados:
        entrada_orig = entrada.strip()
        entrada = entrada_orig.lower()
        if not entrada:
            continue

        if entrada.startswith('@'):
            if from_domain and from_domain == entrada:
                return entrada_orig
        elif entrada.startswith('*.'):
            sufixo = entrada[1:]
            if from_domain_bare and from_domain_bare.endswith(sufixo):
                return entrada_orig
        elif '*@' in entrada:
            prefixo = entrada.split('*@')[0]
            if from_user and from_user.startswith(prefixo):
                return entrada_orig
        elif entrada.endswith('@'):
            prefixo = entrada[:-1]
            if from_user and from_user == prefixo:
                return entrada_orig
        elif '@' in entrada:
            if from_email and from_email == entrada:
                return entrada_orig
        else:
            entrada_norm = _normalizar_acentos(entrada)
            if display_name and (entrada in display_name or entrada_norm in display_name_norm):
                return entrada_orig

    return None


def remetente_permitido(remetente_raw: Optional[str], lista_permitidos: List[str]) -> bool:
    """
    Verifica se o remetente está na whitelist (remetentes_permitidos).
    Aceita os formatos de remetente_bloqueado_entrada e, para @dominio.com,
    também aceita subdomínios (ex.: @empresa.com cobre user@mail.empresa.com).
    """
    if not remetente_raw or not lista_permitidos:
        return False

    if remetente_bloqueado_entrada(remetente_raw, lista_permitidos) is not None:
        return True

    from_email = (_extrair_email_remetente(remetente_raw) or '').lower()
    if not from_email or '@' not in from_email:
        return False
    from_domain_bare = from_email.split('@', 1)[1]

    for entrada in lista_permitidos:
        entrada = entrada.strip().lower()
        if not entrada.startswith('@'):
            continue
        dominio = entrada[1:]
        if not dominio:
            continue
        if from_domain_bare == dominio or from_domain_bare.endswith('.' + dominio):
            return True
    return False


def remetente_bloqueado(remetente_raw: Optional[str], lista_bloqueados: List[str]) -> bool:
    """
    Verifica se um remetente está na lista de bloqueados.
    Formatos suportados (case-insensitive, ignora acentos no display name):
      - email@dominio.com   → email exato
      - @dominio.com        → qualquer email do domínio
      - prefixo@            → usuário exato: docusing@ bloqueia docusing@qualquer.dominio
      - prefixo*@           → usuário começa com prefixo: dinhnaogosto*@ bloqueia
                              dinhnaogosto_mingmei-2ha@xerionz.com, dinhnaogosto_nayma@etc.com
      - *.dominio.com       → qualquer email cujo domínio termina com ".dominio.com"
      - Texto sem @ ou *    → bloqueia pelo nome de exibição (display name) — contém
    """
    if not remetente_raw or not lista_bloqueados:
        return False

    from_email = (_extrair_email_remetente(remetente_raw) or '').lower()
    display_name = (_extrair_display_name(remetente_raw) or '').lower()
    display_name_norm = _normalizar_acentos(display_name) if display_name else ''
    from_domain = ('@' + from_email.split('@')[1]) if from_email and '@' in from_email else ''
    from_domain_bare = from_email.split('@')[1] if from_email and '@' in from_email else ''
    from_user = from_email.split('@')[0] if from_email and '@' in from_email else ''

    for entrada in lista_bloqueados:
        entrada = entrada.strip().lower()
        if not entrada:
            continue

        if entrada.startswith('@'):
            if from_domain and from_domain == entrada:
                return True

        elif entrada.startswith('*.'):
            sufixo = entrada[1:]
            if from_domain_bare and from_domain_bare.endswith(sufixo):
                return True

        elif '*@' in entrada:
            prefixo = entrada.split('*@')[0]
            if from_user and from_user.startswith(prefixo):
                return True

        elif entrada.endswith('@'):
            prefixo = entrada[:-1]
            if from_user and from_user == prefixo:
                return True

        elif '@' in entrada:
            if from_email and from_email == entrada:
                return True

        else:
            entrada_norm = _normalizar_acentos(entrada)
            if display_name and (entrada in display_name or entrada_norm in display_name_norm):
                return True

    return False


def _extrair_frases_de_assunto_remetente(assunto: Optional[str], remetente: Optional[str]) -> List[str]:
    """
    Extrai trigramas (exatamente 3 palavras) do assunto para usar como filtro de spam.
    Trigramas são específicos o suficiente para evitar falsos positivos e genéricos
    o suficiente para pegar variações do mesmo tipo de spam.
    Se o assunto tiver menos de 3 palavras úteis, usa bigramas como fallback.
    Limitado a MAX_FRASES_POR_EMAIL entradas por email.
    """
    frases: List[str] = []
    seen: set = set()

    if assunto:
        tokens = [
            w.lower() for w in
            re.findall(r'[a-zA-Z0-9\u00c0-\u024f]{' + str(MIN_TAMANHO_PALAVRA) + r',}', assunto)
            if len(w) >= MIN_TAMANHO_PALAVRA and w.lower() not in _STOPWORDS_WORDLIST
        ]

        # Tentar trigramas primeiro (3 palavras — mais específico)
        for i in range(len(tokens) - MAX_PALAVRAS_FRASE + 1):
            frase = ' '.join(tokens[i:i + MAX_PALAVRAS_FRASE])
            if frase not in seen:
                seen.add(frase)
                frases.append(frase)
            if len(frases) >= MAX_FRASES_POR_EMAIL:
                break

        # Fallback para bigramas se o assunto for curto demais para trigramas
        if not frases and len(tokens) >= 2:
            frase = ' '.join(tokens[:2])
            frases.append(frase)

    return frases


def _wordlist_para_lista(texto: Optional[str]) -> List[str]:
    """Converte wordlist_extra em lista de entradas (uma por linha): cada linha é uma frase ou palavra.
    Mantém frases com 2+ palavras intactas; usado para merge e para o Spam Analyzer."""
    if not texto or not texto.strip():
        return []
    seen = set()
    entradas = []
    for line in (texto or '').replace(',', '\n').splitlines():
        w = line.strip().lower()
        if not w or w in seen:
            continue
        seen.add(w)
        entradas.append(w)
    return entradas


def _lista_para_wordlist(entradas: List[str], limite: int = 500) -> str:
    """Converte lista de frases/palavras em texto (uma por linha). Limita tamanho total."""
    return '\n'.join(entradas[:limite]) if entradas else ''


def _base_sem_num_final(frase: str) -> str:
    """Remove token(s) numérico(s) do final da frase.
    'protocolo 268763913' → 'protocolo'
    'assinatura protocolo 212692' → 'assinatura protocolo'
    """
    return re.sub(r'(\s+\d+)+$', '', frase).strip()


def _frase_coberta_por(longa: str, curta: str) -> bool:
    """True se as palavras de `curta` aparecem consecutivamente dentro de `longa`,
    `curta` é mais curta E tem pelo menos 2 palavras (evita que palavras únicas
    genéricas cubram frases específicas e gerem falsos positivos)."""
    if longa == curta:
        return False
    wL = longa.split()
    wC = curta.split()
    if len(wC) < 2 or len(wC) >= len(wL):
        return False
    for i in range(len(wL) - len(wC) + 1):
        if wL[i:i + len(wC)] == wC:
            return True
    return False


def _limpar_wordlist_redundante(entradas: List[str]) -> List[str]:
    """Remove entradas redundantes da wordlist:
    1. Duplicatas exatas
    2. Frases longas cobertas por frases mais curtas com 2+ palavras (subfrase consecutiva)
    3. Variantes numéricas com mesma base de 2+ palavras (consolida na base)
       — bases de 1 palavra são ignoradas para evitar filtros genéricos demais
    """
    # 1) Remover duplicatas preservando ordem
    unicas: List[str] = list(dict.fromkeys(entradas))

    # 2) Variantes numéricas: agrupar por base sem número final
    #    Só consolida se a base tiver 2+ palavras (ex: "assinatura protocolo", não "regularize")
    grupos: dict = {}
    for e in unicas:
        base = _base_sem_num_final(e)
        if base != e and base and len(base.split()) >= 2:
            grupos.setdefault(base, []).append(e)

    substituir: set = set()
    bases_adicionar: set = set()
    for base, variantes in grupos.items():
        if len(variantes) >= 2:
            for v in variantes:
                substituir.add(v)
            bases_adicionar.add(base)

    # 3) Filtrar variantes numéricas e subfrases cobertas
    filtradas = [
        e for e in unicas
        if e not in substituir
        and not any(_frase_coberta_por(e, curta) for curta in unicas if curta != e)
    ]

    # Adicionar bases consolidadas (se não existirem já)
    for base in bases_adicionar:
        if base not in filtradas:
            filtradas.append(base)

    # Segunda passagem: remover frases agora cobertas pelas bases recém-adicionadas
    filtradas = [
        e for e in filtradas
        if not any(_frase_coberta_por(e, curta) for curta in filtradas if curta != e)
    ]

    return filtradas


def _entrada_remetente_a_adicionar(email_remetente: str, remetentes_atual: List[str]) -> Optional[str]:
    """Decide qual entrada adicionar à lista de bloqueados para um email de remetente.

    - Se já coberto (exato, prefixo@, @dominio, *.base) → None (não adicionar)
    - Se o mesmo username já aparece em outra entrada exata → retorna 'username@' (prefix)
    - Caso contrário → retorna o email exato
    """
    if not email_remetente or '@' not in email_remetente:
        return email_remetente or None

    username, domain = email_remetente.split('@', 1)
    prefixo_fmt = username + '@'
    dominio_fmt = '@' + domain

    for entrada in remetentes_atual:
        if entrada == email_remetente:
            return None  # já existe exato
        if entrada == prefixo_fmt:
            return None  # já coberto por username@
        if entrada == dominio_fmt:
            return None  # já coberto por @dominio
        if entrada.startswith('*.') and domain.endswith(entrada[1:]):
            return None  # já coberto por *.base
        if '*@' in entrada:
            pfx = entrada.split('*@')[0]
            if username.startswith(pfx):
                return None  # já coberto por prefixo*@

    # Verificar se username já aparece em outra entrada exata (mesmo username, domínio diferente)
    mesmo_username = [
        e for e in remetentes_atual
        if '@' in e
        and not e.startswith('@')
        and not e.endswith('@')
        and not e.startswith('*.')
        and e.split('@')[0] == username
    ]
    if mesmo_username:
        # Já existe outro email com o mesmo username → consolidar em prefix@
        return prefixo_fmt

    return email_remetente


def adicionar_palavras_spam_do_email(historico_id: int, conta_origem_id: int, usuario_id: int) -> bool:
    """
    Quando o usuário marca um email como spam na lista "Emails sincronizados",
    busca assunto e remetente do histórico, extrai frases (2+ palavras) e adiciona
    à wordlist do Spam Analyzer. Guarda em spam_wordlist_adicionada o que foi
    adicionado para poder remover ao desmarcar.
    Retorna True se a wordlist foi atualizada.
    """
    try:
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute('''
                SELECT h.assunto, h.remetente, cp.id AS conta_principal_id
                FROM historico_emails_sincronizados h
                INNER JOIN contas_origem co ON co.id = h.conta_origem_id
                INNER JOIN contas_principais cp ON cp.id = co.conta_principal_id AND cp.usuario_id = %s
                WHERE h.id = %s AND h.conta_origem_id = %s
            ''', (usuario_id, historico_id, conta_origem_id))
            row = cursor.fetchone()
        if not row:
            return False

        assunto = row.get('assunto') or ''
        remetente = row.get('remetente') or ''
        conta_principal_id = row['conta_principal_id']

        cfg = get_config(conta_principal_id)
        ativo = bool(cfg.get('ativo')) if cfg else False
        acao = (cfg.get('acao') or ACAO_MARCAR_SPAM) if cfg else ACAO_MARCAR_SPAM
        model_path = (cfg.get('model_path') or '') if cfg else ''

        # 1) Adicionar remetente à lista de bloqueados (com consolidação inteligente)
        remetentes_atual = [l.strip().lower() for l in (cfg.get('remetentes_bloqueados') or '').splitlines() if l.strip()]
        email_remetente = _extrair_email_remetente(remetente)
        entrada_remetente = _entrada_remetente_a_adicionar(email_remetente, remetentes_atual) if email_remetente else None
        if entrada_remetente:
            remetentes_atual.append(entrada_remetente)
            if entrada_remetente.endswith('@'):
                # Consolidou em prefix@ → o email exato com mesmo username fica redundante,
                # mas é mantido para não quebrar histórico de desmarcar de outros emails.
                # O JS de limpeza vai sugerir a remoção ao usuário.
                logger.info(f"Remetente consolidado em prefixo: {entrada_remetente} (mesmo username já existia)")
        nova_remetentes_bloqueados = '\n'.join(remetentes_atual)

        # 1b) Remover da whitelist se estiver — blacklist e whitelist são exclusivos
        permitidos_atual = [l.strip().lower() for l in (cfg.get('remetentes_permitidos') or '').splitlines() if l.strip()]
        if email_remetente and email_remetente in permitidos_atual:
            permitidos_atual = [e for e in permitidos_atual if e != email_remetente]
        nova_remetentes_permitidos = '\n'.join(permitidos_atual)

        # 2) Extrair frases do assunto e adicionar à wordlist
        novas_frases = _extrair_frases_de_assunto_remetente(assunto, remetente)
        if not novas_frases and not entrada_remetente:
            return False

        existentes = _wordlist_para_lista(cfg.get('wordlist_extra') if cfg else None)
        seen = {e for e in existentes}
        for f in (novas_frases or []):
            if f not in seen:
                seen.add(f)
                existentes.append(f)

        # Limpar redundâncias após adicionar as novas frases
        existentes = _limpar_wordlist_redundante(existentes)
        nova_wordlist = _lista_para_wordlist(existentes)

        criar_tabela_se_nao_existe()
        ok = salvar_config(
            conta_principal_id,
            ativo=ativo,
            acao=acao,
            wordlist_extra=nova_wordlist,
            model_path=model_path,
            remetentes_bloqueados=nova_remetentes_bloqueados,
            remetentes_permitidos=nova_remetentes_permitidos,
            pasta_spam=cfg.get('pasta_spam') if cfg else None,
            heuristica_dominio_numerico=cfg.get('heuristica_dominio_numerico') if cfg else None,
            heuristica_reply_to=cfg.get('heuristica_reply_to') if cfg else None,
            heuristica_display_name=cfg.get('heuristica_display_name') if cfg else None,
        )
        if not ok:
            return False

        # 3) Guardar no histórico o que foi adicionado (frases + remetente) para remover ao desmarcar
        #    Usa a entrada efetivamente adicionada (pode ser prefix@ em vez de email exato)
        linhas_adicionadas = list(novas_frases) if novas_frases else []
        if entrada_remetente:
            linhas_adicionadas.append(PREFIXO_REMETENTE_GUARDADO + entrada_remetente)
        texto_adicionado = '\n'.join(linhas_adicionadas)
        from db_manager import EmailHistoryManager
        EmailHistoryManager.criar_tabela_se_nao_existe()
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute('''
                UPDATE historico_emails_sincronizados
                SET spam_wordlist_adicionada = %s
                WHERE id = %s AND conta_origem_id = %s
            ''', (texto_adicionado, historico_id, conta_origem_id))
        return True
    except Exception as e:
        logger.error(f"Erro ao adicionar palavras spam do email: {e}")
        return False


def remover_palavras_spam_do_email(historico_id: int, conta_origem_id: int, usuario_id: int) -> bool:
    """
    Quando o usuário desmarca um email como spam, remove da config do Spam Analyzer
    as frases e o remetente que tinham sido adicionados ao marcar (lidos de spam_wordlist_adicionada).
    Limpa a coluna spam_wordlist_adicionada no histórico.
    Retorna True se algo foi removido ou já estava vazio.
    """
    try:
        from db_manager import EmailHistoryManager
        EmailHistoryManager.criar_tabela_se_nao_existe()
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute('''
                SELECT h.spam_wordlist_adicionada, cp.id AS conta_principal_id
                FROM historico_emails_sincronizados h
                INNER JOIN contas_origem co ON co.id = h.conta_origem_id
                INNER JOIN contas_principais cp ON cp.id = co.conta_principal_id AND cp.usuario_id = %s
                WHERE h.id = %s AND h.conta_origem_id = %s
            ''', (usuario_id, historico_id, conta_origem_id))
            row = cursor.fetchone()
        if not row:
            return False

        texto = (row.get('spam_wordlist_adicionada') or '').strip()
        conta_principal_id = row['conta_principal_id']
        if not texto:
            return True  # nada foi adicionado na época, não há o que remover

        frases_a_remover = []
        email_remetente_remover = None
        for line in texto.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith(PREFIXO_REMETENTE_GUARDADO):
                email_remetente_remover = line[len(PREFIXO_REMETENTE_GUARDADO):].strip().lower()
            else:
                frases_a_remover.append(line.lower())

        cfg = get_config(conta_principal_id)
        if not cfg:
            _limpar_spam_wordlist_adicionada(historico_id, conta_origem_id)
            return True

        # Remover frases da wordlist
        existentes = _wordlist_para_lista(cfg.get('wordlist_extra') or None)
        remover_set = set(frases_a_remover)
        nova_wordlist_entradas = [e for e in existentes if e not in remover_set]
        nova_wordlist = _lista_para_wordlist(nova_wordlist_entradas)

        # Remover remetente dos bloqueados
        remetentes_atual = [l.strip().lower() for l in (cfg.get('remetentes_bloqueados') or '').splitlines() if l.strip()]
        if email_remetente_remover and email_remetente_remover in remetentes_atual:
            remetentes_atual = [e for e in remetentes_atual if e != email_remetente_remover]
        nova_remetentes_bloqueados = '\n'.join(remetentes_atual)

        ok = salvar_config(
            conta_principal_id,
            ativo=bool(cfg.get('ativo')),
            acao=cfg.get('acao') or ACAO_MARCAR_SPAM,
            wordlist_extra=nova_wordlist if nova_wordlist else '',
            model_path=cfg.get('model_path') or '',
            remetentes_bloqueados=nova_remetentes_bloqueados if nova_remetentes_bloqueados else '',
            remetentes_permitidos=cfg.get('remetentes_permitidos') or '',
            pasta_spam=cfg.get('pasta_spam')
        )
        _limpar_spam_wordlist_adicionada(historico_id, conta_origem_id)
        return ok
    except Exception as e:
        logger.error(f"Erro ao remover palavras spam do email: {e}")
        return False


def _limpar_spam_wordlist_adicionada(historico_id: int, conta_origem_id: int) -> None:
    """Limpa a coluna spam_wordlist_adicionada do histórico."""
    try:
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute('''
                UPDATE historico_emails_sincronizados
                SET spam_wordlist_adicionada = NULL
                WHERE id = %s AND conta_origem_id = %s
            ''', (historico_id, conta_origem_id))
    except Exception as e:
        logger.warning(f"Erro ao limpar spam_wordlist_adicionada: {e}")


def adicionar_remetente_whitelist(historico_id: int, conta_origem_id: int, usuario_id: int) -> bool:
    """
    Adiciona o remetente do email (histórico) à whitelist (remetentes_permitidos).
    Assim esse remetente nunca será tratado como spam. Retorna True se adicionado.
    """
    try:
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute('''
                SELECT h.remetente, cp.id AS conta_principal_id
                FROM historico_emails_sincronizados h
                INNER JOIN contas_origem co ON co.id = h.conta_origem_id
                INNER JOIN contas_principais cp ON cp.id = co.conta_principal_id AND cp.usuario_id = %s
                WHERE h.id = %s AND h.conta_origem_id = %s
            ''', (usuario_id, historico_id, conta_origem_id))
            row = cursor.fetchone()
        if not row:
            return False

        remetente = row.get('remetente') or ''
        conta_principal_id = row['conta_principal_id']
        email = _extrair_email_remetente(remetente)
        if not email:
            return False

        cfg = get_config(conta_principal_id)
        permitidos = [l.strip().lower() for l in (cfg.get('remetentes_permitidos') or '').splitlines() if l.strip()]
        if email in permitidos:
            # Já estava na whitelist; mesmo assim garante que não está na blacklist
            bloqueados = [l.strip().lower() for l in (cfg.get('remetentes_bloqueados') or '').splitlines() if l.strip()]
            if email in bloqueados:
                bloqueados = [e for e in bloqueados if e != email]
                nova_bloqueados = '\n'.join(bloqueados)
                criar_tabela_se_nao_existe()
                return salvar_config(
                    conta_principal_id,
                    ativo=bool(cfg.get('ativo')),
                    acao=cfg.get('acao') or ACAO_MARCAR_SPAM,
                    wordlist_extra=cfg.get('wordlist_extra') or '',
                    model_path=cfg.get('model_path') or '',
                    remetentes_bloqueados=nova_bloqueados or None,
                    remetentes_permitidos=cfg.get('remetentes_permitidos') or '',
                    pasta_spam=cfg.get('pasta_spam'),
                    heuristica_dominio_numerico=cfg.get('heuristica_dominio_numerico'),
                    heuristica_reply_to=cfg.get('heuristica_reply_to'),
                    heuristica_display_name=cfg.get('heuristica_display_name'),
                )
            return True  # já na whitelist e não estava na blacklist
        permitidos.append(email)
        nova_remetentes_permitidos = '\n'.join(permitidos)

        # Remover da blacklist (remetentes_bloqueados) se estiver — whitelist e blacklist são exclusivos
        bloqueados = [l.strip().lower() for l in (cfg.get('remetentes_bloqueados') or '').splitlines() if l.strip()]
        if email in bloqueados:
            bloqueados = [e for e in bloqueados if e != email]
        nova_remetentes_bloqueados = '\n'.join(bloqueados)

        ativo = bool(cfg.get('ativo')) if cfg else False
        acao = (cfg.get('acao') or ACAO_MARCAR_SPAM) if cfg else ACAO_MARCAR_SPAM
        wordlist_extra = (cfg.get('wordlist_extra') or '') if cfg else ''
        model_path = (cfg.get('model_path') or '') if cfg else ''

        criar_tabela_se_nao_existe()
        return salvar_config(
            conta_principal_id,
            ativo=ativo,
            acao=acao,
            wordlist_extra=wordlist_extra,
            model_path=model_path,
            remetentes_bloqueados=nova_remetentes_bloqueados or None,
            remetentes_permitidos=nova_remetentes_permitidos,
            pasta_spam=cfg.get('pasta_spam')
        )
    except Exception as e:
        logger.error(f"Erro ao adicionar remetente à whitelist: {e}")
        return False


def remover_remetente_do_filtro_spam(historico_id: int, conta_origem_id: int, usuario_id: int) -> bool:
    """
    Remove o remetente do email do filtro de spam: tira da lista de bloqueados (remetentes_bloqueados)
    e adiciona à whitelist (remetentes_permitidos). Assim esse remetente deixa de ser tratado como spam,
    seja porque estava bloqueado por email ou porque você quer que não seja mais bloqueado por conteúdo.
    Retorna True se a config foi atualizada.
    """
    try:
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute('''
                SELECT h.remetente, cp.id AS conta_principal_id
                FROM historico_emails_sincronizados h
                INNER JOIN contas_origem co ON co.id = h.conta_origem_id
                INNER JOIN contas_principais cp ON cp.id = co.conta_principal_id AND cp.usuario_id = %s
                WHERE h.id = %s AND h.conta_origem_id = %s
            ''', (usuario_id, historico_id, conta_origem_id))
            row = cursor.fetchone()
        if not row:
            return False

        remetente = row.get('remetente') or ''
        conta_principal_id = row['conta_principal_id']
        email = _extrair_email_remetente(remetente)
        if not email:
            return False

        cfg = get_config(conta_principal_id)
        if not cfg:
            return False

        # Remover da lista de bloqueados
        bloqueados = [l.strip().lower() for l in (cfg.get('remetentes_bloqueados') or '').splitlines() if l.strip()]
        if email in bloqueados:
            bloqueados = [e for e in bloqueados if e != email]
        nova_bloqueados = '\n'.join(bloqueados)

        # Adicionar à whitelist (remetentes permitidos)
        permitidos = [l.strip().lower() for l in (cfg.get('remetentes_permitidos') or '').splitlines() if l.strip()]
        if email not in permitidos:
            permitidos.append(email)
        nova_permitidos = '\n'.join(permitidos)

        ativo = bool(cfg.get('ativo'))
        acao = cfg.get('acao') or ACAO_MARCAR_SPAM
        wordlist_extra = cfg.get('wordlist_extra') or ''
        model_path = cfg.get('model_path') or ''
        pasta_spam = cfg.get('pasta_spam')

        criar_tabela_se_nao_existe()
        return salvar_config(
            conta_principal_id,
            ativo=ativo,
            acao=acao,
            wordlist_extra=wordlist_extra,
            model_path=model_path,
            remetentes_bloqueados=nova_bloqueados or None,
            remetentes_permitidos=nova_permitidos,
            pasta_spam=pasta_spam
        )
    except Exception as e:
        logger.error(f"Erro ao remover remetente do filtro spam: {e}")
        return False


def reply_to_mismatch(from_raw: str, reply_to_raw: str) -> bool:
    """
    Retorna True se Reply-To e From pertencem a domínios diferentes.
    Padrão clássico de phishing: remetente parece legítimo mas respostas
    vão para outro domínio controlado pelo atacante.
    """
    if not reply_to_raw or not reply_to_raw.strip():
        return False
    from_email = _extrair_email_remetente(from_raw)
    reply_to_email = _extrair_email_remetente(reply_to_raw)
    if not from_email or not reply_to_email:
        return False
    from_domain = from_email.split('@')[1].lower()
    reply_to_domain = reply_to_email.split('@')[1].lower()
    return from_domain != reply_to_domain


def display_name_spoofing(from_raw: str, dominios_gratuitos: List[str], palavras_institucionais: List[str]) -> bool:
    """
    Retorna True se o display name do remetente parece forjado. Dois critérios:
    1. Display name contém um endereço de email diferente do From real
       (ex: 'security@paypal.com <phisher@evil.com>')
    2. Display name contém palavras institucionais configuradas mas o email
       vem de um domínio gratuito configurado (ex: 'Bradesco <cobranca@gmail.com>')
    As listas dominios_gratuitos e palavras_institucionais vêm da config do banco.
    """
    if not from_raw:
        return False
    display = _extrair_display_name(from_raw)
    email = _extrair_email_remetente(from_raw)
    if not display or not email:
        return False

    # Critério 1: email embutido no display name diferente do From real (não depende de listas)
    embedded_match = re.search(r'[\w.+%-]+@[\w.-]+\.\w{2,}', display)
    if embedded_match and embedded_match.group(0).lower() != email.lower():
        return True

    # Critério 2: palavra institucional + domínio gratuito (depende de listas configuradas)
    if not dominios_gratuitos or not palavras_institucionais:
        return False
    domain = email.split('@')[1].lower() if '@' in email else ''
    if domain not in set(dominios_gratuitos):
        return False
    display_norm = _normalizar_acentos(display.lower())
    palavras_set = set(p.lower() for p in palavras_institucionais)
    palavras_norm_set = set(_normalizar_acentos(p.lower()) for p in palavras_institucionais)
    words = set(display.lower().split()) | set(display_norm.split())
    return bool(words & palavras_set) or bool(words & palavras_norm_set)


def dominio_numerico_suspeito(from_raw: str) -> bool:
    """
    Retorna True se o endereço de email apresenta padrões numéricos típicos de
    infraestruturas de spam/phishing em massa:

      1. SLD (domínio de segundo nível) termina em 1–2 dígitos
         ex: envboxd2.com, sendbox54.com, fluxonativo2.com, atrushx19.com
      2. Qualquer subdomínio (parte antes do SLD) contém 2 ou mais dígitos consecutivos
         ex: relatorios07g.escolapapel.shop, atendimento567b.luminel.makeup,
             sp195.urbanhaven.homes, adm629.twinway.motorcycles
      3. Nome de usuário (local part) contém 5 ou mais dígitos consecutivos
         ex: livia4331212@, contato32159@, faturadigital.minhaclaro0934842738@
      4. Nome de usuário com 50 ou mais caracteres (string de rastreamento/campanha)
         ex: faturadigital.minhaclaro07623421717842656667@despachopedidos.com
      5. Nome de usuário termina em sufixo aleatório com dígito logo após traço
         ex: pauloroberto-ijs2@, pedagio-aarao-4j@, amadeu-atendimento-x0v@,
             detran_placido-f4@, mrdigital-30ka@, noreply-spe-subq@

    Emails na whitelist (remetentes_permitidos) NUNCA chegam aqui — o chamador
    deve filtrar antes de invocar esta função.
    """
    email = _extrair_email_remetente(from_raw)
    if not email or '@' not in email:
        return False

    local, domain = email.split('@', 1)
    domain = domain.lower()
    parts = domain.split('.')

    if len(parts) < 2:
        return False

    # Detectar TLD duplo (ex: .com.br, .co.uk, .org.uk):
    # se o penúltimo componente tem ≤ 4 chars E o último ≤ 3 chars → TLD duplo
    if len(parts) >= 3 and len(parts[-2]) <= 4 and len(parts[-1]) <= 3:
        sld_idx = len(parts) - 3
    else:
        sld_idx = len(parts) - 2

    if sld_idx < 0:
        return False

    sld = parts[sld_idx]
    subdomains = parts[:sld_idx]

    # ── Regra 1: SLD termina em 1–2 dígitos ─────────────────────────────────
    # Limite de 2 dígitos evita falsos positivos em domínios como office365.com
    if re.search(r'\d{1,2}$', sld):
        logger.debug(f"dominio_numerico_suspeito R1: SLD '{sld}' termina em dígito(s) ({email})")
        return True

    # ── Regra 2: Subdomínio com 2+ dígitos consecutivos ──────────────────────
    for sub in subdomains:
        if re.search(r'\d{2,}', sub):
            logger.debug(f"dominio_numerico_suspeito R2: subdomínio '{sub}' com dígitos ({email})")
            return True

    # ── Regra 3: Username com 5+ dígitos consecutivos ────────────────────────
    if re.search(r'\d{5,}', local):
        logger.debug(f"dominio_numerico_suspeito R3: username com dígitos consecutivos ({email})")
        return True

    # ── Regra 4: Username muito longo (≥ 50 chars) ───────────────────────────
    if len(local) >= 50:
        logger.debug(f"dominio_numerico_suspeito R4: username longo ({len(local)} chars) ({email})")
        return True

    # ── Regra 5: Username com sufixo aleatório contendo dígito após traço ─────
    # Padrão: termina em -SUFIXO onde SUFIXO tem 2–6 chars com pelo menos 1 dígito
    m = re.search(r'-([a-z0-9]{2,6})$', local)
    if m and re.search(r'\d', m.group(1)):
        logger.debug(f"dominio_numerico_suspeito R5: sufixo aleatório '-{m.group(1)}' ({email})")
        return True

    return False


def salvar_config(
    conta_principal_id: int,
    ativo: bool,
    acao: str,
    wordlist_extra: Optional[str] = None,
    model_path: Optional[str] = None,
    remetentes_bloqueados: Optional[str] = None,
    remetentes_permitidos: Optional[str] = None,
    pasta_spam: Optional[str] = None,
    dominios_gratuitos: Optional[str] = None,
    palavras_institucionais: Optional[str] = None,
    heuristica_dominio_numerico: Optional[bool] = None,
    heuristica_reply_to: Optional[bool] = None,
    heuristica_display_name: Optional[bool] = None,
) -> bool:
    """
    Salva ou atualiza a configuração do Spam Analyzer por conta.
    Heurísticas None = herdar do nível superior (utilizador/global).
    """
    if acao not in (ACAO_MARCAR_SPAM, ACAO_PULAR_INBOX):
        acao = ACAO_MARCAR_SPAM
    wordlist_extra = (wordlist_extra or '').strip() or None
    if wordlist_extra:
        entradas = _wordlist_para_lista(wordlist_extra)
        entradas = _limpar_wordlist_redundante(entradas)
        wordlist_extra = _lista_para_wordlist(entradas) or None
    model_path = (model_path or '').strip() or None
    remetentes_bloqueados = (remetentes_bloqueados or '').strip() or None
    remetentes_permitidos = (remetentes_permitidos or '').strip() or None
    pasta_spam = (pasta_spam or '').strip() or None
    dominios_gratuitos = (dominios_gratuitos or '').strip() or None
    palavras_institucionais = (palavras_institucionais or '').strip() or None
    try:
        criar_tabela_se_nao_existe()
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute('''
                INSERT INTO config_spam_analyzer (conta_principal_id, ativo, acao, wordlist_extra, model_path,
                    remetentes_bloqueados, remetentes_permitidos, pasta_spam, dominios_gratuitos,
                    palavras_institucionais, heuristica_dominio_numerico,
                    heuristica_reply_to, heuristica_display_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    ativo = VALUES(ativo),
                    acao = VALUES(acao),
                    wordlist_extra = VALUES(wordlist_extra),
                    model_path = VALUES(model_path),
                    remetentes_bloqueados = VALUES(remetentes_bloqueados),
                    remetentes_permitidos = VALUES(remetentes_permitidos),
                    pasta_spam = VALUES(pasta_spam),
                    dominios_gratuitos = VALUES(dominios_gratuitos),
                    palavras_institucionais = VALUES(palavras_institucionais),
                    heuristica_dominio_numerico = VALUES(heuristica_dominio_numerico),
                    heuristica_reply_to = VALUES(heuristica_reply_to),
                    heuristica_display_name = VALUES(heuristica_display_name)
            ''', (
                conta_principal_id, bool(ativo), acao, wordlist_extra, model_path,
                remetentes_bloqueados, remetentes_permitidos, pasta_spam, dominios_gratuitos,
                palavras_institucionais,
                None if heuristica_dominio_numerico is None else int(heuristica_dominio_numerico),
                None if heuristica_reply_to is None else int(heuristica_reply_to),
                None if heuristica_display_name is None else int(heuristica_display_name),
            ))
            logger.info(f"Config Spam Analyzer salva: conta_principal_id={conta_principal_id} ativo={ativo} acao={acao}")
            return True
    except Exception as e:
        logger.error(f"Erro ao salvar config spam analyzer: {e}")
        return False


# ════════════════════════════════════════════════════════════════════════════
# CONFIG SPAM GLOBAL (nível instância / admin)
# ════════════════════════════════════════════════════════════════════════════

_CONFIG_GLOBAL_CACHE: Optional[Dict[str, Any]] = None


def _criar_tabela_config_spam_global():
    """Cria a tabela de configuração global de spam se não existir."""
    try:
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS config_spam_global (
                    id TINYINT UNSIGNED PRIMARY KEY DEFAULT 1,
                    ativo_por_defeito TINYINT(1) NOT NULL DEFAULT 0,
                    acao_por_defeito ENUM('mark_spam','skip_inbox') NOT NULL DEFAULT 'mark_spam',
                    pasta_spam_padrao VARCHAR(255) DEFAULT NULL,
                    wordlist_global MEDIUMTEXT DEFAULT NULL,
                    remetentes_bloqueados MEDIUMTEXT DEFAULT NULL,
                    remetentes_permitidos MEDIUMTEXT DEFAULT NULL,
                    dominios_gratuitos MEDIUMTEXT DEFAULT NULL,
                    palavras_institucionais MEDIUMTEXT DEFAULT NULL,
                    heuristica_reply_to TINYINT(1) NOT NULL DEFAULT 1,
                    heuristica_display_name TINYINT(1) NOT NULL DEFAULT 1,
                    heuristica_dominio_numerico TINYINT(1) NOT NULL DEFAULT 1,
                    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')
            # Garantir que existe sempre uma linha (id=1)
            cursor.execute('''
                INSERT IGNORE INTO config_spam_global (id) VALUES (1)
            ''')
            logger.info("Tabela config_spam_global verificada/criada")
    except Exception as e:
        logger.error(f"Erro ao criar tabela config_spam_global: {e}")
        raise


def get_config_global(force_reload: bool = False) -> Dict[str, Any]:
    """Retorna a configuração global de spam (nível admin). Usa cache em memória."""
    global _CONFIG_GLOBAL_CACHE
    if _CONFIG_GLOBAL_CACHE is not None and not force_reload:
        return _CONFIG_GLOBAL_CACHE
    try:
        _criar_tabela_config_spam_global()
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute('SELECT * FROM config_spam_global WHERE id = 1')
            row = cursor.fetchone() or {}
        _CONFIG_GLOBAL_CACHE = {
            'ativo_por_defeito': bool(row.get('ativo_por_defeito', False)),
            'acao_por_defeito': row.get('acao_por_defeito') or ACAO_MARCAR_SPAM,
            'pasta_spam_padrao': (row.get('pasta_spam_padrao') or '').strip() or None,
            'wordlist_global': row.get('wordlist_global') or '',
            'remetentes_bloqueados': row.get('remetentes_bloqueados') or '',
            'remetentes_permitidos': row.get('remetentes_permitidos') or '',
            'dominios_gratuitos': row.get('dominios_gratuitos') or '',
            'palavras_institucionais': row.get('palavras_institucionais') or '',
            'heuristica_reply_to': bool(row.get('heuristica_reply_to', True)),
            'heuristica_display_name': bool(row.get('heuristica_display_name', True)),
            'heuristica_dominio_numerico': bool(row.get('heuristica_dominio_numerico', True)),
        }
        return _CONFIG_GLOBAL_CACHE
    except Exception as e:
        logger.error(f"Erro ao buscar config spam global: {e}")
        return {
            'ativo_por_defeito': False,
            'acao_por_defeito': ACAO_MARCAR_SPAM,
            'pasta_spam_padrao': None,
            'wordlist_global': '',
            'remetentes_bloqueados': '',
            'remetentes_permitidos': '',
            'dominios_gratuitos': '',
            'palavras_institucionais': '',
            'heuristica_reply_to': True,
            'heuristica_display_name': True,
            'heuristica_dominio_numerico': True,
        }


def salvar_config_global(
    ativo_por_defeito: bool = False,
    acao_por_defeito: str = ACAO_MARCAR_SPAM,
    pasta_spam_padrao: Optional[str] = None,
    wordlist_global: Optional[str] = None,
    remetentes_bloqueados: Optional[str] = None,
    remetentes_permitidos: Optional[str] = None,
    dominios_gratuitos: Optional[str] = None,
    palavras_institucionais: Optional[str] = None,
    heuristica_reply_to: bool = True,
    heuristica_display_name: bool = True,
    heuristica_dominio_numerico: bool = True,
) -> bool:
    global _CONFIG_GLOBAL_CACHE
    if acao_por_defeito not in (ACAO_MARCAR_SPAM, ACAO_PULAR_INBOX):
        acao_por_defeito = ACAO_MARCAR_SPAM
    wordlist_global = (wordlist_global or '').strip() or None
    if wordlist_global:
        entradas = _wordlist_para_lista(wordlist_global)
        entradas = _limpar_wordlist_redundante(entradas)
        wordlist_global = _lista_para_wordlist(entradas) or None
    remetentes_bloqueados = (remetentes_bloqueados or '').strip() or None
    remetentes_permitidos = (remetentes_permitidos or '').strip() or None
    dominios_gratuitos = (dominios_gratuitos or '').strip() or None
    palavras_institucionais = (palavras_institucionais or '').strip() or None
    pasta_spam_padrao = (pasta_spam_padrao or '').strip() or None
    try:
        _criar_tabela_config_spam_global()
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute('''
                INSERT INTO config_spam_global (
                    id, ativo_por_defeito, acao_por_defeito, pasta_spam_padrao,
                    wordlist_global, remetentes_bloqueados, remetentes_permitidos,
                    dominios_gratuitos, palavras_institucionais,
                    heuristica_reply_to, heuristica_display_name, heuristica_dominio_numerico
                ) VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    ativo_por_defeito = VALUES(ativo_por_defeito),
                    acao_por_defeito = VALUES(acao_por_defeito),
                    pasta_spam_padrao = VALUES(pasta_spam_padrao),
                    wordlist_global = VALUES(wordlist_global),
                    remetentes_bloqueados = VALUES(remetentes_bloqueados),
                    remetentes_permitidos = VALUES(remetentes_permitidos),
                    dominios_gratuitos = VALUES(dominios_gratuitos),
                    palavras_institucionais = VALUES(palavras_institucionais),
                    heuristica_reply_to = VALUES(heuristica_reply_to),
                    heuristica_display_name = VALUES(heuristica_display_name),
                    heuristica_dominio_numerico = VALUES(heuristica_dominio_numerico)
            ''', (
                int(ativo_por_defeito), acao_por_defeito, pasta_spam_padrao,
                wordlist_global, remetentes_bloqueados, remetentes_permitidos,
                dominios_gratuitos, palavras_institucionais,
                int(heuristica_reply_to), int(heuristica_display_name), int(heuristica_dominio_numerico),
            ))
        _CONFIG_GLOBAL_CACHE = None  # invalidar cache
        logger.info("Config spam global salva")
        return True
    except Exception as e:
        logger.error(f"Erro ao salvar config spam global: {e}")
        return False


# ════════════════════════════════════════════════════════════════════════════
# CONFIG SPAM POR UTILIZADOR (nível médio)
# ════════════════════════════════════════════════════════════════════════════

def _criar_tabela_config_spam_usuario():
    """Cria a tabela de configuração de spam por utilizador se não existir."""
    try:
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS config_spam_usuario (
                    usuario_id INT PRIMARY KEY,
                    acao ENUM('mark_spam', 'skip_inbox') DEFAULT NULL,
                    pasta_spam VARCHAR(255) DEFAULT NULL,
                    wordlist_extra MEDIUMTEXT DEFAULT NULL,
                    remetentes_bloqueados MEDIUMTEXT DEFAULT NULL,
                    remetentes_permitidos MEDIUMTEXT DEFAULT NULL,
                    heuristica_reply_to TINYINT(1) DEFAULT NULL,
                    heuristica_display_name TINYINT(1) DEFAULT NULL,
                    heuristica_dominio_numerico TINYINT(1) DEFAULT NULL,
                    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')
            _ensure_config_spam_usuario_columns(cursor)
            logger.info("Tabela config_spam_usuario verificada/criada")
    except Exception as e:
        logger.error(f"Erro ao criar tabela config_spam_usuario: {e}")
        raise


def _ensure_config_spam_usuario_columns(cursor):
    """
    Garante colunas novas no nível utilizador (migração incremental).
    Usa ALTER direto para evitar depender de information_schema.
    """
    try:
        cursor.execute("ALTER TABLE config_spam_usuario ADD COLUMN acao ENUM('mark_spam','skip_inbox') DEFAULT NULL")
    except Exception as e:
        err = str(e).lower()
        if '1060' in str(e) or 'duplicate column' in err:
            pass
        else:
            logger.warning(f"Coluna acao (config_spam_usuario): {e}")
    try:
        cursor.execute("ALTER TABLE config_spam_usuario ADD COLUMN pasta_spam VARCHAR(255) DEFAULT NULL")
    except Exception as e:
        err = str(e).lower()
        if '1060' in str(e) or 'duplicate column' in err:
            pass
        else:
            logger.warning(f"Coluna pasta_spam (config_spam_usuario): {e}")


def get_config_usuario(usuario_id: int) -> Optional[Dict[str, Any]]:
    """Retorna configuração de spam do utilizador. None se não configurado."""
    try:
        _criar_tabela_config_spam_usuario()
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute('SELECT * FROM config_spam_usuario WHERE usuario_id = %s', (usuario_id,))
            row = cursor.fetchone()
        if not row:
            return None
        return {
            'usuario_id': row['usuario_id'],
            'acao': row.get('acao') or None,                 # None = herdar (conta/global)
            'pasta_spam': row.get('pasta_spam') or None,     # None = herdar (conta/global)
            'wordlist_extra': row.get('wordlist_extra') or '',
            'remetentes_bloqueados': row.get('remetentes_bloqueados') or '',
            'remetentes_permitidos': row.get('remetentes_permitidos') or '',
            'heuristica_reply_to': row.get('heuristica_reply_to'),        # None = herdar
            'heuristica_display_name': row.get('heuristica_display_name'),
            'heuristica_dominio_numerico': row.get('heuristica_dominio_numerico'),
        }
    except Exception as e:
        logger.error(f"Erro ao buscar config spam utilizador {usuario_id}: {e}")
        return None


def salvar_config_usuario(
    usuario_id: int,
    acao: Optional[str] = None,
    pasta_spam: Optional[str] = None,
    wordlist_extra: Optional[str] = None,
    remetentes_bloqueados: Optional[str] = None,
    remetentes_permitidos: Optional[str] = None,
    heuristica_reply_to: Optional[bool] = None,
    heuristica_display_name: Optional[bool] = None,
    heuristica_dominio_numerico: Optional[bool] = None,
) -> bool:
    """Salva ou actualiza a configuração de spam do utilizador. Heurísticas None = herdar global."""
    if acao is not None:
        acao = (acao or '').strip() or None
        if acao not in (ACAO_MARCAR_SPAM, ACAO_PULAR_INBOX):
            acao = None
    pasta_spam = (pasta_spam or '').strip() or None
    wordlist_extra = (wordlist_extra or '').strip() or None
    if wordlist_extra:
        entradas = _wordlist_para_lista(wordlist_extra)
        entradas = _limpar_wordlist_redundante(entradas)
        wordlist_extra = _lista_para_wordlist(entradas) or None
    remetentes_bloqueados = (remetentes_bloqueados or '').strip() or None
    remetentes_permitidos = (remetentes_permitidos or '').strip() or None
    try:
        _criar_tabela_config_spam_usuario()
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute('''
                INSERT INTO config_spam_usuario (
                    usuario_id, acao, pasta_spam,
                    wordlist_extra, remetentes_bloqueados, remetentes_permitidos,
                    heuristica_reply_to, heuristica_display_name, heuristica_dominio_numerico
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    acao = VALUES(acao),
                    pasta_spam = VALUES(pasta_spam),
                    wordlist_extra = VALUES(wordlist_extra),
                    remetentes_bloqueados = VALUES(remetentes_bloqueados),
                    remetentes_permitidos = VALUES(remetentes_permitidos),
                    heuristica_reply_to = VALUES(heuristica_reply_to),
                    heuristica_display_name = VALUES(heuristica_display_name),
                    heuristica_dominio_numerico = VALUES(heuristica_dominio_numerico)
            ''', (
                usuario_id, acao, pasta_spam,
                wordlist_extra, remetentes_bloqueados, remetentes_permitidos,
                None if heuristica_reply_to is None else int(heuristica_reply_to),
                None if heuristica_display_name is None else int(heuristica_display_name),
                None if heuristica_dominio_numerico is None else int(heuristica_dominio_numerico),
            ))
        logger.info(f"Config spam utilizador salva: usuario_id={usuario_id}")
        return True
    except Exception as e:
        logger.error(f"Erro ao salvar config spam utilizador {usuario_id}: {e}")
        return False


def limpar_configs_usuario_redundantes_com_global() -> int:
    """
    Remove do nível usuário (config_spam_usuario) entradas que já existem no nível global,
    sem afetar regras exclusivas do usuário. Útil para enxugar storage após promover listas
    para o global.

    Retorna: quantidade de usuários atualizados.
    """
    try:
        global_cfg = get_config_global(force_reload=True)
        global_word = set(_wordlist_para_lista(global_cfg.get('wordlist_global') or ''))
        global_white = set([l.strip().lower() for l in (global_cfg.get('remetentes_permitidos') or '').splitlines() if l.strip()])
        global_black = set([l.strip().lower() for l in (global_cfg.get('remetentes_bloqueados') or '').splitlines() if l.strip()])

        _criar_tabela_config_spam_usuario()
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute('SELECT usuario_id, wordlist_extra, remetentes_bloqueados, remetentes_permitidos FROM config_spam_usuario')
            rows = cursor.fetchall() or []

        atualizados = 0
        for r in rows:
            uid = r['usuario_id']
            wl = _wordlist_para_lista(r.get('wordlist_extra') or '')
            wl2 = [e for e in wl if e not in global_word]
            wl2 = _limpar_wordlist_redundante(wl2)
            wl_txt = _lista_para_wordlist(wl2) or None

            white = [l.strip().lower() for l in (r.get('remetentes_permitidos') or '').splitlines() if l.strip()]
            white2 = [e for e in white if e not in global_white]
            white_txt = '\n'.join(dict.fromkeys(white2)) or None

            black = [l.strip().lower() for l in (r.get('remetentes_bloqueados') or '').splitlines() if l.strip()]
            black2 = [e for e in black if e not in global_black]
            black_txt = '\n'.join(dict.fromkeys(black2)) or None

            if (wl_txt or None) != ((r.get('wordlist_extra') or '').strip() or None) or \
               (white_txt or None) != ((r.get('remetentes_permitidos') or '').strip() or None) or \
               (black_txt or None) != ((r.get('remetentes_bloqueados') or '').strip() or None):
                with DatabaseManager.get_cursor() as cursor:
                    cursor.execute('''
                        UPDATE config_spam_usuario
                        SET wordlist_extra = %s,
                            remetentes_bloqueados = %s,
                            remetentes_permitidos = %s
                        WHERE usuario_id = %s
                    ''', (wl_txt, black_txt, white_txt, uid))
                atualizados += 1
        return atualizados
    except Exception as e:
        logger.error(f"Erro ao limpar configs de usuário redundantes: {e}")
        return 0


# ════════════════════════════════════════════════════════════════════════════
# MERGE: combina os 3 níveis para a sincronização
# ════════════════════════════════════════════════════════════════════════════

def _merge_listas(*textos: Optional[str]) -> str:
    """Une múltiplas listas de linhas, removendo duplicatas e linhas vazias."""
    seen: set = set()
    resultado: List[str] = []
    for texto in textos:
        for linha in (texto or '').splitlines():
            linha = linha.strip().lower()
            if linha and linha not in seen:
                seen.add(linha)
                resultado.append(linha)
    return '\n'.join(resultado)


def _resolve_heuristica(key: str, account_cfg: Optional[Dict], user_cfg: Optional[Dict], global_cfg: Dict, default: bool = True) -> bool:
    """Resolve o valor de uma heurística seguindo a hierarquia conta > utilizador > global."""
    if account_cfg:
        val = account_cfg.get(key)
        if val is not None:
            return bool(val)
    if user_cfg:
        val = user_cfg.get(key)
        if val is not None:
            return bool(val)
    return bool(global_cfg.get(key, default))


def get_config_merged_para_sync(conta_principal_id: int, usuario_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """
    Combina as configurações de spam dos 3 níveis (global + utilizador + conta)
    para uso na sincronização. Retorna None se o spam não estiver activo na conta.

    Regras de merge:
    - Whitelist: união global ∪ utilizador ∪ conta — qualquer match = não é spam
    - Blacklist: união global ∪ utilizador ∪ conta — qualquer match = spam (após whitelist)
    - Wordlist: união global ∪ utilizador ∪ conta
    - Domínios gratuitos / palavras inst.: união global ∪ conta
    - Heurísticas: conta > utilizador > global (NULL = herdar)
    - Ação / pasta spam: conta > utilizador > global
    """
    account_cfg = get_config(conta_principal_id)
    if not account_cfg or not account_cfg.get('ativo'):
        return None

    global_cfg = get_config_global()
    user_cfg = get_config_usuario(usuario_id) if usuario_id else None

    merged = {
        'ativo': True,
        'acao': (
            account_cfg.get('acao')
            or (user_cfg.get('acao') if user_cfg else None)
            or global_cfg.get('acao_por_defeito')
            or ACAO_MARCAR_SPAM
        ),
        'pasta_spam': (
            account_cfg.get('pasta_spam')
            or (user_cfg.get('pasta_spam') if user_cfg else None)
            or global_cfg.get('pasta_spam_padrao')
            or None
        ),
        'model_path': account_cfg.get('model_path') or '',

        # Listas — union dos três níveis
        'remetentes_permitidos': _merge_listas(
            global_cfg.get('remetentes_permitidos'),
            user_cfg.get('remetentes_permitidos') if user_cfg else None,
            account_cfg.get('remetentes_permitidos'),
        ),
        'remetentes_bloqueados': _merge_listas(
            global_cfg.get('remetentes_bloqueados'),
            user_cfg.get('remetentes_bloqueados') if user_cfg else None,
            account_cfg.get('remetentes_bloqueados'),
        ),
        'wordlist_extra': _merge_listas(
            global_cfg.get('wordlist_global'),
            user_cfg.get('wordlist_extra') if user_cfg else None,
            account_cfg.get('wordlist_extra'),
        ),
        # Definições de sistema — geridas exclusivamente pelo admin no nível global
        'dominios_gratuitos': global_cfg.get('dominios_gratuitos') or '',
        'palavras_institucionais': global_cfg.get('palavras_institucionais') or '',

        # Heurísticas — hierarquia conta > utilizador > global
        'heuristica_reply_to': _resolve_heuristica('heuristica_reply_to', account_cfg, user_cfg, global_cfg, True),
        'heuristica_display_name': _resolve_heuristica('heuristica_display_name', account_cfg, user_cfg, global_cfg, True),
        'heuristica_dominio_numerico': _resolve_heuristica('heuristica_dominio_numerico', account_cfg, user_cfg, global_cfg, True),
    }
    return merged
