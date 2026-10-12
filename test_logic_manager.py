"""Offline tests with hardcoded sample AI responses - no live API, functions only.
Run: python test_logic_manager.py   (also works with pytest)"""
import copy
import json
import os
import sys
import tempfile

import ai_manager
import data_manager
import logic_manager

PROFILE = {"age": 30, "height_cm": 175, "weight_kg": 70, "fitness_level": "intermediate",
           "health_concerns": "none", "fitness_goal": "run 5k"}

GOOD_AI = {"exercise_type": "easy run", "duration_min": 30, "intensity": "moderate",
           "target_hr_bpm": 140, "target_pace_min_per_km": 6.5, "warmup": "5 min walk",
           "main_set": "20 min steady", "cooldown": "5 min walk",
           "rationale": "Builds aerobic base.", "risk_level": "low"}


def make(**overrides):
    data = copy.deepcopy(GOOD_AI)
    data.update(overrides)
    return data


def past(intensity="moderate", duration=30, rpe=5, pain=False):
    return {"id": 1, "date": "2026-10-01", "planned": {"intensity": intensity},
            "actual": {"duration_min": duration, "rpe": rpe, "pain_reported": pain}}


def outcome(session, profile=PROFILE, history=()):
    return logic_manager.evaluate_session(session, profile, list(history))["outcome"]


# ---------- logic_manager ----------
def test_accepts_good_session():
    assert outcome(GOOD_AI) == "accept"


def test_rejects_heart_rate_above_90_percent():
    assert outcome(make(target_hr_bpm=180)) == "reject"


def test_rejects_impossible_duration():
    assert outcome(make(duration_min=200)) == "reject"


def test_rejects_impossible_pace():
    assert outcome(make(target_pace_min_per_km=1.5)) == "reject"


def test_null_pace_is_allowed():
    assert outcome(make(target_pace_min_per_km=None)) == "accept"


def test_rejects_high_intensity_with_health_concerns():
    assert outcome(make(intensity="high"), dict(PROFILE, health_concerns="knee injury")) == "reject"


def test_rejects_long_session_for_beginner():
    assert outcome(make(duration_min=90), dict(PROFILE, fitness_level="beginner")) == "reject"


def test_rejects_high_intensity_after_pain():
    assert outcome(make(intensity="high"), history=[past(pain=True)]) == "reject"


def test_rejects_high_intensity_after_high_rpe():
    assert outcome(make(intensity="high"), history=[past(rpe=9)]) == "reject"


def test_rejects_back_to_back_high_intensity():
    assert outcome(make(intensity="high"), history=[past(intensity="high")]) == "reject"


def test_rejects_ai_self_rated_high_risk():
    assert outcome(make(risk_level="high")) == "reject"


def test_flags_big_duration_jump():
    result = logic_manager.evaluate_session(make(duration_min=50), PROFILE, [past(duration=30)])
    assert result["outcome"] == "flag"
    assert result["score"] < 100


def test_session_log_accepts_realistic_values():
    assert logic_manager.validate_session_log({"avg_pace_min_per_km": 6.0, "avg_hr_bpm": 150}, PROFILE) == []


def test_session_log_rejects_impossible_values():
    errors = logic_manager.validate_session_log({"avg_pace_min_per_km": 2.0, "avg_hr_bpm": 220}, PROFILE)
    assert len(errors) == 2


# ---------- ai_manager parsing (no network) ----------
def test_parse_valid_json():
    data, error = ai_manager.parse_and_validate_response(json.dumps(GOOD_AI))
    assert error is None and data["exercise_type"] == "easy run"


def test_parse_json_in_code_fence():
    _, error = ai_manager.parse_and_validate_response("```json\n" + json.dumps(GOOD_AI) + "\n```")
    assert error is None


def test_parse_plain_text_is_malformed():
    data, error = ai_manager.parse_and_validate_response("Go for a nice jog!")
    assert data is None and error is not None


def test_parse_missing_field():
    bad = make()
    del bad["intensity"]
    assert "intensity" in ai_manager.parse_and_validate_response(json.dumps(bad))[1]


def test_parse_wrong_type():
    assert ai_manager.parse_and_validate_response(json.dumps(make(duration_min="thirty")))[1] is not None


def test_parse_bad_enum_value():
    assert ai_manager.parse_and_validate_response(json.dumps(make(intensity="insane")))[1] is not None


def test_parse_empty_response():
    assert ai_manager.parse_and_validate_response("")[1] is not None


# ---------- data_manager ----------
def test_missing_file_gives_empty_store():
    with tempfile.TemporaryDirectory() as tmp:
        store, status = data_manager.load_data(os.path.join(tmp, "none.json"))
    assert status == "missing" and store["sessions"] == []


def test_corrupt_file_is_backed_up_not_fatal():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "data.json")
        with open(path, "w") as handle:
            handle.write("{not valid json")
        store, status = data_manager.load_data(path)
        assert status == "corrupt" and store["sessions"] == []
        assert any(".corrupt-" in name for name in os.listdir(tmp))


def test_round_trip_and_filter():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "data.json")
        store = data_manager.empty_store()
        data_manager.save_profile(store, PROFILE, path)
        data_manager.set_pending_session(store, GOOD_AI, path)
        data_manager.complete_pending_session(store, {"duration_min": 30, "rpe": 6, "pain_reported": True}, path)
        loaded, status = data_manager.load_data(path)
    assert status == "ok" and len(loaded["sessions"]) == 1
    assert len(data_manager.filter_sessions(loaded, pain_only=True)) == 1
    assert len(data_manager.filter_sessions(loaded, min_rpe=9)) == 0


def run_all():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, func in tests:
        try:
            func()
            sys.stdout.write("PASS {}\n".format(name))
        except AssertionError:
            failed += 1
            sys.stdout.write("FAIL {}\n".format(name))
    sys.stdout.write("{} passed, {} failed\n".format(len(tests) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_all())
