"""Disposable parsing process. Parent enforces a hard wall-clock timeout."""

import json
import sys
import time
from pathlib import Path

from backend.core.config import Settings
from backend.core.schemas import RuleProfile
from backend.documents.errors import DocumentError
from backend.documents.parser import parse_document
from backend.documents.rules import inspect_document
from backend.documents.security import validate_docx


def main():
    try:
        request = json.loads(sys.stdin.read())
        settings = Settings(_env_file=None, **request["settings"])
        profile = RuleProfile.model_validate(request["profile"])
        started = time.monotonic()
        data = Path(request["original_path"]).read_bytes()
        validate_docx(data, settings)
        parsed = parse_document(data)
        findings = inspect_document(parsed, profile)
        result = {
            "original_sha256": parsed.sha256,
            "elements": [e.model_dump(mode="json") for e in parsed.elements],
            "findings": [f.model_dump(mode="json") for f in findings],
            "unsupported_features": [f.model_dump(mode="json") for f in parsed.unsupported],
            "metrics": {
                "duration_seconds": round(time.monotonic() - started, 4),
                "element_count": len(parsed.elements),
                "finding_count": len(findings),
                "undetermined_count": sum(f.status == "undetermined" for f in findings),
                "unsupported_count": len(parsed.unsupported),
            },
        }
    except DocumentError as exc:
        result = {"error": {"code": exc.code, "message": exc.message, "status_code": exc.status_code}}
    except Exception:
        # Corrupt documents must not leak local paths, traceback or document text.
        result = {"error": {
            "code": "processing_failed", "message": "Не удалось разобрать документ.", "status_code": 422,
        }}
    sys.stdout.write(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
