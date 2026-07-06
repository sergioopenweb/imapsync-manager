"""
Sinal de cancelamento de sincronização por conta (arquivo em /tmp).
Usado pelo botão "Parar" na UI e verificado no loop IMAP em copy_folder.
"""
import os
import time
import logging

logger = logging.getLogger(__name__)

CANCEL_DIR = '/tmp/imapsync-cancel'
# Após este tempo, o flag de cancelamento é ignorado (processo provavelmente morreu)
CANCEL_FLAG_MAX_AGE_SEC = 600


def _flag_path(conta_origem_id: int) -> str:
    return os.path.join(CANCEL_DIR, str(int(conta_origem_id)))


def solicitar_cancelamento(conta_origem_id: int) -> None:
    os.makedirs(CANCEL_DIR, mode=0o700, exist_ok=True)
    with open(_flag_path(conta_origem_id), 'w', encoding='utf-8') as f:
        f.write(str(time.time()))


def limpar_cancelamento(conta_origem_id: int) -> None:
    try:
        os.unlink(_flag_path(conta_origem_id))
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.debug(f"limpar_cancelamento({conta_origem_id}): {e}")


def cancelamento_solicitado(conta_origem_id: int) -> bool:
    """True se o usuário pediu parada e o flag ainda é válido."""
    if not conta_origem_id:
        return False
    path = _flag_path(conta_origem_id)
    if not os.path.isfile(path):
        return False
    try:
        age = time.time() - os.path.getmtime(path)
        if age > CANCEL_FLAG_MAX_AGE_SEC:
            limpar_cancelamento(conta_origem_id)
            return False
    except OSError:
        return False
    return True
