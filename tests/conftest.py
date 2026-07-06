"""
Fixtures compartilhadas para os testes.
Usa mocks para isolar o banco de dados e módulos externos.
"""
import sys
import os
import pytest
from unittest.mock import MagicMock, patch

# Garante que o diretório raiz do projeto está no path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ── Mocks de módulos que dependem de infra externa ───────────────────────────

class MockCursor:
    """Simula um cursor de banco de dados para testes."""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.last_query = None
        self.last_params = None

    def execute(self, query, params=None):
        self.last_query  = query
        self.last_params = params

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def close(self):
        pass


class MockCursorContext:
    def __init__(self, rows=None):
        self._cursor = MockCursor(rows)

    def __enter__(self):
        return self._cursor

    def __exit__(self, *args):
        pass


@pytest.fixture
def mock_db(monkeypatch):
    """Monkeypatch do DatabaseManager para retornar cursor mock."""
    from db_manager import DatabaseManager

    def fake_get_cursor(dictionary=True):
        return MockCursorContext()

    monkeypatch.setattr(DatabaseManager, 'initialize_pool', lambda: None)
    monkeypatch.setattr(DatabaseManager, 'get_cursor', staticmethod(fake_get_cursor))
    return DatabaseManager


@pytest.fixture
def app(mock_db):
    """Cria instância do Flask app para testes, com banco mockado."""
    # Garante variáveis de ambiente mínimas
    os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-testing-only')
    os.environ.setdefault('DB_HOST', 'localhost')
    os.environ.setdefault('DB_USER', 'test')
    os.environ.setdefault('DB_PASSWORD', 'test')
    os.environ.setdefault('DB_NAME', 'test_db')

    # Importa somente após setar envs
    with patch('db_manager.SyncLockManager.criar_tabela_se_nao_existe'), \
         patch('db_manager.EmailHistoryManager.criar_tabela_se_nao_existe'), \
         patch('db_manager.EmailHistoryManager.criar_tabela_filtro_aplicado_se_nao_existe'), \
         patch('db_manager.EmailHistoryManager.limpar_historico_filtro_antigo'), \
         patch('db_manager.DatabaseManager.ensure_ativacao_columns'), \
         patch('db_manager.DatabaseManager.ensure_sync_intervalo_column'), \
         patch('db_manager.DatabaseManager.ensure_api_key_column'), \
         patch('db_manager.DatabaseManager.ensure_alertas_table'):
        import app as flask_app_module
        flask_app_module.app.config.update({
            'TESTING': True,
            'SECRET_KEY': 'test-secret-key',
            'WTF_CSRF_ENABLED': False,
        })
        yield flask_app_module.app


@pytest.fixture
def client(app):
    """Cliente de teste Flask."""
    return app.test_client()


@pytest.fixture
def auth_client(client):
    """Cliente de teste já autenticado como usuário normal."""
    with client.session_transaction() as sess:
        sess['user_id']   = 1
        sess['user_nome'] = 'Usuário Teste'
        sess['user_email'] = 'teste@example.com'
        sess['is_admin']  = False
    return client


@pytest.fixture
def admin_client(client):
    """Cliente de teste já autenticado como administrador."""
    with client.session_transaction() as sess:
        sess['user_id']   = 1
        sess['user_nome'] = 'Admin Teste'
        sess['user_email'] = 'admin@example.com'
        sess['is_admin']  = True
    return client
