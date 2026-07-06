# ImapSync Manager

Gerenciador web de sincronização IMAP — copia e-mails de contas de origem para uma conta de destino (principal), com suporte a filtros estilo Gmail, análise de spam e agendamento por conta.

## Funcionalidades

- **Sincronização IMAP nativa** (sem dependência de binários externos)
- **Multi-usuário** com painel de administração
- **Filtros de e-mail** globais e por conta de origem (importação de filtros do Gmail)
- **Spam Analyzer** com wordlists configuráveis e aprendizado por feedback
- **Agendamento individual** por conta de origem (intervalo em minutos)
- **Alertas por e-mail** quando uma conta falha repetidamente
- **Dashboard** com métricas em tempo real e gráfico de atividade
- **API REST** para integrações externas
- **Dark mode**

## Pré-requisitos

- Python 3.10+
- MySQL 5.7+ ou MariaDB 10.4+
- (Opcional) `spamanalyzer` para análise de spam com IA

## Instalação

```bash
git clone <repo-url> imapsync-manager
cd imapsync-manager
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuração

Copie o arquivo de exemplo e edite com suas credenciais:

```bash
cp .env.example .env
nano .env
```

Variáveis obrigatórias em `.env`:

| Variável | Descrição |
|----------|-----------|
| `SECRET_KEY` | Chave secreta Flask (gere com `openssl rand -hex 32`) |
| `DB_HOST` | Host do MySQL |
| `DB_USER` | Usuário do banco |
| `DB_PASSWORD` | Senha do banco |
| `DB_NAME` | Nome do banco de dados |

Variáveis opcionais (alertas por e-mail):

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `SMTP_HOST` | — | Servidor SMTP para alertas |
| `SMTP_PORT` | 587 | Porta SMTP |
| `SMTP_USER` | — | Usuário SMTP |
| `SMTP_PASSWORD` | — | Senha SMTP |
| `ALERT_FALHAS_CONSECUTIVAS` | 3 | Número de falhas antes de alertar |

## Banco de Dados

Crie o banco e o usuário no MySQL:

```sql
CREATE DATABASE imapsync_manager CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'imapsync'@'localhost' IDENTIFIED BY 'sua-senha';
GRANT ALL PRIVILEGES ON imapsync_manager.* TO 'imapsync'@'localhost';
```

As tabelas são criadas automaticamente na primeira inicialização.

## Executando

### Desenvolvimento

```bash
source venv/bin/activate
python app.py
# Acesse: http://localhost:5001
```

### Produção (systemd + Gunicorn)

Crie o arquivo de serviço:

```bash
sudo nano /etc/systemd/system/imapsync-manager.service
```

```ini
[Unit]
Description=ImapSync Manager Web Application
After=network.target mysql.service
Requires=mysql.service

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/imapsync-manager
Environment="PATH=/opt/imapsync-manager/venv311/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="PYTHONPATH=/opt/imapsync-manager"
EnvironmentFile=-/opt/imapsync-manager/.env
ExecStartPre=/bin/mkdir -p /var/log/imapsync-manager
ExecStartPre=/bin/chown ubuntu:ubuntu /var/log/imapsync-manager
ExecStart=/opt/imapsync-manager/venv311/bin/gunicorn \
    --workers 4 \
    --bind 127.0.0.1:5001 \
    --timeout 7200 \
    --access-logfile /var/log/imapsync-manager/access.log \
    --error-logfile /var/log/imapsync-manager/error.log \
    --log-level info \
    --preload \
    app:app
Restart=always
RestartSec=10
StartLimitInterval=60
StartLimitBurst=3
LimitNOFILE=65536
MemoryLimit=2G
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Ative e inicie o serviço:

```bash
sudo systemctl daemon-reload
sudo systemctl enable imapsync-manager
sudo systemctl start imapsync-manager
sudo systemctl status imapsync-manager
```

### Docker (alternativa)

```bash
cp .env.example .env
# Edite .env com suas credenciais
docker-compose up -d
```

## Sincronização Automática (Cron)

O script `auto_sync.py` respeita o **intervalo individual** configurado por conta (`sync_intervalo_minutos`). Para isso, a cron deve disparar com frequência alta (a cada minuto), e o `flock` garante que nunca rodarão duas instâncias ao mesmo tempo:

```bash
crontab -e
```

```
* * * * * /usr/bin/flock -n /tmp/imapsync-auto.lock /opt/imapsync-manager/auto_sync.py > /dev/null 2>&1
```

> **Como funciona:** o cron dispara a cada minuto; se uma execução anterior ainda estiver em andamento, o `flock -n` sai imediatamente sem iniciar outra. O script verifica conta a conta se o intervalo configurado já foi atingido antes de sincronizar.

Certifique-se de que o script tem permissão de execução:

```bash
chmod +x /opt/imapsync-manager/auto_sync.py
```

Os logs da cron ficam em `$LOG_DIR/cron.log` (configurado em `.env`).

## API REST

A API aceita autenticação por API key no header `X-API-Key`.

Para obter uma API key, acesse o painel de administração > Usuários > sua conta.

Endpoints disponíveis:

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| `GET` | `/health` | Status do sistema |
| `GET` | `/api/v1/accounts` | Lista contas do usuário |
| `POST` | `/api/v1/sync/<id>` | Inicia sincronização manual |
| `GET` | `/api/v1/sync/<id>/status` | Status da última sincronização |

## Estrutura do Projeto

```
imapsync-manager/
├── app.py                      # Aplicação Flask principal
├── config.py                   # Configurações (lê do .env)
├── db_manager.py               # Pool MySQL e managers de dados
├── sync_executor.py            # Executor de sincronização
├── imap_sync_native.py         # Motor IMAP nativo (Python)
├── auto_sync.py                # Script para cron (batch sync)
├── filter_manager.py           # Gerenciamento de filtros
├── spam_analyzer.py            # Integração com spam-analyzer
├── spam_analyzer_config.py     # Configuração do spam analyzer
├── gmail_filters_import.py     # Importação de filtros do Gmail
├── alert_manager.py            # Envio de alertas por e-mail
├── blueprints/                 # Módulos de rotas (organizados)
│   ├── auth.py
│   ├── admin.py
│   ├── dashboard.py
│   ├── accounts.py
│   ├── sync.py
│   ├── filters.py
│   ├── spam.py
│   └── api.py
├── templates/                  # Templates Jinja2
├── .env                        # Credenciais (não versionar)
├── .env.example                # Template de variáveis
├── requirements.txt
└── docker-compose.yml
```
