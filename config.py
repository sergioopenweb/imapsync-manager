"""
Configurações centralizadas do ImapSync Manager
Todas as variáveis sensíveis são lidas do arquivo .env (nunca hardcoded aqui).
"""
import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Configurações da aplicação"""

    # Flask
    SECRET_KEY = os.getenv('SECRET_KEY')
    if not SECRET_KEY:
        raise RuntimeError(
            "SECRET_KEY não definida. Configure a variável de ambiente ou o arquivo .env"
        )
    PERMANENT_SESSION_LIFETIME = timedelta(hours=24)

    # Banco de Dados
    DB_CONFIG = {
        'host':               os.getenv('DB_HOST', 'localhost'),
        'user':               os.getenv('DB_USER'),
        'password':           os.getenv('DB_PASSWORD'),
        'database':           os.getenv('DB_NAME', 'imapsync_manager'),
        'autocommit':         False,
        'pool_name':          'imapsync_pool',
        'pool_size':          int(os.getenv('DB_POOL_SIZE', 10)),
        'pool_reset_session': True,
    }

    # ImapSync
    IMAPSYNC_PATH         = '/usr/bin/imapsync'
    IMAPSYNC_TIMEOUT      = 7200   # 2 horas
    IMAPSYNC_LOCK_TIMEOUT = 30     # segundos para aguardar lock

    # Logs
    LOG_DIR         = os.getenv('LOG_DIR', '/var/log/imapsync-manager')
    LOG_FILE        = os.path.join(LOG_DIR, 'app.log')
    LOG_LEVEL       = os.getenv('LOG_LEVEL', 'INFO')
    LOG_MAX_BYTES   = int(os.getenv('LOG_MAX_BYTES', 5 * 1024 * 1024))   # 5 MiB
    LOG_BACKUP_COUNT = int(os.getenv('LOG_BACKUP_COUNT', 5))

    # Retenção de Logs de Sincronização
    MAX_LOGS_POR_CONTA = int(os.getenv('MAX_LOGS_POR_CONTA', 10))

    # Sincronização paralela (cron)
    SYNC_MAX_WORKERS = int(os.getenv('SYNC_MAX_WORKERS', 3))
    # Limites anti-travamento por conta (segundos / contadores)
    SYNC_CONTA_TIMEOUT_SEC       = int(os.getenv('SYNC_CONTA_TIMEOUT_SEC', 1800))       # 30 min
    SYNC_MAX_ERROS_CONSECUTIVOS  = int(os.getenv('SYNC_MAX_ERROS_CONSECUTIVOS', 50))
    SYNC_IMAP_MAX_RECONNECTS     = int(os.getenv('SYNC_IMAP_MAX_RECONNECTS', 3))
    # Logs "executando" sem processo vivo (ex.: kill manual) — liberar lock na BD
    SYNC_ORPHAN_LOG_SEC          = int(os.getenv('SYNC_ORPHAN_LOG_SEC', 180))          # 3 min
    # Progresso do log na interface durante sync
    SYNC_LOG_PROGRESS_EVERY_N        = int(os.getenv('SYNC_LOG_PROGRESS_EVERY_N', 25))
    SYNC_LOG_PROGRESS_INTERVAL_SEC   = int(os.getenv('SYNC_LOG_PROGRESS_INTERVAL_SEC', 15))
    # Grava Message-IDs no histórico a cada N cópias (evita perder marcação se a sync cair)
    SYNC_HISTORICO_FLUSH_EVERY_N     = int(os.getenv('SYNC_HISTORICO_FLUSH_EVERY_N', 50))

    # Paginação
    EMAILS_POR_PAGINA = int(os.getenv('EMAILS_POR_PAGINA', 50))

    # Flask / Servidor de desenvolvimento
    FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    APP_HOST    = os.getenv('APP_HOST', '0.0.0.0')
    APP_PORT    = int(os.getenv('APP_PORT', 5001))

    # Fuso horário de exibição (UTC offset em horas inteiras)
    APP_TIMEZONE_OFFSET = int(os.getenv('APP_TIMEZONE_OFFSET', -3))

    # Retenção de histórico
    # - HISTORICO_FILTRO_RETENTION_DAYS: retenção do histórico de "filtros aplicados" (tabela historico_filtro_aplicado)
    # - HISTORICO_EMAILS_RETENTION_DAYS: retenção do histórico de emails sincronizados (tabela historico_emails_sincronizados)
    HISTORICO_FILTRO_RETENTION_DAYS = int(os.getenv('HISTORICO_FILTRO_RETENTION_DAYS', 90))
    HISTORICO_EMAILS_RETENTION_DAYS = int(os.getenv('HISTORICO_EMAILS_RETENTION_DAYS', 90))

    # Dashboard — janelas de tempo
    DASHBOARD_ERROS_HORAS    = int(os.getenv('DASHBOARD_ERROS_HORAS', 24))
    DASHBOARD_ATIVIDADE_DIAS = int(os.getenv('DASHBOARD_ATIVIDADE_DIAS', 6))

    # Truncamento de mensagens salvas / exibidas
    LOG_MENSAGEM_DB_MAX_LEN         = int(os.getenv('LOG_MENSAGEM_DB_MAX_LEN', 1000))
    SYNC_STATUS_MESSAGE_MAX_LEN     = int(os.getenv('SYNC_STATUS_MESSAGE_MAX_LEN', 200))
    API_SYNC_STATUS_MESSAGE_MAX_LEN = int(os.getenv('API_SYNC_STATUS_MESSAGE_MAX_LEN', 500))

    # Erros de sync por mensagem (UI / retenção)
    SYNC_ERROS_LOG_MAX_UI     = int(os.getenv('SYNC_ERROS_LOG_MAX_UI', 15))
    SYNC_ERROS_UI_LIMIT       = int(os.getenv('SYNC_ERROS_UI_LIMIT', 200))
    SYNC_ERROS_RETENTION_DAYS = int(os.getenv('SYNC_ERROS_RETENTION_DAYS', 60))

    # Limites de listagem (UI / SQL)
    LOGS_UI_LIMIT                  = int(os.getenv('LOGS_UI_LIMIT', 50))
    LOGS_CONTA_DETALHE_LIMIT       = int(os.getenv('LOGS_CONTA_DETALHE_LIMIT', 20))
    HISTORICO_EMAILS_DEFAULT_LIMIT = int(os.getenv('HISTORICO_EMAILS_DEFAULT_LIMIT', 200))
    FILTRO_ULTIMOS_EMAILS_LIMIT    = int(os.getenv('FILTRO_ULTIMOS_EMAILS_LIMIT', 30))

    # IMAP
    DEFAULT_IMAP_SSL_PORT      = int(os.getenv('DEFAULT_IMAP_SSL_PORT', 993))
    IMAP_SOCKET_TIMEOUT        = int(os.getenv('IMAP_SOCKET_TIMEOUT', 30))
    IMAP_MESSAGE_ID_BATCH_SIZE = int(os.getenv('IMAP_MESSAGE_ID_BATCH_SIZE', 100))

    # Segurança
    MIN_PASSWORD_LENGTH = int(os.getenv('MIN_PASSWORD_LENGTH', 6))
    API_KEY_BYTES       = int(os.getenv('API_KEY_BYTES', 32))
    API_KEY_HEADER      = 'X-API-Key'

    # SMTP
    SMTP_CONNECT_TIMEOUT = int(os.getenv('SMTP_CONNECT_TIMEOUT', 15))

    # Alertas por e-mail (opcional)
    SMTP_HOST                    = os.getenv('SMTP_HOST', '')
    SMTP_PORT                    = int(os.getenv('SMTP_PORT', 587))
    SMTP_USER                    = os.getenv('SMTP_USER', '')
    SMTP_PASSWORD                = os.getenv('SMTP_PASSWORD', '')
    SMTP_FROM                    = os.getenv('SMTP_FROM', '')
    ALERT_FALHAS_CONSECUTIVAS    = int(os.getenv('ALERT_FALHAS_CONSECUTIVAS', 3))
