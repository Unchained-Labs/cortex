import pytest

from cortex import identity
from cortex.brain import Brain
from cortex.config import load_config


def test_starter_is_written_once(brain: Brain):
    assert identity.ensure(brain.config) is True
    assert identity.path(brain.config).is_file()
    assert identity.ensure(brain.config) is False  # never clobbers
    assert identity.is_untouched(brain.config)


def test_the_starter_never_reaches_the_prompt(brain: Brain):
    """A placeholder in every conversation is worse than nothing."""
    identity.ensure(brain.config)
    assert identity.effective(brain.config) == ""
    identity.write(brain.config, "# About\n\nWe eat at seven.\n")
    assert "We eat at seven" in identity.effective(brain.config)
    assert not identity.is_untouched(brain.config)


def test_an_existing_persona_string_still_works(brain_dir):
    """Upgrading must not drop a persona someone configured."""
    (brain_dir / "cortex.yaml").write_text(
        'name: t\npersona: "Be brief."\n', encoding="utf-8"
    )
    config = load_config(brain_dir)
    assert identity.effective(config) == "Be brief."

    identity.write(config, "We eat at seven.\n")
    both = identity.effective(config)
    assert "Be brief." in both and "We eat at seven." in both


def test_identity_is_length_capped(brain: Brain):
    with pytest.raises(identity.IdentityError, match="under"):
        identity.write(brain.config, "x" * (identity.MAX_IDENTITY_CHARS + 1))


def test_summarise_flattens_headings():
    assert identity.summarise("# Title\n\nsome body text") == "Title some body text"
    assert identity.summarise("x" * 200).endswith("…")


# -- proposals -------------------------------------------------------------


def test_the_agent_proposes_and_cannot_write(brain: Brain):
    identity.write(brain.config, "original\n")
    out = brain.registry.invoke("propose_identity_change", {
        "text": "rewritten by the agent", "reason": "they told me they eat at seven",
    }).text
    assert "Proposed" in out and "until somebody accepts" in out
    # the file is untouched: proposing is not writing
    assert identity.read(brain.config) == "original\n"
    pending = brain.store.identity_proposals()
    assert len(pending) == 1 and pending[0]["reason"].startswith("they told me")


def test_a_proposal_needs_text_and_a_reason(brain: Brain):
    assert "needs the full replacement" in brain.registry.invoke(
        "propose_identity_change", {"text": "  ", "reason": "x"}
    ).text
    assert "not reviewable" in brain.registry.invoke(
        "propose_identity_change", {"text": "new", "reason": " "}
    ).text
    assert brain.store.identity_proposals() == []


def test_read_identity_tool(brain: Brain):
    assert "no identity note yet" in brain.registry.invoke("read_identity", {}).text
    identity.write(brain.config, "We eat at seven.\n")
    assert "seven" in brain.registry.invoke("read_identity", {}).text


def test_deciding_a_proposal(brain: Brain):
    store = brain.store
    first = store.add_identity_proposal("version one", "because")
    assert store.decide_identity_proposal(first, "accepted", "erwin")
    assert not store.decide_identity_proposal(first, "accepted", "erwin")  # once only
    assert store.identity_proposals("pending") == []
    assert store.identity_proposals("")[0]["status"] == "accepted"
