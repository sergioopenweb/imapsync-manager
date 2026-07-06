"""
Integração com o pacote spam-analyzer para classificar emails como spam.
Usado durante a sincronização: se ativo, emails detectados como spam podem
ser enviados para a pasta Spam ou arquivados (pular inbox).
"""
import logging
import tempfile
import os
import asyncio

logger = logging.getLogger(__name__)

_SPAM_ANALYZER_AVAILABLE = None
_SKLEARN_COMPAT_PATCHED = False


def _patch_sklearn_pickle_compat():
    """
    Modelos do spam-analyzer foram serializados com sklearn antigo.
    sklearn>=1.4 exige monotonic_cst em DecisionTreeClassifier ao prever.
    """
    global _SKLEARN_COMPAT_PATCHED
    if _SKLEARN_COMPAT_PATCHED:
        return
    try:
        from sklearn.tree import DecisionTreeClassifier
        from spamanalyzer.ml import SpamClassifier

        def _fix_tree(est):
            if isinstance(est, DecisionTreeClassifier) and not hasattr(est, 'monotonic_cst'):
                est.monotonic_cst = None

        def _walk(obj, seen):
            oid = id(obj)
            if oid in seen:
                return
            seen.add(oid)
            _fix_tree(obj)
            for attr in ('estimators_', 'estimators'):
                children = getattr(obj, attr, None)
                if children is not None:
                    try:
                        for child in children:
                            _walk(child, seen)
                    except TypeError:
                        _walk(children, seen)

        _orig_init = SpamClassifier.__init__

        def _patched_init(self, path_to_model: str) -> None:
            _orig_init(self, path_to_model)
            _walk(self.model, set())

        SpamClassifier.__init__ = _patched_init
        _SKLEARN_COMPAT_PATCHED = True
    except Exception as e:
        logger.debug(f"Patch sklearn/spam-analyzer não aplicado: {e}")


def _check_availability():
    global _SPAM_ANALYZER_AVAILABLE
    if _SPAM_ANALYZER_AVAILABLE is not None:
        return _SPAM_ANALYZER_AVAILABLE
    try:
        from spamanalyzer import SpamAnalyzer
        _patch_sklearn_pickle_compat()
        _SPAM_ANALYZER_AVAILABLE = True
        return True
    except ImportError:
        _SPAM_ANALYZER_AVAILABLE = False
        logger.debug("spam-analyzer não instalado; análise de spam desativada")
        return False


def is_available():
    """Retorna True se o pacote spam-analyzer está instalado e pode ser usado."""
    return _check_availability()


# Palavras que aparecem em todo email (cabeçalhos, etc.) — não usar como filtro de spam
_STOPWORDS_WORDLIST = frozenset([
    'from', 'to', 're', 'subject', 'date', 'message', 'sent', 'mail', 'com', 'net', 'org', 'br',
])
_MIN_LEN_WORDLIST = 5  # palavras com menos caracteres são ignoradas


def _build_wordlist(config: dict) -> list:
    """Monta lista de entradas a partir de wordlist_extra: uma por linha (frase ou palavra).
    Cada linha é uma entrada; frases com 2+ palavras são mantidas para reduzir falsos positivos."""
    extra = (config or {}).get('wordlist_extra') or ''
    if not extra.strip():
        return []
    entries = []
    seen = set()
    for line in extra.replace(',', '\n').splitlines():
        entry = line.strip().lower()
        if not entry or entry in seen:
            continue
        # Palavra única: ignora stopwords e muito curtas
        if ' ' not in entry:
            if len(entry) < _MIN_LEN_WORDLIST or entry in _STOPWORDS_WORDLIST:
                continue
        seen.add(entry)
        entries.append(entry)
    return entries


def is_spam(raw_email_bytes: bytes, config: dict = None) -> bool:
    """
    Classifica um email (bytes brutos RFC822) como spam ou não.
    Usa o pacote spam-analyzer; se não estiver instalado, retorna False (não spam).
    config: opcional, dict com wordlist_extra (str) e/ou model_path (str) para afinar o filtro.
    """
    if not _check_availability():
        return False
    if not raw_email_bytes or len(raw_email_bytes) == 0:
        return False

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.eml', delete=False) as f:
            f.write(raw_email_bytes)
            tmp_path = f.name

        wordlist = _build_wordlist(config or {})
        model_path = (config or {}).get('model_path') or None
        if model_path and not os.path.isfile(model_path):
            model_path = None

        analyzer, result = asyncio.run(_analyze_coro(tmp_path, wordlist=wordlist, model_path=model_path))
        if result is None:
            logger.debug("Spam Analyzer: analyze retornou None")
            return False
        is_spam_result = False
        if analyzer is not None and hasattr(analyzer, 'is_spam'):
            is_spam_result = bool(analyzer.is_spam(result))
        else:
            is_spam_result = bool(getattr(result, 'is_spam', lambda: False)())
        logger.debug(f"Spam Analyzer: is_spam={is_spam_result}")
        return is_spam_result
    except Exception as e:
        logger.warning(f"Erro ao analisar spam: {e}")
        return False
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


async def _analyze_coro(path: str, wordlist: list = None, model_path: str = None):
    """Coroutine que chama o SpamAnalyzer.analyze(). Retorna (analyzer, result)."""
    from spamanalyzer import SpamAnalyzer
    wordlist = wordlist or []
    try:
        # API do pacote: SpamAnalyzer(wordlist, model=None)
        analyzer = SpamAnalyzer(wordlist, model_path)
    except TypeError:
        # Algumas versões aceitam forbidden_words= ou só ()
        try:
            analyzer = SpamAnalyzer(forbidden_words=wordlist)
        except TypeError:
            analyzer = SpamAnalyzer()
    result = await analyzer.analyze(path)
    return (analyzer, result)
