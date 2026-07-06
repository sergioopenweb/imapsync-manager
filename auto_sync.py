#!/opt/imapsync-manager/venv311/bin/python
"""
Script de Sincronização Automática - ImapSync Manager
Versão unificada que compartilha código com app.py
"""

import sys
import os
import logging
import logging.handlers
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# Adicionar o diretório da aplicação ao path
sys.path.insert(0, '/opt/imapsync-manager')

from config import Config
from db_manager import DatabaseManager
from sync_executor import ImapSyncExecutor

# Configurar logging específico para cron (com rotação)
_cron_log = os.path.join(Config.LOG_DIR, 'cron.log')
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.handlers.RotatingFileHandler(
            _cron_log,
            maxBytes=getattr(Config, 'LOG_MAX_BYTES', 5 * 1024 * 1024),
            backupCount=getattr(Config, 'LOG_BACKUP_COUNT', 5),
            encoding='utf-8',
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('auto_sync')

def log_message(message, level='info'):
    """Registra mensagem com timestamp no console e no log"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    console_msg = f"[{timestamp}] {message}"
    print(console_msg)
    
    if level == 'info':
        logger.info(message)
    elif level == 'warning':
        logger.warning(message)
    elif level == 'error':
        logger.error(message)

def limpar_logs_antigos():
    """Limpa logs antigos de todas as contas mantendo apenas os N mais recentes"""
    try:
        from db_manager import SyncLockManager
        
        log_message("\n--- Limpeza de Logs Antigos ---")
        
        with DatabaseManager.get_cursor() as cursor:
            cursor.execute('SELECT id, nome FROM contas_origem ORDER BY id')
            contas = cursor.fetchall()
        
        if not contas:
            log_message("Nenhuma conta para limpar logs", 'warning')
            return 0
        
        total_removidos = 0
        contas_limpas = 0
        
        for conta in contas:
            removidos = SyncLockManager.limpar_logs_antigos(
                conta['id'], 
                Config.MAX_LOGS_POR_CONTA
            )
            if removidos > 0:
                log_message(f"  {conta['nome']}: removidos {removidos} log(s) antigo(s)")
                total_removidos += removidos
                contas_limpas += 1
        
        if total_removidos > 0:
            log_message(f"Total: {total_removidos} log(s) removido(s) de {contas_limpas} conta(s)")
            log_message(f"Limite: {Config.MAX_LOGS_POR_CONTA} logs por conta")
        else:
            log_message("Nenhum log antigo para remover")
        
        return total_removidos
        
    except Exception as e:
        log_message(f"Erro ao limpar logs antigos: {e}", 'error')
        return 0

def _sincronizar_conta_worker(conta):
    """Executa a sincronização de uma única conta (chamado em thread pool)."""
    inicio = datetime.now()
    resultado = ImapSyncExecutor.executar_sincronizacao(conta['id'], conta)
    duracao = (datetime.now() - inicio).total_seconds()
    return conta, resultado, duracao


def _deve_sincronizar(conta):
    """
    Verifica se a conta deve ser sincronizada agora, respeitando o intervalo individual.
    Se sync_intervalo_minutos == 0, sempre sincroniza (comportamento padrão).
    """
    intervalo = conta.get('sync_intervalo_minutos', 0)
    if not intervalo:
        return True
    ultima = conta.get('ultima_sincronizacao')
    if not ultima:
        return True
    from datetime import datetime, timezone
    agora = datetime.now()
    if hasattr(ultima, 'tzinfo') and ultima.tzinfo is not None:
        agora = datetime.now(timezone.utc)
    elapsed_minutes = (agora - ultima).total_seconds() / 60
    return elapsed_minutes >= intervalo


def sincronizar_contas():
    """Sincroniza todas as contas ativas em paralelo, respeitando intervalos individuais."""
    try:
        # Inicializar pool de conexões
        DatabaseManager.initialize_pool()
        log_message("Pool de conexões inicializado")

        # Liberar logs "executando" de processos que morreram sem finalizar (kill, crash)
        from db_manager import SyncLockManager
        orfaos = SyncLockManager.limpar_logs_executando_orfaos()
        if orfaos:
            log_message(f"Limpos {orfaos} log(s) órfão(s) em 'executando'", 'warning')

        # Buscar contas ativas
        todas_contas = ImapSyncExecutor.get_contas_ativas()
        contas = [c for c in todas_contas if _deve_sincronizar(c)]
        ignoradas_intervalo = len(todas_contas) - len(contas)

        if ignoradas_intervalo:
            log_message(f"{ignoradas_intervalo} conta(s) ignorada(s) por intervalo individual ainda não atingido")

        total_contas = len(contas)

        if total_contas == 0:
            log_message("Nenhuma conta ativa encontrada para sincronizar", 'warning')
            return 0

        max_workers = min(Config.SYNC_MAX_WORKERS, total_contas)
        log_message(f"Iniciando sincronização de {total_contas} conta(s) ativa(s) "
                    f"[{max_workers} worker(s) paralelo(s)]")
        log_message("=" * 60)

        sucesso = 0
        erro = 0
        ignorado = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_conta = {
                executor.submit(_sincronizar_conta_worker, conta): conta
                for conta in contas
            }

            for future in as_completed(future_to_conta):
                try:
                    conta, resultado, duracao = future.result()
                    nome = f"{conta['nome']} ({conta['email']})"

                    if resultado['success']:
                        log_message(f"  ✓ {nome} — SUCESSO em {duracao:.1f}s")
                        sucesso += 1
                    elif 'já em andamento' in resultado['message']:
                        log_message(f"  ⊘ {nome} — IGNORADO: {resultado['message']}")
                        ignorado += 1
                    else:
                        log_message(f"  ✗ {nome} — ERRO em {duracao:.1f}s", 'error')
                        log_message(f"    Detalhes: {resultado['message'][:Config.SYNC_STATUS_MESSAGE_MAX_LEN]}", 'error')
                        erro += 1

                except Exception as exc:
                    conta = future_to_conta[future]
                    log_message(f"  ✗ {conta['nome']} — exceção inesperada: {exc}", 'error')
                    logger.exception(f"Exceção na thread da conta {conta['id']}")
                    erro += 1

        # Resumo final
        log_message("\n" + "=" * 60)
        log_message(f"RESUMO: {sucesso} sucesso | {erro} erro(s) | {ignorado} ignorado(s) | {total_contas} total")
        log_message("Sincronização automática concluída")

        # Limpar logs antigos de todas as contas
        limpar_logs_antigos()

        return 0 if erro == 0 else 1

    except Exception as e:
        log_message(f"✗ ERRO GERAL: {e}", 'error')
        logger.exception("Exceção durante sincronização automática")
        return 1

def main():
    """Função principal"""
    log_message("=" * 60)
    log_message("IMAPSYNC MANAGER - Sincronização Automática")
    log_message("=" * 60)
    
    try:
        exit_code = sincronizar_contas()
        
        if exit_code == 0:
            log_message("\n✓ Execução concluída com sucesso")
        else:
            log_message("\n✗ Execução concluída com erros", 'warning')
        
        sys.exit(exit_code)
        
    except KeyboardInterrupt:
        log_message("\n⚠ Sincronização interrompida pelo usuário", 'warning')
        sys.exit(130)
        
    except Exception as e:
        log_message(f"\n✗ ERRO FATAL: {e}", 'error')
        logger.exception("Erro fatal na execução")
        sys.exit(1)

if __name__ == '__main__':
    main()
