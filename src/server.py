"""Web view of the audit pipeline.

Three views, intentionally document-oriented:

  GET /                                   — index of available wargames
  GET /wargame/{namespace}                — full audit report (long-form)
  GET /wargame/{namespace}/branch/{id}    — drilldown for a single branch

The aesthetic is a research report, not a dashboard. The 2x2 plot is one
figure within the report, not its centerpiece. Caveats are prominent
prose; tables are tables; charts are figures with captions and source
lines. See README's epistemic-stance section for the principles the UI
must visibly enforce (markets-vs-Metaculus separation, three first-class
output components, conditional-vs-unconditional structured comparisons).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jinja2
import markdown as md_lib
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .analysis.audit import audit_path_for
from .analysis.types import AuditReport
from .forecasts.sidecar import sidecar_path_for
from .ingest.assumption_extractor import assumptions_path_for
from .ingest.types import Wargame, WargameAssumption
from .ingest.wargame_loader import load_wargame
from .matching.matcher import MatchResult, matches_path_for

REPO = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = REPO / "examples"
UI_DIR = REPO / "ui"
TEMPLATES_DIR = UI_DIR / "templates"
STATIC_DIR = UI_DIR / "static"

app = FastAPI(title="Wargame Reality Check")
# Build the Jinja env explicitly with cache_size=0 — a default-cache env
# combined with non-hashable template globals (our pydantic models) crashes
# inside Jinja2's cache key construction. Templates are small; we don't
# need the cache.
_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=jinja2.select_autoescape(["html", "xml"]),
    cache_size=0,
)


def _intcomma(value: float | int | None) -> str:
    """Thousands-separated number filter — Jinja's `format` uses %-style."""
    if value is None:
        return "—"
    return f"{value:,.0f}"


_env.filters["intcomma"] = _intcomma
templates = Jinja2Templates(env=_env)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ----- Helpers: discovery + loading -----

def _wargames_available() -> list[dict[str, Any]]:
    """Scan examples/ for wargame.yaml files; return summary metadata."""
    out: list[dict[str, Any]] = []
    if not EXAMPLES_DIR.exists():
        return out
    for sub in sorted(EXAMPLES_DIR.iterdir()):
        if not sub.is_dir():
            continue
        wg_path = sub / "wargame.yaml"
        if not wg_path.exists():
            continue
        try:
            wg = load_wargame(wg_path)
        except Exception as e:
            out.append({"namespace": sub.name, "name": sub.name, "error": str(e)})
            continue
        readme = sub / "README.md"
        out.append({
            "namespace": sub.name,
            "name": wg.name,
            "brief": wg.brief,
            "n_branches": len(wg.branches),
            "wargame_path": str(wg_path),
            "has_readme": readme.exists(),
            "has_audit": audit_path_for(wg_path).exists(),
            "has_matches": matches_path_for(wg_path).exists(),
            "has_sidecar": sidecar_path_for(wg_path).exists(),
        })
    return out


def _load_namespace(namespace: str) -> dict[str, Any]:
    """Locate a wargame by namespace and load all artifacts that exist."""
    sub = EXAMPLES_DIR / namespace
    wg_path = sub / "wargame.yaml"
    if not wg_path.exists():
        raise HTTPException(404, f"unknown wargame namespace: {namespace}")
    wg = load_wargame(wg_path)

    readme_html: str | None = None
    readme_path = sub / "README.md"
    if readme_path.exists():
        readme_html = md_lib.markdown(
            readme_path.read_text(),
            extensions=["fenced_code", "tables"],
        )

    a_path = assumptions_path_for(wg_path)
    assumptions: list[WargameAssumption] = []
    if a_path.exists():
        assumptions = [
            WargameAssumption.model_validate(item)
            for item in json.loads(a_path.read_text())
        ]

    m_path = matches_path_for(wg_path)
    matches: list[MatchResult] = []
    if m_path.exists():
        matches = [
            MatchResult.model_validate(item)
            for item in json.loads(m_path.read_text())
        ]
    matches_by_branch = {m.branch_id: m for m in matches}

    audit: AuditReport | None = None
    branch_audit_by_id: dict[str, Any] = {}
    a_audit_path = audit_path_for(wg_path)
    if a_audit_path.exists():
        audit = AuditReport.model_validate(json.loads(a_audit_path.read_text()))
        branch_audit_by_id = {b.branch_id: b for b in audit.branches}

    sidecar_entries: list[dict[str, Any]] = []
    s_path = sidecar_path_for(wg_path)
    if s_path.exists():
        sidecar_entries = json.loads(s_path.read_text()).get("entries", [])
    sidecar_by_branch = {e["branch_id"]: e for e in sidecar_entries}

    return {
        "namespace": namespace,
        "wargame": wg,
        "readme_html": readme_html,
        "assumptions": assumptions,
        "assumptions_by_branch": {a.branch_id: a for a in assumptions},
        "matches_by_branch": matches_by_branch,
        "audit": audit,
        "branch_audit_by_id": branch_audit_by_id,
        "sidecar_by_branch": sidecar_by_branch,
    }


# ----- Routes -----

@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "index.html", {"wargames": _wargames_available()}
    )


@app.get("/wargame/{namespace}", response_class=HTMLResponse)
def wargame_view(namespace: str, request: Request) -> HTMLResponse:
    ctx = _load_namespace(namespace)
    return templates.TemplateResponse(request, "wargame.html", ctx)


@app.get("/wargame/{namespace}/branch/{branch_id}", response_class=HTMLResponse)
def branch_view(namespace: str, branch_id: str, request: Request) -> HTMLResponse:
    ctx = _load_namespace(namespace)
    a = ctx["assumptions_by_branch"].get(branch_id)
    if not a:
        raise HTTPException(404, f"unknown branch_id: {branch_id}")
    match = ctx["matches_by_branch"].get(branch_id)
    audit = ctx["audit"]
    branch_audit = None
    if audit:
        branch_audit = next(
            (b for b in audit.branches if b.branch_id == branch_id), None
        )
    return templates.TemplateResponse(
        request,
        "branch.html",
        {
            "namespace": namespace,
            "wargame": ctx["wargame"],
            "assumption": a,
            "match": match,
            "branch_audit": branch_audit,
            "audit": audit,
            "sidecar": ctx["sidecar_by_branch"].get(branch_id),
        },
    )


@app.get("/api/wargame/{namespace}/audit.json")
def wargame_audit_json(namespace: str) -> dict[str, Any]:
    """Used by the 2x2 chart in the browser."""
    ctx = _load_namespace(namespace)
    if ctx["audit"] is None:
        return {}
    return ctx["audit"].model_dump(mode="json")
