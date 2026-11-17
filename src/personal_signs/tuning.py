"""Measure the match / maybe / not-recognized cut-offs on every recorded signer.

Reads the clips saved under ``data/training_samples/<user>/<sign>/`` (a
``seed`` folder counts as one signer) and replays the same decision rule as
``engine.predict_user`` with different cut-offs. Three tests are run:

* ``same_signer``  - each clip against the same person's other clips.
* ``unknown_sign`` - the clip's whole sign is removed first, so the recognizer
  was never taught it. Any "match" here is a false match.
* ``cross_signer`` - each clip against everyone else's clips (does one
  person's model work for a new person?).

The report is written to ``data/personal_models/threshold_report.json``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import engine

QUERY_FRAMES = engine.PREDICT_FRAMES
MIN_CLIPS_PER_SIGN = 3  # same rule as engine.train_user


@dataclass
class Clip:
    signer: str
    label: str
    path: Path
    sequence: np.ndarray  # 16-frame features (examples)
    query: np.ndarray  # 8-frame features (what live recognition sees)


@dataclass
class Trial:
    test: str
    signer: str
    actual: str
    known: bool
    best_label: str
    best_distance: float
    gap: float
    single_label: bool
    hands_ok: bool


def load_clips(exact: bool = False) -> list[Clip]:
    clips: list[Clip] = []
    if not engine.SAMPLE_ROOT.is_dir():
        return clips
    for user_dir in sorted(engine.SAMPLE_ROOT.iterdir()):
        if not user_dir.is_dir() or user_dir.name.startswith("."):
            continue
        for label_dir in sorted(user_dir.iterdir()):
            if not label_dir.is_dir() or label_dir.name.startswith("."):
                continue
            for path in engine._clip_files(label_dir):
                try:
                    sequence = np.asarray(engine._load_sequence(path), dtype=np.float32)
                    if exact:
                        query = np.asarray(
                            engine.extract_sequence(path.read_bytes(), frame_count=QUERY_FRAMES), dtype=np.float32
                        )
                    else:
                        query = sequence[np.linspace(0, len(sequence) - 1, QUERY_FRAMES).astype(int)]
                except ValueError as exc:
                    print(f"  skipped {path.name}: {exc}")
                    continue
                clips.append(Clip(user_dir.name, label_dir.name, path, sequence, query))
    return clips


def _trainable(clips: list[Clip]) -> list[Clip]:
    counts: dict[str, int] = {}
    for clip in clips:
        counts[clip.label] = counts.get(clip.label, 0) + 1
    return [clip for clip in clips if counts[clip.label] >= MIN_CLIPS_PER_SIGN]


def _score(test: str, query: Clip, examples: list[Clip]) -> Trial | None:
    if not examples:
        return None
    distances: dict[str, list[float]] = {}
    for example in examples:
        distances.setdefault(example.label, []).append(engine.sequence_distance(query.query, example.sequence))
    ranked = sorted((float(np.mean(values)), label) for label, values in distances.items())
    best_distance, best_label = ranked[0]
    signals = engine._signal_diagnostics(query.query)
    hands_ok = signals["hands_frames"] >= engine.PREDICT_MIN_HANDS_RATIO * max(signals["frames_analyzed"], 1)
    return Trial(
        test=test,
        signer=query.signer,
        actual=query.label,
        known=query.label in distances,
        best_label=best_label,
        best_distance=best_distance,
        gap=(ranked[1][0] - best_distance) if len(ranked) > 1 else 0.0,
        single_label=len(ranked) == 1,
        hands_ok=hands_ok,
    )


def run_trials(clips: list[Clip]) -> list[Trial]:
    trials: list[Trial | None] = []
    for signer in sorted({clip.signer for clip in clips}):
        own = _trainable([clip for clip in clips if clip.signer == signer])
        others = _trainable([clip for clip in clips if clip.signer != signer])
        for clip in own:
            trials.append(_score("same_signer", clip, [other for other in own if other is not clip]))
            trials.append(_score("unknown_sign", clip, [other for other in own if other.label != clip.label]))
            trials.append(_score("cross_signer", clip, others))
    return [trial for trial in trials if trial is not None]


def decide(trial: Trial, accept: float, gap_min: float, confirm: float) -> str:
    """Same rule as engine.predict_user."""
    if trial.hands_ok and trial.best_distance < accept and (trial.single_label or trial.gap > gap_min):
        return "accepted"
    if trial.hands_ok and trial.best_distance < confirm:
        return "confirm"
    return "rejected"


def rates(trials: list[Trial], accept: float, gap_min: float, confirm: float) -> dict:
    known = [t for t in trials if t.known]
    unknown = [t for t in trials if not t.known]
    counts = dict.fromkeys(
        ("correct_match", "wrong_match", "correct_maybe", "wrong_maybe", "not_recognized", "unknown_matched", "unknown_maybe"), 0
    )
    for trial in known:
        decision = decide(trial, accept, gap_min, confirm)
        correct = trial.best_label == trial.actual
        if decision == "accepted":
            counts["correct_match" if correct else "wrong_match"] += 1
        elif decision == "confirm":
            counts["correct_maybe" if correct else "wrong_maybe"] += 1
        else:
            counts["not_recognized"] += 1
    for trial in unknown:
        decision = decide(trial, accept, gap_min, confirm)
        counts["unknown_matched"] += decision == "accepted"
        counts["unknown_maybe"] += decision == "confirm"

    def share(value: int, total: int) -> float | None:
        return round(value / total, 4) if total else None

    result = {"known_trials": len(known), "unknown_trials": len(unknown)}
    for name, value in counts.items():
        result[name] = share(value, len(unknown) if name.startswith("unknown") else len(known))
    return result


def false_match_rate(trials: list[Trial], accept: float, gap_min: float) -> float:
    """Share of trials where the recognizer would confidently name the wrong sign."""
    bad = sum(
        1
        for trial in trials
        if decide(trial, accept, gap_min, np.inf) == "accepted" and (not trial.known or trial.best_label != trial.actual)
    )
    return bad / len(trials) if trials else 0.0


def suggest(trials: list[Trial], max_false_match: float) -> dict:
    personal = [t for t in trials if t.test in {"same_signer", "unknown_sign"}]
    gap_min = engine.ACCEPT_GAP
    accept = 0.02
    for value in np.round(np.arange(0.02, 0.301, 0.005), 3):
        if false_match_rate(personal, float(value), gap_min) <= max_false_match:
            accept = float(value)
    correct = sorted(t.best_distance for t in personal if t.known and t.best_label == t.actual and t.hands_ok)
    confirm = float(np.round(np.percentile(correct, 95), 3)) if correct else engine.CONFIRM_DISTANCE
    return {
        "ACCEPT_DISTANCE": accept,
        "ACCEPT_GAP": gap_min,
        "CONFIRM_DISTANCE": max(confirm, accept),
        "rule": (
            f"Largest ACCEPT_DISTANCE whose false-match rate on same_signer + unknown_sign is <= {max_false_match:.0%}; "
            "CONFIRM_DISTANCE = 95th percentile of correct-match distances."
        ),
    }


def report(max_false_match: float = 0.02, exact: bool = False) -> dict:
    """Run every test, write the JSON report and return it."""
    clips = load_clips(exact)
    signers = sorted({clip.signer for clip in clips})
    per_signer = {
        signer: {
            label: sum(1 for c in clips if c.signer == signer and c.label == label)
            for label in sorted({c.label for c in clips if c.signer == signer})
        }
        for signer in signers
    }
    trials = run_trials(clips)
    current = (engine.ACCEPT_DISTANCE, engine.ACCEPT_GAP, engine.CONFIRM_DISTANCE)
    suggestion = suggest(trials, max_false_match)
    proposed = (suggestion["ACCEPT_DISTANCE"], suggestion["ACCEPT_GAP"], suggestion["CONFIRM_DISTANCE"])
    warnings = []
    if len(signers) < 3:
        warnings.append(f"Only {len(signers)} signer(s). Record at least 3-5 different people before changing the cut-offs.")
    if any(len(labels) < 3 for labels in per_signer.values()):
        warnings.append("Some signers have fewer than 3 signs; the unknown-sign test is weak for them.")
    result = {
        "signers": per_signer,
        "clips": len(clips),
        "query_frames": "exact" if exact else "sub-sampled from cached 16-frame features",
        "current": dict(zip(("ACCEPT_DISTANCE", "ACCEPT_GAP", "CONFIRM_DISTANCE"), current)),
        "suggested": suggestion,
        "results": {
            test: {
                "current": rates([t for t in trials if t.test == test], *current),
                "suggested": rates([t for t in trials if t.test == test], *proposed),
            }
            for test in ("same_signer", "unknown_sign", "cross_signer")
        },
        "warnings": warnings,
    }
    engine.MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    (engine.MODEL_ROOT / "threshold_report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def print_report(result: dict) -> None:
    print(f"Signers: {len(result['signers'])}  clips: {result['clips']}")
    for signer, labels in result["signers"].items():
        print(f"  {signer}: " + ", ".join(f"{label}={count}" for label, count in labels.items()))
    current, suggested = result["current"], result["suggested"]
    print(f"\nCurrent   accept<{current['ACCEPT_DISTANCE']}  gap>{current['ACCEPT_GAP']}  maybe<{current['CONFIRM_DISTANCE']}")
    print(f"Suggested accept<{suggested['ACCEPT_DISTANCE']}  gap>{suggested['ACCEPT_GAP']}  maybe<{suggested['CONFIRM_DISTANCE']}")
    for test, rows in result["results"].items():
        for name, r in rows.items():
            if r["known_trials"]:
                maybe = (r["correct_maybe"] or 0) + (r["wrong_maybe"] or 0)
                print(f"  {test:13s} {name:9s} correct match {r['correct_match']:.0%}  wrong match {r['wrong_match']:.0%}"
                      f"  maybe {maybe:.0%}  not recognized {r['not_recognized']:.0%}")
            if r["unknown_trials"]:
                print(f"  {test:13s} {name:9s} untaught sign called a match {r['unknown_matched']:.0%}"
                      f"  maybe {r['unknown_maybe']:.0%}")
    for warning in result["warnings"]:
        print(f"\nWARNING: {warning}")
    print(f"\nFull report: {engine.MODEL_ROOT / 'threshold_report.json'}")
