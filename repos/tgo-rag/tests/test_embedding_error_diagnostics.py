from src.rag_service.services.qa_errors import safe_exception_chain


def test_exception_chain_keeps_types_not_secrets():
    root = ConnectionError('https://private:secret@example.test')
    failure = RuntimeError('api_key=secret')
    failure.__cause__ = root
    assert safe_exception_chain(failure) == 'RuntimeError > ConnectionError'


def test_exception_chain_is_bounded_for_cycles():
    failure = RuntimeError('secret')
    failure.__cause__ = failure
    assert safe_exception_chain(failure) == 'RuntimeError'
