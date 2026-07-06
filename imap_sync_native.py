"""
Módulo de sincronização IMAP nativo (sem dependência de imapsync externo)
Copia emails de uma conta de origem para uma conta de destino via Python puro.
Funcionalidades:
  - Copia mensagens novas (evita duplicatas via Message-ID)
  - Marca como lido na origem após copiar
  - Deleta mensagens com mais de X dias na origem (opcional)
  - Preserva flags e datas originais
  - Mapeia pastas automaticamente
"""

import hashlib
import imaplib
import email
import email.header
import logging
import time
import socket
import ssl
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from config import Config

logger = logging.getLogger(__name__)

# Mapeamento de nomes de pastas comuns entre provedores
FOLDER_MAP = {
    'sent': [
        'Sent', 'Sent Items', 'Sent Messages', 'Enviados', 
        '[Gmail]/Sent Mail', 'INBOX.Sent',
        'Itens Enviados',  # Outlook PT-BR
        'Mensagens Enviadas',  # Thunderbird PT-BR
        'Éléments envoyés',  # Francês
        'Gesendete Elemente',  # Alemão
    ],
    'trash': [
        'Trash', 'Deleted Items', 'Deleted Messages', 'Lixeira',
        '[Gmail]/Trash', 'INBOX.Trash',
        'Itens Excluídos',  # Outlook PT-BR
        'Corbeille',  # Francês
        'Papierkorb',  # Alemão
        'Deleted',  # Algumas contas
    ],
    'spam': [
        'Spam', 'Junk', 'Junk Email', 'Lixo Eletrônico',
        '[Gmail]/Spam', 'INBOX.Spam',
        'Lixo Eletrônico',  # Outlook PT-BR
        'Courrier indésirable',  # Francês
        'Junk-E-Mail',  # Alemão
    ],
    'drafts': [
        'Drafts', 'Draft', 'Rascunhos',
        '[Gmail]/Drafts', 'INBOX.Drafts',
        'Brouillons',  # Francês
        'Entwürfe',  # Alemão
    ],
    'archive': [
        'Archive', 'All Mail', '[Gmail]/All Mail', 'Arquivo',
        'Arquivar',  # Variação PT-BR
        'Archives',  # Francês
        'Archiv',  # Alemão
    ],
    'inbox': [
        'INBOX', 'Inbox', 'Caixa de Entrada',
        'Boîte de réception',  # Francês
        'Posteingang',  # Alemão
    ],
}


def decode_header_value(value: str) -> str:
    """Decodifica cabeçalhos MIME que podem estar em diferentes encodings."""
    if not value:
        return ""
    parts = email.header.decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            try:
                decoded.append(part.decode(charset or 'utf-8', errors='replace'))
            except (LookupError, UnicodeDecodeError):
                decoded.append(part.decode('latin-1', errors='replace'))
        else:
            decoded.append(str(part))
    return " ".join(decoded)


def connect_imap(servidor: str, porta: int, email_addr: str, senha: str, ssl: bool) -> imaplib.IMAP4:
    """Cria e retorna uma conexão IMAP autenticada."""
    try:
        socket.setdefaulttimeout(Config.IMAP_SOCKET_TIMEOUT)
        
        if ssl:
            # CORREÇÃO: Removido o parâmetro timeout que causava erro
            conn = imaplib.IMAP4_SSL(servidor, porta)
        else:
            conn = imaplib.IMAP4(servidor, porta)
            conn.starttls() if hasattr(conn, 'starttls') else None

        conn.login(email_addr, senha)
        logger.debug(f"Conectado com sucesso em {servidor}:{porta} como {email_addr}")
        return conn
    except imaplib.IMAP4.error as e:
        raise ConnectionError(f"Falha na autenticação IMAP ({servidor}:{porta}, {email_addr}): {e}")
    except OSError as e:
        raise ConnectionError(f"Falha na conexão com {servidor}:{porta}: {e}")


def _imap_bytes_to_str(value) -> str:
    """Converte bytes/str/int de respostas IMAP para str com segurança."""
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    if isinstance(value, str):
        return value
    return str(value)


def _imap_uid_str(uid) -> str:
    """Normaliza UID IMAP (bytes, str ou int) para string."""
    if uid is None:
        return ''
    if isinstance(uid, bytes):
        return uid.decode('ascii', errors='replace').strip()
    return str(uid).strip()


def _extrair_raw_email_fetch(fetch_data) -> Optional[bytes]:
    """Extrai o corpo RFC822 da resposta UID FETCH (vários formatos de servidor)."""
    if not fetch_data:
        return None
    melhor = None
    for item in fetch_data:
        if isinstance(item, tuple) and len(item) >= 2:
            body = item[1]
            if isinstance(body, (bytes, bytearray)) and len(body) > 0:
                b = bytes(body)
                if melhor is None or len(b) > len(melhor):
                    melhor = b
    if melhor:
        return melhor
    for item in fetch_data:
        if isinstance(item, (bytes, bytearray)):
            b = bytes(item)
            if b.strip() in (b')', b''):
                continue
            if b'RFC822' in b[:120] or b'UID' in b[:60]:
                continue
            if melhor is None or len(b) > len(melhor):
                melhor = b
    return melhor


def _imap_quote(value: str) -> str:
    s = (value or '').replace('\\', '\\\\').replace('"', '\\"')
    return f'"{s}"'


def _normalizar_message_id_busca(message_id: str) -> str:
    mid = (message_id or '').strip()
    if not mid:
        return ''
    if not mid.startswith('<'):
        mid = f'<{mid}>'
    return mid


def resolver_uid_mensagem(
    conn: imaplib.IMAP4,
    uid_armazenado,
    message_id: str = None,
    assunto: str = None,
    remetente: str = None,
) -> Optional[str]:
    """
    Resolve o UID IMAP atual de uma mensagem.
    Prioriza Message-ID, depois assunto/remetente, por último o UID armazenado.
    """
    def _uids_da_busca(*criteria):
        try:
            typ, data = conn.uid('SEARCH', None, *criteria)
            if typ == 'OK' and data and data[0]:
                found = data[0].split()
                if found:
                    return _imap_uid_str(found[-1])
        except Exception as e:
            logger.debug(f"UID SEARCH {criteria} falhou: {e}")
        return None

    if message_id:
        mid = _normalizar_message_id_busca(message_id)
        if mid:
            uid = _uids_da_busca('HEADER', 'Message-ID', mid)
            if uid:
                return uid

    subj = (assunto or '').strip()
    if subj:
        uid = _uids_da_busca('HEADER', 'SUBJECT', _imap_quote(subj))
        if uid:
            return uid
        # Palavra-chave estável (ex.: D21_2, Backup Sistema)
        for token in subj.replace('[', ' ').replace(']', ' ').replace(':', ' ').split():
            if len(token) >= 4 and token not in ('Backup', 'Sistema', 'backup'):
                uid = _uids_da_busca('TEXT', token)
                if uid:
                    return uid

    rem = (remetente or '').strip()
    if rem:
        import re
        from email.utils import parseaddr
        _, email_addr = parseaddr(rem)
        email_addr = (email_addr or rem).strip().lower()
        if '@' in email_addr and subj:
            uid = _uids_da_busca('HEADER', 'FROM', _imap_quote(email_addr), 'HEADER', 'SUBJECT', _imap_quote(subj))
            if uid:
                return uid

    uid_str = str(uid_armazenado or '').strip()
    if not uid_str:
        return None
    try:
        typ, data = conn.uid('FETCH', uid_str, '(FLAGS)')
        if typ == 'OK' and data and data[0] is not None:
            return uid_str
    except Exception as e:
        logger.debug(f"UID FETCH falhou para {uid_str}: {e}")
    return None


def deletar_uids_origem(
    conn: imaplib.IMAP4,
    folder: str,
    uids: list,
    mensagens: list = None,
) -> dict:
    """
    Marca UIDs como \\Deleted na pasta de origem e executa EXPUNGE.
    mensagens: opcional, lista de dict com uid_origem e message_id para resolver UIDs atuais.
    Retorna {'ok': bool, 'deletadas': int, 'erro': str|None, 'nao_encontradas': list}.
    """
    try:
        typ, _ = conn.select(f'"{folder}"', readonly=False)
        if typ != 'OK':
            return {'ok': False, 'deletadas': 0, 'erro': f'Não foi possível abrir pasta {folder}', 'nao_encontradas': []}

        resolved = []
        nao_encontradas = []

        if mensagens:
            itens = mensagens
        else:
            itens = [{'uid_origem': u, 'message_id': None} for u in (uids or [])]

        for item in itens:
            uid_atual = resolver_uid_mensagem(
                conn,
                item.get('uid_origem'),
                item.get('message_id'),
                assunto=item.get('assunto'),
                remetente=item.get('remetente'),
            )
            if uid_atual:
                resolved.append(uid_atual)
            else:
                nao_encontradas.append(str(item.get('uid_origem') or ''))

        resolved = list(dict.fromkeys(resolved))
        if not resolved:
            return {
                'ok': False,
                'deletadas': 0,
                'erro': 'Nenhuma mensagem encontrada na origem (pode já ter sido removida).',
                'nao_encontradas': nao_encontradas,
            }

        uid_list = ','.join(resolved)
        typ, _ = conn.uid('STORE', uid_list, '+FLAGS', '\\Deleted')
        if typ != 'OK':
            return {
                'ok': False,
                'deletadas': 0,
                'erro': f'Falha ao marcar mensagens para exclusão (UID STORE)',
                'nao_encontradas': nao_encontradas,
            }
        conn.expunge()
        logger.info(f"Deletadas na origem ({folder}): {len(resolved)} mensagem(ns)")
        out = {'ok': True, 'deletadas': len(resolved), 'erro': None, 'nao_encontradas': nao_encontradas}
        if nao_encontradas:
            out['aviso'] = f'{len(nao_encontradas)} mensagem(ns) não encontrada(s) na origem.'
        return out
    except Exception as e:
        logger.error(f"Erro ao deletar UIDs na origem ({folder}): {e}")
        return {'ok': False, 'deletadas': 0, 'erro': str(e), 'nao_encontradas': []}


def list_folders(conn: imaplib.IMAP4) -> list:
    """Lista todas as pastas disponíveis na conta IMAP."""
    folders = []
    try:
        status, data = conn.list()
        if status != 'OK' or not data:
            logger.warning("Falha ao listar pastas ou lista vazia")
            return folders
            
        for item in data:
            if item is None:
                continue
                
            if isinstance(item, bytes):
                line = item.decode('utf-8', errors='replace')
                
                # Formato típico IMAP LIST:
                # (\HasNoChildren) "." "INBOX"
                # (\HasChildren \Noselect) "/" "[Gmail]"
                # () NIL "Trash"
                
                import re
                
                # Método 1: Extrair todas as strings entre aspas
                quoted_strings = re.findall(r'"([^"]+)"', line)
                
                if len(quoted_strings) >= 2:
                    # O último item entre aspas é o nome da pasta
                    folder_name = quoted_strings[-1].strip()
                    
                elif len(quoted_strings) == 1:
                    # Apenas um item entre aspas, pode ser o nome
                    folder_name = quoted_strings[0].strip()
                    
                else:
                    # Sem aspas, tentar extrair da última parte
                    # Exemplo: (\HasNoChildren) NIL INBOX
                    parts = line.split()
                    if len(parts) >= 3:
                        folder_name = parts[-1].strip()
                    else:
                        continue
                
                # Validar o nome da pasta
                if folder_name and len(folder_name) > 0:
                    # Ignorar delimitadores comuns e strings vazias
                    if folder_name not in ['.', '/', '\\', ' ', 'NIL', '..', '...']:
                        # Ignorar pastas que começam com ponto (ocultas/sistema)
                        if not folder_name.startswith('.'):
                            folders.append(folder_name)
                            logger.debug(f"Pasta detectada: {folder_name}")
        
        if folders:
            logger.info(f"Total de pastas encontradas: {len(folders)}")
        else:
            logger.warning("Nenhuma pasta válida encontrada!")
            
    except Exception as e:
        logger.error(f"Erro ao listar pastas: {e}")
        logger.exception("Detalhes do erro:")
        
    return folders


def find_matching_folder(dest_folders: list, source_folder: str) -> Optional[str]:
    """
    Encontra a pasta correspondente na conta destino para uma pasta da origem.
    Usa mapeamento de nomes comuns e depois tenta correspondência direta.
    """
    source_lower = source_folder.lower().strip()

    # Verificar correspondência direta (case-insensitive)
    for folder in dest_folders:
        if folder.lower() == source_lower:
            return folder

    # Verificar pelo mapeamento de pastas conhecidas
    for category, names in FOLDER_MAP.items():
        names_lower = [n.lower() for n in names]
        if source_lower in names_lower:
            # A origem é uma pasta conhecida, procurar equivalente no destino
            for dest_folder in dest_folders:
                if dest_folder.lower() in names_lower:
                    return dest_folder

    # Tentar por sufixo (ex: "INBOX.Sent" → "Sent")
    source_parts = source_folder.replace('[', '').replace(']', '').split('/')
    source_base = source_parts[-1].lower()
    for folder in dest_folders:
        folder_base = folder.split('/')[-1].lower()
        if folder_base == source_base:
            return folder

    return None


# Pastas de sistema Gmail (prefixo [Gmail]/ no IMAP). Labels de usuário (LogWatch, Sync - …) não usam esse prefixo.
_GMAIL_SYSTEM_LABEL_KEYS = frozenset({
    'inbox', 'sent', 'sent mail', 'drafts', 'trash', 'spam', 'starred', 'important',
    'all mail', 'all', 'chat', 'chats',
})
# Ordem para "pular inbox" em contas Gmail
_GMAIL_ARCHIVE_FOLDER_CANDIDATES = (
    '[Gmail]/All Mail', 'All Mail', '[Gmail]/All', 'Archive', 'Arquivo',
)


def _normalize_folder_key(name: str) -> str:
    if not name:
        return ''
    return str(name).replace('\\', '/').strip().lower()


def _folder_matches_label(label_name: str, folder_name: str) -> bool:
    """Correspondência case-insensitive (LogWatch == logwatch; sufixo de caminho)."""
    lk = _normalize_folder_key(label_name)
    fk = _normalize_folder_key(folder_name)
    if not lk or not fk:
        return False
    if lk == fk or fk.endswith('/' + lk):
        return True
    if fk == f'[gmail]/{lk}':
        return True
    if fk.split('/')[-1] == lk:
        return True
    return False


def _imap_connection_alive(conn: imaplib.IMAP4) -> bool:
    try:
        typ, _ = conn.noop()
        return typ == 'OK'
    except Exception:
        return False


def ensure_folder_exists(
    conn: imaplib.IMAP4,
    folder_name: str,
    *,
    log_create_failure: bool = True,
) -> bool:
    """Cria a pasta no servidor destino se ela não existir."""
    if not folder_name or not _imap_connection_alive(conn):
        return False
    mailbox = _imap_mailbox_arg(folder_name)
    try:
        status, _ = conn.select(mailbox)
        if status == 'OK':
            return True
    except Exception:
        pass

    try:
        status, _ = conn.create(mailbox)
        if status == 'OK':
            logger.info(f"Pasta criada: {folder_name}")
            return True
    except Exception as e:
        if log_create_failure and _imap_connection_alive(conn):
            logger.warning(f"Não foi possível criar pasta '{folder_name}': {e}")
    return False


def _try_select_folder(conn: imaplib.IMAP4, folder_name: str, readonly: bool = False) -> bool:
    """Tenta SELECT na pasta; retorna True se OK."""
    if not _imap_connection_alive(conn):
        return False
    mailbox = _imap_mailbox_arg(folder_name)
    try:
        status, _ = conn.select(mailbox, readonly=readonly)
        return status == 'OK'
    except Exception:
        try:
            status, _ = conn.select(folder_name, readonly=readonly)
            return status == 'OK'
        except Exception:
            return False


def resolve_label_folder(
    conn: imaplib.IMAP4,
    label_name: str,
    folder_cache: Optional[list] = None,
):
    """
    Resolve o nome da pasta/label para o formato usado pelo servidor (APPEND/SELECT).
    Labels de usuário no Gmail (ex.: LogWatch, Sync - Openweb) aparecem no LIST com o
    nome exato da UI — sem prefixo [Gmail]/.
    """
    if not label_name or not str(label_name).strip():
        return None
    if not _imap_connection_alive(conn):
        return None
    label_name = str(label_name).strip()
    label_lower = _normalize_folder_key(label_name)

    def _match_in_list(folders: list) -> Optional[str]:
        for f in folders:
            if _folder_matches_label(label_name, f):
                if _try_select_folder(conn, f, readonly=True) or _try_select_folder(conn, f, readonly=False):
                    return f
        return None

    # 0) Gmail: pasta de sistema "Spam"
    if label_lower == 'spam':
        folders = folder_cache if folder_cache is not None else list_folders(conn)
        for f in folders:
            fnorm = _normalize_folder_key(f)
            if fnorm == '[gmail]/spam' or fnorm.endswith('/spam'):
                if _try_select_folder(conn, f, readonly=True) or _try_select_folder(conn, f, readonly=False):
                    logger.info(f"Label Spam resolvida (nome do servidor): '{f}'")
                    return f
        for gmail_spam in ('[Gmail]/Spam',):
            if _try_select_folder(conn, gmail_spam, readonly=True) or _try_select_folder(conn, gmail_spam, readonly=False):
                logger.info(f"Label Spam resolvida (fallback): '{gmail_spam}'")
                return gmail_spam
        logger.warning(
            "Filtro com label 'Spam': pasta [Gmail]/Spam não encontrada ou inacessível. "
            "Ative a pasta Spam em Gmail (Configurações > Encaminhamento e POP/IMAP > Mostrar na IMAP)."
        )
        return None

    folders = folder_cache
    if folders is None:
        try:
            folders = list_folders(conn)
        except Exception as e:
            logger.debug(f"Erro ao listar pastas para label '{label_name}': {e}")
            folders = []

    # 1) LIST primeiro (evita CREATE em conexão morta; acha LogWatch etc. pelo nome do servidor)
    found = _match_in_list(folders)
    if found:
        logger.debug(f"Label '{label_name}' resolvida via LIST → '{found}'")
        return found

    # 2) Prefixo [Gmail]/ só para pastas de sistema, não para labels de usuário
    if '[Gmail]/' not in label_name and label_lower in _GMAIL_SYSTEM_LABEL_KEYS:
        gmail_name = '[Gmail]/' + label_name
        if ensure_folder_exists(conn, gmail_name, log_create_failure=False):
            return gmail_name

    # 3) SELECT/CREATE com nome do filtro (Gmail cria label se ainda não existir)
    if ensure_folder_exists(conn, label_name):
        return label_name

    return None


def _gmail_supports_xgm_labels(conn: imaplib.IMAP4) -> bool:
    """Gmail IMAP: extensão X-GM-LABELS permite etiquetar sem APPEND na pasta do label."""
    try:
        typ, data = conn.capability()
        if typ == 'OK' and data:
            cap = _imap_bytes_to_str(data[0]).upper()
            return 'X-GM-EXT-1' in cap or 'X-GM-LABELS' in cap
    except Exception as e:
        logger.debug(f"Capability Gmail: {e}")
    return False


def _label_nome_gmail_ui(label_name: str) -> Optional[str]:
    """Nome do label como na interface Gmail (sem prefixo IMAP [Gmail]/)."""
    if not label_name or not str(label_name).strip():
        return None
    s = str(label_name).strip()
    if s.upper().startswith('[GMAIL]/'):
        s = s[9:]
    return s.strip() or None


def _is_pasta_spam_imap(folder_name: str) -> bool:
    if not folder_name:
        return False
    n = folder_name.replace('\\', '/').lower()
    return n == '[gmail]/spam' or n.endswith('/spam')


def _imap_mailbox_arg(folder_name: str) -> str:
    """Nome de mailbox para SELECT/APPEND (pastas com espaço precisam de aspas)."""
    if not folder_name:
        return 'INBOX'
    if folder_name.startswith('"') and folder_name.endswith('"'):
        return folder_name
    if any(c in folder_name for c in ' \t-/&'):
        escaped = folder_name.replace('\\', '\\\\').replace('"', '\\"')
        return f'"{escaped}"'
    return folder_name


def _format_imap_append_flags(email_flags, flags_fallback: str = '()'):
    """
    Formata flags para imaplib.append.
    imaplib aceita None ou string '(\Seen ...)'; tuplas quebram ('not all arguments converted').
    """
    parts = []
    if email_flags:
        for flag in email_flags:
            if isinstance(flag, bytes):
                s = flag.decode('ascii', errors='replace').strip()
            elif isinstance(flag, int):
                continue
            else:
                s = str(flag).strip()
            if not s:
                continue
            if not s.startswith('\\'):
                s = '\\' + s.lstrip('\\')
            parts.append(s)
    if parts:
        return '(' + ' '.join(parts) + ')'
    fb = (flags_fallback if isinstance(flags_fallback, str) else '()') or '()'
    fb = fb.strip()
    if fb in ('', '()'):
        return None
    if fb.startswith('(') and fb.endswith(')'):
        return fb
    inner = fb.strip('()').strip()
    return f'({inner})' if inner else None


def _imap_append_datetime(internal_date_str: Optional[str]):
    """Converte INTERNALDATE do FETCH para o formato aceito por imaplib.append."""
    if not internal_date_str:
        return None
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(internal_date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return imaplib.Time2Internaldate(dt.timestamp())
    except Exception:
        return imaplib.Time2Internaldate(time.time())


def _extrair_flags_e_internaldate(fetch_data) -> tuple:
    """Extrai FLAGS e INTERNALDATE da resposta do UID FETCH (RFC822 ou envelope)."""
    import re

    flags = '()'
    internal_date = None
    meta_chunks = []

    for item in fetch_data or []:
        if isinstance(item, tuple):
            if item and isinstance(item[0], bytes):
                meta_chunks.append(item[0])
        elif isinstance(item, bytes) and item.strip() and item != b')':
            meta_chunks.append(item)

    for chunk in meta_chunks:
        item_str = chunk.decode('utf-8', errors='replace')
        if 'FLAGS' in item_str and flags == '()':
            match = re.search(r'FLAGS \(([^)]*)\)', item_str)
            if match:
                flags = f"({match.group(1)})"
        if internal_date is None and 'INTERNALDATE' in item_str:
            match = re.search(r'INTERNALDATE "([^"]*)"', item_str)
            if match:
                internal_date = match.group(1)

    return flags, internal_date


def _datetime_mensagem_para_corte(parsed_msg, internal_date_str: Optional[str] = None):
    """
    Data para regra dias_manter_origem.
    Usa cabeçalho Date; se ausente/inválido, usa INTERNALDATE do IMAP.
    """
    from email.utils import parsedate_to_datetime

    candidatos = []
    if parsed_msg:
        date_hdr = (parsed_msg.get('Date') or '').strip()
        if date_hdr:
            candidatos.append(date_hdr)
    if internal_date_str:
        candidatos.append(internal_date_str)

    for raw in candidatos:
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            continue
    return None


def _aplicar_gmail_labels_meta(
    conn: imaplib.IMAP4,
    pasta_spam: str,
    message_id: str,
    label_names: list,
) -> None:
    """
    Aplica labels de origem (ex.: Sync - Studiogt) só como metadado Gmail,
    sem APPEND na pasta do label — o e-mail permanece apenas na pasta Spam.
    """
    labels_ui = []
    for name in label_names:
        ui = _label_nome_gmail_ui(name)
        if ui and ui.lower() not in ('spam', 'inbox', 'sent', 'draft', 'trash', 'all mail'):
            labels_ui.append(ui)
    if not labels_ui or not message_id or not pasta_spam:
        return
    mid = message_id.strip()
    if not mid.startswith('<'):
        mid = f'<{mid}>'
    try:
        typ, _ = conn.select(f'"{pasta_spam}"', readonly=False)
        if typ != 'OK':
            logger.warning(f"Não foi possível SELECT em '{pasta_spam}' para X-GM-LABELS")
            return
        typ, data = conn.uid('SEARCH', None, 'HEADER', 'Message-ID', mid)
        if typ != 'OK' or not data or not data[0]:
            logger.debug(f"Message-ID não encontrado em '{pasta_spam}' para X-GM-LABELS")
            return
        uids = data[0].split()
        if not uids:
            return
        uid = uids[-1]
        label_arg = '(' + ' '.join(f'"{lbl.replace(chr(34), "")}"' for lbl in labels_ui) + ')'
        typ, _ = conn.uid('STORE', uid, '+X-GM-LABELS', label_arg)
        if typ == 'OK':
            logger.debug(
                f"X-GM-LABELS em '{pasta_spam}' (uid {_imap_uid_str(uid)}): {labels_ui}"
            )
        else:
            logger.warning(f"Falha X-GM-LABELS {labels_ui} em '{pasta_spam}'")
    except Exception as e:
        logger.warning(f"Erro ao aplicar X-GM-LABELS {labels_ui}: {e}")


def _fechar_imap(conn) -> None:
    if not conn:
        return
    try:
        conn.logout()
    except Exception:
        pass


def _is_imap_transient_error(exc: BaseException) -> bool:
    """Erros de rede/SSL/IMAP que costumam resolver com reconexão."""
    if isinstance(exc, (
        imaplib.IMAP4.abort,
        imaplib.IMAP4.error,
        ConnectionError,
        OSError,
        socket.timeout,
        ssl.SSLError,
        BrokenPipeError,
        ConnectionResetError,
    )):
        return True
    msg = str(exc).lower()
    return any(
        s in msg for s in (
            'socket error', 'eof', 'bad length', 'ssl', 'connection reset',
            'timed out', 'broken pipe', 'connection aborted', 'temporarily unavailable',
        )
    )


def _reconectar_imap(conexoes: Dict[str, Any], dados: dict, lado: str = 'ambos') -> bool:
    """
    Fecha e recria conexões IMAP (origem e/ou destino).
    Atualiza o dict conexoes in-place. Retorna True se pelo menos uma reconexão ok.
    """
    ok = False
    try:
        if lado in ('ambos', 'origem', 'src'):
            _fechar_imap(conexoes.get('src'))
            conexoes['src'] = connect_imap(
                dados['servidor'], dados['porta'],
                dados['email'], dados['senha'],
                bool(dados.get('ssl', True)),
            )
            ok = True
            logger.info('IMAP origem reconectado')
        if lado in ('ambos', 'destino', 'dst'):
            _fechar_imap(conexoes.get('dst'))
            conexoes['dst'] = connect_imap(
                dados['dest_servidor'], dados['dest_porta'],
                dados['dest_email'], dados['dest_senha'],
                bool(dados.get('dest_ssl', True)),
            )
            ok = True
            logger.info('IMAP destino reconectado')
    except Exception as e:
        logger.error(f"Falha ao reconectar IMAP ({lado}): {e}")
        return False
    return ok


def resolve_message_id(parsed_msg) -> str:
    """
    Obtém Message-ID normalizado do cabeçalho.
    Se ausente, gera ID sintético estável (From + Subject + Date) para deduplicação.
    """
    from db_manager import EmailHistoryManager

    mid = EmailHistoryManager.normalize_message_id(parsed_msg.get('Message-ID', ''))
    if mid:
        return mid
    from email.utils import parseaddr

    from_addr = decode_header_value(parsed_msg.get('From', ''))
    _, from_email = parseaddr(from_addr)
    subject = decode_header_value(parsed_msg.get('Subject', ''))
    date_hdr = (parsed_msg.get('Date') or '').strip()
    if not from_email and not subject and not date_hdr:
        return ''
    key = f"{(from_email or '').lower()}|{subject}|{date_hdr}"
    digest = hashlib.sha256(key.encode('utf-8', errors='replace')).hexdigest()[:32]
    return EmailHistoryManager.normalize_message_id(
        f"synthetic.{digest}@imapsync-manager.local"
    )


def get_existing_message_ids(conn: imaplib.IMAP4, folder: str) -> set:
    """
    Busca todos os Message-IDs existentes na pasta destino.
    Usado para evitar duplicatas.
    """
    from db_manager import EmailHistoryManager

    message_ids = set()
    try:
        status, _ = conn.select(_imap_mailbox_arg(folder), readonly=True)
        if status != 'OK':
            return message_ids

        status, data = conn.search(None, 'ALL')
        if status != 'OK' or not data[0]:
            return message_ids

        uids = data[0].split()
        if not uids:
            return message_ids

        batch_size = Config.IMAP_MESSAGE_ID_BATCH_SIZE
        for i in range(0, len(uids), batch_size):
            batch = uids[i:i + batch_size]
            uid_list = ','.join(_imap_uid_str(u) for u in batch)
            try:
                status, fetch_data = conn.fetch(uid_list, '(BODY[HEADER.FIELDS (MESSAGE-ID)])')
                if status == 'OK':
                    for item in fetch_data:
                        if isinstance(item, tuple) and len(item) >= 2:
                            header_data = item[1]
                            if isinstance(header_data, bytes):
                                msg = email.message_from_bytes(header_data)
                                mid = EmailHistoryManager.normalize_message_id(
                                    msg.get('Message-ID', '')
                                )
                                if mid:
                                    message_ids.add(mid)
            except Exception as e:
                logger.debug(f"Erro ao buscar batch de Message-IDs: {e}")

    except Exception as e:
        logger.warning(f"Erro ao buscar Message-IDs existentes em '{folder}': {e}")

    return message_ids


def copy_folder(
    src_conn: imaplib.IMAP4,
    dst_conn: imaplib.IMAP4,
    src_folder: str,
    dst_folder: str,
    marcar_lido: bool = True,
    dias_manter: int = 0,
    conta_origem_id: int = None,
    conta_principal_id: int = None,
    label_destino: str = None,
    usuario_id: int = None,
    imap_dados: dict = None,
    conexoes: dict = None,
) -> dict:
    """
    Copia mensagens de uma pasta origem para uma pasta destino.
    
    Args:
        src_conn: Conexão IMAP da origem
        dst_conn: Conexão IMAP do destino
        src_folder: Nome da pasta na origem
        dst_folder: Nome da pasta no destino
        marcar_lido: Se True, marca as mensagens copiadas como lidas na origem
        dias_manter: Se > 0, deleta mensagens com mais de X dias na origem
        conta_origem_id: ID da conta de origem (para histórico e filtros)
        conta_principal_id: ID da conta principal (para filtros globais)
    
    Returns:
        dict com contadores: copiadas, ignoradas, erros, deletadas; interrompido (motivo) se abortado
    """
    stats = {
        'copiadas': 0, 'ignoradas': 0, 'erros': 0, 'deletadas': 0,
        'interrompido': None, 'erros_resumo': [],
    }
    erros_resumo = stats['erros_resumo']
    max_erros_log = Config.SYNC_ERROS_LOG_MAX_UI
    sync_log_id = (imap_dados or {}).get('sync_log_id')

    def _meta_email(parsed_msg=None, assunto=None, remetente=None, message_id=None):
        subj = assunto
        rem = remetente
        mid = message_id
        if parsed_msg is not None:
            if subj is None:
                subj = decode_header_value(parsed_msg.get('Subject', ''))
            if rem is None:
                rem = decode_header_value(parsed_msg.get('From', ''))
            if mid is None:
                mid = resolve_message_id(parsed_msg)
        return subj, rem, mid

    def _registrar_erro_sync(uid_str, fase, exc=None, parsed_msg=None,
                           assunto=None, remetente=None, message_id=None):
        err_msg = str(exc).strip() if exc else 'Falha desconhecida'
        subj, rem, mid = _meta_email(parsed_msg, assunto, remetente, message_id)
        if conta_origem_id:
            try:
                from db_manager import SyncErrorManager
                SyncErrorManager.registrar_erro(
                    conta_origem_id=conta_origem_id,
                    uid_origem=uid_str,
                    fase=fase,
                    erro_mensagem=err_msg,
                    log_sincronizacao_id=sync_log_id,
                    message_id=mid,
                    assunto=subj,
                    remetente=rem,
                    pasta_origem=src_folder,
                )
            except Exception:
                pass
        linha = f"UID {uid_str}"
        if rem:
            linha += f" | De: {(rem or '')[:60]}"
        if subj:
            linha += f" | {(subj or '')[:80]}"
        linha += f" | {fase}: {err_msg[:150]}"
        logger.error(f"Erro sync {linha}")
        if len(erros_resumo) < max_erros_log:
            erros_resumo.append(linha)
        elif len(erros_resumo) == max_erros_log:
            erros_resumo.append('… demais erros no painel "Mensagens problemáticas"')

    if conexoes is None:
        conexoes = {'src': src_conn, 'dst': dst_conn}
    else:
        conexoes.setdefault('src', src_conn)
        conexoes.setdefault('dst', dst_conn)
    src_conn = conexoes['src']
    dst_conn = conexoes['dst']

    inicio_sync = time.monotonic()
    erros_consecutivos = 0
    reconnect_count = 0
    timeout_sec = Config.SYNC_CONTA_TIMEOUT_SEC
    max_erros = Config.SYNC_MAX_ERROS_CONSECUTIVOS
    max_reconnects = Config.SYNC_IMAP_MAX_RECONNECTS

    def _motivo_parar() -> Optional[str]:
        if conta_origem_id:
            try:
                from sync_cancel import cancelamento_solicitado
                if cancelamento_solicitado(conta_origem_id):
                    return 'cancelado'
            except Exception:
                pass
        if time.monotonic() - inicio_sync >= timeout_sec:
            return 'timeout'
        if erros_consecutivos >= max_erros:
            return 'erros_consecutivos'
        return None

    def _apos_sucesso_uid() -> None:
        nonlocal erros_consecutivos
        erros_consecutivos = 0

    dst_folder_cache: list = []

    def _refresh_dst_folders(force_reconnect: bool = False) -> list:
        nonlocal dst_folder_cache, dst_conn, reconnect_count
        if force_reconnect and imap_dados and reconnect_count < max_reconnects:
            if _reconectar_imap(conexoes, imap_dados, 'dst'):
                reconnect_count += 1
                dst_conn = conexoes['dst']
        if not _imap_connection_alive(dst_conn):
            if imap_dados and reconnect_count < max_reconnects:
                if _reconectar_imap(conexoes, imap_dados, 'dst'):
                    reconnect_count += 1
                    dst_conn = conexoes['dst']
            else:
                return dst_folder_cache
        try:
            dst_folder_cache = list_folders(dst_conn)
            logger.debug(f"Cache de pastas destino: {len(dst_folder_cache)} entradas")
        except Exception as e:
            logger.warning(f"Não foi possível listar pastas do destino: {e}")
            if imap_dados and _is_imap_transient_error(e) and reconnect_count < max_reconnects:
                if _reconectar_imap(conexoes, imap_dados, 'dst'):
                    reconnect_count += 1
                    dst_conn = conexoes['dst']
                    try:
                        dst_folder_cache = list_folders(dst_conn)
                    except Exception as e2:
                        logger.warning(f"LIST destino após reconexão falhou: {e2}")
        return dst_folder_cache

    def _resolve_label(label_name: str) -> Optional[str]:
        folder = resolve_label_folder(dst_conn, label_name, dst_folder_cache)
        if folder:
            return folder
        _refresh_dst_folders(force_reconnect=True)
        return resolve_label_folder(dst_conn, label_name, dst_folder_cache)

    def _registrar_falha_uid(exc: Optional[BaseException] = None, lado_reconnect: str = 'ambos') -> Optional[str]:
        nonlocal erros_consecutivos, reconnect_count, src_conn, dst_conn
        erros_consecutivos += 1
        parar = _motivo_parar()
        if parar:
            return parar
        if exc and imap_dados and _is_imap_transient_error(exc) and reconnect_count < max_reconnects:
            if _reconectar_imap(conexoes, imap_dados, lado_reconnect):
                reconnect_count += 1
                src_conn = conexoes['src']
                dst_conn = conexoes['dst']
                if lado_reconnect in ('ambos', 'destino', 'dst'):
                    _refresh_dst_folders()
                try:
                    src_conn.select(_imap_mailbox_arg(src_folder))
                except Exception as sel_err:
                    logger.warning(f"Reselect origem após reconexão: {sel_err}")
                erros_consecutivos = 0
        return None

    # Selecionar pasta de origem
    try:
        status, data = src_conn.select(_imap_mailbox_arg(src_folder))
        if status != 'OK':
            logger.warning(f"Não foi possível abrir pasta de origem '{src_folder}'")
            return stats
    except Exception as e:
        logger.warning(f"Erro ao selecionar pasta de origem '{src_folder}': {e}")
        return stats

    # Criar pasta destino se não existir + cache de labels/pastas Gmail
    _refresh_dst_folders()
    if not ensure_folder_exists(dst_conn, dst_folder):
        _refresh_dst_folders(force_reconnect=True)
        if not ensure_folder_exists(dst_conn, dst_folder):
            logger.warning(f"Não foi possível criar ou acessar pasta de destino '{dst_folder}'")
            return stats

    # ═══════════════════════════════════════════════════════════════════
    # HISTÓRICO PERSISTENTE - Evita baixar emails já sincronizados
    # ═══════════════════════════════════════════════════════════════════
    existing_ids = set()
    
    remetentes_conhecidos = set()
    if conta_origem_id:
        # Carregar histórico do banco de dados (emails já sincronizados)
        from db_manager import EmailHistoryManager
        existing_ids = EmailHistoryManager.get_message_ids_sincronizados(conta_origem_id)
        logger.info(f"Carregados {len(existing_ids)} emails do histórico (já sincronizados anteriormente)")
        remetentes_conhecidos = EmailHistoryManager.get_remetentes_emails_conhecidos(conta_origem_id)
    else:
        # Fallback: Buscar apenas os que existem no destino agora
        existing_ids = get_existing_message_ids(dst_conn, dst_folder)
        logger.info(f"Encontrados {len(existing_ids)} emails no destino atual")
    
    # ═══════════════════════════════════════════════════════════════════
    # CARREGAR FILTROS ATIVOS (GLOBAIS + ESPECÍFICOS)
    # ═══════════════════════════════════════════════════════════════════
    filtros = []
    if conta_origem_id and conta_principal_id:
        try:
            from filter_manager import FilterManager
            filtros = FilterManager.get_filtros_ativos_para_sincronizacao(
                conta_origem_id, conta_principal_id
            )
            if filtros:
                n_globais = sum(1 for f in filtros if f.is_global)
                n_especificos = len(filtros) - n_globais
                logger.info(f"Carregados {len(filtros)} filtro(s) ativo(s): {n_globais} global(is), {n_especificos} específico(s)")
        except Exception as e:
            logger.warning(f"Erro ao carregar filtros: {e}")
    elif conta_origem_id:
        try:
            from filter_manager import FilterManager
            filtros = FilterManager.get_filtros_ativos(conta_origem_id)
            if filtros:
                logger.info(f"Carregados {len(filtros)} filtro(s) específico(s) ativo(s) para aplicar")
        except Exception as e:
            logger.warning(f"Erro ao carregar filtros: {e}")

    # ═══════════════════════════════════════════════════════════════════
    # CONFIG SPAM ANALYZER (opcional)
    # ═══════════════════════════════════════════════════════════════════
    spam_config = None
    spam_folder_dest = None
    if conta_principal_id:
        try:
            from spam_analyzer_config import get_config_merged_para_sync
            spam_config = get_config_merged_para_sync(conta_principal_id, usuario_id)
            if spam_config:
                nome_pasta = (spam_config.get('pasta_spam') or '').strip() or 'Spam'
                # resolve_label_folder já tem lógica especializada para [Gmail]/Spam
                spam_folder_dest = _resolve_label(nome_pasta)
                if not spam_folder_dest:
                    # Fallback: tentar outros nomes conhecidos da pasta spam
                    for name in FOLDER_MAP.get('spam', ['Spam']):
                        resolved = _resolve_label(name)
                        if resolved:
                            spam_folder_dest = resolved
                            break
                if spam_folder_dest:
                    logger.info(f"Spam Analyzer: pasta spam resolvida → '{spam_folder_dest}'")
            if spam_config:
                logger.info(f"Spam Analyzer ativo: acao={spam_config.get('acao', 'mark_spam')}")
        except Exception as e:
            logger.warning(f"Erro ao carregar config Spam Analyzer: {e}")

    # Buscar todas as mensagens da origem
    try:
        status, data = src_conn.uid('SEARCH', None, 'ALL')
        if status != 'OK' or not data[0]:
            return stats

        uids = data[0].split()
        total_msgs = len(uids)

        if total_msgs == 0:
            return stats

        logger.debug(f"Encontradas {total_msgs} mensagens na pasta '{src_folder}'")

    except Exception as e:
        logger.warning(f"Erro ao buscar mensagens de '{src_folder}': {e}")
        return stats

    # Calcular data de corte para deleção se necessário
    cutoff_date = None
    if dias_manter > 0:
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=dias_manter)

    # Listas para ações em lote
    uids_para_marcar_lido = []
    uids_para_deletar = []
    message_ids_copiados = []  # Lista de dict: {message_id, assunto, remetente, data_email}
    historico_flush_every = Config.SYNC_HISTORICO_FLUSH_EVERY_N

    def _flush_historico_parcial(force: bool = False) -> None:
        """Persiste Message-IDs no MySQL em lotes (não só no fim da pasta)."""
        nonlocal message_ids_copiados
        if not conta_origem_id or not message_ids_copiados:
            return
        if not force and len(message_ids_copiados) < historico_flush_every:
            return
        from db_manager import EmailHistoryManager
        batch = message_ids_copiados
        message_ids_copiados = []
        EmailHistoryManager.adicionar_message_ids(conta_origem_id, batch)
        EmailHistoryManager.registrar_filtros_aplicados(conta_origem_id, batch)
        logger.info(f"Histórico: {len(batch)} Message-ID(s) gravados no banco")

    sync_log_id = (imap_dados or {}).get('sync_log_id')
    progress_every_n = Config.SYNC_LOG_PROGRESS_EVERY_N
    progress_interval = Config.SYNC_LOG_PROGRESS_INTERVAL_SEC
    last_progress_at = time.monotonic()
    processed_count = 0
    remetentes_rapidos = 0

    def _report_sync_progress(force: bool = False) -> None:
        nonlocal last_progress_at
        if not sync_log_id or total_msgs <= 0:
            return
        now = time.monotonic()
        if not force and processed_count % progress_every_n != 0 and (now - last_progress_at) < progress_interval:
            return
        last_progress_at = now
        from db_manager import SyncLockManager
        msg = (
            f"Processando INBOX… {processed_count}/{total_msgs} | "
            f"copiadas={stats['copiadas']} ignoradas={stats['ignoradas']} "
            f"erros={stats['erros']} rápidas={remetentes_rapidos}"
        )
        SyncLockManager.update_log_progress(sync_log_id, msg)

    # ── Processar cada mensagem ───────────────────────────────────────────────

    for uid in uids:
        processed_count += 1
        parar = _motivo_parar()
        if parar:
            stats['interrompido'] = parar
            logger.error(
                f"Sync interrompida ({parar}): timeout={timeout_sec}s ou "
                f"erros_consecutivos>={max_erros}"
            )
            break

        uid_str = _imap_uid_str(uid)
        if not uid_str:
            continue

        try:
            # Buscar mensagem completa
            try:
                status, fetch_data = src_conn.uid('FETCH', uid_str, '(RFC822 FLAGS INTERNALDATE)')
            except Exception as fetch_err:
                parar = _registrar_falha_uid(fetch_err, 'ambos')
                stats['erros'] += 1
                logger.error(f"Erro sync UID {uid_str} | fetch: {fetch_err}")
                if parar:
                    stats['interrompido'] = parar
                    logger.error(f"Sync interrompida após fetch UID {uid_str}: {parar}")
                    break
                continue
            raw_email = _extrair_raw_email_fetch(fetch_data)
            if status != 'OK' or not raw_email:
                stats['erros'] += 1
                logger.error(f"Erro sync UID {uid_str} | fetch: resposta vazia do servidor IMAP")
                parar = _registrar_falha_uid()
                if parar:
                    stats['interrompido'] = parar
                    break
                continue

            parsed_msg = None
            try:
                parsed_msg = email.message_from_bytes(raw_email)
                message_id = resolve_message_id(parsed_msg)

                flags, internal_date = _extrair_flags_e_internaldate(fetch_data)

                # Verificar duplicatas usando HISTÓRICO PERSISTENTE
                if message_id and message_id in existing_ids:
                    stats['ignoradas'] += 1
                    _apos_sucesso_uid()
                    
                    # Marcar para leitura e deleção mesmo que já tenha sido copiado antes
                    uids_para_marcar_lido.append(uid_str)
                    
                    if cutoff_date:
                        msg_datetime = _datetime_mensagem_para_corte(parsed_msg, internal_date)
                        if msg_datetime and msg_datetime < cutoff_date:
                            uids_para_deletar.append(uid_str)

                    continue

                # ═══════════════════════════════════════════════════════════
                # EXTRAIR DADOS DO EMAIL PARA FILTROS
                # ═══════════════════════════════════════════════════════════
                from_addr = decode_header_value(parsed_msg.get('From', ''))
                to_addr = decode_header_value(parsed_msg.get('To', ''))
                subject = decode_header_value(parsed_msg.get('Subject', ''))

                from email.utils import parseaddr
                _, from_email = parseaddr(from_addr)
                from_email = (from_email or '').strip().lower()
                remetente_conhecido = bool(
                    from_email and from_email in remetentes_conhecidos
                )
                if remetente_conhecido:
                    remetentes_rapidos += 1
                
                # Verificar se tem anexo
                has_attachment = False
                if parsed_msg.is_multipart():
                    for part in parsed_msg.walk():
                        if part.get_content_maintype() == 'multipart':
                            continue
                        if part.get('Content-Disposition') is not None:
                            has_attachment = True
                            break
                
                # Preparar dados para filtros
                email_data = {
                    'from': from_addr,
                    'to': to_addr,
                    'subject': subject,
                    'body': '',  # Corpo não extraído por performance
                    'has_attachment': has_attachment
                }
                
                # ═══════════════════════════════════════════════════════════
                # SPAM ANALYZER (antes dos filtros)
                # ═══════════════════════════════════════════════════════════
                filter_actions = {
                    'pular_inbox': False,
                    'aplicar_label': [label_destino] if label_destino else [],
                    'gmail_meta_labels': [],  # labels só como etiqueta (spam), sem pasta extra
                    'marcar_lido': False,
                    'marcar_importante': False,
                    'deletar': False,
                    'encaminhar_para': None
                }
                detectado_spam_pelo_filtro = False  # para histórico: emails que caíram no filtro de spam
                detectado_spam_motivo = None        # motivo persistido no histórico
                detectado_spam_detalhe = None       # valor concreto que disparou a detecção
                if remetente_conhecido:
                    if label_destino:
                        filter_actions['aplicar_label'] = [label_destino]
                    logger.debug(
                        f"Remetente conhecido no histórico, pulando spam analyzer: {from_email}"
                    )
                elif spam_config:
                    from spam_analyzer_config import remetente_bloqueado_entrada, remetente_permitido
                    acao_spam = (spam_config.get('acao') or 'mark_spam').strip()

                    def _aplicar_acao_spam():
                        """
                        Aplica a ação configurada quando um email é detectado como spam.
                        - mark_spam: pasta/label spam + INBOX (+ label_destino via APPEND)
                        - skip_inbox: APPEND só na pasta Spam; label_destino via X-GM-LABELS (Gmail)
                        """
                        nonlocal spam_folder_dest
                        # Re-tenta resolver a pasta spam se não foi encontrada na inicialização
                        if not spam_folder_dest:
                            spam_folder_dest = _resolve_label(nome_pasta or 'Spam')
                            if not spam_folder_dest:
                                for name in FOLDER_MAP.get('spam', ['Spam']):
                                    resolved = _resolve_label(name)
                                    if resolved:
                                        spam_folder_dest = resolved
                                        logger.info(f"Spam Analyzer: pasta spam resolvida (retry) → '{spam_folder_dest}'")
                                        break
                        if acao_spam == 'skip_inbox':
                            filter_actions['pular_inbox'] = True
                            meta = []
                            if label_destino:
                                meta.append(label_destino)
                            filter_actions['gmail_meta_labels'] = meta
                            # Pasta física = só Spam (label de origem não vira pasta IMAP)
                            filter_actions['aplicar_label'] = (
                                [spam_folder_dest] if spam_folder_dest else []
                            )
                        elif spam_folder_dest and spam_folder_dest not in filter_actions['aplicar_label']:
                            filter_actions['aplicar_label'].append(spam_folder_dest)

                    # 0) Whitelist: remetentes permitidos nunca são tratados como spam
                    allowed = [
                        l.strip() for l in (spam_config.get('remetentes_permitidos') or '').splitlines()
                        if l.strip()
                    ]
                    from_email_whitelist = remetente_permitido(from_addr, allowed) if allowed else False
                    if from_email_whitelist:
                        logger.debug(f"Remetente na whitelist, ignorando análise de spam: {from_email}")
                    else:
                        # 1) Remetentes bloqueados: suporta email exato, @dominio.com e nome de exibição
                        blocked = [l.strip() for l in (spam_config.get('remetentes_bloqueados') or '').splitlines() if l.strip()]
                        dominios_spam = [l.strip().lower() for l in (spam_config.get('dominios_gratuitos') or '').splitlines() if l.strip()]
                        palavras_spam = [l.strip().lower() for l in (spam_config.get('palavras_institucionais') or '').splitlines() if l.strip()]
                        prefixo_estrito = bool(spam_config.get('bloqueio_prefixo_estrito'))
                        entrada_bloqueada = remetente_bloqueado_entrada(
                            from_addr,
                            blocked,
                            prefixo_estrito=prefixo_estrito,
                        )
                        if entrada_bloqueada:
                            _aplicar_acao_spam()
                            detectado_spam_pelo_filtro = True
                            detectado_spam_motivo = 'remetente'
                            detectado_spam_detalhe = entrada_bloqueada
                            logger.info(
                                f"Spam (remetente bloqueado): {from_addr!r} → regra '{entrada_bloqueada}'"
                                + (f" → label/pasta '{spam_folder_dest}'" if spam_folder_dest else "")
                                + (" → pular inbox" if acao_spam == 'skip_inbox' else "")
                            )
                        else:
                            # 1b) Reply-To mismatch
                            if not detectado_spam_pelo_filtro and spam_config.get('heuristica_reply_to', True):
                                from spam_analyzer_config import reply_to_mismatch
                                reply_to_raw = decode_header_value(parsed_msg.get('Reply-To', ''))
                                if reply_to_mismatch(
                                    from_addr,
                                    reply_to_raw,
                                    remetentes_permitidos=allowed,
                                    dominios_gratuitos=dominios_spam,
                                ):
                                    _aplicar_acao_spam()
                                    detectado_spam_pelo_filtro = True
                                    detectado_spam_motivo = 'reply_to'
                                    detectado_spam_detalhe = reply_to_raw[:200] if reply_to_raw else None
                                    logger.info(
                                        f"Spam (Reply-To mismatch): From={from_addr!r} Reply-To={reply_to_raw!r}"
                                        + (f" → label/pasta '{spam_folder_dest}'" if spam_folder_dest else "")
                                        + (" → pular inbox" if acao_spam == 'skip_inbox' else "")
                                    )

                            # 1c) Display name spoofing
                            if not detectado_spam_pelo_filtro and spam_config.get('heuristica_display_name', True):
                                from spam_analyzer_config import display_name_spoofing, _extrair_display_name
                                dominios = [l.strip().lower() for l in (spam_config.get('dominios_gratuitos') or '').splitlines() if l.strip()]
                                palavras = [l.strip().lower() for l in (spam_config.get('palavras_institucionais') or '').splitlines() if l.strip()]
                                if display_name_spoofing(from_addr, dominios, palavras):
                                    _aplicar_acao_spam()
                                    detectado_spam_pelo_filtro = True
                                    detectado_spam_motivo = 'display_name'
                                    detectado_spam_detalhe = _extrair_display_name(from_addr) or from_addr[:100]
                                    logger.info(
                                        f"Spam (display name spoofing): {from_addr!r}"
                                        + (f" → label/pasta '{spam_folder_dest}'" if spam_folder_dest else "")
                                        + (" → pular inbox" if acao_spam == 'skip_inbox' else "")
                                    )

                            # 1d) Domínio/usuário com padrão numérico suspeito
                            if not detectado_spam_pelo_filtro and spam_config.get('heuristica_dominio_numerico', True):
                                from spam_analyzer_config import dominio_numerico_suspeito, _extrair_email_remetente
                                if dominio_numerico_suspeito(from_addr):
                                    _aplicar_acao_spam()
                                    detectado_spam_pelo_filtro = True
                                    detectado_spam_motivo = 'dominio_numerico'
                                    detectado_spam_detalhe = _extrair_email_remetente(from_addr) or from_email
                                    logger.info(
                                        f"Spam (domínio numérico suspeito): {from_addr!r}"
                                        + (f" → label/pasta '{spam_folder_dest}'" if spam_folder_dest else "")
                                        + (" → pular inbox" if acao_spam == 'skip_inbox' else "")
                                    )

                            # 2) Wordlist built-in: verifica assunto contra wordlist_extra
                            wordlist_entries = [l.strip().lower() for l in (spam_config.get('wordlist_extra') or '').replace(',', '\n').splitlines() if l.strip()]
                            if not detectado_spam_pelo_filtro and wordlist_entries and subject:
                                import unicodedata
                                subj_lower = subject.lower()
                                subj_norm = unicodedata.normalize('NFKD', subj_lower).encode('ASCII', 'ignore').decode('ASCII')
                                for wentry in wordlist_entries:
                                    wentry_norm = unicodedata.normalize('NFKD', wentry).encode('ASCII', 'ignore').decode('ASCII')
                                    if wentry in subj_lower or wentry_norm in subj_norm:
                                        _aplicar_acao_spam()
                                        detectado_spam_pelo_filtro = True
                                        detectado_spam_motivo = 'wordlist'
                                        detectado_spam_detalhe = wentry
                                        logger.info(
                                            f"Spam (wordlist assunto): '{wentry}' encontrado no assunto"
                                            + (f" → label/pasta '{spam_folder_dest}'" if spam_folder_dest else "")
                                            + (" → pular inbox" if acao_spam == 'skip_inbox' else "")
                                        )
                                        break

                            # 3) Análise avançada de conteúdo (pacote spamanalyzer, se instalado)
                            if not detectado_spam_pelo_filtro:
                                try:
                                    from spam_analyzer import is_spam
                                    if is_spam(raw_email, spam_config):
                                        _aplicar_acao_spam()
                                        detectado_spam_pelo_filtro = True
                                        detectado_spam_motivo = 'analyzer'
                                        logger.info(
                                            "Spam detectado (analyzer)"
                                            + (f": aplicando label/pasta '{spam_folder_dest}'" if spam_folder_dest else "")
                                            + (" → pular inbox" if acao_spam == 'skip_inbox' else "")
                                        )
                                except Exception as e:
                                    logger.debug(f"Spam Analyzer: {e}")
                
                # ═══════════════════════════════════════════════════════════
                # APLICAR FILTROS (GLOBAIS + ESPECÍFICOS)
                # ═══════════════════════════════════════════════════════════
                aplicado_filtro_email = False  # para histórico: email afetado por filtro global ou específico
                filtro_ids_matched = []  # IDs dos filtros que bateram (para exibir na interna do filtro)
                for filtro in filtros:
                    if filtro.matches(email_data):
                        aplicado_filtro_email = True
                        filtro_ids_matched.append(filtro.id)
                        logger.debug(f"Email corresponde ao filtro '{filtro.nome}'")
                        actions = filtro.get_actions()

                        # Combinar ações de múltiplos filtros
                        if actions['pular_inbox']:
                            filter_actions['pular_inbox'] = True
                        if actions['aplicar_label']:
                            if actions['aplicar_label'] not in filter_actions['aplicar_label']:
                                filter_actions['aplicar_label'].append(actions['aplicar_label'])
                        if actions['marcar_lido']:
                            filter_actions['marcar_lido'] = True
                        if actions['marcar_importante']:
                            filter_actions['marcar_importante'] = True
                        if actions['deletar']:
                            filter_actions['deletar'] = True
                        if actions['encaminhar_para']:
                            filter_actions['encaminhar_para'] = actions['encaminhar_para']

                # Spam + skip_inbox: só pasta Spam; label de sync vira metadado (X-GM-LABELS)
                if detectado_spam_pelo_filtro and spam_config:
                    acao_final = (spam_config.get('acao') or 'mark_spam').strip()
                    if acao_final == 'skip_inbox':
                        filter_actions['pular_inbox'] = True
                        meta = list(filter_actions.get('gmail_meta_labels') or [])
                        if label_destino and label_destino not in meta:
                            meta.insert(0, label_destino)
                        filter_actions['gmail_meta_labels'] = meta
                        if spam_folder_dest:
                            filter_actions['aplicar_label'] = [spam_folder_dest]
                        else:
                            filter_actions['aplicar_label'] = [
                                lb for lb in filter_actions['aplicar_label']
                                if lb and 'spam' in str(lb).lower()
                            ]
                
                # ═══════════════════════════════════════════════════════════
                # APLICAR AÇÃO DE DELETAR (tem prioridade)
                # ═══════════════════════════════════════════════════════════
                if filter_actions['deletar']:
                    logger.debug(f"Email será deletado por filtro (UID {uid_str})")
                    stats['ignoradas'] += 1
                    uids_para_deletar.append(uid_str)
                    continue
                
                # ═══════════════════════════════════════════════════════════
                # DETERMINAR PASTAS DE DESTINO (respeitar "pular inbox" quando há label)
                # ═══════════════════════════════════════════════════════════
                pastas_destino = []
                if filter_actions['aplicar_label']:
                    if not filter_actions['pular_inbox']:
                        pastas_destino.append(dst_folder)
                    for label_name in filter_actions['aplicar_label']:
                        label_folder = _resolve_label(label_name)
                        if label_folder:
                            if label_folder not in pastas_destino:
                                pastas_destino.append(label_folder)
                            logger.debug(f"Aplicando label '{label_folder}' ao email")
                        else:
                            hint = ''
                            lk = _normalize_folder_key(label_name)
                            if dst_folder_cache and lk:
                                parecidos = [
                                    f for f in dst_folder_cache
                                    if lk in _normalize_folder_key(f)
                                ][:5]
                                if parecidos:
                                    hint = f" Pastas parecidas no IMAP: {parecidos}."
                            logger.warning(
                                f"Label '{label_name}' não encontrada no destino; será ignorada.{hint}"
                            )
                if not pastas_destino and filter_actions['pular_inbox']:
                    # Apenas "pular inbox" (sem label): arquivar
                    for archive_name in _GMAIL_ARCHIVE_FOLDER_CANDIDATES:
                        if ensure_folder_exists(dst_conn, archive_name):
                            pastas_destino.append(archive_name)
                            logger.debug(f"Pulando inbox, arquivando em '{archive_name}'")
                            break
                if not pastas_destino:
                    pastas_destino.append(dst_folder)
                
                # ═══════════════════════════════════════════════════════════
                # FLAGS NO DESTINO (Gmail): não replicar \Seen da origem.
                # marcar_lido_origem só afeta a caixa de origem (STORE pós-sync).
                # ═══════════════════════════════════════════════════════════
                email_flags = []
                if filter_actions['marcar_importante']:
                    email_flags.append(b'\\Flagged')
                    logger.debug("Marcando email como importante no destino (filtro)")
                if filter_actions['marcar_lido']:
                    email_flags.append(b'\\Seen')
                    logger.debug("Marcando email como lido no destino (filtro explícito)")
                
                # ═══════════════════════════════════════════════════════════
                # COPIAR PARA O(S) DESTINO(S)
                # ═══════════════════════════════════════════════════════════
                copiou_alguma = False
                pasta_spam_copiada = None
                gmail_meta = filter_actions.get('gmail_meta_labels') or []
                append_falhou_ssl = False
                ultimo_erro_append = None
                append_flags = _format_imap_append_flags(email_flags, '()')
                append_date = _imap_append_datetime(internal_date)
                msg_append = raw_email
                if isinstance(msg_append, str):
                    msg_append = msg_append.encode('utf-8', errors='replace')
                elif not isinstance(msg_append, (bytes, bytearray)):
                    raise TypeError(f'Corpo do email inválido (tipo {type(msg_append).__name__})')

                for target_folder in pastas_destino:
                    try:
                        dst_conn.append(
                            _imap_mailbox_arg(target_folder),
                            append_flags,
                            append_date,
                            msg_append
                        )
                        copiou_alguma = True
                        if _is_pasta_spam_imap(target_folder):
                            pasta_spam_copiada = target_folder
                        logger.debug(f"Email copiado para '{target_folder}'")
                    except Exception as e:
                        ultimo_erro_append = e
                        logger.error(f"Erro ao copiar email para '{target_folder}': {e}")
                        if _is_imap_transient_error(e):
                            append_falhou_ssl = True
                if not copiou_alguma and append_falhou_ssl:
                    parar = _registrar_falha_uid(ultimo_erro_append or Exception('append IMAP destino'), 'dst')
                    stats['erros'] += 1
                    _registrar_erro_sync(
                        uid_str, 'append',
                        ultimo_erro_append or Exception('append IMAP destino (erro de conexão)'),
                        parsed_msg=parsed_msg,
                    )
                    if parar:
                        stats['interrompido'] = parar
                        break
                    continue
                if copiou_alguma and gmail_meta and pasta_spam_copiada:
                    if _gmail_supports_xgm_labels(dst_conn):
                        _aplicar_gmail_labels_meta(
                            dst_conn, pasta_spam_copiada, message_id, gmail_meta
                        )
                    else:
                        logger.debug(
                            "Destino sem X-GM-LABELS: label de origem omitido em spam "
                            f"(meta={gmail_meta})"
                        )
                if not copiou_alguma:
                    stats['erros'] += 1
                    _registrar_erro_sync(
                        uid_str, 'append',
                        ultimo_erro_append or Exception('Falha ao copiar para o destino'),
                        parsed_msg=parsed_msg,
                    )
                    parar = _registrar_falha_uid()
                    if parar:
                        stats['interrompido'] = parar
                        break
                    continue
                stats['copiadas'] += 1
                _apos_sucesso_uid()
                if from_email:
                    remetentes_conhecidos.add(from_email)

                # Adicionar ao histórico persistente (com detalhes para exibição)
                if message_id:
                    existing_ids.add(message_id)
                    msg_date = parsed_msg.get('Date', '')[:100] if parsed_msg.get('Date') else ''
                    message_ids_copiados.append({
                        'message_id': message_id,
                        'assunto': subject[:500] if subject else '',
                        'remetente': from_addr[:500] if from_addr else '',
                        'data_email': msg_date,
                        'detectado_spam_pelo_filtro': detectado_spam_pelo_filtro,
                        'detectado_spam_motivo': detectado_spam_motivo,
                        'detectado_spam_detalhe': detectado_spam_detalhe,
                        'aplicado_filtro_email': aplicado_filtro_email,
                        'filtro_ids': filtro_ids_matched.copy() if filtro_ids_matched else [],
                    })
                    _flush_historico_parcial()

                # Marcar para ações pós-cópia
                uids_para_marcar_lido.append(uid_str)

                # Verificar se deve ser deletada pela idade
                if cutoff_date:
                    msg_datetime = _datetime_mensagem_para_corte(parsed_msg, internal_date)
                    if msg_datetime and msg_datetime < cutoff_date:
                        uids_para_deletar.append(uid_str)

            except Exception as e:
                stats['erros'] += 1
                _registrar_erro_sync(uid_str, 'copiar', e, parsed_msg=parsed_msg)
                parar = _registrar_falha_uid(e, 'ambos')
                if parar:
                    stats['interrompido'] = parar
                    break
                continue

        except Exception as e:
            stats['erros'] += 1
            _registrar_erro_sync(uid_str, 'processar', e)
            parar = _registrar_falha_uid(e, 'ambos')
            if parar:
                stats['interrompido'] = parar
                break
            continue

        _report_sync_progress()

    _report_sync_progress(force=True)

    # ── Salvar histórico no banco de dados ────────────────────────────────
    
    _flush_historico_parcial(force=True)

    # ── Ações em lote após cópia ──────────────────────────────────────────────

    # Reselecionar pasta de origem em modo escrita para marcar/deletar
    if uids_para_marcar_lido or uids_para_deletar:
        try:
            src_conn.select(f'"{src_folder}"')
        except Exception as e:
            logger.warning(f"Não foi possível reselecionar pasta para ações pós-cópia: {e}")
            return stats

    # Marcar como lido na origem
    if marcar_lido and uids_para_marcar_lido:
        try:
            uid_list = ','.join(uids_para_marcar_lido)
            src_conn.uid('STORE', uid_list, '+FLAGS', '\\Seen')
            logger.info(f"Marcadas como lidas na origem: {len(uids_para_marcar_lido)} mensagens")
        except Exception as e:
            logger.warning(f"Erro ao marcar mensagens como lidas: {e}")

    # Deletar mensagens antigas na origem
    if uids_para_deletar:
        resultado_del = deletar_uids_origem(src_conn, src_folder, uids_para_deletar)
        if resultado_del['ok']:
            stats['deletadas'] = resultado_del['deletadas']
            logger.info(
                f"Deletadas da origem: {stats['deletadas']} mensagens (>{dias_manter} dias)"
            )
        else:
            logger.warning(f"Erro ao deletar mensagens antigas: {resultado_del.get('erro')}")

    return stats


def sincronizar_conta(dados: dict) -> dict:
    """
    Ponto de entrada principal para sincronização de uma conta.
    
    Args:
        dados: Dict com campos:
            - servidor, email, senha, porta, ssl (origem)
            - dest_servidor, dest_email, dest_senha, dest_porta, dest_ssl (destino)
            - marcar_lido_origem: bool (padrão True)
            - dias_manter_origem: int (0 = não deletar)
    
    Returns:
        dict: {
            'success': bool,
            'output': str (mensagem resumo),
            'stats': dict com totais por pasta
        }
    """
    inicio = datetime.now()
    linhas_log = []

    def log(msg: str, level: str = 'info'):
        linhas_log.append(msg)
        getattr(logger, level)(msg)

    log(f"Iniciando sincronização: {dados['email']} → {dados['dest_email']}")

    src_conn = None
    dst_conn = None

    try:
        # Conectar nas duas contas
        log(f"Conectando na origem: {dados['servidor']}:{dados['porta']}")
        src_conn = connect_imap(
            dados['servidor'], dados['porta'],
            dados['email'], dados['senha'],
            bool(dados.get('ssl', True))
        )

        log(f"Conectando no destino: {dados['dest_servidor']}:{dados['dest_porta']}")
        dst_conn = connect_imap(
            dados['dest_servidor'], dados['dest_porta'],
            dados['dest_email'], dados['dest_senha'],
            bool(dados.get('dest_ssl', True))
        )

        # Configurações
        marcar_lido   = bool(dados.get('marcar_lido_origem', True))
        dias_manter   = int(dados.get('dias_manter_origem', 0))
        label_destino = (dados.get('label_destino') or '').strip() or None

        if marcar_lido:
            log("Modo: marcar como lido na origem após copiar")
        if dias_manter > 0:
            log(f"Modo: deletar mensagens com mais de {dias_manter} dias na origem")

        # Estatísticas globais
        total = {'copiadas': 0, 'ignoradas': 0, 'erros': 0, 'deletadas': 0}

        # ═══════════════════════════════════════════════════════════════════
        # MODO POP3: Sincronizar APENAS a INBOX
        # ═══════════════════════════════════════════════════════════════════
        log("Modo POP3: sincronizando APENAS a pasta INBOX")
        
        src_folder = 'INBOX'
        dst_folder = 'INBOX'
        
        log(f"  Sincronizando: '{src_folder}' → '{dst_folder}'")

        conexoes = {'src': src_conn, 'dst': dst_conn}
        stats = {}
        try:
            stats = copy_folder(
                src_conn, dst_conn,
                src_folder, dst_folder,
                marcar_lido=marcar_lido,
                dias_manter=dias_manter,
                conta_origem_id=dados.get('id'),
                conta_principal_id=dados.get('conta_principal_id'),
                label_destino=label_destino,
                usuario_id=dados.get('usuario_id'),
                imap_dados=dados,
                conexoes=conexoes,
            )
            src_conn = conexoes['src']
            dst_conn = conexoes['dst']

            for k in ('copiadas', 'ignoradas', 'erros', 'deletadas'):
                total[k] += stats.get(k, 0)

            linha_stats = (
                f"    ✓ copiadas={stats['copiadas']} ignoradas={stats['ignoradas']} "
                f"erros={stats['erros']} deletadas={stats['deletadas']}"
            )
            if stats.get('interrompido'):
                linha_stats += f" | INTERROMPIDO: {stats['interrompido']}"
            log(linha_stats)
            if stats.get('erros') and stats.get('erros_resumo'):
                log(f"    Erros ({stats['erros']}) — detalhes:")
                for det in stats['erros_resumo']:
                    log(f"      ✗ {det}")
                log("    → Ver painel 'Mensagens problemáticas' para marcar exclusão na origem")

        except Exception as e:
            log(f"    ✗ Erro ao sincronizar INBOX: {e}", 'error')
            total['erros'] += 1

        duracao = (datetime.now() - inicio).total_seconds()

        interrompido = stats.get('interrompido') if stats else None
        resumo = (
            f"Sincronização concluída em {duracao:.1f}s | "
            f"Copiadas: {total['copiadas']} | "
            f"Já existiam: {total['ignoradas']} | "
            f"Erros: {total['erros']} | "
            f"Deletadas da origem: {total['deletadas']}"
        )
        if interrompido:
            resumo += f" | Interrompido: {interrompido}"
        log(resumo)

        sucesso_parcial = total['copiadas'] > 0 or total['ignoradas'] > 0
        return {
            'success': (total['erros'] == 0 or sucesso_parcial) and not interrompido,
            'output': '\n'.join(linhas_log),
            'stats': total,
            'interrompido': interrompido,
        }

    except ConnectionError as e:
        msg = f"Erro de conexão: {e}"
        log(msg, 'error')
        return {'success': False, 'output': '\n'.join(linhas_log), 'stats': {}}

    except Exception as e:
        msg = f"Erro inesperado: {e}"
        log(msg, 'error')
        logger.exception("Exceção durante sincronização")
        return {'success': False, 'output': '\n'.join(linhas_log), 'stats': {}}

    finally:
        _fechar_imap(src_conn)
        _fechar_imap(dst_conn)
