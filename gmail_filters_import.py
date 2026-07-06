"""
Importação de filtros do Gmail (XML exportado).
Evita duplicatas usando o ID do filtro no Gmail (gmail_filter_id).
Formato: XML Atom exportado em Gmail > Configurações > Filtros > Exportar.
"""
import logging
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Namespaces do XML de filtros do Gmail (Atom + Google Apps)
NS_ATOM = 'http://www.w3.org/2005/Atom'
NS_APPS = 'http://schemas.google.com/apps/2006'


def _get_prop(entry: ET.Element, name: str) -> Optional[str]:
    """Obtém o valor de um apps:property pelo name."""
    for prop in entry.findall(f'{{{NS_APPS}}}property'):
        if prop.get('name') == name:
            return prop.get('value')
    return None


def _texto_para_nome(entry_data: Dict[str, Any]) -> str:
    """Gera um nome curto para o filtro a partir dos critérios."""
    partes = []
    if entry_data.get('from'):
        v = entry_data['from']
        partes.append(f"De: {v[:40]}" + ('…' if len(v) > 40 else ''))
    if entry_data.get('to'):
        v = entry_data['to']
        partes.append(f"Para: {v[:40]}" + ('…' if len(v) > 40 else ''))
    if entry_data.get('subject'):
        v = entry_data['subject']
        partes.append(f"Assunto: {v[:40]}" + ('…' if len(v) > 40 else ''))
    if entry_data.get('hasTheWord'):
        partes.append("Contém")
    if entry_data.get('doesNotHaveTheWord'):
        partes.append("Não contém")
    if not partes:
        return "Filtro Gmail"
    return " | ".join(partes[:3])  # no máximo 3 partes


def _entry_para_filtro_data(entry: ET.Element, entry_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Converte um entry do XML Gmail para o formato interno de filtro (filtro_data).
    Retorna None se não houver nenhum critério suportado.
    """
    from_val = entry_data.get('from')
    to_val = entry_data.get('to')
    subject_val = entry_data.get('subject')
    has_the_word = entry_data.get('hasTheWord')
    does_not_have = entry_data.get('doesNotHaveTheWord')
    # Pelo menos um critério suportado (doesNotHaveTheWord não é mapeado)
    if not any([from_val, to_val, subject_val, has_the_word]):
        return None

    # Corpo: só hasTheWord é mapeado; doesNotHaveTheWord não tem equivalente (não temos "não contém")
    criterio_corpo = has_the_word if has_the_word else None

    nome = _texto_para_nome(entry_data)

    label = entry_data.get('label')
    should_archive = (entry_data.get('shouldArchive') or '').lower() == 'true'
    should_mark_read = (entry_data.get('shouldMarkAsRead') or '').lower() == 'true'
    should_never_spam = (entry_data.get('shouldNeverSpam') or '').lower() == 'true'
    should_trash = (entry_data.get('shouldTrash') or '').lower() == 'true'
    forward_to = entry_data.get('forwardTo')

    return {
        'nome': nome,
        'ativo': True,
        'criterio_remetente': from_val or None,
        'criterio_destinatario': to_val or None,
        'criterio_assunto': subject_val or None,
        'criterio_corpo': criterio_corpo or None,
        'criterio_tem_anexo': None,
        'acao_pular_inbox': should_archive,
        'acao_aplicar_label': label or None,
        'acao_marcar_lido': should_mark_read,
        'acao_marcar_importante': should_never_spam,  # aproximação
        'acao_deletar': should_trash,
        'acao_encaminhar_para': forward_to or None,
    }


def parse_gmail_xml(xml_content: str) -> List[Dict[str, Any]]:
    """
    Faz o parse do XML de filtros exportados do Gmail.
    Retorna lista de dicts com: gmail_id, entry_data (props), filtro_data (None se sem critério).
    """
    result = []
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        logger.warning(f"Parse XML Gmail: {e}")
        return result

    for entry in root.findall(f'{{{NS_ATOM}}}entry'):
        gmail_id_elem = entry.find(f'{{{NS_ATOM}}}id')
        gmail_id = gmail_id_elem.text.strip() if gmail_id_elem is not None and gmail_id_elem.text else None
        if not gmail_id:
            continue

        entry_data = {}
        for prop in entry.findall(f'{{{NS_APPS}}}property'):
            name = prop.get('name')
            value = prop.get('value')
            if name and value is not None:
                entry_data[name] = value

        filtro_data = _entry_para_filtro_data(entry, entry_data)
        result.append({
            'gmail_id': gmail_id,
            'entry_data': entry_data,
            'filtro_data': filtro_data,
        })
    return result


def importar_filtros_gmail(
    xml_content: str,
    conta_principal_id: int,
) -> Dict[str, Any]:
    """
    Importa filtros do XML do Gmail para filtros globais da conta principal.
    Não duplica: filtros já importados (mesmo gmail_filter_id) são ignorados.
    
    Returns:
        dict com: importados (int), ja_existentes (int), ignorados (int), erros (list de str)
    """
    from filter_manager import FilterManager

    stats = {'importados': 0, 'ja_existentes': 0, 'ignorados': 0, 'possiveis_duplicatas': 0, 'erros': []}
    parsed = parse_gmail_xml(xml_content)

    FilterManager.ensure_gmail_import_support()

    for item in parsed:
        gmail_id = item['gmail_id']
        filtro_data = item['filtro_data']

        if not filtro_data:
            stats['ignorados'] += 1
            continue

        if FilterManager.existe_filtro_por_gmail_id(conta_principal_id, gmail_id):
            stats['ja_existentes'] += 1
            continue

        # Regra equivalente já existe (ex.: criada manualmente) → não duplicar, avisar
        if FilterManager.existe_filtro_global_equivalente(conta_principal_id, filtro_data):
            stats['possiveis_duplicatas'] += 1
            continue

        try:
            fid = FilterManager.criar_filtro_global(
                conta_principal_id,
                filtro_data,
                gmail_filter_id=gmail_id,
            )
            if fid:
                stats['importados'] += 1
            else:
                stats['erros'].append(f"Falha ao criar: {filtro_data.get('nome', '?')}")
        except Exception as e:
            logger.exception(f"Erro ao importar filtro Gmail {gmail_id}")
            stats['erros'].append(f"{filtro_data.get('nome', '?')}: {e}")

    return stats
