"""Pick which CV track (Data Eng / ML Eng / Research, or whatever the user configured)
a job belongs to."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .llm import LLM, untrusted
from .settings import Track


@dataclass
class RouteResult:
    track: Track | None
    confidence: float
    scores: dict[str, float]
    method: str = "rules"


def _hits(terms: list[str], text: str) -> int:
    return sum(1 for t in terms if re.search(r"(?<![\w-])" + re.escape(t) + r"(?![\w-])", text))


def score_tracks(title: str, description: str, tracks: list[Track]) -> dict[str, float]:
    t, d = (title or "").lower(), (description or "").lower()
    out = {}
    for tr in tracks:
        title_score = 3.0 * _hits(tr.titles, t)
        kw = _hits(tr.keywords, d) / max(1, len(tr.keywords))     # 0..1 coverage
        out[tr.id] = title_score + 4.0 * kw
    return out


def route(title: str, description: str, tracks: list[Track], llm: LLM | None = None) -> RouteResult:
    if not tracks:
        return RouteResult(None, 0.0, {})
    if len(tracks) == 1:
        return RouteResult(tracks[0], 1.0, {tracks[0].id: 1.0})
    scores = score_tracks(title, description, tracks)
    ranked = sorted(tracks, key=lambda tr: scores[tr.id], reverse=True)
    best, second = scores[ranked[0].id], scores[ranked[1].id]
    conf = 0.0 if best <= 0 else (best - second) / best
    if conf >= 0.25 or not (llm and llm.available):
        return RouteResult(ranked[0] if best > 0 else None, conf, scores)
    # Ambiguous (e.g. "ML Engineer – data platform"): let the model choose among tracks.
    menu = "\n".join(f"- {tr.id}: {tr.label}" for tr in tracks)
    try:
        data = llm.complete_json(
            f"Which resume track best fits this job? Options:\n{menu}\n\nJob title: {title}\n"
            f"{untrusted(description, 5000)}\n\nRespond as {{\"track\": \"<id>\", \"why\": \"...\"}}",
            max_tokens=200)
        chosen = next((tr for tr in tracks if tr.id == data.get("track")), None)
        if chosen:
            return RouteResult(chosen, max(conf, 0.5), scores, "llm")
    except Exception:
        pass
    return RouteResult(ranked[0] if best > 0 else None, conf, scores)
