from bedjet_nights.model import (
    MAX_STEP_MINUTES,
    ActiveRun,
    Mode,
    Phase,
    Schedule,
    describe_run,
    longest_minutes,
    night_phases,
    problem,
    templates,
)


def test_heat_is_cut_off_sooner_when_hotter_and_faster():
    # The same values Lull checks, from the heat table in the official BedJet app.
    assert longest_minutes(Mode.HEAT, 80, 19) == 60
    assert longest_minutes(Mode.HEAT, 75, 19) == 120
    assert longest_minutes(Mode.HEAT, 80, 0) == 240
    assert longest_minutes(Mode.HEAT, 81, 0) == 60
    assert longest_minutes(Mode.HEAT, 66, 19) == MAX_STEP_MINUTES
    assert longest_minutes(Mode.HEAT, 50, 19) == MAX_STEP_MINUTES
    assert longest_minutes(Mode.COOL, 80, 19) == MAX_STEP_MINUTES


def test_phase_limits_combine_mode_and_heat_table():
    assert Phase.make(Mode.TURBO, fan=100, minutes=10).max_minutes == 10
    assert Phase.make(Mode.HEAT, temperature=66, fan=40).max_minutes == 240
    assert Phase.make(Mode.HEAT, temperature=80, fan=100).max_minutes == 60
    assert Phase.make(Mode.COOL, fan=40).max_minutes == MAX_STEP_MINUTES


def test_phase_from_web_is_pulled_inside_the_bedjets_limits():
    phase = Phase.from_dict({"mode": "cool", "temperature": 200, "fan": 103, "minutes": 5000})
    assert phase.temperature == 62
    assert phase.fan == 100
    assert phase.minutes == MAX_STEP_MINUTES
    phase = Phase.from_dict({"mode": "heat", "temperature": 1, "fan": 2, "minutes": 0})
    assert (phase.temperature, phase.fan, phase.minutes) == (45, 5, 1)


def test_standby_is_not_a_phase_mode():
    try:
        Phase.from_dict({"mode": "standby"})
    except ValueError:
        return
    raise AssertionError("standby was accepted as a phase")


def test_schedule_round_trip_and_name_tidying():
    schedule = Schedule.from_dict({"name": "  Late   night  ", "bedtime": 1500, "phases": [{"mode": "dry", "minutes": 30}]})
    assert schedule.name == "Late night"
    assert schedule.bedtime == 60
    again = Schedule.from_dict(schedule.to_dict())
    assert again == schedule


def test_problems():
    assert problem(Schedule("Empty", [])) == "Add at least one phase first."
    too_hot = Schedule("Hot", [Phase(Mode.HEAT, 80, 100, 120)])
    assert "won't run a phase this long" in problem(too_hot)
    gentle = Schedule("Gentle", [Phase.make(Mode.EXTENDED_HEAT, minutes=60), Phase.make(Mode.DRY, minutes=30)])
    assert problem(gentle) is None
    message = problem(gentle, "v2", "Left side")
    assert message.startswith("Left side is a BedJet V2, which can't run Dry or Gentle heat.")


def test_templates_all_run_on_a_bedjet_3():
    for schedule in templates():
        assert problem(schedule) is None, schedule.name


def test_active_run_walks_through_phases():
    schedule = Schedule("Two", [Phase.make(Mode.COOL, minutes=30), Phase.make(Mode.HEAT, minutes=15)])
    run = ActiveRun(schedule, started_at=1000)
    assert run.index_at(1000) == 0
    assert run.index_at(1000 + 29 * 60) == 0
    assert run.index_at(1000 + 30 * 60) == 1
    assert run.index_at(1000 + 45 * 60) is None
    assert run.ends_at == 1000 + 45 * 60
    assert run.seconds_left_in_phase(1000 + 10 * 60) == 20 * 60
    assert describe_run(run, 1000 + 10 * 60) == "Two: Cooling, 20 min left (phase 1 of 2)"
    assert describe_run(None) == "Not running"


def test_night_is_redrawn_from_history():
    phases = [Phase.make(Mode.COOL, fan=40, minutes=60).to_dict(), Phase.make(Mode.HEAT, minutes=30).to_dict()]
    entries = [
        {"ts": 0, "kind": "schedule_started", "data": {"name": "Test", "phases": phases}},
        # A tweak 20 minutes in lasts until the schedule moves on to its next phase.
        {"ts": 20 * 60, "kind": "air", "data": {"mode": "cool", "temperature": 44, "fan": 80}},
        {"ts": 90 * 60, "kind": "schedule_ended", "data": {"name": "Test", "ending": "finished"}},
    ]
    drawn = night_phases(entries)
    assert [(p["mode"], p["minutes"]) for p in drawn] == [("cool", 20), ("cool", 40), ("heat", 30)]
    assert drawn[1]["fan"] == 80


def test_night_without_a_schedule():
    entries = [
        {"ts": 0, "kind": "air", "data": {"mode": "heat", "temperature": 70, "fan": 50}},
        {"ts": 45 * 60, "kind": "off", "data": {"timer_ran_out": True}},
        {"ts": 60 * 60, "kind": "air", "data": {"mode": "cool", "temperature": 44, "fan": 20}},
    ]
    drawn = night_phases(entries)
    assert [(p["mode"], p["minutes"]) for p in drawn] == [("heat", 45), ("wait", 15), ("cool", 30)]
