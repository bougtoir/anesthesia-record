from datetime import datetime

import pytest

from anesthesia_record.events import (
    ClinicalEvent,
    DEFAULT_EVENT_ICONS,
    EventLog,
    STANDARD_EVENT_TYPES,
    load_event_log,
)


def test_add_and_sorted_orders_by_time():
    log = EventLog()
    t0 = datetime(2026, 6, 30, 9, 0, 0)
    t1 = datetime(2026, 6, 30, 9, 5, 0)
    t2 = datetime(2026, 6, 30, 9, 10, 0)
    log.add(t2, "incision")
    log.add(t0, "anesthesia_start")
    log.add(t1, "intubation")

    assert [ev.time for ev in log.sorted()] == [t0, t1, t2]


def test_display_label_falls_back_to_standard_then_raw():
    std = ClinicalEvent(datetime(2026, 6, 30, 9, 0, 0), "incision")
    raw = ClinicalEvent(datetime(2026, 6, 30, 9, 0, 0), "custom_event")

    assert std.display_label == STANDARD_EVENT_TYPES["incision"]
    assert std.display_icon == DEFAULT_EVENT_ICONS["incision"]
    assert raw.display_label == "custom_event"
    assert raw.display_icon == "#"


def test_save_and_load_round_trip(tmp_path):
    log = EventLog()
    log.add(datetime(2026, 6, 30, 9, 0, 0), "anesthesia_start")
    log.add(
        datetime(2026, 6, 30, 9, 15, 0),
        "custom_event",
        icon="★",
        label="独自ラベル",
        note="メモ",
    )

    path = tmp_path / "events.yaml"
    log.save(path)
    loaded = load_event_log(path)

    assert [ev.type for ev in loaded.sorted()] == [
        "anesthesia_start",
        "custom_event",
    ]
    assert loaded.sorted()[1].icon == "★"
    assert loaded.sorted()[1].label == "独自ラベル"
    assert loaded.sorted()[1].note == "メモ"


@pytest.mark.parametrize(
    "content",
    [
        "events:\n  - type: anesthesia_start\n",
        "events:\n  - time: 2026-06-30T09:00:00\n",
    ],
)
def test_load_raises_when_time_or_type_missing(tmp_path, content):
    path = tmp_path / "events.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError):
        load_event_log(path)
