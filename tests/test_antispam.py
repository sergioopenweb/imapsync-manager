"""Testes das heurísticas de antispam."""
import pytest

from spam_analyzer_config import (
    remetente_bloqueado_entrada,
    reply_to_mismatch,
    dominio_numerico_suspeito,
    _is_remetente_sistema,
    _dominio_base_registravel,
)


class TestRemetenteSistema:
    def test_mailer_daemon(self):
        assert _is_remetente_sistema('mailer-daemon@plesk42.openwebhost.com.br')
        assert _is_remetente_sistema('MAILER-DAEMON@example.com')

    def test_normal_user(self):
        assert not _is_remetente_sistema('contato@empresa.com.br')


class TestDominioNumerico:
    def test_ignora_mailer_daemon(self):
        assert not dominio_numerico_suspeito(
            'MAILER-DAEMON@plesk42.openwebhost.com.br (Mail Delivery System)'
        )

    def test_detecta_subdominio_numerico(self):
        assert dominio_numerico_suspeito('"Daniela Gomes" <danielagomes@relatorios01a.tipicamoquecaparaense.sbs>')

    def test_detecta_usuario_longo_numerico(self):
        assert dominio_numerico_suspeito('Reclame Aqui <contato95290@vendasloja5.simplesnacional052026.com>')


class TestReplyToMismatch:
    def test_mesmo_dominio_base_ignora(self):
        assert not reply_to_mismatch(
            'user@mail.empresa.com.br',
            'outro@empresa.com.br',
        )

    def test_mailer_daemon_ignora(self):
        assert not reply_to_mismatch(
            'MAILER-DAEMON@plesk42.openwebhost.com.br',
            'contactarinteresse@gmail.com',
        )

    def test_legitimo_parceiro_nao_suspeito(self):
        assert not reply_to_mismatch(
            '"Daniela Silva" <contatos@instructorbras.sjc.br>',
            'respostas@treinaon.com.br',
        )

    def test_phishing_from_suspeito(self):
        assert reply_to_mismatch(
            'mxesqccz@cnco.top',
            'contactarinteresse@gmail.com',
            dominios_gratuitos=['gmail.com'],
        )


class TestPrefixoBloqueado:
    def test_contato_legitimo_modo_padrao_ignora(self):
        blocked = ['contato@']
        assert remetente_bloqueado_entrada(
            'Holding Familiar <contato@holdingcursos.com.br>',
            blocked,
            prefixo_estrito=False,
        ) is None

    def test_contato_spam_modo_padrao_ignora_prefixo(self):
        """prefixo@ não bloqueia sem modo estrito — use @dominio ou email exato."""
        blocked = ['contato@']
        assert remetente_bloqueado_entrada(
            'Patrícia <contato@contatol15.justrabalhos.com>',
            blocked,
            prefixo_estrito=False,
        ) is None

    def test_contato_spam_por_dominio(self):
        blocked = ['@contatol15.justrabalhos.com']
        assert remetente_bloqueado_entrada(
            'Patrícia <contato@contatol15.justrabalhos.com>',
            blocked,
        ) == '@contatol15.justrabalhos.com'

    def test_contato_estrito(self):
        blocked = ['contato@']
        assert remetente_bloqueado_entrada(
            'Holding <contato@holdingcursos.com.br>',
            blocked,
            prefixo_estrito=True,
        ) == 'contato@'

    def test_wildcard_prefixo_ignorado_sem_estrito(self):
        blocked = ['contato*@']
        assert remetente_bloqueado_entrada(
            'x <contato123@spam.evil.com>',
            blocked,
            prefixo_estrito=False,
        ) is None

    def test_wildcard_prefixo_estrito(self):
        blocked = ['contato*@']
        assert remetente_bloqueado_entrada(
            'x <contato123@spam.evil.com>',
            blocked,
            prefixo_estrito=True,
        ) == 'contato*@'


class TestDominioBase:
    def test_com_br(self):
        assert _dominio_base_registravel('mail.empresa.com.br') == 'empresa.com.br'

    def test_simples(self):
        assert _dominio_base_registravel('sub.example.com') == 'example.com'
