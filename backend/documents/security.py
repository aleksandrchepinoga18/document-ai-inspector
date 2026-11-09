"""Bounded DOCX/OPC validation. ZIP contents are never extracted to disk."""

import io
import posixpath
import re
import stat
import zipfile
from urllib.parse import unquote, urlsplit

from lxml import etree

from backend.core.config import Settings
from backend.documents.errors import DocumentError


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
MAIN_TYPE = DOCX_MIME + ".main+xml"
REQUIRED = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}


def validate_upload_metadata(filename: str | None, content_type: str | None) -> None:
    if (
        not filename or len(filename) > 255 or "/" in filename or "\\" in filename
        or not filename.lower().endswith(".docx")
    ):
        raise DocumentError("unsupported_file", "Допускается только файл .docx без пути в имени.", 415)
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if mime not in {DOCX_MIME, "application/octet-stream"}:
        raise DocumentError("unsupported_mime", "Неподдерживаемый MIME-тип DOCX.", 415)


def safe_xml(data: bytes) -> etree._Element:
    try:
        parser = etree.XMLParser(
            resolve_entities=False, no_network=True, load_dtd=False,
            recover=False, huge_tree=False,
        )
        root = etree.fromstring(data, parser)
        if root.getroottree().docinfo.doctype or any(
            isinstance(node, etree._Entity) for node in root.iter()
        ):
            raise DocumentError("unsafe_xml", "DTD и XML-сущности не допускаются.")
        return root
    except etree.XMLSyntaxError as exc:
        raise DocumentError("invalid_xml", "Повреждённый XML внутри DOCX.") from exc


def validate_docx(data: bytes, settings: Settings) -> None:
    if not data:
        raise DocumentError("empty_file", "Передан пустой файл.")
    if len(data) > settings.max_upload_bytes:
        raise DocumentError("file_too_large", "Превышен лимит размера DOCX.", 413)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > settings.max_zip_entries:
                raise DocumentError("zip_entry_limit", "Слишком много элементов в ZIP.")
            names: set[str] = set()
            total = 0
            for entry in entries:
                name = entry.filename
                if (
                    not name or name.startswith("/") or "\\" in name or ":" in name
                    or "\x00" in name or ".." in name.split("/")
                    or entry.orig_filename != name
                    or stat.S_ISLNK(entry.external_attr >> 16)
                ):
                    raise DocumentError("unsafe_zip_path", "Недопустимый путь внутри ZIP.")
                if name in names:
                    raise DocumentError("duplicate_zip_entry", "Дублирующиеся элементы ZIP.")
                names.add(name)
                if entry.flag_bits & 1 or entry.compress_type not in {
                    zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED,
                }:
                    raise DocumentError("unsupported_zip", "Шифрование или метод сжатия ZIP не поддерживается.")
                total += entry.file_size
                if total > settings.max_uncompressed_bytes:
                    raise DocumentError("zip_size_limit", "Превышен лимит распакованного DOCX.")
                if entry.file_size / max(entry.compress_size, 1) > settings.max_zip_ratio:
                    raise DocumentError("zip_ratio_limit", "Превышено допустимое отношение распаковки.")
                if re.search(r"(?:^|/)vbaProject\.bin$", name, re.IGNORECASE):
                    raise DocumentError("macros_not_allowed", "Документы с макросами не допускаются.")
            if not REQUIRED.issubset(names):
                raise DocumentError("missing_ooxml_parts", "В DOCX отсутствуют обязательные OOXML-части.")

            types = safe_xml(archive.read("[Content_Types].xml"))
            overrides = {item.get("PartName"): item.get("ContentType", "") for item in types}
            defaults = {item.get("Extension"): item.get("ContentType", "") for item in types}
            xml_parts: dict[str, etree._Element] = {}
            for entry in entries:
                if entry.is_dir():
                    continue
                # Check actual decompressed content and CRC, including binary parts.
                with archive.open(entry) as stream:
                    payload = stream.read(min(entry.file_size, settings.max_uncompressed_bytes) + 1)
                if len(payload) != entry.file_size:
                    raise DocumentError("invalid_zip_size", "Некорректный размер элемента ZIP.")
                content_type = overrides.get("/" + entry.filename) or defaults.get(entry.filename.rsplit(".", 1)[-1], "")
                if entry.filename.endswith((".xml", ".rels")) or content_type.endswith(("+xml", "/xml")):
                    xml_parts[entry.filename] = safe_xml(payload)

            types = xml_parts["[Content_Types].xml"]
            if types.tag != f"{{{CT}}}Types":
                raise DocumentError("invalid_content_types", "Некорректный список типов OOXML.")
            if any(
                "macroenabled" in item.get("ContentType", "").lower()
                or "vba" in item.get("ContentType", "").lower()
                for item in types
            ):
                raise DocumentError("macros_not_allowed", "Документы с макросами не допускаются.")
            main_parts = [
                item for item in types
                if item.tag == f"{{{CT}}}Override" and item.get("PartName") == "/word/document.xml"
            ]
            if len(main_parts) != 1 or main_parts[0].get("ContentType") != MAIN_TYPE:
                raise DocumentError("invalid_document_type", "Основная часть не является обычным DOCX.")
            if xml_parts["word/document.xml"].tag != f"{{{W}}}document":
                raise DocumentError("unsupported_ooxml", "Поддерживается transitional WordprocessingML DOCX.")
            if xml_parts["word/document.xml"].find(f"{{{W}}}body") is None:
                raise DocumentError("missing_document_body", "Отсутствует тело документа.")

            main_links = []
            for name, root in xml_parts.items():
                if not name.endswith(".rels"):
                    continue
                if root.tag != f"{{{REL}}}Relationships":
                    raise DocumentError("invalid_relationships", "Некорректные связи OOXML.")
                base = "" if name == "_rels/.rels" else posixpath.dirname(posixpath.dirname(name))
                ids: set[str] = set()
                for link in root:
                    if not isinstance(link.tag, str):
                        continue
                    target = unquote(link.get("Target", ""))
                    link_id = link.get("Id")
                    if link.tag != f"{{{REL}}}Relationship" or not link_id or link_id in ids:
                        raise DocumentError("invalid_relationships", "Некорректные идентификаторы связей OOXML.")
                    ids.add(link_id)
                    if link.get("TargetMode", "Internal") != "Internal":
                        raise DocumentError("external_relationship", "Внешние связи, включая гиперссылки, пока не допускаются.")
                    split = urlsplit(target)
                    if not target or split.scheme or split.netloc or "\\" in target or "\x00" in target:
                        raise DocumentError("unsafe_relationship", "Недопустимая цель связи OOXML.")
                    resolved = posixpath.normpath(
                        split.path.lstrip("/") if split.path.startswith("/")
                        else posixpath.join(base, split.path)
                    )
                    if resolved.startswith("../") or resolved == ".." or resolved not in names:
                        raise DocumentError("broken_relationship", "Связь OOXML выходит за пакет или ссылается на отсутствующую часть.")
                    if name == "_rels/.rels" and link.get("Type", "").endswith("/officeDocument"):
                        main_links.append(resolved)
            if main_links != ["word/document.xml"]:
                raise DocumentError("missing_document_relationship", "Нет связи с основной частью DOCX.")
    except DocumentError:
        raise
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, ValueError, OSError) as exc:
        raise DocumentError("invalid_docx", "Файл повреждён или не является корректным DOCX.") from exc
