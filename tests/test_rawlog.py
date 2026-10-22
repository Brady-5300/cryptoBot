import json
from compression import zstd
from pathlib import Path

from basis.data.rawlog import RawWriter, compress_file, encode_line, iter_raw, wrap_rest

HOUR_NS = 3600 * 10**9
T0 = 1_792_670_400 * 10**9  # 2026-10-22T12:00:00Z


def test_encode_line_embeds_compact_json_verbatim() -> None:
    raw = '{"type":"ticker","msg":{"price":"8.1660","rate":0.00014027561878120001}}'
    line = encode_line(raw, 5, 6)
    assert line == f'{{"t":5,"m":6,"d":{raw}}}\n'
    assert json.loads(line)["d"]["msg"]["price"] == "8.1660"


def test_encode_line_multiline_and_non_json() -> None:
    assert json.loads(encode_line('{\n "a": 1\n}', 1, 2))["d"] == {"a": 1}
    assert json.loads(encode_line("heartbeat", 1, 2))["s"] == "heartbeat"
    assert json.loads(encode_line("{not json\n", 1, 2))["s"] == "{not json\n"


def test_wrap_rest_envelope() -> None:
    env = json.loads(wrap_rest("/x?a=1", 10, 200, '{"k":"v"}'))
    assert env == {"path": "/x?a=1", "sent_ns": 10, "status": 200, "body": {"k": "v"}}


def test_writer_rotates_hourly_and_queues_compression(tmp_path: Path) -> None:
    w = RawWriter(tmp_path, "kalshi", "ws")
    w.write('{"n":1}', T0, 1)
    w.write('{"n":2}', T0 + HOUR_NS - 1, 2)
    w.write('{"n":3}', T0 + HOUR_NS, 3)
    first = tmp_path / "raw/kalshi/ws/2026-10-22/12.jsonl"
    assert w.take_pending() == [first]
    w.close()
    assert [r["d"]["n"] for r in iter_raw(first)] == [1, 2]
    assert [r["d"]["n"] for r in iter_raw(first.with_name("13.jsonl"))] == [3]


def test_append_after_torn_line_keeps_records_separate(tmp_path: Path) -> None:
    w = RawWriter(tmp_path, "k", "s")
    path = w.path_for(("2026-10-22", "12"))
    path.parent.mkdir(parents=True)
    path.write_text('{"t":1,"m":1,"d":{"n":1}}\n{"t":2,"m":2,"d":{"n"')  # crash mid-line
    w.write('{"n":3}', T0, 3)
    w.shutdown()
    assert [r["d"]["n"] for r in iter_raw(path)] == [1, 3]
    assert w.take_pending() == []  # current hour is not compressed on shutdown


def test_compress_never_overwrites(tmp_path: Path) -> None:
    for i in range(2):
        p = tmp_path / "12.jsonl"
        p.write_text(f'{{"t":{i},"m":0,"d":{{}}}}\n')
        compress_file(p)
    assert sorted(x.name for x in tmp_path.iterdir()) == ["12-1.jsonl.zst", "12.jsonl.zst"]
    assert [r["t"] for r in iter_raw(tmp_path / "12-1.jsonl.zst")] == [1]


def test_iter_raw_tolerates_truncated_zst(tmp_path: Path) -> None:
    lines = "".join(f'{{"t":{i},"m":0,"d":{{}}}}\n' for i in range(2000))
    blob = zstd.compress(lines.encode())
    p = tmp_path / "x.jsonl.zst"
    p.write_bytes(blob[: len(blob) // 2])
    got = [r["t"] for r in iter_raw(p)]
    assert got == list(range(len(got)))  # a clean prefix, no exception


def test_stale_uncompressed_excludes_current_hour(tmp_path: Path) -> None:
    w = RawWriter(tmp_path, "k", "s")
    w.write("{}", T0, 0)
    w.write("{}", T0 + HOUR_NS, 0)
    w.take_pending()
    w.shutdown()
    assert [p.name for p in w.stale_uncompressed(T0 + HOUR_NS)] == ["12.jsonl"]
