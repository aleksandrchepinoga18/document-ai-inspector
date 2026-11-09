"""Disposable parsing process. Parent enforces a hard wall-clock timeout."""

import json
import sys
import time
import hashlib
from pathlib import Path

from backend.core.config import Settings
from backend.core.schemas import Correction, RuleProfile
from backend.documents.editor import apply_fixes
from backend.documents.validator import validate_invariants
from backend.documents.errors import DocumentError
from backend.documents.parser import parse_document
from backend.documents.rules import inspect_document
from backend.documents.security import validate_docx


def process_request(request):
    try:
        settings = Settings(_env_file=None, **request["settings"])
        profile = RuleProfile.model_validate(request["profile"])
        started = time.monotonic()
        data = Path(request["original_path"]).read_bytes()
        validate_docx(data, settings)
        parsed = parse_document(data)
        findings = inspect_document(parsed, profile)
        correction = Correction()
        if request.get("auto_fix", False):
            try:
                candidate, edits = apply_fixes(data, parsed, findings)
                invariants = validate_invariants(data, candidate, edits, profile, settings)
                if invariants.passed:
                    corrected_findings = inspect_document(parse_document(candidate), profile)
                    if any(f.rule_id == "font_size_pt" and f.element_id == edit.element_id
                           for f in corrected_findings for edit in edits):
                        invariants.passed = False
                        invariants.errors.append("correction_not_effective")
                if invariants.passed:
                    correction = Correction(
                        status="applied" if edits else "unchanged", validation="passed",
                        edits=edits, output_sha256=hashlib.sha256(candidate).hexdigest(),
                    )
                    if edits:
                        target = Path(request["original_path"]).with_name("corrected.docx")
                        with target.open("xb") as stream:
                            stream.write(candidate)
                    applied = {edit.finding_id for edit in edits}
                    for finding in findings:
                        if finding.id in applied:
                            finding.status = "fixed"
                else:
                    for edit in edits:
                        edit.status = "rejected"
                    correction = Correction(
                        status="rejected", validation="failed", errors=invariants.errors, edits=edits,
                    )
            except Exception:
                correction = Correction(status="rejected", validation="failed", errors=["editing_failed"])
        result = {
            "original_sha256": parsed.sha256,
            "elements": [e.model_dump(mode="json") for e in parsed.elements],
            "findings": [f.model_dump(mode="json") for f in findings],
            "unsupported_features": [f.model_dump(mode="json") for f in parsed.unsupported],
            "correction": correction.model_dump(mode="json"),
            "metrics": {
                "duration_seconds": round(time.monotonic() - started, 4),
                "element_count": len(parsed.elements),
                "finding_count": len(findings),
                "undetermined_count": sum(f.status == "undetermined" for f in findings),
                "unsupported_count": len(parsed.unsupported),
                "fixed_count": sum(f.status == "fixed" for f in findings),
                "open_count": sum(f.status == "open" for f in findings),
            },
        }
    except DocumentError as exc:
        result = {"error": {"code": exc.code, "message": exc.message, "status_code": exc.status_code}}
    except Exception:
        # Corrupt documents must not leak local paths, traceback or document text.
        result = {"error": {
            "code": "processing_failed", "message": "Не удалось разобрать документ.", "status_code": 422,
        }}
    return result


def main():
    result = process_request(json.loads(sys.stdin.read()))
    sys.stdout.write(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
