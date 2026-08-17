/* REST API 래퍼 — CONTRACT §4. 에러는 Error(detail)로 throw. */
"use strict";

const API = {
  async _j(url, opts) {
    let r;
    try { r = await fetch(url, opts); }
    catch (e) { throw new Error("서버에 연결할 수 없습니다"); }
    if (!r.ok) {
      let d = "";
      try { d = (await r.json()).detail || ""; } catch (e) { /* ignore */ }
      const err = new Error(d || `HTTP ${r.status}`);
      err.status = r.status;
      throw err;
    }
    return r.json();
  },
  _put(url, body) {
    return API._j(url, { method: "PUT",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  },
  _post(url, body) {
    return API._j(url, { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body) });
  },
  // 현재 층 id — 인자로 명시하지 않으면 App.currentFloor(없으면 default).
  _floor: (floor) =>
    floor || (typeof App !== "undefined" && App.currentFloor) || "default",
  _fq: (floor) => "floor=" + encodeURIComponent(API._floor(floor)),

  // ---- site
  getSite: () => API._j("/api/site"),
  putSite: (cfg) => API._put("/api/site", cfg),
  uploadMap(file, meta, floor) {
    const fd = new FormData();
    fd.append("image", file);
    if (meta) fd.append("meta", JSON.stringify(meta));
    return API._j("/api/site/map?" + API._fq(floor), { method: "POST", body: fd });
  },
  mapImageUrl: (floor) => "/api/site/map?ts=" + Date.now() + "&" + API._fq(floor),

  // ---- floors (다중 도면 v1.7)
  getFloors: () => API._j("/api/floors"),
  addFloor: (name, id) => API._post("/api/floors",
    { ...(id ? { id } : {}), name: name || "" }),
  deleteFloor: (id) => API._j(`/api/floors/${id}`, { method: "DELETE" }),

  // ---- cameras
  getCameras: () => API._j("/api/cameras"),
  addCamera: (body) => API._post("/api/cameras", body),
  // 여러 대 일괄 등록 — body {cameras:[{rtsp,name?,analyze_fps?,floor_id?}, ...]}.
  // 한 대씩 addCamera를 반복하면 deepstream 워커가 매번 재시작돼 같은 GPU의
  // 기존 채널이 전부 ~8s 끊긴다. 벌크는 그 비용을 1회로 묶는다.
  addCameras: (body) => API._post("/api/cameras/bulk", body),
  // 등록 전 RTSP 연결 검사 — {rtsp} → {ok,width,height}. 저장을 남기지 않는다.
  probeRtsp: (body) => API._post("/api/cameras/probe", body),
  updateCamera: (id, patch) => API._put(`/api/cameras/${id}`, patch),
  // 여러 대 설정 일괄 변경 — body {cameras:[{cam_id, ...바꿀필드}, ...]}.
  // 비활성→활성은 신규 추가와 같은 경로라 하나씩 하면 매번 워커가 재시작된다.
  updateCameras: (body) => API._put("/api/cameras/bulk", body),
  deleteCamera: (id) => API._j(`/api/cameras/${id}`, { method: "DELETE" }),
  testCamera: (id) => API._post(`/api/cameras/${id}/test`),
  snapshotUrl: (id) => `/api/cameras/${id}/snapshot?ts=` + Date.now(),
  putMapping: (id, body) => API._put(`/api/cameras/${id}/mapping`, body),

  // ---- 평가 세션 (CONTRACT v1.2) — 세션은 층별. floor 미지정 시 현재 층.
  startSession: (origin, { origins, t_alarm } = {}, floor) => {
    const body = {};
    if (origins && origins.length) body.origins = origins;
    else if (origin) body.origin = origin;
    if (t_alarm != null) body.t_alarm = t_alarm;
    return API._post("/api/session/start?" + API._fq(floor), body);
  },
  stopSession: (floor) => API._post("/api/session/stop?" + API._fq(floor)),
  getSession: (floor) => API._j("/api/session?" + API._fq(floor)),
  getSessionResult: (floor) => API._j("/api/session/result?" + API._fq(floor)),
  getSessionTimeline: (floor) => API._j("/api/session/timeline?" + API._fq(floor)),
  getPersonSeries: (floor) => API._j("/api/session/person_series?" + API._fq(floor)),
  exportUrl: (format, floor) =>
    `/api/session/export?format=${format || "json"}&` + API._fq(floor),

  // ---- 세션 이력·리플레이/재계산 (CONTRACT v1.10)
  getSessions: (floor) => API._j("/api/sessions?" + API._fq(floor)),
  getSavedSession: (id, floor) => API._j(`/api/sessions/${id}?` + API._fq(floor)),
  replaySession: (id, body, floor) =>
    API._post(`/api/session/${encodeURIComponent(id)}/replay?` + API._fq(floor), body || {}),

  // ---- map state (층별)
  getMapState: (floor) => API._j("/api/map/state?" + API._fq(floor)),
  streamUrl: (floor) => "/api/map/stream?" + API._fq(floor),
  getStatus: () => API._j("/api/status"),
};
