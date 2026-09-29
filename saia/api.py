"""SAIA API: research pipeline and scout-facing candidate screening."""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, StrictInt

from saia import methodology
from saia.discovery import discover
from saia.scoring import CandidateMetrics, assess_candidate
from saia.web import WEB_APP_HTML
from saia.scout_web import SCOUT_WEB_HTML
from saia.public_signals_web import PUBLIC_SIGNALS_HTML
from saia.annotation_web import ANNOTATION_WEB_HTML
from saia.coherence_review_web import COHERENCE_REVIEW_HTML
from saia.retrieval_review_web import RETRIEVAL_REVIEW_HTML
from saia.external_evidence_web import EXTERNAL_EVIDENCE_HTML
from saia.retrieval_adjudication_web import RETRIEVAL_ADJUDICATION_HTML
from saia.measurement import WindowCounts, analyze_series


app = FastAPI(
    title="Horizon — слабые научно-технологические сигналы",
    version="0.4.65",
    description="Доказательный API. LLM не рассчитывает score и confidence.",
)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def web_app() -> str:
    return WEB_APP_HTML


@app.get('/scout', response_class=HTMLResponse, include_in_schema=False)
def scout_app(response: Response) -> str:
    response.headers['Cache-Control'] = 'no-store, max-age=0'
    return SCOUT_WEB_HTML


@app.get('/public-signals', response_class=HTMLResponse, include_in_schema=False)
def public_signals_app() -> str:
    return PUBLIC_SIGNALS_HTML


@app.get('/help', response_class=HTMLResponse, include_in_schema=False)
def product_help(response: Response) -> str:
    from saia.help_web import render_help
    response.headers['Cache-Control'] = 'no-store, max-age=0'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
    )
    return render_help()


@app.get('/api/public-signals')
def public_signals_catalog(query: str = '', source_id: str | None = None,
                           category: str | None = None, source_type: str | None = None,
                           year: int | None = None, limit: int = 15,
                           offset: int = 0) -> dict:
    from saia.public_signals import search
    try:
        return search(query=query, source_id=source_id, category=category,
                      source_type=source_type, year=year,
                      limit=limit, offset=offset)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/public-signals/{public_signal_id}/brief')
def published_signal_brief(public_signal_id: str, lang: str = "ru") -> HTMLResponse:
    from saia.public_signal_brief import build
    try:
        content = build(public_signal_id, lang=lang)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return HTMLResponse(content, headers={
        'Content-Disposition': f'attachment; filename="Horizon-public-signal-{public_signal_id}-{lang}.html"',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
    })


@app.get('/annotation-review', response_class=HTMLResponse, include_in_schema=False)
def annotation_review_app() -> str:
    return ANNOTATION_WEB_HTML


@app.get('/coherence-review', response_class=HTMLResponse, include_in_schema=False)
def coherence_review_app() -> str:
    return COHERENCE_REVIEW_HTML


@app.get('/coherence-review/packet')
def coherence_review_packet() -> dict:
    from saia.coherence_review import read_packet
    try:
        return read_packet()
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.post('/coherence-review/validate')
def coherence_review_validate(submission: dict) -> dict:
    from saia.coherence_review import validate_submission
    try:
        return validate_submission(submission)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/retrieval-review', response_class=HTMLResponse, include_in_schema=False)
def retrieval_review_app() -> str:
    return RETRIEVAL_REVIEW_HTML


@app.get('/retrieval-adjudication', response_class=HTMLResponse, include_in_schema=False)
def retrieval_adjudication_app() -> str:
    return RETRIEVAL_ADJUDICATION_HTML


@app.get('/external-evidence', response_class=HTMLResponse, include_in_schema=False)
def external_evidence_app() -> str:
    return EXTERNAL_EVIDENCE_HTML


@app.get('/source-catalog')
def available_source_catalog() -> dict:
    from saia.source_context import catalog
    return catalog()


class SourceContextRequest(BaseModel):
    query: str | None = Field(default=None, min_length=2, max_length=160)
    sources: list[str] | None = Field(default=None, min_length=1, max_length=8)


class CardPresentationRequest(BaseModel):
    model: str = Field(min_length=1, max_length=120)


class CandidateAnalysisNoteRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    author: str = Field(min_length=1, max_length=120)
    summary: str = Field(default="", max_length=1800)
    pestle: list[dict[str, Any]] = Field(default_factory=list, max_length=6)
    industry_impacts: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    expected_revision: int = Field(ge=0, le=100000, strict=True)
    operation_id: UUID | None = None


@app.get('/signals/{mission_id}/{candidate_id}/analysis-note')
def candidate_analysis_note(mission_id: str, candidate_id: int, score_run_id: int) -> dict:
    from saia.candidate_analysis import read
    try:
        return read(mission_id, score_run_id, candidate_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/signals/{mission_id}/{candidate_id}/analysis-references')
def candidate_analysis_reference_catalog(mission_id: str, candidate_id: int, score_run_id: int) -> dict:
    from saia.candidate_analysis import reference_catalog
    try:
        return reference_catalog(mission_id, score_run_id, candidate_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/signals/{mission_id}/{candidate_id}/analysis-note')
def save_candidate_analysis_note(mission_id: str, candidate_id: int,
                                 request: CandidateAnalysisNoteRequest, score_run_id: int) -> dict:
    from saia.candidate_analysis import save, RevisionConflict
    try:
        return save(mission_id, score_run_id, candidate_id,
                    request.model_dump(exclude={'expected_revision', 'operation_id'}),
                    request.expected_revision, str(request.operation_id) if request.operation_id else None)
    except RevisionConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/signals/{mission_id}/{candidate_id}/brief', response_class=HTMLResponse)
def download_candidate_brief(mission_id: str, candidate_id: int, score_run_id: int,
                             query: str | None = None, sources: str | None = None,
                             download: bool = True) -> HTMLResponse:
    from saia.candidate_brief import build
    try:
        content = build(mission_id, score_run_id, candidate_id, query,
                        sources.split(',') if sources is not None else None)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
               'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"}
    if download:
        headers['Content-Disposition'] = f'attachment; filename="Horizon-card-{candidate_id}-score-{score_run_id}.html"'
    return HTMLResponse(content, headers=headers)


@app.get('/results/{mission_id}/export')
def download_result_snapshot(mission_id: str, score_run_id: int, candidate_ids: str | None = None,
                             public_signal_ids: str | None = None, include_scientific: bool = True) -> Response:
    from saia.result_export import build, parse_ids
    from saia.scout_public_signals import parse_ids as parse_public_ids
    try:
        published = parse_public_ids(public_signal_ids)
        if not include_scientific and (candidate_ids is not None or not published):
            raise ValueError("Для выгрузки только внешних сигналов укажите их идентификаторы без научных кандидатов.")
        ids = parse_ids(candidate_ids) if include_scientific else []
        report = build(mission_id, score_run_id, ids, public_signal_ids=published)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return Response(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2), media_type='application/json',
                    headers={'Content-Disposition': f'attachment; filename="Horizon-results-score-{score_run_id}.json"',
                             'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


@app.get('/presentation-models')
def card_presentation_models() -> dict:
    from saia.card_presentation import models
    return models()


@app.get('/signals/{mission_id}/{candidate_id}/presentation')
def saved_card_presentation(mission_id: str, candidate_id: int,
                            score_run_id: int, model: str | None = None) -> dict:
    from saia.card_presentation import read
    try:
        return read(mission_id, score_run_id, candidate_id, model)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/signals/{mission_id}/{candidate_id}/presentation')
def generate_card_presentation(mission_id: str, candidate_id: int,
                               request: CardPresentationRequest, score_run_id: int) -> dict:
    from saia.card_presentation import generate
    try:
        return generate(mission_id, score_run_id, candidate_id, request.model)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/signals/{mission_id}/{candidate_id}/source-context')
def saved_candidate_source_context(mission_id: str, candidate_id: int,
                                   score_run_id: int, query: str | None = None,
                                   sources: str | None = None, all_saved: bool = False) -> dict:
    from saia.source_context import read, read_visible
    try:
        if all_saved:
            return read_visible(mission_id, score_run_id, candidate_id,
                                sources.split(',') if sources is not None else None)
        return read(mission_id, score_run_id, candidate_id, query,
                    sources.split(',') if sources is not None else None)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/signals/{mission_id}/{candidate_id}/source-context')
def collect_candidate_source_context(mission_id: str, candidate_id: int,
                                    request: SourceContextRequest,
                                    score_run_id: int) -> dict:
    from saia.source_context import collect, ContextBusy
    try:
        return collect(mission_id, score_run_id, candidate_id, request.query, request.sources)
    except ContextBusy as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/focus-areas')
def focus_area_catalog() -> dict:
    """Return controlled query-planning profiles, never weak-signal labels."""
    from saia.focus_areas import catalog
    return catalog()


@app.get('/focus-areas/{profile_id}')
def focus_area_profile(profile_id: str) -> dict:
    from saia.focus_areas import profile
    try:
        return profile(profile_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


class NewsEvidenceRequest(BaseModel):
    topic_id: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=2, max_length=200)
    start: date
    end: date
    as_of: date
    max_records: int = Field(default=25, ge=1, le=50)
    save: bool = False
    operation_id: UUID | None = None


class PatentEvidenceRequest(BaseModel):
    topic_id: str = Field(min_length=1, max_length=200)
    phrase: str = Field(min_length=2, max_length=160)
    start: date
    end: date
    as_of: date
    max_records: int = Field(default=25, ge=1, le=25)
    save: bool = False
    operation_id: UUID | None = None


class FundingEvidenceRequest(BaseModel):
    topic_id: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=2, max_length=200)
    start: date
    end: date
    as_of: date
    max_records: int = Field(default=25, ge=1, le=50)
    save: bool = False
    operation_id: UUID | None = None


class RSSEvidenceRequest(FundingEvidenceRequest):
    source: str = 'mit_news_rss'
    query: str = Field(min_length=2, max_length=160)
    max_records: int = Field(default=10, ge=1, le=15)


class PublicMetadataEvidenceRequest(FundingEvidenceRequest):
    query: str = Field(min_length=2, max_length=160)
    max_records: int = Field(default=10, ge=1, le=15)


@app.post('/external-evidence/funding/nsf')
def nsf_funding_evidence(request: PublicMetadataEvidenceRequest) -> dict:
    from saia.nsf_evidence import NSFQuery, fetch
    try:
        result = fetch(NSFQuery(request.topic_id, request.query, request.start, request.end, request.as_of, request.max_records))
        return _saved_external(result, request.save, request.operation_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/external-evidence/funding/openaire')
def openaire_project_evidence(request: PublicMetadataEvidenceRequest) -> dict:
    from saia.openaire_project_evidence import OpenAIREProjectQuery, fetch
    try:
        result = fetch(OpenAIREProjectQuery(request.topic_id, request.query, request.start, request.end, request.as_of, request.max_records))
        return _saved_external(result, request.save, request.operation_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/external-evidence/artifacts/datacite')
def datacite_artifact_evidence(request: PublicMetadataEvidenceRequest) -> dict:
    from saia.datacite_evidence import DataCiteQuery, fetch
    try:
        result = fetch(DataCiteQuery(request.topic_id, request.query, request.start, request.end, request.as_of, request.max_records))
        return _saved_external(result, request.save, request.operation_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/external-evidence/news/rss')
def official_rss_evidence(request: RSSEvidenceRequest) -> dict:
    from saia.rss_evidence import RSSQuery, fetch
    try:
        result = fetch(RSSQuery(request.topic_id, request.query, request.start,
                                request.end, request.as_of, request.source,
                                request.max_records))
        return _saved_external(result, request.save, request.operation_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/external-evidence/papers/osti')
def osti_research_evidence(request: PublicMetadataEvidenceRequest) -> dict:
    from saia.osti_evidence import OSTIQuery, fetch
    try:
        result = fetch(OSTIQuery(request.topic_id, request.query, request.start,
                                request.end, request.as_of, request.max_records))
        return _saved_external(result, request.save, request.operation_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


class SoftwareEvidenceRequest(BaseModel):
    topic_id: str = Field(min_length=1, max_length=200)
    system: str = Field(min_length=1, max_length=20)
    package: str = Field(min_length=1, max_length=200)
    start: date
    end: date
    as_of: date
    max_versions: int = Field(default=50, ge=1, le=100)
    save: bool = False
    operation_id: UUID | None = None


class ScholarEvidenceRequest(BaseModel):
    topic_id: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=2, max_length=200)
    start: date
    end: date
    as_of: date
    max_records: int = Field(default=25, ge=1, le=50)
    save: bool = False
    operation_id: UUID | None = None


class DiscoveryEvidenceRequest(BaseModel):
    topic_id: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=2, max_length=200)
    start: date
    end: date
    as_of: date
    max_records: int = Field(default=10, ge=1, le=50)
    save: bool = False
    operation_id: UUID | None = None


def _saved_external(payload: dict, save: bool, operation_id: UUID | None) -> dict:
    if save:
        from saia.external_evidence_store import record
        payload = dict(payload)
        payload['saved'] = record(payload, str(operation_id) if operation_id else None)
    return payload


@app.post('/external-evidence/news/gdelt')
def gdelt_news_evidence(request: NewsEvidenceRequest) -> dict:
    from saia.news_evidence import NewsQuery, fetch
    try:
        payload = fetch(NewsQuery(
            request.topic_id, request.query, request.start, request.end,
            request.as_of, request.max_records,
        ))
        return _saved_external(payload, request.save, request.operation_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/external-evidence/patents/epo-ops')
def epo_patent_evidence(request: PatentEvidenceRequest) -> dict:
    from saia.patent_evidence import PatentQuery, fetch
    try:
        payload = fetch(PatentQuery(
            request.topic_id, request.phrase, request.start, request.end,
            request.as_of, request.max_records,
        ))
        return _saved_external(payload, request.save, request.operation_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/external-evidence/funding/nih-reporter')
def nih_funding_evidence(request: FundingEvidenceRequest) -> dict:
    from saia.funding_evidence import FundingQuery, fetch
    try:
        payload = fetch(FundingQuery(
            request.topic_id, request.query, request.start, request.end,
            request.as_of, request.max_records,
        ))
        return _saved_external(payload, request.save, request.operation_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/external-evidence/software/deps-dev')
def deps_software_evidence(request: SoftwareEvidenceRequest) -> dict:
    from saia.software_evidence import SoftwareQuery, fetch
    try:
        payload = fetch(SoftwareQuery(
            request.topic_id, request.system, request.package, request.start,
            request.end, request.as_of, request.max_versions,
        ))
        return _saved_external(payload, request.save, request.operation_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/external-evidence/scholar/semantic-scholar')
def semantic_scholar_evidence(request: ScholarEvidenceRequest) -> dict:
    from saia.scholar_evidence import ScholarQuery, fetch
    try:
        payload = fetch(ScholarQuery(
            request.topic_id, request.query, request.start, request.end,
            request.as_of, request.max_records,
        ))
        return _saved_external(payload, request.save, request.operation_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/external-evidence/funding/ukri')
def ukri_funding_evidence(request: DiscoveryEvidenceRequest) -> dict:
    from saia.ukri_evidence import UKRIQuery, fetch
    try:
        payload = fetch(UKRIQuery(
            request.topic_id, request.query, request.start, request.end,
            request.as_of, request.max_records,
        ))
        return _saved_external(payload, request.save, request.operation_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/external-evidence/programmes/eu-funding')
def eu_programme_evidence(request: DiscoveryEvidenceRequest) -> dict:
    from saia.eu_programme_evidence import EUProgrammeQuery, fetch
    try:
        payload = fetch(EUProgrammeQuery(
            request.topic_id, request.query, request.start, request.end,
            request.as_of, min(request.max_records, 20),
        ))
        return _saved_external(payload, request.save, request.operation_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/external-evidence/ai-artifacts/hugging-face')
def huggingface_artifact_evidence(request: DiscoveryEvidenceRequest) -> dict:
    from saia.huggingface_evidence import HuggingFaceQuery, fetch
    try:
        payload = fetch(HuggingFaceQuery(
            request.topic_id, request.query, request.start, request.end,
            request.as_of, min(request.max_records, 20),
        ))
        return _saved_external(payload, request.save, request.operation_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/external-evidence/clinical-trials/clinicaltrials-gov')
def clinical_trial_evidence(request: DiscoveryEvidenceRequest) -> dict:
    from saia.clinical_trial_evidence import ClinicalTrialQuery, fetch
    try:
        payload = fetch(ClinicalTrialQuery(
            request.topic_id, request.query, request.start, request.end,
            request.as_of, request.max_records,
        ))
        return _saved_external(payload, request.save, request.operation_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/external-evidence/biomedical/europe-pmc')
def europe_pmc_evidence(request: DiscoveryEvidenceRequest) -> dict:
    from saia.europe_pmc_evidence import EuropePmcQuery, fetch
    try:
        payload = fetch(EuropePmcQuery(
            request.topic_id, request.query, request.start, request.end,
            request.as_of, request.max_records,
        ))
        return _saved_external(payload, request.save, request.operation_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/external-evidence/aerospace/nasa-ntrs')
def nasa_ntrs_evidence(request: DiscoveryEvidenceRequest) -> dict:
    from saia.nasa_ntrs_evidence import NasaNtrsQuery, fetch
    try:
        payload = fetch(NasaNtrsQuery(
            request.topic_id, request.query, request.start, request.end,
            request.as_of, request.max_records,
        ))
        return _saved_external(payload, request.save, request.operation_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/external-evidence/procurement/usaspending')
def usaspending_evidence(request: DiscoveryEvidenceRequest) -> dict:
    from saia.usaspending_evidence import USAspendingQuery, fetch
    try:
        payload = fetch(USAspendingQuery(
            request.topic_id, request.query, request.start, request.end,
            request.as_of, request.max_records,
        ))
        return _saved_external(payload, request.save, request.operation_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/external-evidence/observations')
def external_evidence_history(topic_id: str | None = None, source: str | None = None,
                              limit: int = 100) -> dict:
    from saia.external_evidence_store import history
    try:
        return history(topic_id, source, limit)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/external-evidence/observations/{observation_id}')
def external_evidence_observation(observation_id: UUID) -> dict:
    from saia.external_evidence_store import read
    try:
        return read(str(observation_id))
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get('/retrieval-review/current')
def retrieval_review_current_packet() -> dict:
    from saia.retrieval_relevance_review import current_packet
    try:
        return current_packet()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get('/retrieval-review/current/template')
def retrieval_review_current_template() -> dict:
    from saia.retrieval_relevance_review import current_template
    try:
        return current_template()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.post('/retrieval-review/current/validate')
def retrieval_review_validate_submission(submission: dict[str, Any]) -> dict:
    from saia.retrieval_relevance_review import current_packet, validate_submission
    try:
        return validate_submission(current_packet(), submission)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/annotation/current')
def annotation_current_packet() -> dict:
    from saia.annotation_protocol import current_packet
    try:
        return current_packet()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get('/annotation/current/template')
def annotation_current_template() -> dict:
    from saia.annotation_protocol import current_template
    try:
        return current_template()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.post('/annotation/current/validate')
def annotation_validate_submission(submission: dict[str, Any]) -> dict:
    from saia.annotation_protocol import current_packet, validate_submission
    try:
        return validate_submission(current_packet(), submission)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


class AnnotationComparisonRequest(BaseModel):
    left: dict[str, Any]
    right: dict[str, Any]


class AnnotationSubmissionStoreRequest(BaseModel):
    submission: dict[str, Any]
    operation_id: UUID | None = None


class AnnotationStoredComparisonRequest(BaseModel):
    left_submission_id: UUID
    right_submission_id: UUID


class RetrievalAdjudicationDecision(BaseModel):
    item_id: str = Field(min_length=1, max_length=200)
    topical_relevance: str
    rationale: str = Field(min_length=20, max_length=5000)
    sources: list[str] = Field(default_factory=list, max_length=5)


class RetrievalAdjudicationRequest(BaseModel):
    left_submission_id: UUID
    right_submission_id: UUID
    adjudicator_id: str = Field(min_length=1, max_length=120)
    decisions: list[RetrievalAdjudicationDecision] = Field(min_length=1, max_length=500)
    operation_id: UUID | None = None


class UniversalFullAnalysisRequest(BaseModel):
    adjudication_id: UUID
    accepted_by: str = Field(min_length=1, max_length=120)
    acknowledge_arxiv_only: bool = False
    acknowledge_phrase_union: bool = False
    max_records: int = Field(default=10_000, ge=100, le=20_000)
    top_n: int = Field(default=15, ge=1, le=100)
    max_attempts: int = Field(default=3, ge=1, le=10)
    operation_id: UUID | None = None


class AutomaticCandidateAnalysisRequest(BaseModel):
    requested_by: str = Field(default="system", min_length=1, max_length=120)
    max_records: int = Field(default=10_000, ge=100, le=20_000)
    top_n: int = Field(default=15, ge=1, le=100)
    max_attempts: int = Field(default=3, ge=1, le=10)
    operation_id: UUID | None = None


@app.post('/annotation/current/compare')
def annotation_compare_submissions(request: AnnotationComparisonRequest) -> dict:
    from saia.annotation_protocol import compare_submissions, current_packet
    try:
        return compare_submissions(current_packet(), request.left, request.right)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/retrieval-review/current/compare')
def retrieval_review_compare_submissions(request: AnnotationComparisonRequest) -> dict:
    from saia.retrieval_relevance_review import compare_submissions, current_packet
    try:
        return compare_submissions(current_packet(), request.left, request.right)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/retrieval-review/current/submissions')
def retrieval_review_store_submission(request: AnnotationSubmissionStoreRequest) -> dict:
    from saia.retrieval_review_store import record
    try:
        return record(
            request.submission,
            str(request.operation_id) if request.operation_id is not None else None,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/retrieval-review/current/submissions')
def retrieval_review_submission_history(limit: int = 100) -> dict:
    from saia.retrieval_review_store import history
    try:
        return history(limit)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/retrieval-review/current/submissions/{submission_id}')
def retrieval_review_saved_submission(submission_id: UUID) -> dict:
    from saia.retrieval_review_store import read
    try:
        return read(str(submission_id))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/retrieval-review/current/compare-stored')
def retrieval_review_compare_stored(request: AnnotationStoredComparisonRequest) -> dict:
    from saia.retrieval_review_store import compare
    try:
        return compare(str(request.left_submission_id), str(request.right_submission_id))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/annotation/current/submissions')
def annotation_store_submission(request: AnnotationSubmissionStoreRequest) -> dict:
    from saia.annotation_store import record
    try:
        return record(request.submission,
                      str(request.operation_id) if request.operation_id is not None else None)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/annotation/current/submissions')
def annotation_submission_history(limit: int = 100) -> dict:
    from saia.annotation_store import history
    try:
        return history(limit)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/annotation/current/submissions/{submission_id}')
def annotation_saved_submission(submission_id: UUID) -> dict:
    from saia.annotation_store import read
    try:
        return read(str(submission_id))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/annotation/current/compare-stored')
def annotation_compare_stored(request: AnnotationStoredComparisonRequest) -> dict:
    from saia.annotation_store import compare
    try:
        return compare(str(request.left_submission_id), str(request.right_submission_id))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


class AssessmentRequest(BaseModel):
    doc_count: int = Field(ge=0)
    independent_orgs: int | None = Field(default=None, ge=0)
    independent_teams: int | None = Field(default=None, ge=0)
    single_org_share: float | None = Field(default=None, ge=0, le=1)
    maturity_percentile: float | None = Field(default=None, ge=0, le=100)
    novelty_percentile: float | None = Field(default=None, ge=0, le=100)
    windows_present: int | None = Field(default=None, ge=0)
    momentum_percentile: float | None = Field(default=None, ge=0, le=100)
    primary_sources: int | None = Field(default=None, ge=0)
    age_years: float | None = Field(default=None, ge=0)
    normalized: dict[str, float | None] = Field(default_factory=dict)
    penalties: dict[str, float] = Field(default_factory=dict)
    confidence_components: dict[str, float | None] = Field(default_factory=dict)
    share_slope: float | None = None
    share_change: float | None = None
    consecutive_active_windows: int | None = Field(default=None, ge=0)
    coverage_comparable: bool | None = None
    coherence_calibrated: bool | None = None
    embedding_coverage: float | None = Field(default=None, ge=0, le=1)


class DiscoveryRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    date_from: date
    as_of_date: date
    limit_per_source: int = Field(default=50, ge=1, le=100)


class QueryPlanPreviewRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    max_suggestions: int = Field(default=12, ge=1, le=20)


class QueryPlanApprovalRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    max_suggestions: int = Field(default=12, ge=1, le=20)
    preview_payload_sha256: str = Field(min_length=64, max_length=64)
    selected_branch_ids: list[str] = Field(default_factory=list, max_length=20)
    approved_by: str = Field(min_length=1, max_length=120)
    operation_id: UUID | None = None


class BalancedDiscoveryJobRequest(BaseModel):
    requested_by: str = Field(min_length=1, max_length=120)
    date_from: date
    as_of_date: date
    limit_per_source: int = Field(default=10, ge=1, le=100)
    max_results: int = Field(default=15, ge=1, le=300)
    max_attempts: int = Field(default=3, ge=1, le=10)
    compiled_query_plan_id: UUID | None = None
    openalex_collection_mode: str = Field(default="live", pattern="^(live|cache_year_spread|live_with_cache_fallback|compound_boolean_live|live_with_prepared_supplement)$")
    operation_id: UUID | None = None


class CompiledBranchSpec(BaseModel):
    branch_id: str = Field(min_length=1, max_length=200)
    included_phrases: list[str] = Field(min_length=1, max_length=10)
    excluded_phrases: list[str] = Field(default_factory=list, max_length=10)
    concept_groups: list[list[str]] = Field(default_factory=list, max_length=5)


class QueryPlanCompilationRequest(BaseModel):
    branch_specs: list[CompiledBranchSpec] = Field(min_length=1, max_length=12)
    compiled_by: str = Field(min_length=1, max_length=120)
    operation_id: UUID | None = None


class QueryProposalRequest(BaseModel):
    query: str = Field(min_length=2, max_length=80)
    quality_generation_id: int | None = Field(default=None, ge=1)


class QueryApprovalRequest(BaseModel):
    selected_ids: list[str] = Field(min_length=1, max_length=4)
    exclusions: list[str] = Field(default_factory=list, max_length=10)
    reviewed_by: str = Field(min_length=1, max_length=120)


class QueryPreviewRequest(BaseModel):
    query_version_id: str = Field(min_length=1, max_length=200)
    limit_per_source: int = Field(default=10, ge=1, le=100)


class DiscoveryJobRequest(BaseModel):
    query_version_id: str = Field(min_length=1, max_length=200)
    requested_by: str = Field(min_length=1, max_length=120)
    limit_per_source: int = Field(default=50, ge=1, le=100)
    max_attempts: int = Field(default=3, ge=1, le=10)
    operation_id: UUID | None = None


class FullAnalysisJobRequest(BaseModel):
    query_version_id: str = Field(min_length=1, max_length=200)
    requested_by: str = Field(min_length=1, max_length=120)
    max_records: int = Field(default=10_000, ge=100, le=20_000)
    top_n: int = Field(default=15, ge=1, le=100)
    acknowledge_source_scope: bool = False
    max_attempts: int = Field(default=3, ge=1, le=10)
    operation_id: UUID | None = None


class JobActionRequest(BaseModel):
    requested_by: str = Field(min_length=1, max_length=120)
    operation_id: UUID | None = None


class HybridRequest(BaseModel):
    cluster_run_id: int = Field(ge=1)


class ExpertReviewRequest(BaseModel):
    decision: str
    reviewed_by: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=20, max_length=5000)
    sources: list[str] = Field(default_factory=list, max_length=10)
    operation_id: UUID | None = None


class ExpertValidationDispatchRequest(BaseModel):
    mission_id: str = Field(min_length=1, max_length=200)
    score_run_id: int = Field(ge=1)
    candidate_ids: list[StrictInt] = Field(default_factory=list, max_length=15)
    public_signal_ids: list[str] = Field(default_factory=list, max_length=15)
    requested_by: str = Field(min_length=1, max_length=120)
    recipient: str = Field(min_length=1, max_length=120)
    note: str = Field(default='', max_length=2000)
    operation_id: UUID | None = None


class CandidateExternalLinkRequest(BaseModel):
    observation_id: UUID
    record_url: str = Field(min_length=10, max_length=2048)
    assessment: str
    linked_by: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=20, max_length=2000)
    operation_id: UUID | None = None


@app.post('/expert-requests', status_code=201)
def dispatch_expert_validation(request: ExpertValidationDispatchRequest) -> dict:
    from saia.expert_requests import create
    try:
        extra = {"public_signal_ids": request.public_signal_ids} if request.public_signal_ids else {}
        return create(
            request.mission_id, request.score_run_id, request.candidate_ids,
            request.requested_by, request.recipient, request.note,
            str(request.operation_id) if request.operation_id else None,
            **extra,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/expert-requests')
def expert_validation_queue(limit: int = 50) -> dict:
    from saia.expert_requests import history
    try:
        return history(limit)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/public-signals/{public_signal_id}/reviews', status_code=201)
def review_published_signal(public_signal_id: str, request_id: UUID, request: ExpertReviewRequest) -> dict:
    from saia.public_signal_reviews import record
    try:
        return record(str(request_id), public_signal_id, request.decision, request.reviewed_by,
                      request.rationale, request.sources, str(request.operation_id) if request.operation_id else None)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/public-signals/{public_signal_id}/reviews')
def published_signal_review_history(public_signal_id: str, request_id: UUID) -> dict:
    from saia.public_signal_reviews import history_for_reference, request_snapshot
    try:
        snapshot = request_snapshot(str(request_id), public_signal_id)
        return {"snapshot": snapshot, **history_for_reference(snapshot['reference_content_sha256'], request_id=str(request_id))}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/hybrid/{snapshot_id}/candidates/{candidate_id}/reviews')
def save_expert_review(snapshot_id: str, candidate_id: str, request: ExpertReviewRequest) -> dict:
    from saia.expert import record
    try:
        return record(snapshot_id, candidate_id, request.decision, request.reviewed_by, request.rationale, request.sources,
                      str(request.operation_id) if request.operation_id is not None else None)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/hybrid/{snapshot_id}/candidates/{candidate_id}/reviews')
def expert_review_history(snapshot_id: str, candidate_id: str, limit: int = 100) -> dict:
    from saia.expert import history
    try:
        return history(snapshot_id, candidate_id, limit)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/corpus/{mission_id}/hybrid')
def hybrid_analysis(mission_id: str, request: HybridRequest) -> dict:
    from saia.hybrid import analyze
    try:
        return analyze(mission_id, request.cluster_run_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/corpus/{mission_id}/hybrid-history')
def hybrid_history(mission_id: str) -> dict:
    from saia.hybrid import history
    return history(mission_id)


@app.get('/hybrid/{snapshot_id}')
def hybrid_snapshot(snapshot_id: str) -> dict:
    from saia.hybrid import read
    try:
        return read(snapshot_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/hybrid/{snapshot_id}/portfolio')
def hybrid_portfolio(snapshot_id: str, assessment_id: str | None = None) -> dict:
    from saia.hybrid import read
    from saia.portfolio import project
    try:
        from saia.assessment_store import read as read_assessment
        snapshot = read(snapshot_id)
        return project(snapshot, read_assessment(assessment_id)) if assessment_id else project(snapshot)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/hybrid/{snapshot_id}/assessment-history')
def assessment_history(snapshot_id: str, limit: int = 50) -> dict:
    from saia.assessment_store import history
    try:
        return history(snapshot_id, limit)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/assessments/{assessment_id}')
def saved_assessment(assessment_id: str) -> dict:
    from saia.assessment_store import read
    try:
        return read(assessment_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/corpus/{mission_id}/query-proposals')
def query_proposal(mission_id: str, request: QueryProposalRequest) -> dict:
    from saia.query_expansion import create_proposal
    try:
        return create_proposal(mission_id, request.query, request.quality_generation_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/query-proposals/{proposal_id}/approve')
def query_approval(proposal_id: str, request: QueryApprovalRequest) -> dict:
    from saia.query_expansion import approve, QueryConflict
    try:
        return approve(proposal_id, request.selected_ids, request.exclusions, request.reviewed_by)
    except QueryConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/query-proposals/{proposal_id}')
def query_proposal_history(proposal_id: str) -> dict:
    from saia.query_expansion import get_proposal
    try:
        return get_proposal(proposal_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post('/queries/preview')
def query_preview(request: QueryPreviewRequest) -> dict:
    from saia.query_expansion import saved_plan
    try:
        plan = saved_plan(request.query_version_id)
        return discover(plan['original_query'], date.fromisoformat(plan['date_from']),
                        date.fromisoformat(plan['as_of_date']), request.limit_per_source,
                        search_plan=plan).to_dict()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/queries/approved')
def query_approved_packet(query_version_id: str) -> dict:
    from saia.query_expansion import read_approved
    try:
        return read_approved(query_version_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post('/missions/{mission_id}/jobs/discovery', status_code=202)
def create_discovery_job(mission_id: str, request: DiscoveryJobRequest) -> dict:
    """Queue a durable candidate-corpus collection; do not call it a signal run."""
    from saia.jobs import JobConflict, enqueue_controlled_discovery
    try:
        return enqueue_controlled_discovery(
            mission_id, request.query_version_id, request.requested_by,
            request.limit_per_source,
            str(request.operation_id) if request.operation_id is not None else None,
            request.max_attempts,
        )
    except JobConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/missions/{mission_id}/jobs/full-analysis', status_code=202)
def create_full_analysis_job(mission_id: str,
                             request: FullAnalysisJobRequest) -> dict:
    """Queue a bounded frozen pipeline ending in an analyst review queue."""
    from saia.jobs import JobConflict, enqueue_full_analysis
    try:
        return enqueue_full_analysis(
            mission_id, request.query_version_id, request.requested_by,
            max_records=request.max_records, top_n=request.top_n,
            acknowledge_source_scope=request.acknowledge_source_scope,
            operation_id=(str(request.operation_id)
                          if request.operation_id is not None else None),
            max_attempts=request.max_attempts,
        )
    except JobConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/jobs/{job_id}')
def job_status(job_id: UUID) -> dict:
    from saia.jobs import read
    try:
        return read(str(job_id))
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get('/jobs/{job_id}/retrieval-review')
def balanced_job_retrieval_review(job_id: UUID) -> dict:
    """Build a blinded relevance packet from this exact immutable result."""
    from saia.balanced_retrieval_review import from_job_id
    try:
        return from_job_id(str(job_id))[0]
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/jobs/{job_id}/retrieval-review/template')
def balanced_job_retrieval_template(job_id: UUID) -> dict:
    from saia.balanced_retrieval_review import from_job_id
    try:
        return from_job_id(str(job_id))[1]
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/jobs/{job_id}/retrieval-review/validate')
def balanced_job_retrieval_validate(job_id: UUID, submission: dict[str, Any]) -> dict:
    from saia.balanced_retrieval_review import from_job_id
    from saia.retrieval_relevance_review import validate_submission
    try:
        return validate_submission(from_job_id(str(job_id))[0], submission)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/jobs/{job_id}/retrieval-review/submissions')
def balanced_job_retrieval_store(
    job_id: UUID, request: AnnotationSubmissionStoreRequest,
) -> dict:
    from saia.balanced_retrieval_review_store import record
    try:
        return record(
            str(job_id), request.submission,
            str(request.operation_id) if request.operation_id is not None else None,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/jobs/{job_id}/retrieval-review/submissions')
def balanced_job_retrieval_history(job_id: UUID, limit: int = 100) -> dict:
    from saia.balanced_retrieval_review_store import history
    try:
        return history(str(job_id), limit)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/jobs/{job_id}/retrieval-review/submissions/{submission_id}')
def balanced_job_retrieval_saved_submission(
    job_id: UUID, submission_id: UUID,
) -> dict:
    from saia.balanced_retrieval_review_store import read
    try:
        return read(str(job_id), str(submission_id))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/jobs/{job_id}/retrieval-review/compare-stored')
def balanced_job_retrieval_compare(
    job_id: UUID, request: AnnotationStoredComparisonRequest,
) -> dict:
    from saia.balanced_retrieval_review_store import compare
    try:
        return compare(
            str(job_id), str(request.left_submission_id), str(request.right_submission_id)
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/jobs/{job_id}/retrieval-review/adjudications')
def balanced_job_retrieval_adjudicate(
    job_id: UUID, request: RetrievalAdjudicationRequest,
) -> dict:
    from saia.balanced_retrieval_adjudication import record
    try:
        return record(
            str(job_id), str(request.left_submission_id),
            str(request.right_submission_id), request.adjudicator_id,
            [item.model_dump() for item in request.decisions],
            str(request.operation_id) if request.operation_id is not None else None,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/jobs/{job_id}/retrieval-review/adjudications')
def balanced_job_retrieval_adjudication_history(
    job_id: UUID, limit: int = 100,
) -> dict:
    from saia.balanced_retrieval_adjudication import history
    try:
        return history(str(job_id), limit)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/jobs/{job_id}/retrieval-review/status')
def balanced_job_retrieval_status(job_id: UUID) -> dict:
    from saia.balanced_retrieval_adjudication import status
    try:
        return status(str(job_id))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/jobs/{job_id}/retrieval-review/full-analysis', status_code=202)
def balanced_job_full_analysis(
    job_id: UUID, request: UniversalFullAnalysisRequest,
) -> dict:
    """Explicitly bridge an adjudicated query into the frozen arXiv pipeline."""
    from saia.jobs import JobConflict, enqueue_full_analysis
    from saia.universal_materialization import materialize
    try:
        materialization = materialize(
            str(job_id), str(request.adjudication_id), request.accepted_by,
            acknowledge_arxiv_only=request.acknowledge_arxiv_only,
            acknowledge_phrase_union=request.acknowledge_phrase_union,
        )
        analysis_job = enqueue_full_analysis(
            materialization["mission_id"], materialization["query_version_id"],
            request.accepted_by, max_records=request.max_records, top_n=request.top_n,
            acknowledge_source_scope=True,
            operation_id=(str(request.operation_id)
                          if request.operation_id is not None else None),
            max_attempts=request.max_attempts,
        )
        return {
            "materialization": materialization,
            "analysis_job": analysis_job,
            "retrieval_precision_is_not_weak_signal_accuracy": True,
            "automatic_threshold_change": False,
        }
    except JobConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/jobs/{job_id}/full-analysis', status_code=202)
def automatic_candidate_full_analysis(
    job_id: UUID, request: AutomaticCandidateAnalysisRequest,
) -> dict:
    """Queue automatic candidate generation; expert validation is optional."""
    from saia.jobs import JobConflict, enqueue_full_analysis
    from saia.universal_materialization import materialize_candidate
    try:
        materialization = materialize_candidate(str(job_id), request.requested_by)
        analysis_job = enqueue_full_analysis(
            materialization["mission_id"], materialization["query_version_id"],
            request.requested_by, max_records=request.max_records, top_n=request.top_n,
            acknowledge_source_scope=True,
            operation_id=(str(request.operation_id)
                          if request.operation_id is not None else None),
            max_attempts=request.max_attempts,
        )
        return {
            "materialization": materialization,
            "analysis_job": analysis_job,
            "candidate_generation": "automatic",
            "expert_validation_required": False,
            "expert_validation_status": "not_requested",
            "retrieval_review_required": False,
        }
    except JobConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/jobs/{job_id}/cancel')
def cancel_job(job_id: UUID, request: JobActionRequest) -> dict:
    from saia.jobs import cancel
    try:
        return cancel(str(job_id), request.requested_by)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/jobs/{job_id}/retry', status_code=202)
def retry_job(job_id: UUID, request: JobActionRequest) -> dict:
    from saia.jobs import JobConflict, retry
    try:
        return retry(
            str(job_id), request.requested_by,
            str(request.operation_id) if request.operation_id is not None else None,
        )
    except JobConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/missions/{mission_id}/jobs')
def job_history(mission_id: str, limit: int = 50) -> dict:
    from saia.jobs import history
    try:
        return history(mission_id, limit)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


class SeriesWindow(BaseModel):
    start: date
    end: date
    topic_works: int = Field(ge=0)
    corpus_works: int | None = Field(default=None, ge=0)
    complete: bool = True
    coverage_comparable: bool | None = None


class SeriesRequest(BaseModel):
    as_of_date: date
    windows: list[SeriesWindow] = Field(min_length=1, max_length=200)


@app.post('/analysis/series')
def series_measurement(request: SeriesRequest) -> dict:
    """Измерения публикационного ряда; сами по себе не классификация сигналов."""
    try:
        return analyze_series([WindowCounts(**w.model_dump()) for w in request.windows],
                              request.as_of_date)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/corpus/{mission_id}/terminology')
def corpus_terminology(mission_id: str, quality_generation_id: int | None = None) -> dict:
    from saia.terminology import analyze_mission
    try:
        return analyze_mission(mission_id, quality_generation_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "saia", "version": app.version}


@app.get("/methodology")
def methodology_info() -> dict[str, Any]:
    config = methodology.load_default()
    return {
        "version": config.version,
        "hash": config.config_hash,
        "gates": config.gates_name,
        "scoring": config.score_name,
        "confidence_stage": config.confidence_stage,
        "top_n": config.top_n,
        "sources": ["OpenAlex", "arXiv"],
    }


@app.post("/assess")
def assess(request: AssessmentRequest) -> dict:
    metrics = CandidateMetrics(**request.model_dump())
    return assess_candidate(metrics, methodology.load_default()).to_dict()


@app.post("/discover/preview")
def discovery_preview(request: DiscoveryRequest) -> dict:
    """Получить публикационный корпус, не выдавая его за список сигналов."""
    return discover(
        request.query, request.date_from, request.as_of_date, request.limit_per_source
    ).to_dict()


@app.post("/query-plan/preview")
def transparent_query_plan_preview(request: QueryPlanPreviewRequest) -> dict:
    """Suggest optional branches while preserving the user's free query."""
    from saia.query_planning import preview
    try:
        return preview(request.query, request.max_suggestions)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/query-plan/approve")
def approve_transparent_query_plan(request: QueryPlanApprovalRequest) -> dict:
    """Persist explicit branch selection without starting collection."""
    from saia.query_plan_store import approve
    try:
        return approve(
            request.query, request.max_suggestions, request.preview_payload_sha256,
            request.selected_branch_ids, request.approved_by,
            str(request.operation_id) if request.operation_id else None,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/query-plans")
def approved_query_plan_history(limit: int = 100) -> dict:
    from saia.query_plan_store import history
    try:
        return history(limit)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/query-plans/{plan_id}")
def approved_query_plan(plan_id: str) -> dict:
    from saia.query_plan_store import read
    try:
        return read(plan_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/query-plans/{plan_id}/compile")
def compile_approved_query_plan(plan_id: UUID, request: QueryPlanCompilationRequest) -> dict:
    from saia.compiled_query_plan_store import compile_plan
    try:
        return compile_plan(
            str(plan_id), [item.model_dump() for item in request.branch_specs],
            request.compiled_by,
            str(request.operation_id) if request.operation_id else None,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/compiled-query-plans/{compilation_id}")
def compiled_query_plan(compilation_id: UUID) -> dict:
    from saia.compiled_query_plan_store import read
    try:
        return read(str(compilation_id))
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/query-plans/{plan_id}/jobs/discovery", status_code=202)
def create_balanced_discovery_job(
    plan_id: UUID, request: BalancedDiscoveryJobRequest,
) -> dict:
    from saia.jobs import JobConflict, enqueue_balanced_discovery
    try:
        return enqueue_balanced_discovery(
            str(plan_id), request.requested_by, request.date_from, request.as_of_date,
            limit_per_source=request.limit_per_source,
            max_results=request.max_results,
            compiled_query_plan_id=(str(request.compiled_query_plan_id)
                                    if request.compiled_query_plan_id else None),
            openalex_collection_mode=request.openalex_collection_mode,
            operation_id=(str(request.operation_id) if request.operation_id else None),
            max_attempts=request.max_attempts,
        )
    except JobConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/query-plans/{plan_id}/jobs")
def balanced_discovery_job_history(plan_id: UUID, limit: int = 50) -> dict:
    from saia.jobs import query_plan_history
    try:
        return query_plan_history(str(plan_id), limit)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/signals/{mission_id}")
def signal_cards(mission_id: str, score_run_id: int | None = None) -> dict:
    """Вернуть последний доказательный пакет, а не поисковую выдачу."""
    try:
        from saia.candidates import export_cards
        return export_cards(mission_id, score_run_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/triage/{mission_id}")
def current_triage(mission_id: str, score_run_id: int | None = None,
                   limit: int = 15) -> dict:
    """Order saved cards for analyst review; never return a prospectivity TOP."""
    try:
        from saia.candidates import export_cards
        from saia.triage import build_queue
        from saia.coverage_passport import read_summary
        cards = export_cards(mission_id, score_run_id)
        result = build_queue(cards, limit)
        # A saved, integrity-checked venue layer may explain where selected
        # OpenAlex records appeared. It never modifies the stored score.
        if result.get("queue"):
            from saia.openalex_venue_view import enrich_triage, load_index
            try:
                result = enrich_triage(result, load_index())
            except (FileNotFoundError, ValueError, OSError):
                result["venue_context"] = {
                    "status": "unavailable", "ranking_changed": False,
                    "scientific_primary_result_verified": False,
                }
        notes = (cards.get("provenance") or [{}])[0].get("notes") or {}
        passport_id = notes.get("coverage_passport_id")
        result["parent_corpus_context"] = (
            read_summary(passport_id) if passport_id is not None else None
        )
        return result
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/scout-results/{mission_id}')
def scout_multisource_results(mission_id: str, score_run_id: int | None = None, limit: int = 100,
                             include_public_signals: bool = True) -> dict:
    from saia.scout_results import build
    try:
        return build(mission_id, score_run_id, limit, include_public_signals=include_public_signals)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


class ScoutEnrichmentRequest(BaseModel):
    candidate_ids: list[StrictInt] = Field(min_length=1, max_length=15)
    sources: list[str] | None = Field(default=None, min_length=1, max_length=8)


@app.post('/scout-results/{mission_id}/enrich')
def enrich_scout_results(mission_id: str, score_run_id: int, request: ScoutEnrichmentRequest) -> dict:
    from saia.scout_results import enrich
    try:
        return enrich(mission_id, score_run_id, request.candidate_ids, request.sources)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/signals/{mission_id}/{candidate_id}/assessment')
def candidate_multisource_assessment(mission_id: str, candidate_id: int, score_run_id: int) -> dict:
    from saia.candidate_assessment import for_candidate
    try:
        return for_candidate(mission_id, score_run_id, candidate_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/signals/{mission_id}/{candidate_id}/external-links", status_code=201)
def link_candidate_external_record(mission_id: str, candidate_id: int,
                                   request: CandidateExternalLinkRequest,
                                   score_run_id: int) -> dict:
    from saia.candidate_external_links import link
    try:
        return link(
            mission_id, score_run_id, candidate_id,
            str(request.observation_id), request.record_url,
            request.assessment, request.linked_by, request.rationale,
            str(request.operation_id) if request.operation_id else None,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/signals/{mission_id}/{candidate_id}/external-links")
def candidate_external_records(mission_id: str, candidate_id: int,
                               score_run_id: int) -> dict:
    from saia.candidate_external_links import for_candidate
    try:
        return for_candidate(mission_id, score_run_id, candidate_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/signals/{mission_id}/{candidate_id}/publications")
def candidate_publication_page(mission_id: str, candidate_id: int,
                               score_run_id: int, limit: int = 20, offset: int = 0) -> dict:
    from saia.candidate_publications import for_candidate, validate_page
    try:
        validate_page(limit, offset)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        return for_candidate(mission_id, score_run_id, candidate_id, limit, offset)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/signals/{mission_id}/{candidate_id}/reviews")
def save_score_candidate_review(mission_id: str, candidate_id: int,
                                request: ExpertReviewRequest,
                                score_run_id: int | None = None) -> dict:
    from saia.score_expert import record
    try:
        return record(
            mission_id, candidate_id, request.decision, request.reviewed_by,
            request.rationale, request.sources, score_run_id,
            str(request.operation_id) if request.operation_id is not None else None,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/signals/{mission_id}/{candidate_id}/reviews")
def score_candidate_review_history(mission_id: str, candidate_id: int,
                                   score_run_id: int | None = None,
                                   limit: int = 100) -> dict:
    from saia.score_expert import history
    try:
        return history(mission_id, candidate_id, score_run_id, limit)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/benchmarks/{mission_id}")
def benchmark_report(mission_id: str, score_run_id: int | None = None) -> dict:
    """Вернуть контрольный ретротест, включая честный отрицательный вывод."""
    try:
        from saia.benchmark import build_benchmark
        return build_benchmark(mission_id, score_run_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get('/history/{mission_id}')
def run_history(mission_id: str, limit: int = 100) -> dict:
    from saia import runs
    try:
        return runs.history(mission_id, limit)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get('/corpus/{mission_id}/embedding-status')
def corpus_embedding_status(mission_id: str, quality_generation_id: int, model: str) -> dict:
    from saia import embedding_status
    try:
        return embedding_status.read(mission_id, quality_generation_id, model)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


def main() -> None:
    import uvicorn

    uvicorn.run("saia.api:app", host="127.0.0.1", port=8080, reload=False)
