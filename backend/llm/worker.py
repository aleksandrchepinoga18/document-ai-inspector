"""Disposable, read-only LLM phase. No editing or artifact-writing entry points."""

import json
import sys
from pathlib import Path

from backend.core.config import Settings
from backend.core.schemas import LLMAnalysis
from backend.documents.parser import parse_document
from backend.documents.security import validate_docx
from backend.llm.analyzer import analyze_document


def process_request(request):
    try:
        # Credentials come from this process's environment, never JSON/files/CLI args.
        settings = Settings(_env_file=None, **request["settings"])
        data = Path(request["original_path"]).read_bytes()
        validate_docx(data, settings)
        parsed = parse_document(data)
        findings, metadata = analyze_document(
            parsed, settings, request["provider"], allow_cloud=request.get("allow_cloud", False),
            synthetic_data=request.get("synthetic_data", False),
        )
        return {"findings": [f.model_dump(mode="json") for f in findings], "llm": metadata.model_dump(mode="json")}
    except Exception:
        return {"findings": [], "llm": LLMAnalysis(
            status="failed", provider=request["provider"], error_code="llm_failed",
            attempts=None,
            message="LLM-фаза завершилась с ошибкой; формальная проверка сохранена.",
        ).model_dump(mode="json")}


def main():
    sys.stdout.write(json.dumps(process_request(json.loads(sys.stdin.read())), ensure_ascii=True))


if __name__ == "__main__":
    main()
