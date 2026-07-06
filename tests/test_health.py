"""
Testes para o endpoint de health check e API REST.
"""
from unittest.mock import patch, MagicMock
import json


def test_health_endpoint_available(client):
    """GET /health retorna JSON."""
    with patch('blueprints.api.DatabaseManager') as mock_db:
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__  = MagicMock(return_value=False)
        mock_cursor.fetchone.side_effect = [
            {'n': 3},   # contas_ativas
            {'ts': None},  # ultima_sync
        ]
        mock_db.get_cursor.return_value = mock_cursor

        r = client.get('/health')
        assert r.status_code in (200, 503)
        data = json.loads(r.data)
        assert 'status' in data


def test_api_requires_auth(client):
    """GET /api/v1/accounts sem autenticação retorna 401."""
    r = client.get('/api/v1/accounts')
    assert r.status_code == 401
    data = json.loads(r.data)
    assert 'error' in data


def test_api_with_invalid_key(client):
    """GET /api/v1/accounts com API key inválida retorna 401."""
    with patch('blueprints.api.DatabaseManager') as mock_db:
        mock_db.get_usuario_por_api_key.return_value = None
        r = client.get('/api/v1/accounts', headers={'X-API-Key': 'key-invalida'})
        assert r.status_code == 401


def test_dashboard_requires_login(client):
    """GET /dashboard sem sessão redireciona para login."""
    r = client.get('/dashboard')
    assert r.status_code == 302
    assert 'login' in r.headers['Location']


def test_dashboard_accessible_when_logged_in(auth_client):
    """GET /dashboard com sessão ativa retorna 200 (com dados mockados)."""
    with patch('blueprints.dashboard.DatabaseManager') as mock_db:
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__  = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = []
        mock_cursor.fetchone.return_value = {'total': 0}
        mock_db.get_cursor.return_value = mock_cursor
        mock_db.get_dashboard_stats.return_value = {
            'emails_hoje': 0,
            'spam_recebidos_hoje': 0,
            'spam_bloqueados_hoje': 0,
            'erros_24h': 0,
            'ultima_sync': None,
            'contas_com_erro': 0,
        }
        r = auth_client.get('/dashboard')
        assert r.status_code == 200
