/* 앱 셸 — 상태(site/cameras/mapImg) 로드, 뷰 전환, 상단 바. */
"use strict";

const App = {
  site: null,        // SiteConfig (없으면 기본값)
  mapImg: null,      // 업로드된 맵 Image (배경 렌더용)
  cameras: [],       // [{...CameraConfig, state}]
  view: "map",

  defaultSite() {
    return { site_id: "mock", version: 0, map: null,
             routes: [], zones: [], bottlenecks: [], exits: [],
             thresholds: { v_th: 0.5, a_th: 0.7, r_th: 0.5, dt_hold: 3.0, d_allow: 2.0 } };
  },

  async reloadSite() {
    try { App.site = await API.getSite(); }
    catch (e) {
      if (e.status !== 404) console.warn("site 로드 실패:", e.message);
      App.site = App.defaultSite();
    }
    App.mapImg = null;
    if (App.site.map) {
      try {
        const img = new Image();
        await new Promise((res, rej) => {
          img.onload = res; img.onerror = rej; img.src = API.mapImageUrl();
        });
        App.mapImg = img;
      } catch (e) { console.warn("맵 이미지 로드 실패"); }
    }
    App.updateChip();
  },

  async reloadCameras() {
    try { App.cameras = await API.getCameras(); }
    catch (e) { console.warn("cameras 로드 실패:", e.message); App.cameras = []; }
  },

  updateChip() {
    const chip = document.getElementById("siteChip");
    chip.textContent = `site: ${App.site.site_id} · v${App.site.version}`;
  },

  switchView(v) {
    if (Views[App.view] && Views[App.view].leave) Views[App.view].leave();
    App.view = v;
    ["map", "cams", "live"].forEach((k) => {
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
  await Promise.all([App.reloadSite(), App.reloadCameras()]);
  App.switchView("map");
});
