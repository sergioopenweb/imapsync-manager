# DOX — blueprints/

## Purpose

Rotas HTTP do ImapSync Manager, uma responsabilidade por módulo.

## Ownership

| Arquivo | Responsabilidade |
|---------|------------------|
| `auth.py` | Login, registro, sessão |
| `dashboard.py` | Painel e métricas |
| `accounts.py` | Contas principais e origem |
| `sync.py` | Logs, histórico de e-mails, status de sync |
| `filters.py` | Filtros globais e por conta |
| `spam.py` | Configuração Spam Analyzer por conta |
| `admin.py` | Admin, spam global, usuários |
| `api.py` | API REST |
| `utils.py` | `login_required`, helpers compartilhados |

Registro central em `app.py` via `register_routes(app)`.

## Local Contracts

- Decorador `@login_required` em rotas autenticadas.
- Flash messages com categorias Bootstrap (`success`, `danger`, `warning`).
- Queries via `DatabaseManager.get_cursor()`; não abrir conexões ad hoc.
- Blueprints não importam uns aos outros em ciclo; helpers em `utils.py`.

## Work Guidance

- Novas rotas: mesmo padrão `register_routes(app)` + função com decorator.
- JSON para AJAX: `jsonify()`; HTML: `render_template()` com contexto explícito.
- Spam: config em `spam_analyzer_config`; rotas finas em `spam.py`.

## Verification

Testes em `tests/test_auth.py`, `tests/test_health.py`; adicionar testes de rota ao alterar contratos HTTP.

## Child DOX Index

Nenhum filho — módulos são arquivos planos nesta pasta.
