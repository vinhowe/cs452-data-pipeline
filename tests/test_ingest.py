from datetime import datetime, timezone

from pipeline import usgs
from pipeline.ingest import classify
from seed.download_history import months


def row(id_, updated):
    return {"id": id_, "updated": updated, "time": "2026-11-12T00:00:00.000Z"}


def t(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_new_newer_and_unchanged():
    stored = {"a": t("2026-11-12T01:00:00"), "b": t("2026-11-12T01:00:00")}
    rows = [row("a", "2026-11-12T01:00:00.000Z"),   # same revision we have -> skip
            row("b", "2026-11-12T02:00:00.000Z"),   # newer revision -> update
            row("c", "2026-11-12T01:00:00.000Z")]   # never seen -> insert
    keep, ins, upd, same = classify(rows, stored)
    assert (ins, upd, same) == (1, 1, 1)
    assert sorted(r["id"] for r in keep) == ["b", "c"]


def test_older_revision_never_overwrites_newer():
    stored = {"a": t("2026-11-12T05:00:00")}
    keep, ins, upd, same = classify([row("a", "2026-11-12T01:00:00.000Z")], stored)
    assert keep == [] and same == 1


def test_duplicate_ids_within_one_fetch_keep_newest():
    rows = [row("a", "2026-11-12T01:00:00.000Z"), row("a", "2026-11-12T03:00:00.000Z")]
    keep, ins, upd, same = classify(rows, {})
    assert ins == 1 and keep[0]["updated"].startswith("2026-11-12T03")


def test_rerunning_the_same_batch_is_a_no_op():
    rows = [row("a", "2026-11-12T01:00:00.000Z"), row("b", "2026-11-12T01:00:00.000Z")]
    keep, *_ = classify(rows, {})
    stored = {r["id"]: usgs.parse_ts(r["updated"]) for r in keep}
    keep2, ins, upd, same = classify(rows, stored)
    assert keep2 == [] and (ins, upd, same) == (0, 0, 2)


def test_csv_round_trip_keeps_quoted_commas():
    text = 'time,id,place\n2026-11-12T00:00:00.000Z,x1,"4 km SSW of Cianjur, Indonesia"\n'
    rows = usgs.parse_csv(text)
    assert rows[0]["place"] == "4 km SSW of Cianjur, Indonesia"
    assert usgs.parse_csv(usgs.to_csv(rows)) == rows


def test_months_cover_range_without_gaps():
    ms = list(months(t("2023-11-15T00:00:00"), t("2024-02-01T00:00:00")))
    assert ms[0][0] == t("2023-11-15T00:00:00") and ms[-1][1] == t("2024-02-01T00:00:00")
    assert all(a[1] == b[0] for a, b in zip(ms, ms[1:]))
    assert len(ms) == 3
