/* 앱 셸 — 상태(site/floors/cameras/mapImg) 로드, 뷰 전환, 상단 바.
 * 다중 도면(v1.7): site.floors[]가 공간요소의 정본. App.site의 공간 필드는
 * 현재 층(App.currentFloor)의 필드를 별칭(alias)으로 가리킨다 → 기존 뷰 코드는
 * App.site.routes/zones/map... 을 그대로 읽고, 저장 시 syncFloor()로 floors에 반영. */
"use strict";

// 층에 속하는 공간요소 키 (thresholds/version/site_id는 사이트 공용 — 제외)
const FLOOR_KEYS = ["map", "routes", "zones", "bottlenecks", "exits",
                    "graph", "alarm_origins", "grid"];

const App = {
  site: null,          // 전체 SiteConfig (floors[] 포함) — 공간 필드는 현재 층 별칭
  floor: null,         // 현재 Floor 객체 (site.floors 안의 참조)
  floors: [],          // 층 요약 목록 [{id,name,has_map,map,camera_count}]
  currentFloor: "default",
  mapImg: null,        // 현재 층 맵 Image (배경 렌더용)
  cameras: [],         // [{...CameraConfig, state}]
  view: "map",

  defaultSite() {
    const floor = { id: "default", name: "기본", map: null,
                    routes: [], zones: [], bottlenecks: [], exits: [],
                    graph: { nodes: [], edges: [] }, alarm_origins: [], grid: {} };
    return { site_id: "mock", version: 0, floors: [floor],
             thresholds: { v_th: 0.5, a_th: 0.7, r_th: 0.5, dt_hold: 3.0, d_allow: 2.0 },
             ...floor };
  },

  // 현재 층의 공간 필드를 App.site 최상위에 별칭으로 노출 (기존 뷰 호환)
  applyFloor() {
    const s = App.site;
    if (!s || !s.floors || !s.floors.length) return;
    let fl = s.floors.find((f) => f.id === App.currentFloor);
    if (!fl) { fl = s.floors[0]; App.currentFloor = fl.id; }
    App.floor = fl;
    FLOOR_KEYS.forEach((k) => { s[k] = fl[k]; });
  },

  // 별칭된 최상위 공간 필드를 현재 층 객체로 되돌려 저장 (재할당된 필드 대비)
  syncFloor() {
    if (!App.floor || !App.site) return;
    FLOOR_KEYS.forEach((k) => { App.floor[k] = App.site[k]; });
  },

  async loadMapImage() {
    App.mapImg = null;
    if (App.site && App.site.map) {
      try {
        const img = new Image();
        await new Promise((res, rej) => {
          img.onload = res; img.onerror = rej; img.src = API.mapImageUrl(App.currentFloor);
        });
        App.mapImg = img;
      } catch (e) { console.warn("맵 이미지 로드 실패"); }
    }
  },

  async reloadSite() {
    try { App.site = await API.getSite(); }
    catch (e) {
      if (e.status !== 404) console.warn("site 로드 실패:", e.message);
      App.site = App.defaultSite();
    }
    if (!App.site.floors || !App.site.floors.length) App.site.floors = [App.defaultSite().floors[0]];
    // 층 요약 목록 (백엔드 우선, 실패 시 site.floors에서 파생)
    try { App.floors = await API.getFloors(); }
    catch (e) { App.floors = App.deriveFloors(); }
    // 현재 층 유효성 보정
    if (!App.site.floors.some((f) => f.id === App.currentFloor)) App.currentFloor = "default";
    App.applyFloor();
    await App.loadMapImage();
    App.updateChip();
    App.renderFloorSelector();
    App.updateExportLinks();
  },

  deriveFloors() {
    return (App.site.floors || []).map((f) => ({
      id: f.id, name: f.name, has_map: !!f.map, map: f.map || null,
      camera_count: App.cameras.filter((c) => (c.floor_id || "default") === f.id).length,
    }));
  },

  async reloadCameras() {
    try { App.cameras = await API.getCameras(); }
    catch (e) { console.warn("cameras 로드 실패:", e.message); App.cameras = []; }
  },

  // 층 전환 — 현재 편집 상태 저장(syncFloor) 후 층 교체 + 현재 뷰 재로드
  async setFloor(fid) {
    if (fid === App.currentFloor) return;
    App.syncFloor();
    App.currentFloor = fid;
    App.applyFloor();
    await App.loadMapImage();
    App.updateChip();
    App.renderFloorSelector();
    App.updateExportLinks();
    // 현재 뷰 재진입 (live: SSE 재구독 / cams·map: 재드로잉)
    const v = App.view;
    if (Views[v] && Views[v].leave) Views[v].leave();
    if (Views[v] && Views[v].enter) Views[v].enter();
  },

  floorName(fid) {
    const f = (App.floors || []).find((x) => x.id === fid)
          || (App.site.floors || []).find((x) => x.id === fid);
    return (f && (f.name || f.id)) || fid;
  },

  updateChip() {
    const chip = document.getElementById("siteChip");
    if (chip) chip.textContent = `site: ${App.site.site_id} · v${App.site.version}`;
  },

  // 상단 바 층 셀렉터 — 층 2개 이상일 때만 표시 (1개면 기존 UI와 동일하게 숨김)
  renderFloorSelector() {
    const wrap = document.getElementById("floorSelWrap");
    const sel = document.getElementById("floorSel");
    if (!wrap || !sel) return;
    const floors = App.site.floors || [];
    wrap.classList.toggle("hidden", floors.length <= 1);
    sel.innerHTML = floors.map((f) =>
      `<option value="${f.id}"${f.id === App.currentFloor ? " selected" : ""}>${f.name || f.id}</option>`
    ).join("");
  },

  // 세션 내보내기 링크(JSON/CSV)에 현재 층 반영 (session.js는 정적 href 미변경)
  updateExportLinks() {
    [["footJson", "json"], ["footCsv", "csv"], ["resJson", "json"], ["resCsv", "csv"]]
      .forEach(([id, fmt]) => {
        const el = document.getElementById(id);
        if (el) el.href = API.exportUrl(fmt, App.currentFloor);
      });
  },

  switchView(v) {
    if (Views[App.view] && Views[App.view].leave) Views[App.view].leave();
    App.view = v;
    ["map", "cams", "live", "replay"].forEach((k) => {
      document.getElementById("view" + k[0].toUpperCase() + k.slice(1))
        .classList.toggle("hidden", k !== v);
    });
    document.querySelectorAll(".vbtn").forEach((b) =>
      b.classList.toggle("on", b.dataset.view === v));
    Views[v].enter();
  },
};

window.addEventListener("DOMContentLoaded", async () => {
  setInterval(() => {
    document.getElementById("clock").textContent = new Date().toTimeString().slice(0, 8);
  }, 1000);
  document.querySelectorAll(".vbtn").forEach((b) =>
    b.onclick = () => App.switchView(b.dataset.view));
  // 결과/드릴 롤업 모달 닫기 — 전역 바인딩(④ 리플레이에서 ③ 운영뷰를 안 거쳐도 닫히게).
  // (Session.init에도 있지만 그 init은 운영뷰 진입 시에만 실행되므로 여기서 보장.)
  const resModal = document.getElementById("resultModal");
  const resClose = document.getElementById("resClose");
  if (resClose) resClose.onclick = () => resModal.classList.add("hidden");
  if (resModal) resModal.onclick = (e) => {
    if (e.target === resModal) resModal.classList.add("hidden");
  };
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && resModal && !resModal.classList.contains("hidden"))
      resModal.classList.add("hidden");
  });
  const sel = document.getElementById("floorSel");
  if (sel) sel.onchange = () => App.setFloor(sel.value);
  await Promise.all([App.reloadCameras()]);
  await App.reloadSite();   // cameras 먼저 로드해야 deriveFloors의 camera_count 정확
  App.switchView("map");
});
