"""Offline tests with hardcoded sample AI responses - no live API, functions only.
Run: python test_logic_manager.py   (also works with pytest)"""
import copy
import datetime
import json
import os
import sys
import tempfile

import ai_manager
import data_manager
import logic_manager

PROFILE = {"age": 30, "sex": "male", "height_cm": 175, "weight_kg": 70, "fitness_level": "intermediate",
           "health_concerns": "none", "fitness_goal": "run 5k", "days_per_week": 4,
           "session_duration_min": 90, "equipment": ["dumbbells", "bench"],
           "preferred_activities": ["running", "strength training"]}

GOOD_AI = {"exercise_type": "easy run", "activity": "running", "duration_min": 30, "intensity": "moderate",
           "target_hr_bpm": 140, "target_pace_min_per_km": 6.5, "equipment_used": ["bodyweight"],
           "warmup": "5 min walk",
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
def test_rejects_activity_not_chosen():
    assert outcome(make(activity="swimming")) == "reject"


def test_rejects_swimming_without_pool():
    profile = dict(PROFILE, preferred_activities=["running", "swimming"])
    assert outcome(make(activity="swimming", target_pace_min_per_km=None), profile) == "reject"


def test_accepts_swimming_with_pool():
    profile = dict(PROFILE, preferred_activities=["swimming"], equipment=["swimming pool"])
    assert outcome(make(activity="swimming", target_pace_min_per_km=None,
                        equipment_used=["swimming pool"]), profile) == "accept"


def test_usable_activities_filters_by_equipment():
    profile = dict(PROFILE, preferred_activities=["running", "cycling", "swimming"], equipment=["bicycle"])
    assert logic_manager.usable_activities(profile) == ["running", "cycling"]
    assert logic_manager.missing_equipment_for("swimming", profile) == ["swimming pool"]


def test_old_profile_without_activities_is_unrestricted():
    profile = {k: v for k, v in PROFILE.items() if k != "preferred_activities"}
    assert logic_manager.usable_activities(profile) is None
    assert outcome(make(activity="swimming"), profile) == "accept"


def test_schema_requires_activity():
    data = {k: v for k, v in GOOD_AI.items() if k != "activity"}
    _, error = ai_manager.parse_and_validate_response(json.dumps(data))
    assert error and "activity" in error


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


def test_reset_data_deletes_files_and_empties_store():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "data.json")
        store = data_manager.empty_store()
        data_manager.save_profile(store, PROFILE, path)
        with open(path + ".corrupt-123", "w") as handle:
            handle.write("old backup")
        assert data_manager.reset_data(store, path) is True
        assert os.listdir(tmp) == []
    assert store["profile"] is None and store["sessions"] == [] and store["pending_session"] is None


def test_reset_data_with_no_file_still_succeeds():
    with tempfile.TemporaryDirectory() as tmp:
        store = data_manager.empty_store()
        store["profile"] = PROFILE
        assert data_manager.reset_data(store, os.path.join(tmp, "none.json")) is True
    assert store["profile"] is None


def test_setup_logging_survives_unwritable_log_path():
    import logging
    with tempfile.TemporaryDirectory() as tmp:
        blocker = os.path.join(tmp, "not_a_folder")
        with open(blocker, "w") as handle:
            handle.write("x")
        original = data_manager.LOG_FILE
        data_manager.LOG_FILE = os.path.join(blocker, "sub", "app.log")
        try:
            data_manager.setup_logging()   # must not raise
        finally:
            data_manager.LOG_FILE = original
            logging.disable(logging.NOTSET)


# ---------- new profile fields, equipment and the weekly plan ----------
def target(profile=PROFILE, done=0, history=()):
    return logic_manager.weekly_target(profile, done, list(history))


def test_weekly_pattern_follows_days_available():
    assert logic_manager.weekly_intensity_plan(1) == ("moderate",)
    assert logic_manager.weekly_intensity_plan(4) == ("high", "low", "moderate", "low")
    assert len(logic_manager.weekly_intensity_plan(7)) == 7


def test_weekly_target_walks_through_the_week():
    assert [target(done=n)["intensity"] for n in range(4)] == ["high", "low", "moderate", "low"]
    first = target(done=0)
    assert first["session_number"] == 1 and first["sessions_per_week"] == 4


def test_weekly_target_after_all_days_is_light_recovery():
    result = target(done=4)
    assert result["week_complete"] is True and result["intensity"] == "low"


def test_weekly_target_caps_beginner_at_moderate():
    assert target(dict(PROFILE, fitness_level="beginner"), done=0)["intensity"] == "moderate"


def test_weekly_target_caps_after_hard_session():
    assert target(history=[past(rpe=9)], done=0)["intensity"] == "moderate"


def test_weekly_target_caps_with_health_concerns():
    assert target(dict(PROFILE, health_concerns="asthma"), done=0)["intensity"] == "moderate"


def test_rejects_intensity_above_weekly_plan():
    plan = target(done=1)  # a low day
    result = logic_manager.evaluate_session(make(intensity="moderate"), PROFILE, [], plan)
    assert result["outcome"] == "reject"


def test_accepts_intensity_at_or_below_weekly_plan():
    plan = target(done=2)  # a moderate day
    assert logic_manager.evaluate_session(make(intensity="moderate"), PROFILE, [], plan)["outcome"] == "accept"
    assert logic_manager.evaluate_session(make(intensity="low"), PROFILE, [], plan)["outcome"] == "accept"


def test_rejects_duration_longer_than_time_available():
    assert outcome(make(duration_min=100)) == "reject"  # profile allows 90


def test_accepts_duration_equal_to_time_available():
    assert outcome(make(duration_min=90)) == "accept"


def test_rejects_equipment_the_user_does_not_have():
    assert outcome(make(equipment_used=["barbell"])) == "reject"


def test_accepts_owned_equipment_in_any_case():
    assert outcome(make(equipment_used=["Dumbbells", "bench"])) == "accept"


def test_bodyweight_is_always_allowed():
    profile = dict(PROFILE, equipment=[])
    assert outcome(make(equipment_used=["bodyweight"]), profile) == "accept"


def test_older_profile_without_new_fields_still_works():
    old_profile = {"age": 30, "height_cm": 175, "weight_kg": 70, "fitness_level": "intermediate",
                   "health_concerns": "none", "fitness_goal": "run 5k"}
    assert outcome(GOOD_AI, old_profile) == "accept"
    assert logic_manager.available_days(old_profile) == 3


def test_parse_rejects_missing_equipment_used():
    bad = make()
    del bad["equipment_used"]
    assert "equipment_used" in ai_manager.parse_and_validate_response(json.dumps(bad))[1]


def test_parse_rejects_non_string_equipment_items():
    assert ai_manager.parse_and_validate_response(json.dumps(make(equipment_used=[1, 2])))[1] is not None


def test_prompt_carries_profile_fields_and_weekly_target():
    plan = target(done=1)
    prompt = ai_manager.build_user_prompt(PROFILE, [], None, None, plan)
    assert "dumbbells" in prompt and "weekly_plan_target" in prompt and '"sex": "male"' in prompt


def test_sessions_this_week_counts_only_the_current_week():
    monday = datetime.date(2026, 10, 12)
    store = data_manager.empty_store()
    store["sessions"] = [
        {"id": 1, "date": "2026-10-11", "planned": {}, "actual": {}},   # Sunday, previous week
        {"id": 2, "date": "2026-10-12", "planned": {}, "actual": {}},   # Monday
        {"id": 3, "date": "2026-10-14", "planned": {}, "actual": {}},   # Wednesday
        {"id": 4, "date": "not a date", "planned": {}, "actual": {}},   # skipped
    ]
    assert data_manager.sessions_this_week(store, today=monday) == 2
    assert data_manager.sessions_this_week(store, today=datetime.date(2026, 10, 18)) == 2


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
