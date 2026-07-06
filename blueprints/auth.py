"""
Rotas de autenticação: /, /login, /logout, /register
"""
import logging
from flask import render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from config import Config
from db_manager import DatabaseManager

logger = logging.getLogger(__name__)


def register_routes(app):

    @app.route('/')
    def index():
        if 'user_id' in session:
            return redirect(url_for('dashboard'))
        return redirect(url_for('login'))

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if request.method == 'POST':
            nome           = request.form.get('nome')
            email_addr     = request.form.get('email')
            senha          = request.form.get('senha')
            confirmar_senha = request.form.get('confirmar_senha')

            if senha != confirmar_senha:
                flash('As senhas não coincidem', 'danger')
                return redirect(url_for('register'))

            try:
                with DatabaseManager.get_cursor() as cursor:
                    cursor.execute('SELECT id FROM usuarios WHERE email = %s', (email_addr,))
                    if cursor.fetchone():
                        flash('Este email já está cadastrado', 'danger')
                        return redirect(url_for('register'))

                    senha_hash = generate_password_hash(senha)
                    cursor.execute(
                        'INSERT INTO usuarios (nome, email, senha, admin) VALUES (%s, %s, %s, 0)',
                        (nome, email_addr, senha_hash)
                    )

                flash('Cadastro realizado com sucesso! Faça login.', 'success')
                return redirect(url_for('login'))

            except Exception as e:
                logger.error(f"Erro no registro: {e}")
                flash('Erro ao realizar cadastro. Tente novamente.', 'danger')
                return redirect(url_for('register'))

        return render_template('register.html')

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            email_addr = request.form.get('email')
            senha      = request.form.get('senha')

            try:
                with DatabaseManager.get_cursor() as cursor:
                    cursor.execute('SELECT * FROM usuarios WHERE email = %s', (email_addr,))
                    usuario = cursor.fetchone()

                if not usuario:
                    flash('Email ou senha incorretos', 'danger')
                elif not usuario.get('ativo', 1):
                    flash('Esta conta está desativada. Entre em contato com o administrador.', 'danger')
                elif check_password_hash(usuario['senha'], senha):
                    session['user_id']   = usuario['id']
                    session['user_nome'] = usuario['nome']
                    session['user_email'] = usuario['email']
                    session['is_admin']  = bool(usuario.get('admin', 0))
                    flash(f'Bem-vindo, {usuario["nome"]}!', 'success')
                    return redirect(url_for('dashboard'))
                else:
                    flash('Email ou senha incorretos', 'danger')

            except Exception as e:
                logger.error(f"Erro no login: {e}")
                flash('Erro ao fazer login. Tente novamente.', 'danger')

        return render_template('login.html')

    @app.route('/logout')
    def logout():
        session.clear()
        flash('Você saiu do sistema', 'info')
        return redirect(url_for('login'))

    @app.route('/minha-conta', methods=['GET', 'POST'])
    def minha_conta():
        if 'user_id' not in session:
            flash('Você precisa fazer login primeiro', 'warning')
            return redirect(url_for('login'))

        if request.method == 'POST':
            senha_atual   = request.form.get('senha_atual', '')
            nova_senha    = request.form.get('nova_senha', '')
            confirmar     = request.form.get('confirmar_senha', '')

            if not senha_atual or not nova_senha:
                flash('Preencha todos os campos.', 'warning')
                return redirect(url_for('minha_conta'))

            if nova_senha != confirmar:
                flash('A nova senha e a confirmação não coincidem.', 'danger')
                return redirect(url_for('minha_conta'))

            if len(nova_senha) < Config.MIN_PASSWORD_LENGTH:
                flash(f'A nova senha deve ter pelo menos {Config.MIN_PASSWORD_LENGTH} caracteres.', 'warning')
                return redirect(url_for('minha_conta'))

            try:
                with DatabaseManager.get_cursor() as cursor:
                    cursor.execute('SELECT senha FROM usuarios WHERE id = %s', (session['user_id'],))
                    usuario = cursor.fetchone()

                if not usuario or not check_password_hash(usuario['senha'], senha_atual):
                    flash('Senha atual incorreta.', 'danger')
                    return redirect(url_for('minha_conta'))

                with DatabaseManager.get_cursor() as cursor:
                    cursor.execute(
                        'UPDATE usuarios SET senha = %s WHERE id = %s',
                        (generate_password_hash(nova_senha), session['user_id'])
                    )
                flash('Senha alterada com sucesso!', 'success')
                return redirect(url_for('minha_conta'))

            except Exception as e:
                logger.error(f"Erro ao alterar senha: {e}")
                flash('Erro ao alterar senha. Tente novamente.', 'danger')
                return redirect(url_for('minha_conta'))

        try:
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('SELECT api_key FROM usuarios WHERE id = %s', (session['user_id'],))
                row = cursor.fetchone()
            api_key = (row or {}).get('api_key') or None
        except Exception:
            api_key = None

        return render_template('minha_conta.html', api_key=api_key)

    @app.route('/minha-conta/gerar-api-key', methods=['POST'])
    def minha_conta_gerar_api_key():
        """Permite que qualquer usuário gere/regenere sua própria API key."""
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': 'Não autenticado.'}), 401
        import secrets
        try:
            nova_key = secrets.token_hex(Config.API_KEY_BYTES)
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('UPDATE usuarios SET api_key = %s WHERE id = %s',
                               (nova_key, session['user_id']))
            return jsonify({'success': True, 'api_key': nova_key})
        except Exception as e:
            logger.error(f"Erro ao gerar API key própria: {e}")
            return jsonify({'success': False, 'message': str(e)}), 500
