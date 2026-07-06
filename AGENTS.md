# DOX — ImapSync Manager

Contrato de trabalho para agentes de IA neste repositório. Baseado no [DOX framework](https://github.com/agent0ai/dox).

## Purpose

Gerenciador web Flask de sincronização IMAP multi-usuário: copia e-mails de contas de origem para conta principal, com filtros, antispam e agendamento.

## Ownership (raiz)

| Área | Arquivos / pastas |
|------|-------------------|
| App e bootstrap | `app.py`, `config.py`, `auto_sync.py`, `sync_executor.py` |
| Sincronização IMAP | `imap_sync_native.py`, `sync_cancel.py` |
| Antispam | `spam_analyzer.py`, `spam_analyzer_config.py` |
| Dados | `db_manager.py`, `filter_manager.py` |
| Rotas HTTP | `blueprints/` — ver [blueprints/AGENTS.md](blueprints/AGENTS.md) |
| UI | `templates/` — ver [templates/AGENTS.md](templates/AGENTS.md) |
| Testes | `tests/` — ver [tests/AGENTS.md](tests/AGENTS.md) |

## Local Contracts

- Python 3.10+; dependências em `requirements.txt`; venv local (`venv311/` ignorado pelo Git).
- Configuração sensível só em `.env` (nunca commitar). Exemplo em `.env.example`.
- Logs da aplicação em `/var/log/imapsync-manager/` (`config.LOG_DIR`).
- Commits: skill [`.cursor/skills/git-commit-composer/SKILL.md`](.cursor/skills/git-commit-composer/SKILL.md) — Conventional Commits, atômicos, sem segredos.
- Neste servidor usar `/usr/bin/git -c alias.commit= commit` (alias global quebra `git commit`).
- Só criar commit quando o usuário pedir explicitamente.
- Respostas ao usuário em português.
- Escopo mínimo: não refatorar código não relacionado à tarefa.

## Work Guidance

- Antes de editar: ler este arquivo e o `AGENTS.md` mais próximo do caminho alvo.
- Após mudanças que alterem contratos: atualizar o `AGENTS.md` dono e índices pais.
- Migrações de schema: padrão `_ensure_*_column` em `spam_analyzer_config.py` / `db_manager.py`.
- Spam na sync: pipeline em `imap_sync_native.py` (whitelist → blacklist composta → heurísticas → wordlist → ML).
- Remetentes conhecidos: só legítimos (exclui `marcado_spam` e `detectado_spam_pelo_filtro`).
- Prefixo `@` em blacklist (`contato@`, `noreply@`): **desativado por defeito**; só com `bloqueio_prefixo_estrito`. Preferir `@dominio` ou email exato.

## Verification

```bash
cd /opt/imapsync-manager && source venv311/bin/activate
pytest tests/ -q
```

## User Preferences

- Commits e push apenas quando solicitados.
- Preferência por diffs pequenos e convenções existentes do arquivo.

## Child DOX Index

| Caminho | Escopo |
|---------|--------|
| [blueprints/AGENTS.md](blueprints/AGENTS.md) | Rotas Flask, auth, APIs |
| [templates/AGENTS.md](templates/AGENTS.md) | Jinja2, Bootstrap 5, DataTables |
| [tests/AGENTS.md](tests/AGENTS.md) | Pytest, mocks de DB |
