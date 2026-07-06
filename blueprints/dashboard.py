"""
Rota do dashboard com estatísticas agregadas.
"""
import logging
from flask import render_template, session, flash, jsonify
from db_manager import DatabaseManager
from blueprints.utils import login_required

logger = logging.getLogger(__name__)


def register_routes(app):

    @app.route('/dashboard')
    @login_required
    def dashboard():
        try:
            user_id = session['user_id']
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT * FROM contas_principais
                    WHERE usuario_id = %s
                    ORDER BY criado_em DESC
                ''', (user_id,))
                contas_principais = cursor.fetchall()

                cursor.execute('''
                    SELECT COUNT(*) as total FROM contas_origem
                    WHERE conta_principal_id IN (
                        SELECT id FROM contas_principais WHERE usuario_id = %s
                    )
                ''', (user_id,))
                total_contas_origem = cursor.fetchone()['total']

            stats = DatabaseManager.get_dashboard_stats(user_id)

            return render_template(
                'dashboard.html',
                contas_principais=contas_principais,
                total_contas_origem=total_contas_origem,
                stats=stats,
            )

        except Exception as e:
            logger.error(f"Erro no dashboard: {e}")
            flash('Erro ao carregar dashboard', 'danger')
            return render_template('dashboard.html', contas_principais=[], total_contas_origem=0, stats={})

    @app.route('/api/dashboard/stats')
    @login_required
    def api_dashboard_stats():
        """Retorna JSON com dados para o gráfico de atividade semanal."""
        try:
            dados = DatabaseManager.get_atividade_semanal(session['user_id'])
            return jsonify({'success': True, 'data': dados})
        except Exception as e:
            logger.error(f"Erro ao buscar atividade semanal: {e}")
            return jsonify({'success': False, 'data': []}), 500

    @app.route('/api/dashboard/erros-recentes')
    @login_required
    def api_dashboard_erros_recentes():
        """Retorna JSON com os erros de sincronização nas últimas 24h."""
        from config import Config
        try:
            user_id = session['user_id']
            with DatabaseManager.get_cursor() as cursor:
                cursor.execute('''
                    SELECT
                        l.id,
                        l.conta_origem_id,
                        l.mensagem,
                        l.criado_em,
                        l.finalizado_em,
                        co.email AS conta_email,
                        co.nome AS conta_nome,
                        cp.nome AS conta_principal_nome
                    FROM logs_sincronizacao l
                    JOIN contas_origem co ON l.conta_origem_id = co.id
                    JOIN contas_principais cp ON co.conta_principal_id = cp.id
                    WHERE cp.usuario_id = %s
                      AND l.status = 'erro'
                      AND l.criado_em >= DATE_SUB(NOW(), INTERVAL %s HOUR)
                    ORDER BY l.criado_em DESC
                    LIMIT 50
                ''', (user_id, Config.DASHBOARD_ERROS_HORAS))
                rows = cursor.fetchall()

            erros = []
            for r in rows:
                erros.append({
                    'id':                   r['id'],
                    'conta_origem_id':       r['conta_origem_id'],
                    'conta_email':           r.get('conta_email') or '',
                    'conta_nome':            r.get('conta_nome') or r.get('conta_email') or '',
                    'conta_principal_nome':  r.get('conta_principal_nome') or '',
                    'mensagem':              (r.get('mensagem') or '').strip(),
                    'criado_em':             r['criado_em'].strftime('%d/%m/%Y %H:%M') if r.get('criado_em') else '-',
                    'finalizado_em':         r['finalizado_em'].strftime('%d/%m/%Y %H:%M') if r.get('finalizado_em') else '-',
                })

            return jsonify({'success': True, 'erros': erros})
        except Exception as e:
            logger.error(f"Erro ao buscar erros recentes: {e}")
            return jsonify({'success': False, 'erros': []}), 500
