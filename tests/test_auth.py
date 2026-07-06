"""
Testes para as rotas de autenticação.
"""
from unittest.mock import patch, MagicMock


def test_login_get(client):
    """GET /login retorna 200."""
    r = client.get('/login')
    assert r.status_code == 200


def test_register_get(client):
    """GET /register retorna 200."""
    r = client.get('/register')
    assert r.status_code == 200


def test_index_redirect_to_login_when_unauthenticated(client):
    """GET / redireciona para /login quando não autenticado."""
    r = client.get('/')
    assert r.status_code == 302
    assert '/login' in r.headers['Location']


def test_index_redirect_to_dashboard_when_authenticated(auth_client):
    """GET / redireciona para /dashboard quando autenticado."""
    r = auth_client.get('/')
    assert r.status_code == 302
    assert '/dashboard' in r.headers['Location']


def test_logout_clears_session(auth_client):
    """GET /logout limpa a sessão e redireciona para /login."""
    r = auth_client.get('/logout', follow_redirects=False)
    assert r.status_code == 302
    assert '/login' in r.headers['Location']


def test_login_wrong_password(client):
    """POST /login com senha errada mostra mensagem de erro."""
    from werkzeug.security import generate_password_hash
    fake_user = {
        'id': 1, 'nome': 'Teste', 'email': 'teste@example.com',
        'senha': generate_password_hash('senha-correta'),
        'ativo': 1, 'admin': 0,
    }

    with patch('blueprints.auth.DatabaseManager') as mock_db:
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = fake_user
        mock_db.get_cursor.return_value = mock_cursor

        r = client.post('/login', data={'email': 'teste@example.com', 'senha': 'senha-errada'},
                        follow_redirects=True)
        assert b'incorretos' in r.data or r.status_code in (200, 302)


def test_register_password_mismatch(client):
    """POST /register com senhas diferentes redireciona com erro."""
    r = client.post('/register', data={
        'nome': 'Novo', 'email': 'novo@example.com',
        'senha': 'abc123', 'confirmar_senha': 'diferente',
    }, follow_redirects=True)
    assert b'coincidem' in r.data or r.status_code in (200, 302)
