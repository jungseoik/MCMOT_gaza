"""사이트/카메라 설정 영속화 — data/sites/<site_id>/ (요구사항 D-10).

레이아웃:
  data/sites/<site_id>/
    site.json            # SiteConfig (맵·경로·구역·병목·출입구·임계값)
    map.png              # 업로드된 맵 이미지 (site.json map.image가 가리킴)
    cameras/<cam_id>.json  # CameraConfig

원자적 쓰기(tmp→rename), 저장 시 site version 자동 증가.
git으로 diff/버전 추적 가능한 순수 JSON.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .schema import DEFAULT_FLOOR_ID, CameraConfig, SiteConfig

_JSON_KW = dict(ensure_ascii=False, indent=2)


class SiteStore:
    def __init__(self, root: str | Path = "data/sites"):
        self.root = Path(root)

    # ------------------------------------------------------------ 경로
    def site_dir(self, site_id: str) -> Path:
        return self.root / site_id

    def _site_json(self, site_id: str) -> Path:
        return self.site_dir(site_id) / "site.json"

    def _cam_json(self, site_id: str, cam_id: str) -> Path:
        return self.site_dir(site_id) / "cameras" / f"{cam_id}.json"

    def map_path(self, site_id: str, floor_id: str = DEFAULT_FLOOR_ID) -> Path:
        """층별 맵 이미지 경로 (v1.7). default 층은 기존 map.png 유지(하위호환),
        그 외 층은 map_<floor_id>.png."""
        name = ("map.png" if floor_id == DEFAULT_FLOOR_ID
                else f"map_{floor_id}.png")
        return self.site_dir(site_id) / name

    @staticmethod
    def _atomic_write(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, **_JSON_KW), encoding="utf-8")
        tmp.rename(path)

    # ------------------------------------------------------------ 부트스트랩
    def bootstrap_from_seed(self, site_id: str,
                            seed_root: str | Path = "data/seed") -> bool:
        """site.json이 없으면 seed에서 디폴트 세팅 복사 (클론 후 첫 기동용).

        seed는 git에 커밋되는 디폴트 UI 세팅(맵·카메라·매핑·요소) —
        tools/seed_snapshot.sh 로 라이브 세팅에서 갱신한다.
        이미 site.json이 있으면 아무것도 하지 않는다(운영 세팅 보호).
        """
        if self._site_json(site_id).is_file():
            return False
        seed = Path(seed_root) / site_id
        if not (seed / "site.json").is_file():
            return False
        shutil.copytree(seed, self.site_dir(site_id), dirs_exist_ok=True)
        return True

    def reset_from_seed(self, site_id: str,
                        seed_root: str | Path = "data/seed") -> bool:
        """현재 사이트를 seed(커밋된 디폴트)로 **강제 복원**한다 — 실험 후 원복용.

        bootstrap과 달리 이미 site.json이 있어도 덮어쓴다. 설정(site.json·맵·카메라·
        floor.json·distfield)만 seed 기준으로 교체하고, **세션 녹화본(sessions/)은 보존**한다.
        seed에 없는 추가 카메라·맵(층)은 제거된다(정확히 seed 상태로 수렴)."""
        seed = Path(seed_root) / site_id
        if not (seed / "site.json").is_file():
            return False
        site = self.site_dir(site_id)
        site.mkdir(parents=True, exist_ok=True)
        cam_dir = site / "cameras"          # 카메라 전면 교체(추가분 제거)
        if cam_dir.exists():
            shutil.rmtree(cam_dir)
        for p in site.glob("map*.png"):     # 기존 맵 png 제거(추가 층 맵 정리)
            p.unlink()
        for item in seed.iterdir():          # seed 복사 — sessions는 건드리지 않음
            if item.name == "sessions":
                continue
            dst = site / item.name
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)
        return True

    # ------------------------------------------------------------ 사이트
    def list_sites(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if (p / "site.json").is_file())

    def load_site(self, site_id: str) -> SiteConfig | None:
        p = self._site_json(site_id)
        if not p.is_file():
            return None
        return SiteConfig.model_validate_json(p.read_text(encoding="utf-8"))

    def save_site(self, cfg: SiteConfig, bump_version: bool = True) -> SiteConfig:
        if bump_version:
            prev = self.load_site(cfg.site_id)
            cfg.version = (prev.version if prev else 0) + 1
        self._atomic_write(self._site_json(cfg.site_id), cfg.model_dump())
        return cfg

    # ------------------------------------------------------------ 카메라
    def list_cameras(self, site_id: str) -> list[CameraConfig]:
        d = self.site_dir(site_id) / "cameras"
        if not d.is_dir():
            return []
        return [CameraConfig.model_validate_json(p.read_text(encoding="utf-8"))
                for p in sorted(d.glob("*.json"))]

    def load_camera(self, site_id: str, cam_id: str) -> CameraConfig | None:
        p = self._cam_json(site_id, cam_id)
        if not p.is_file():
            return None
        return CameraConfig.model_validate_json(p.read_text(encoding="utf-8"))

    def save_camera(self, site_id: str, cam: CameraConfig) -> CameraConfig:
        self._atomic_write(self._cam_json(site_id, cam.cam_id), cam.model_dump())
        return cam

    def delete_camera(self, site_id: str, cam_id: str) -> bool:
        p = self._cam_json(site_id, cam_id)
        if p.is_file():
            p.unlink()
            return True
        return False
