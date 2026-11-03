"""Another scope cannot decrypt retained evidence."""

from uuid import uuid4

import pytest

from app.exceptions import ConflictError
from app.services.script_evidence_vault import ScriptEvidenceVault


def test_cipher_is_randomized_and_bound_to_exact_owner_and_record():
    vault = ScriptEvidenceVault("fixture-evidence-key-" + "x" * 32)
    scope = [uuid4() for _ in range(4)]
    private = '{"label":"private-customer-fixture"}'
    first = vault.encrypt(*scope, "case", private)
    second = vault.encrypt(*scope, "case", private)
    assert first != second and private not in first
    assert vault.decrypt(*scope, "case", first) == private
    for index in range(4):
        foreign = scope.copy()
        foreign[index] = uuid4()
        with pytest.raises(ConflictError) as error:
            vault.decrypt(*foreign, "case", first)
        assert private not in str(error.value)
    with pytest.raises(ConflictError):
        vault.decrypt(*scope, "result", first)
    with pytest.raises(ConflictError):
        ScriptEvidenceVault("different-fixture-key-" + "y" * 32).decrypt(
            *scope, "case", first
        )


@pytest.mark.parametrize(
    "value",
    ["", "v1:???", "v2:AAAA", "v1:" + "a" * 200001],
    ids=["empty", "invalid-base64", "unknown-version", "oversize"],
)
def test_corrupt_or_oversize_ciphertext_never_leaks_body(value):
    with pytest.raises(ConflictError):
        ScriptEvidenceVault("fixture-key-" + "x" * 40).decrypt(
            *(uuid4() for _ in range(4)), "case", value
        )


def test_placeholder_keys_and_oversize_plaintext_are_rejected():
    scope = [uuid4() for _ in range(4)]
    for key in (
        "",
        "short",
        "your-super-secret-key-change-this-in-production",
    ):
        with pytest.raises(ConflictError):
            ScriptEvidenceVault(key).encrypt(*scope, "case", "private")
    with pytest.raises(ConflictError):
        ScriptEvidenceVault("fixture-key-" + "x" * 40).encrypt(
            *scope, "case", "a" * 131073
        )
