# DOX — templates/

## Purpose

Interface web Jinja2 do ImapSync Manager.

## Ownership

- `base.html` — layout, navbar, dark mode, blocos `content` / `extra_css` / `extra_js`
- `dashboard.html`, `detalhes_conta_principal.html` — painel principal
- `spam_analyzer.html`, `spam_config_usuario.html`, `admin_spam_global.html` — antispam
- `emails_sincronizados*.html` — histórico e marcação de spam
- `logs.html` — logs de sincronização com polling de status
- `filtros*.html` — gestão de filtros

## Local Contracts

- Estender `base.html`; títulos via `{% block title %}`.
- Bootstrap 5 + ícones `bi-*`; DataTables para tabelas longas (CDN em `extra_css`/`extra_js`).
- Textos de UI em português.
- Formulários POST para ações de configuração; polling GET para status em tempo real.

## Work Guidance

- Manter JS inline mínimo nos templates; reutilizar padrões de `logs.html` e `emails_sincronizados.html`.
- Não duplicar lógica de negócio — dados vêm prontos do blueprint.

## Verification

Verificação manual no browser; sem suite de testes de template.

## Child DOX Index

Nenhum — templates são arquivos planos.
