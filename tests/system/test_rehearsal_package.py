"""리허설 패키지(ADR 09) — 폴더 하나가 진실의 원천.

매니페스트 로드 → 시나리오 펼침 → 가상 카메라·층 생성 → 매핑 되쓰기까지,
사이트(data/sites/)를 한 글자도 안 건드리는 게 계약이다.
"""
import json
import subprocess
from pathlib import Path

import pytest

from system.vsource import package as vpkg
from system.vsource import scenario as vsc


@pytest.fixture()
def pkg_dir(tmp_path, monkeypatch):
    """임시 리허설 패키지 — 진짜 1초짜리 mp4 2개 (검증이 ffprobe를 돌린다)."""
    root = tmp_path / "media" / "vsource" / "test" / "rehearsal"
    (root / "scenario_01").mkdir(parents=True)
    for cam in ("cam1", "cam2"):
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
             "color=c=black:s=64x64:r=10:d=1",
             "-c:v", "libx264", "-profile:v", "baseline", "-pix_fmt", "yuv420p",
             str(root / "scenario_01" / f"{cam}.mp4")], check=True)
    manifest = {
        "schema": 1, "id": "test-rehearsal", "name": "테스트 리허설",
        "rtsp_prefix": "test",
        "floors": [{"id": "f10", "name": "10F"}],
        "cameras": [
            {"cam": "cam1", "path": "test_cam1", "floor": "f10",
             "analyze_fps": 5.0, "mapping": None},
            {"cam": "cam2", "path": "test_cam2", "floor": "f10",
             "analyze_fps": 5.0, "mapping": None},
        ],
        "scenarios": [{"id": "scenario_01", "name": "시나리오 01", "cycle_sec": 0,
                       "streams": [{"cam": "cam1", "file": "scenario_01/cam1.mp4"},
                                   {"cam": "cam2", "file": "scenario_01/cam2.mp4"}]}],
        "prep": {"ok": True, "fails": [], "warns": []},
    }
    (root / "rehearsal.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(vpkg, "PKG_ROOT", tmp_path / "media" / "vsource")
    vpkg._cache.clear()
    return root


class TestDiscover:
    def test_scans_site_set_layout(self, pkg_dir):
        """media/vsource/<site>/<set>/rehearsal.json 만 패키지로 잡는다."""
        ids = [p["id"] for p in vpkg.discover()]
        assert ids == ["test-rehearsal"]

    def test_scenario_id_namespace(self, pkg_dir):
        """pkg:<패키지>:<시나리오> — data/scenarios/ 와 절대 안 겹친다."""
        assert vpkg.scenario_ids(vpkg.get("test-rehearsal")) \
            == ["pkg:test-rehearsal:scenario_01"]
        assert vpkg.parse_scenario_id("pkg:test-rehearsal:scenario_01") \
            == ("test-rehearsal", "scenario_01")
        assert vpkg.parse_scenario_id("drill-16f") is None


class TestScenarioLoad:
    def test_load_pkg_scenario(self, pkg_dir):
        """패키지 시나리오가 기존 Scenario 로 펼쳐지고 검증을 통과한다 —
        vsource 컨트롤러는 패키지의 존재를 모른 채 그대로 동작해야 한다."""
        s = vsc.load("pkg:test-rehearsal:scenario_01", cameras=[])
        assert s.ok, s.problems
        assert [x.path for x in s.streams] == ["test_cam1", "test_cam2"]
        assert s.cycle_sec >= 1                     # 자동 산출(최장+여유)
        # cameras=[] 여도 패키지 가상 카메라 기준으로 매칭된다
        assert [x.cam_id for x in s.streams] == ["rh_cam1", "rh_cam2"]

    def test_unknown_pkg_scenario_raises(self, pkg_dir):
        with pytest.raises(FileNotFoundError):
            vsc.load("pkg:test-rehearsal:scenario_99")


class TestVirtual:
    def test_virtual_cameras_are_namespaced(self, pkg_dir):
        """rh_ 접두 — 사이트 cam01.. 발번과 충돌하지 않는 게 복원 안전성의 핵심.
        층은 **사이트 층 id 그대로**(빙의 모드) — ① 맵설정의 도면·구역을 빌려 쓴다."""
        cams = vpkg.virtual_cameras(vpkg.get("test-rehearsal"), rtsp_host="h:8554")
        assert [c.cam_id for c in cams] == ["rh_cam1", "rh_cam2"]
        assert cams[0].rtsp == "rtsp://h:8554/test_cam1"
        assert cams[0].floor_id == "f10"

    def test_bind_mode_makes_no_virtual_floor(self, pkg_dir):
        """도면 없는 floors[] 항목은 '사이트 층이 있어야 한다'는 선언일 뿐 —
        가상 층을 만들지 않는다 (만들면 사이트 층과 이중이 된다)."""
        assert vpkg.virtual_floors(vpkg.get("test-rehearsal")) == []

    def test_own_mode_with_image_makes_virtual_floor(self, pkg_dir):
        """패키지가 도면(image)까지 들고 있으면 자립 모드 — rh_ 가상 층 + 카메라도
        그 층으로. 두 모드는 floors[].image 유무 하나로 갈린다."""
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=white:s=80x40:d=1", "-frames:v", "1",
                        str(pkg_dir / "plan.png")], check=True)
        mf = pkg_dir / "rehearsal.json"
        d = json.loads(mf.read_text(encoding="utf-8"))
        d["floors"][0]["image"] = "plan.png"
        mf.write_text(json.dumps(d), encoding="utf-8")
        vpkg._cache.clear()
        pkg = vpkg.get("test-rehearsal")
        fls = vpkg.virtual_floors(pkg)
        assert [(f.id, f.map.w, f.map.h) for f in fls] == [("rh_f10", 80, 40)]
        assert vpkg.virtual_cameras(pkg)[0].floor_id == "rh_f10"


class TestSaveMapping:
    def test_mapping_written_to_manifest(self, pkg_dir):
        """UI가 찍은 매핑은 사이트가 아니라 **패키지 폴더**에 남는다(결정 ②) —
        폴더만 옮기면 매핑까지 재현되는 근거."""
        m = {"cctv_pts": [[0, 0], [1, 0], [1, 1], [0, 1]],
             "map_pts": [[0, 0], [2, 0], [2, 2], [0, 2]],
             "H": [2, 0, 0, 0, 2, 0, 0, 0, 1]}
        assert vpkg.save_camera("test-rehearsal", "rh_cam1",
                                {"mapping": m, "floor": "f10"})
        d = json.loads((pkg_dir / "rehearsal.json").read_text(encoding="utf-8"))
        cam1 = next(c for c in d["cameras"] if c["cam"] == "cam1")
        assert cam1["mapping"]["H"][0] == 2
        # 다시 로드하면 가상 카메라에 매핑이 실려 나온다
        cams = vpkg.virtual_cameras(vpkg.get("test-rehearsal"))
        assert cams[0].mapping is not None and cams[1].mapping is None

    def test_unknown_cam_returns_false(self, pkg_dir):
        assert not vpkg.save_camera("test-rehearsal", "rh_cam9", {"mapping": None})


class TestSummary:
    def test_summary_counts(self, pkg_dir):
        s = vpkg.summary(vpkg.get("test-rehearsal"))
        assert (s["cameras_total"], s["cameras_mapped"]) == (2, 0)
        assert s["prep_ok"] is True
        assert s["floors"][0] == {"id": "f10", "name": "10F", "has_map": False, "mode": "site"}
