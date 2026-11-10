"""Streamlit -> real FastAPI; only a button click submits a document."""

import hashlib
import json
import os

import streamlit as st

from frontend.client import APIClient, APIError, DOCX_MIME
from samples.generate import build_document


PROVIDERS = {"Без LLM": "none", "Ollama (локальная)": "ollama",
             "Yandex AI Studio (облако, платно)": "yandex", "vLLM (свой сервер)": "vllm"}
SAMPLES = {"Ошибки оформления": "format_errors", "Смысловое противоречие": "semantic_conflict",
           "Без ошибок оформления": "valid"}


@st.cache_data(show_spinner=False)
def sample_bytes(name):
    # Only public synthetic examples are cached; user uploads/results are not.
    return build_document(name)


def finding_rows(report):
    return [{"Источник": f.source, "Правило": f.rule_id, "Важность": f.severity, "Статус": f.status,
             "Место": "\n".join(f.locations), "Цитата": f.evidence,
             "Фактически": json.dumps(f.actual, ensure_ascii=False),
             "Ожидается": json.dumps(f.expected, ensure_ascii=False), "Рекомендация": f.suggestion,
             "Автоправка разрешена": f.auto_fix_allowed} for f in report.findings]


def display_result(result):
    report = result.report
    st.subheader("Результат проверки")
    st.caption(f"Задание: {report.job_id} · Профиль: {report.profile} v{report.profile_version}")
    cols = st.columns(3)
    cols[0].metric("Всего замечаний", len(report.findings))
    cols[1].metric("Исправлено", sum(f.status == "fixed" for f in report.findings))
    cols[2].metric("Требуют проверки", sum(f.status != "fixed" for f in report.findings))
    correction = report.correction
    if correction.status == "applied":
        st.success("Безопасные правки применены в отдельной копии; инварианты пройдены.")
    elif correction.status == "rejected":
        st.error("Правки отклонены проверкой инвариантов. Исправленная копия недоступна.")
        st.write(", ".join(correction.errors))
    elif correction.status == "unchanged":
        st.info("Разрешённых правок для этого документа нет. Результат совпадает с исходником.")
    else:
        st.info("Автоисправление выключено. DOCX результата совпадает с исходником.")
    if report.use_llm:
        if report.llm.status == "completed":
            count = sum(f.source == "llm" for f in report.findings)
            st.info(f"LLM: {report.llm.provider} · {report.llm.model}. Принято замечаний: {count}.")
            if not count:
                st.caption("Отсутствие замечаний LLM не доказывает отсутствие смысловых ошибок.")
        else:
            st.warning(f"LLM-анализ не завершён: {report.llm.error_code or report.llm.status}. Формальные результаты сохранены.")
    if report.findings:
        st.dataframe(finding_rows(report), hide_index=True, width="stretch")
    else:
        st.info("По выбранным проверкам замечаний не найдено.")
    if report.unsupported_features:
        with st.expander("Ограничения обработки этого документа", expanded=True):
            for feature in report.unsupported_features:
                st.text(f"{feature.feature}: {feature.evidence} ({feature.element_id})")
    if correction.edits:
        with st.expander("Журнал правок"):
            st.dataframe([edit.model_dump(mode="json") for edit in correction.edits], hide_index=True)
    if report.preview.status == "failed":
        st.warning(f"PDF недоступен: {report.preview.error_code}. DOCX и отчёт можно скачать отдельно.")
    if report.preview.status == "available":
        st.caption(f"PDF: {report.preview.pages} стр.; источник: {report.preview.source}. Просмотрите вёрстку вручную.")
    labels = {"docx": "Скачать исправленный DOCX" if correction.status == "applied" else "Скачать DOCX результата",
              "original": "Скачать исходный DOCX", "report": "Скачать JSON-отчёт", "pdf": "Скачать PDF"}
    for key, content in result.artifacts.items():
        extension = {"docx": "docx", "original": "docx", "report": "json", "pdf": "pdf"}[key]
        mime = {"docx": DOCX_MIME, "original": DOCX_MIME, "report": "application/json", "pdf": "application/pdf"}[key]
        st.download_button(labels[key], content, file_name=f"{report.job_id}-{key}.{extension}", mime=mime, key=f"download-{key}")
    for key, message in result.errors.items():
        st.warning(f"{labels[key]}: {message}")
    with st.expander("Метрики и контроль исходника"):
        st.text(f"SHA-256 исходника: {report.original_sha256}")
        st.json(report.metrics)


def main():
    st.set_page_config(page_title="Document AI Inspector", page_icon="📄", layout="wide")
    st.title("Document AI Inspector")
    st.write("Проверьте оформление DOCX, найдите возможные смысловые противоречия и скачайте результаты.")
    client = APIClient(os.getenv("API_BASE_URL", "http://localhost:18000"),
                       timeout=float(os.getenv("API_REQUEST_TIMEOUT_SECONDS", "630")))
    try:
        options = client.options()
    except APIError as exc:
        st.error(str(exc))
        st.button("Повторить подключение")
        st.stop()
    if not options.profiles:
        st.error("В API нет доступных профилей проверки.")
        st.stop()
    source = st.radio("Источник документа", ["Загрузить DOCX", "Синтетический пример"], horizontal=True)
    data, filename = None, None
    if source == "Загрузить DOCX":
        uploaded = st.file_uploader("Документ DOCX", type=["docx"])
        if uploaded is not None:
            data, filename = uploaded.getvalue(), uploaded.name
    else:
        sample = SAMPLES[st.selectbox("Пример", list(SAMPLES))]
        data, filename = sample_bytes(sample), f"{sample}.docx"
        st.caption("Данные вымышлены. Пример отправляется на ту же проверку API, что и загруженный файл.")
        st.download_button("Скачать пример DOCX", data, file_name=filename, mime=DOCX_MIME)
    st.caption(f"Лимит API: {options.max_upload_bytes / 1024 / 1024:g} МБ. Только DOCX.")
    profile = st.selectbox("Профиль оформления", [p.name for p in options.profiles],
                           format_func=lambda name: next(f"{p.name} — v{p.version}" for p in options.profiles if p.name == name))
    provider = PROVIDERS[st.selectbox("Провайдер смысловой проверки", list(PROVIDERS))]
    auto_fix = st.checkbox("Применить разрешённые безопасные правки", value=False)
    render_pdf = st.checkbox("Создать PDF-предпросмотр", value=False)
    allow_cloud = synthetic_data = False
    if provider == "yandex":
        st.warning("Yandex отправит текст документа в облако и может списать средства. Только синтетические документы.")
        consent_id = hashlib.sha256((data or b"") + (filename or "").encode()).hexdigest()
        synthetic_data = st.checkbox("Подтверждаю: документ содержит только синтетические данные", key=f"synthetic-consent-{consent_id}")
        allow_cloud = st.checkbox("Разрешаю платный вызов Yandex для этой проверки", key=f"cloud-consent-{consent_id}")
    valid = data is not None and 0 < len(data) <= options.max_upload_bytes
    if data is not None and not valid:
        st.error("Файл пуст или превышает допустимый размер.")
    fingerprint = hashlib.sha256((data or b"") + json.dumps(
        [filename, profile, provider, auto_fix, render_pdf, allow_cloud, synthetic_data], ensure_ascii=False).encode()).hexdigest()
    if st.session_state.get("result_fingerprint") != fingerprint:
        st.session_state.pop("inspection_result", None)
    submit = st.button("Проверить документ", type="primary", disabled=not valid or (provider == "yandex" and not (allow_cloud and synthetic_data)))
    if submit:
        st.session_state.pop("inspection_result", None)
        try:
            with st.spinner("API проверяет документ…"):
                result = client.check(filename, data, profile=profile, provider=provider, auto_fix=auto_fix,
                    render_pdf=render_pdf, allow_cloud=allow_cloud, synthetic_data=synthetic_data)
            st.session_state.inspection_result = result
            st.session_state.result_fingerprint = fingerprint
        except APIError as exc:
            st.error(str(exc))
    if "inspection_result" in st.session_state:
        display_result(st.session_state.inspection_result)
    st.caption("Автоправки касаются только разрешённого форматирования. Смысловые замечания требуют ручной проверки.")


if __name__ == "__main__":
    main()
