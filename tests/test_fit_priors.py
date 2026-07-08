from dataclasses import fields

from fit.priors import (
    DEFAULT_ADDED_RESISTANCE_PRIORS,
    DEFAULT_CALM_RESISTANCE_PRIORS,
    DEFAULT_SFOC_PRIORS,
    Prior,
)


def _all_priors(*prior_structs):
    for struct in prior_structs:
        for f in fields(struct):
            yield f.name, getattr(struct, f.name)


def test_every_default_prior_has_a_nonempty_source():
    for name, prior in _all_priors(
        DEFAULT_CALM_RESISTANCE_PRIORS, DEFAULT_ADDED_RESISTANCE_PRIORS, DEFAULT_SFOC_PRIORS
    ):
        assert isinstance(prior, Prior)
        assert prior.source.strip(), f"{name} has an empty source"


def test_every_default_prior_has_positive_std():
    for name, prior in _all_priors(
        DEFAULT_CALM_RESISTANCE_PRIORS, DEFAULT_ADDED_RESISTANCE_PRIORS, DEFAULT_SFOC_PRIORS
    ):
        assert prior.std > 0, f"{name} has non-positive std"


def test_sfoc_min_load_fraction_prior_is_a_plausible_fraction():
    prior = DEFAULT_SFOC_PRIORS.sfoc_min_load_fraction
    assert 0.0 < prior.mean < 1.0


def test_head_factor_prior_exceeds_following_factor_prior():
    """Physically, head seas cause more added resistance than following
    seas -- the priors should reflect that ordering, not just be
    independently-plausible numbers."""
    added = DEFAULT_ADDED_RESISTANCE_PRIORS
    assert added.head_factor.mean > added.following_factor.mean


def test_calm_resistance_source_flags_the_holtrop_mennen_limitation():
    """The honesty check: this prior is explicitly NOT a rigorous
    Holtrop-Mennen regression (HullParticulars lacks the inputs), and the
    source string must say so rather than implying more precision than it
    has (design principle #4)."""
    source = DEFAULT_CALM_RESISTANCE_PRIORS.linear_coefficient.source.lower()
    assert "holtrop" in source
    assert "not a rigorous" in source or "not a literal" in source
