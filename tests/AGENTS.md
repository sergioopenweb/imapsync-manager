# DOX — tests/

## Purpose

Testes automatizados com pytest.

## Ownership

- `conftest.py` — fixtures `app`, `client`, `auth_client`, `admin_client`, `mock_db`
- `test_auth.py` — autenticação
- `test_health.py` — health/dashboard básico
- `test_antispam.py` — heurísticas de spam (quando existir)

## Local Contracts

- Mock de `DatabaseManager` via `mock_db` — testes não exigem MySQL real.
- `TESTING=True`, `WTF_CSRF_ENABLED=False` no app de teste.
- `sys.path` inclui raiz do projeto (ver `conftest.py`).

## Work Guidance

- Novos testes de lógica pura: importar módulos diretamente sem Flask quando possível.
- Testes de antispam: focar em `spam_analyzer_config.py` com casos reais dos logs.

## Verification

```bash
source venv311/bin/activate && pytest tests/ -q
```

## Child DOX Index

Nenhum.
