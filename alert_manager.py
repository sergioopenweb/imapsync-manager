"""
Gerenciador de alertas por email.
Envia notificações quando uma conta de sincronização falha repetidamente.
"""
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import Config

logger = logging.getLogger(__name__)


def smtp_configurado():
    """Verifica se o SMTP está configurado nas variáveis de ambiente."""
    return bool(Config.SMTP_HOST and Config.SMTP_USER and Config.SMTP_PASSWORD)


def enviar_alerta_falha(conta_nome, conta_email, email_destino, n_falhas):
    """
    Envia email de alerta quando uma conta falha repetidamente.

    Args:
        conta_nome: Nome descritivo da conta de origem
        conta_email: Email da conta que falhou
        email_destino: Endereço de email para receber o alerta
        n_falhas: Número de falhas consecutivas até agora
    """
    if not smtp_configurado():
        logger.debug("SMTP não configurado — alerta de falha ignorado")
        return False

    try:
        remetente = Config.SMTP_FROM or Config.SMTP_USER
        assunto   = f"[ImapSync] Alerta: {n_falhas} falhas consecutivas — {conta_nome}"

        corpo_html = f"""
        <html><body>
        <h2 style="color:#dc3545;">Alerta de Sincronização</h2>
        <p>A conta abaixo falhou <strong>{n_falhas} vezes consecutivas</strong>:</p>
        <table style="border-collapse:collapse;margin:16px 0;">
            <tr><td style="padding:6px 12px;font-weight:bold;">Conta:</td>
                <td style="padding:6px 12px;">{conta_nome}</td></tr>
            <tr><td style="padding:6px 12px;font-weight:bold;">Email:</td>
                <td style="padding:6px 12px;">{conta_email}</td></tr>
            <tr><td style="padding:6px 12px;font-weight:bold;">Falhas:</td>
                <td style="padding:6px 12px;color:#dc3545;font-weight:bold;">{n_falhas}</td></tr>
        </table>
        <p>Acesse o painel e verifique os logs para mais detalhes.</p>
        <hr>
        <small style="color:#6c757d;">ImapSync Manager — alerta automático</small>
        </body></html>
        """

        msg = MIMEMultipart('alternative')
        msg['Subject'] = assunto
        msg['From']    = remetente
        msg['To']      = email_destino
        msg.attach(MIMEText(corpo_html, 'html', 'utf-8'))

        with smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=Config.SMTP_CONNECT_TIMEOUT) as server:
            server.ehlo()
            server.starttls()
            server.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
            server.sendmail(remetente, [email_destino], msg.as_string())

        logger.info(f"Alerta de falha enviado para {email_destino} — conta {conta_nome}")
        return True

    except Exception as e:
        logger.error(f"Erro ao enviar alerta de falha para {email_destino}: {e}")
        return False


def verificar_e_alertar(conta_origem_id, conta_nome, conta_email, conta_principal_id, sucesso):
    """
    Atualiza contador de falhas e envia alerta se atingir o limiar.
    Chamado pelo sync_executor após cada sincronização.

    Args:
        conta_origem_id: ID da conta de origem
        conta_nome: Nome da conta
        conta_email: Email da conta
        conta_principal_id: ID da conta principal (para buscar config de alerta)
        sucesso: bool — True se sincronizou com sucesso
    """
    from db_manager import DatabaseManager

    if sucesso:
        DatabaseManager.resetar_falhas_consecutivas(conta_origem_id)
        return

    n_falhas = DatabaseManager.incrementar_falhas_consecutivas(conta_origem_id)
    limiar   = Config.ALERT_FALHAS_CONSECUTIVAS

    if n_falhas < limiar:
        return

    # Buscar config de alerta da conta principal
    alerta = DatabaseManager.get_alerta_config(conta_principal_id)
    if not alerta:
        return

    # Enviar alerta (só no múltiplo do limiar para não spammar)
    if n_falhas % limiar == 0:
        enviar_alerta_falha(conta_nome, conta_email, alerta['email_destino'], n_falhas)
