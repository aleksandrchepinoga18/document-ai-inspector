"""Profiles are versioned repository files, never arbitrary client paths."""

import re
from pathlib import Path

from backend.core.schemas import ProfileOption, RuleProfile
from backend.documents.errors import DocumentError


PROFILE_DIRECTORY = Path(__file__).with_name("profiles")


def list_profiles() -> list[ProfileOption]:
    return [ProfileOption(name=profile.name, version=profile.version)
            for path in sorted(PROFILE_DIRECTORY.glob("*.json"))
            for profile in [load_profile(path.stem)]]


def load_profile(name: str) -> RuleProfile:
    if not re.fullmatch(r"[a-z0-9_-]{1,64}", name):
        raise DocumentError("unknown_profile", "Неизвестный профиль.")
    path = PROFILE_DIRECTORY / f"{name}.json"
    if not path.is_file():
        raise DocumentError("unknown_profile", "Неизвестный профиль.")
    profile = RuleProfile.model_validate_json(path.read_text(encoding="utf-8"))
    if profile.name != name:
        raise ValueError("Profile name does not match its filename")
    re.compile(profile.table_caption_pattern)
    return profile
