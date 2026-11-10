"""Independent matching/counting checks, including duplicates and ambiguous cases."""

import pytest

from backend.llm.schemas import SemanticIssue
from evaluation.dataset import GoldEvent
from evaluation.metrics import LEGACY_MATCHING_VERSION, aggregate, score_events


def event(a="The price is ten", b="The price is thirty", first="a", second="b"):
    return {"error_type": "contradiction", "references": [{"element_id": first, "quote": a},
            {"element_id": second, "quote": b}]}


def prediction(**kwargs):
    return SemanticIssue(**event(**kwargs), explanation="Mutually exclusive prices.", suggestion="Review source facts.")


def test_duplicates_do_not_inflate_recall_and_missing_events_count():
    gold = [GoldEvent(**event()), GoldEvent(**event(first="c", second="d"))]
    result = score_events([prediction(), prediction()], gold)
    assert result == {"tp": 1, "fp": 1, "fn": 1, "precision": .5, "recall": .5, "f1": .5}


@pytest.mark.parametrize("changes", [{"first": "wrong"}, {"a": "The price"}])
def test_wrong_location_or_insufficient_evidence_cannot_match(changes):
    assert score_events([prediction(**changes)], [GoldEvent(**event())])["tp"] == 0


def test_matching_is_order_independent_and_maximum_cardinality():
    gold = [GoldEvent(**event()), GoldEvent(**event(a="The price is ten plus tax"))]
    broad = prediction(a="The price is ten plus tax")
    narrow = prediction()
    for predictions in ([broad, narrow], [narrow, broad]):
        assert score_events(predictions, gold)["tp"] == 2
    reversed_prediction = prediction()
    reversed_prediction.references.reverse()
    assert score_events([reversed_prediction], gold[:1])["tp"] == 1


def test_empty_denominators_are_zero():
    assert score_events([], []) == {"tp": 0, "fp": 0, "fn": 0, "precision": 0, "recall": 0, "f1": 0}


def test_terminal_period_does_not_create_false_positive_and_false_negative():
    gold = [GoldEvent(**event(a="The price is ten.", b="The price is thirty."))]
    assert score_events([prediction()], gold)["tp"] == 1
    legacy = score_events([prediction()], gold, matching_version=LEGACY_MATCHING_VERSION)
    assert (legacy["tp"], legacy["fp"], legacy["fn"]) == (0, 1, 1)


@pytest.mark.parametrize("text", ["The price is ten", "The price is not ten.", "The price is 1.5.",
                                  "The price is ten...", "The price is ten plus tax."])
def test_period_fix_does_not_erase_claims_or_internal_punctuation(text):
    # Gold qualifies the value: shortening it to a bare number must still fail.
    gold = [GoldEvent(**event(a="The price is ten plus VAT.", b="The price is thirty."))]
    assert score_events([prediction(a=text)], gold)["tp"] == 0


def test_negative_validation_failure_is_not_a_successful_empty_answer():
    base = dict(kind="negative", tp=0, fp=0, fn=0, prediction_count=0, duration_seconds=1,
                json_valid=True, attempts=1)
    failed = dict(base, status="failed", error_code="invalid_llm_evidence", references_valid=False,
                  response_issue_count=1, discarded_issue_count=1)
    empty = dict(base, status="completed", error_code=None, references_valid=True,
                 response_issue_count=0, discarded_issue_count=0)
    result = aggregate([failed, empty])
    assert result["fp"] == 0  # Keep end-to-end event metric policy explicit.
    assert result["failed_negative_case_count"] == result["negative_validation_error_count"] == 1
    assert result["successful_empty_case_count"] == result["observed_nonempty_response_case_count"] == 1
    assert result["observed_discarded_issue_count"] == 1
    assert result["observed_nonempty_references_valid_case_count"] == 0


def test_aggregation_counts_failures_and_keeps_ambiguous_out_of_quality():
    rows = []
    for i in range(20):
        rows.append(dict(kind="negative", tp=0, fp=0, fn=0, prediction_count=0,
                         duration_seconds=i + 1, json_valid=True, references_valid=True,
                         status="completed", error_code=None, attempts=1))
    rows[0].update(kind="positive", tp=1)
    rows[1].update(status="failed", error_code="llm_timeout", attempts=None,
                   json_valid=None, references_valid=None, kind="positive", fn=1)
    rows[-1].update(kind="ambiguous", fp=1, prediction_count=1)
    result = aggregate(rows)
    assert (result["tp"], result["fp"], result["fn"]) == (1, 0, 1)
    assert result["f1"] == pytest.approx(2 / 3)
    assert result["json_valid_rate"] == result["references_valid_rate"] == .95
    assert result["latency_mean_seconds"] == 10.5 and result["latency_p95_seconds"] == 19
    assert result["api_error_count"] == result["failed_case_count"] == 1
    assert result["ambiguous_predictions"] == result["unknown_attempt_case_count"] == 1
    assert aggregate([])["case_count"] == 0
