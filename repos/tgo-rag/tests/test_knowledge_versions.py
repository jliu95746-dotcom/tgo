"""Version publication invariants independent of providers and business data."""
import pytest
from src.rag_service.schemas.knowledge_versions import VersionChunk, VersionSnapshot
from src.rag_service.services.knowledge_versions import snapshot_digest, validate_snapshot


def snapshot(text='旧答案', vector=None):
    return VersionSnapshot(chunks=[VersionChunk(
        content=text, embedding=vector or [0.1] * 1536, embedding_model='fixture',
    )])


def test_digest_ignores_new_embeddings_for_unchanged_text():
    assert snapshot_digest(snapshot()) == snapshot_digest(snapshot(vector=[0.2] * 1536))
    assert snapshot_digest(snapshot()) != snapshot_digest(snapshot('新答案'))


@pytest.mark.parametrize('vector', [[0.0] * 1536, [0.1], [float('nan')] * 1536])
def test_invalid_vectors_cannot_be_published(vector):
    with pytest.raises(ValueError):
        validate_snapshot(snapshot(vector=vector))


def test_empty_content_cannot_replace_a_working_version():
    with pytest.raises(ValueError):
        validate_snapshot(VersionSnapshot())
