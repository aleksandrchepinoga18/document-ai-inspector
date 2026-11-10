"""Event-level scoring with maximum one-to-one matching and explicit denominators."""

import math
from statistics import mean

from backend.llm.schemas import SemanticIssue
from evaluation.dataset import GoldEvent


LEGACY_MATCHING_VERSION = "type-exact-locations-evidence-v1"
MATCHING_VERSION = "type-exact-locations-terminal-period-v2"
METRICS_VERSION = "event-quality-validation-breakdown-v2"


def evidence_text(text: str) -> str:
    # Only surrounding whitespace and ONE final full stop are insignificant.
    # Do not erase numbers, internal punctuation, negation, qualifiers or ellipses.
    text = text.strip()
    return text[:-1] if text.endswith(".") and not text.endswith("..") else text


def compatible(prediction: SemanticIssue, gold: GoldEvent, *, matching_version=MATCHING_VERSION) -> bool:
    if matching_version not in {MATCHING_VERSION, LEGACY_MATCHING_VERSION}:
        raise ValueError("Unknown matching version")
    normalize = evidence_text if matching_version == MATCHING_VERSION else lambda text: text
    refs = {r.element_id: r.quote for r in prediction.references}
    return (prediction.error_type == gold.error_type
            and len(refs) == len(prediction.references)
            and set(refs) == {r.element_id for r in gold.references}
            and all(normalize(r.quote) in normalize(refs[r.element_id]) for r in gold.references))


def score_events(predictions: list[SemanticIssue], gold: list[GoldEvent], *, matching_version=MATCHING_VERSION) -> dict:
    edges = [[j for j, event in enumerate(gold) if compatible(prediction, event, matching_version=matching_version)] for prediction in predictions]
    matched = {}

    def augment(index, visited):
        for target in edges[index]:
            if target in visited:
                continue
            visited.add(target)
            if target not in matched or augment(matched[target], visited):
                matched[target] = index
                return True
        return False

    tp = sum(augment(i, set()) for i in range(len(predictions)))
    return quality(tp, len(predictions) - tp, len(gold) - tp)


def quality(tp: int, fp: int, fn: int) -> dict:
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else 0.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0}


API_ERRORS = {"llm_timeout", "llm_unavailable", "llm_auth_failed", "llm_quota_or_rate_limit",
              "llm_model_or_endpoint_not_found", "llm_request_rejected"}
VALIDATION_ERRORS = {"invalid_llm_json", "invalid_llm_reference", "invalid_llm_evidence"}


def aggregate(rows: list[dict]) -> dict:
    scored = [r for r in rows if r["kind"] != "ambiguous"]
    durations = sorted(r["duration_seconds"] for r in rows)
    count = len(rows)
    result = quality(*(sum(r[k] for r in scored) for k in ("tp", "fp", "fn")))
    result.update(case_count=count, scored_case_count=len(scored),
                  ambiguous_case_count=count - len(scored),
                  ambiguous_predictions=sum(r["prediction_count"] for r in rows if r["kind"] == "ambiguous"),
                  json_valid_rate=sum(r["json_valid"] is True for r in rows) / count if count else 0.0,
                  references_valid_rate=sum(r["references_valid"] is True for r in rows) / count if count else 0.0,
                  latency_mean_seconds=mean(durations) if durations else 0.0,
                  latency_p95_seconds=durations[math.ceil(.95 * count) - 1] if count else 0.0,
                  api_error_count=sum(r["error_code"] in API_ERRORS for r in rows),
                  failed_case_count=sum(r["status"] != "completed" for r in rows),
                  api_attempt_count=sum(r["attempts"] or 0 for r in rows),
                  unknown_attempt_case_count=sum(r["attempts"] is None for r in rows))
    # Failures on negative documents otherwise disappear from event-level P/R/F1.
    for kind in ("positive", "negative", "ambiguous"):
        group = [r for r in rows if r["kind"] == kind]
        result[f"{kind}_case_count"] = len(group)
        result[f"{kind}_validation_error_count"] = sum(r["error_code"] in VALIDATION_ERRORS for r in group)
    result.update(validation_error_count=sum(r["error_code"] in VALIDATION_ERRORS for r in rows),
                  evidence_error_count=sum(r["error_code"] == "invalid_llm_evidence" for r in rows),
                  reference_error_count=sum(r["error_code"] == "invalid_llm_reference" for r in rows),
                  json_error_count=sum(r["error_code"] == "invalid_llm_json" for r in rows),
                  failed_negative_case_count=sum(r["kind"] == "negative" and r["status"] != "completed" for r in rows),
                  successful_empty_case_count=sum(r["status"] == "completed" and r["prediction_count"] == 0 for r in rows),
                  negative_false_positive_case_count=sum(r["kind"] == "negative" and r["fp"] > 0 for r in rows))
    observed = [r for r in rows if r.get("response_issue_count") is not None]
    nonempty = [r for r in observed if r["response_issue_count"] > 0]
    result.update(issue_count_observed_case_count=len(observed), observed_nonempty_response_case_count=len(nonempty),
                  observed_discarded_issue_count=sum(r.get("discarded_issue_count") or 0 for r in rows),
                  discard_count_observed_case_count=sum(r.get("discarded_issue_count") is not None for r in rows),
                  observed_nonempty_references_valid_case_count=sum(r["references_valid"] is True for r in nonempty))
    return result
