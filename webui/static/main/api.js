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

  // ---- site
  getSite: () => API._j("/api/site"),
  putSite: (cfg) => API._put("/api/site", cfg),
  uploadMap(file, meta) {
    const fd = new FormData();
    fd.append("image", file);
    if (meta) fd.append("meta", JSON.stringify(meta));
    return API._j("/api/site/map", { method: "POST", body: fd });
  },
  mapImageUrl: () => "/api/site/map?ts=" + Date.now(),

  // ---- cameras
  getCameras: () => API._j("/api/cameras"),
  addCamera: (body) => API._post("/api/cameras", body),
  updateCamera: (id, patch) => API._put(`/api/cameras/${id}`, patch),
  deleteCamera: (id) => API._j(`/api/cameras/${id}`, { method: "DELETE" }),
  testCamera: (id) => API._post(`/api/cameras/${id}/test`),
  snapshotUrl: (id) => `/api/cameras/${id}/snapshot?ts=` + Date.now(),
  putMapping: (id, body) => API._put(`/api/cameras/${id}/mapping`, body),

  // ---- 평가 세션 (CONTRACT v1.2)
  startSession: (origin, { origins, t_alarm } = {}) => {
    const body = {};
    if (origins && origins.length) body.origins = origins;
    else if (origin) body.origin = origin;
    if (t_alarm != null) body.t_alarm = t_alarm;
    return API._post("/api/session/start", body);
  },
  stopSession: () => API._post("/api/session/stop"),
  getSession: () => API._j("/api/session"),
  getSessionResult: () => API._j("/api/session/result"),
  getSessionTimeline: () => API._j("/api/session/timeline"),
  getPersonSeries: () => API._j("/api/session/person_series"),
  exportUrl: (format) => `/api/session/export?format=${format || "json"}`,

  // ---- map state
  getMapState: () => API._j("/api/map/state"),
  streamUrl: () => "/api/map/stream",
  getStatus: () => API._j("/api/status"),
};
