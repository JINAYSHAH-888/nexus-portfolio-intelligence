"""
The nightly Upstox ISIN map refresh task.

Every test here fakes the network boundary (backend.integrations.upstox.
instruments_downloader.download_bytes) -- this sandbox has no route to
assets.upstox.com, and more importantly, the whole point of this task is its
BEHAVIOUR around good/bad data, which doesn't need a real network call to
verify.
"""
from __future__ import annotations

import gzip
import json

import pytest


def _gz(obj) -> bytes:
    return gzip.compress(json.dumps(obj).encode("utf-8"))


def _load_real_mf_instruments() -> list[dict]:
    """The actual uploaded Upstox file, when present -- gives every test a
    realistic instrument list to start from rather than a hand-rolled stub."""
    from pathlib import Path

    p = Path("/mnt/user-data/uploads/mf-instruments_json.gz")
    if not p.exists():
        pytest.skip("Real Upstox MF instrument file not present in this environment.")
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return json.load(f)


class TestDiffIsinMap:
    def test_instrument_key_change_is_flagged(self):
        from backend.tasks.market_tasks import _diff_isin_map

        old = {"nippon-large-cap-fund": {"instrument_key": "INF204K01562", "purchase_allowed": True}}
        new = {"nippon-large-cap-fund": {"instrument_key": "INF999XYZZZ", "purchase_allowed": True}}
        changes = _diff_isin_map(old, new)
        assert any("instrument_key changed" in c for c in changes)

    def test_purchase_allowed_flip_is_flagged_both_directions(self):
        from backend.tasks.market_tasks import _diff_isin_map

        old = {"s1": {"instrument_key": "K1", "purchase_allowed": True}}
        new = {"s1": {"instrument_key": "K1", "purchase_allowed": False}}
        assert any("purchase_allowed True -> False" in c for c in _diff_isin_map(old, new))

        assert any(
            "purchase_allowed False -> True" in c
            for c in _diff_isin_map(
                {"s1": {"instrument_key": "K1", "purchase_allowed": False}},
                {"s1": {"instrument_key": "K1", "purchase_allowed": True}},
            )
        )

    def test_no_change_is_silent(self):
        from backend.tasks.market_tasks import _diff_isin_map

        row = {"instrument_key": "K1", "purchase_allowed": True}
        assert _diff_isin_map({"s1": row}, {"s1": dict(row)}) == []

    def test_new_and_dropped_schemes_are_flagged(self):
        from backend.tasks.market_tasks import _diff_isin_map

        old = {"s1": {"instrument_key": "K1", "purchase_allowed": True}}
        new = {"s2": {"instrument_key": "K2", "purchase_allowed": True}}
        changes = _diff_isin_map(old, new)
        assert any("newly mapped" in c for c in changes)
        assert any("dropped" in c for c in changes)

    def test_first_run_with_no_prior_map_reports_everything_as_new(self):
        from backend.tasks.market_tasks import _diff_isin_map

        new = {"s1": {"instrument_key": "K1", "purchase_allowed": True}}
        changes = _diff_isin_map({}, new)
        assert len(changes) == 1
        assert "newly mapped" in changes[0]


class TestRefreshTaskHardFailure:
    def test_unresolved_schemes_leave_the_committed_map_untouched(self, monkeypatch, tmp_path):
        """
        THE CORE GUARANTEE. If even one scheme fails to resolve, the task must
        raise -- and the file on disk must be byte-for-byte unchanged. This is
        what stops a bad overnight sync from silently degrading (or corrupting)
        a previously-verified, NAV-corroborated map with nobody watching.
        """
        from backend.core.config import settings
        from backend.integrations.upstox import instruments_downloader
        from backend.tasks import market_tasks

        # A minimal, deliberately empty instrument list -> every scheme is
        # UNRESOLVED. Exactly the failure mode this task exists to catch.
        monkeypatch.setattr(instruments_downloader, "download_bytes", lambda url: _gz([]))

        original_map = json.dumps({"sentinel": "yesterday's known-good map"})
        fake_path = tmp_path / "upstox_isin_map.json"
        fake_path.write_text(original_map)
        monkeypatch.setattr(settings, "ISIN_MAP_PATH", fake_path)

        with pytest.raises(RuntimeError, match="failed to resolve"):
            market_tasks.refresh_upstox_isin_map.run()

        assert fake_path.read_text() == original_map, "the old map must be untouched on failure"

    def test_a_clean_run_writes_atomically_and_resets_the_cache(self, monkeypatch, tmp_path):
        from backend.core.config import settings
        from backend.integrations.upstox import instruments_downloader, mf_mapping
        from backend.tasks import market_tasks

        mf_instruments = _load_real_mf_instruments()
        monkeypatch.setattr(
            instruments_downloader, "download_bytes", lambda url: _gz(mf_instruments)
        )

        fake_path = tmp_path / "upstox_isin_map.json"
        monkeypatch.setattr(settings, "ISIN_MAP_PATH", fake_path)

        cache_reset = {"called": False}
        monkeypatch.setattr(
            mf_mapping, "reset_cache", lambda: cache_reset.__setitem__("called", True)
        )

        result = market_tasks.refresh_upstox_isin_map.run()

        assert fake_path.exists()
        written = json.loads(fake_path.read_text())
        assert len(written) == 35
        assert result["resolved"] == 35
        assert cache_reset["called"] is True

    def test_no_temp_file_is_left_behind_after_a_clean_run(self, monkeypatch, tmp_path):
        from backend.core.config import settings
        from backend.integrations.upstox import instruments_downloader, mf_mapping
        from backend.tasks import market_tasks

        mf_instruments = _load_real_mf_instruments()
        monkeypatch.setattr(
            instruments_downloader, "download_bytes", lambda url: _gz(mf_instruments)
        )
        fake_path = tmp_path / "upstox_isin_map.json"
        monkeypatch.setattr(settings, "ISIN_MAP_PATH", fake_path)
        monkeypatch.setattr(mf_mapping, "reset_cache", lambda: None)

        market_tasks.refresh_upstox_isin_map.run()

        leftovers = list(tmp_path.glob("isin_map_*.tmp"))
        assert leftovers == [], f"temp file(s) left behind: {leftovers}"

    def test_second_run_with_identical_data_reports_no_changes(self, monkeypatch, tmp_path):
        """A stable day -- no scheme changed -- must produce an empty changelog,
        not a spurious diff against itself."""
        from backend.core.config import settings
        from backend.integrations.upstox import instruments_downloader, mf_mapping
        from backend.tasks import market_tasks

        mf_instruments = _load_real_mf_instruments()
        monkeypatch.setattr(
            instruments_downloader, "download_bytes", lambda url: _gz(mf_instruments)
        )
        fake_path = tmp_path / "upstox_isin_map.json"
        monkeypatch.setattr(settings, "ISIN_MAP_PATH", fake_path)
        monkeypatch.setattr(mf_mapping, "reset_cache", lambda: None)

        market_tasks.refresh_upstox_isin_map.run()
        second = market_tasks.refresh_upstox_isin_map.run()

        assert second["changes"] == []

    def test_a_flipped_scheme_is_visible_in_the_changelog(self, monkeypatch, tmp_path):
        """
        The realistic case this whole system exists for: Small Cap reopens to
        new purchase overnight. The task must both update the map AND surface
        the flip, since that's exactly what notification_service.py (or a
        human reading the log) needs to act on.
        """
        from backend.core.config import settings
        from backend.integrations.upstox import instruments_downloader, mf_mapping
        from backend.tasks import market_tasks

        mf_instruments = _load_real_mf_instruments()
        monkeypatch.setattr(
            instruments_downloader, "download_bytes", lambda url: _gz(mf_instruments)
        )
        fake_path = tmp_path / "upstox_isin_map.json"
        monkeypatch.setattr(settings, "ISIN_MAP_PATH", fake_path)
        monkeypatch.setattr(mf_mapping, "reset_cache", lambda: None)

        market_tasks.refresh_upstox_isin_map.run()  # day 1: baseline

        # day 2: Small Cap's instrument rows all flip purchase_allowed -> True
        reopened = []
        for row in mf_instruments:
            row = dict(row)
            if "SMALL CAP" in row.get("name", "").upper() and "NIPPON" in row.get("name", "").upper():
                row["purchase_allowed"] = True
            reopened.append(row)
        monkeypatch.setattr(instruments_downloader, "download_bytes", lambda url: _gz(reopened))

        result = market_tasks.refresh_upstox_isin_map.run()
        assert any(
            "nippon-small-cap-fund" in c and "purchase_allowed" in c for c in result["changes"]
        )

    def test_corrupt_download_triggers_a_retry_not_a_silent_write(self, monkeypatch, tmp_path):
        from backend.core.config import settings
        from backend.integrations.upstox import instruments_downloader
        from backend.tasks import market_tasks

        monkeypatch.setattr(instruments_downloader, "download_bytes", lambda url: b"not gzip at all")
        fake_path = tmp_path / "upstox_isin_map.json"
        fake_path.write_text('{"sentinel": "unchanged"}')
        monkeypatch.setattr(settings, "ISIN_MAP_PATH", fake_path)

        # bind=True tasks raise self.retry(...) which itself raises a Celery
        # Retry exception when not run inside a real worker/broker context.
        with pytest.raises(Exception):  # noqa: B017 - Celery's Retry, not our exception
            market_tasks.refresh_upstox_isin_map.run()

        assert fake_path.read_text() == '{"sentinel": "unchanged"}'


async def test_manual_trigger_endpoint_queues_the_task(client, registered):
    r = await client.post(
        "/api/v1/market/tasks/refresh-upstox-isin-map", headers=registered["headers"]
    )
    assert r.status_code == 202
    body = r.json()
    assert body["task"] == "refresh_upstox_isin_map"
    assert body["task_id"]
