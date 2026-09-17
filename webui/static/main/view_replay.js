/* 화면 4: 리플레이 — 저장된 경보 세션을 2D 도면 위에 그대로 재생하고,
 * 임계값(4대 지표 세팅)을 바꿔 재계산한다 (CONTRACT v1.10).
 * 영상이 아니라 "분석 결과(사람 위치·궤적·지표)"를 재생한다. 도면은 그대로. */
"use strict";

var Views = window.Views || (window.Views = {});

Views.replay = (() => {
  const $ = (id) => document.getElementById(id);
  let inited = false, active = false;
  let mc = null;

  let bnPanel = null;       // CBS 병목 선택 집계 패널 (session.js CbsBnPanel — ③과 동일 UI)
  let sessions = [];        // 이력 목록 [{session_id, sei, epfi_avg, cbs_total, has_record}]
  let selId = null;         // 선택된 session_id
  let baseRow = null;       // 선택 세션의 원본 요약(비교용)
  let data = null;          // {result, timeline, frames, site, meta}
  let drillTimelines = {};  // 층별 1초 타임라인 — 재생 커서 시점 지표용
  let liveAtCursor = true;  // 재생 시점값으로 지표를 따라가게 할지 (끄면 최종값 고정)
  let site = null;          // 세션 당시 공간요소 (배경 렌더)

  // 건물 드릴 모드(Phase 2·3) — 전 층 공유 세션 이력·재계산
  let mode = "drill";       // "drill"(건물 훈련, 기본 — 리허설도 건물 세션) | "sess"(개별 층, 디버그)
  let modeAuto = false;     // 최초 진입 시 사이트에 맞는 기본 모드 1회 자동 설정
  let drills = [];          // 드릴 이력 [{session_id, alarm_ts, floors, epfi_avg, ..., has_record}]
  let drill = null;         // 선택 드릴의 재산출 DrillResult
  let drillFrames = {};     // {floor: frames[]} — 층별 2D 재생 프레임
  let drillSites = {};      // {floor: site_view} — 층별 배경 공간요소
  let curDrillFloor = null; // 현재 재생 중인 층
  let objSortKey = "epfi";   // epfi | dev | dur
  let objRows = [];          // 표시 중인 person_metrics (트랙렛 단위)
  let objSel = null;         // 선택된 객체 id (맵 하이라이트용)

  // 재생 상태
  let playing = false;
  let cursor = 0;           // 재생 위치(세션 초, frames[0].ts 기준 0)
  let duration = 0;         // 전체 길이(초)
  let speed = 1;
  let lastRaf = 0, rafId = null;
  const renderFps = () => Math.min(30, Math.max(1, parseInt($("rpFps").value) || 20));

  const TH_KEYS = [["rpV","v_th"],["rpA","a_th"],["rpR","r_th"],["rpDt","dt_hold"],
                   ["rpD","d_allow"],["rpQd","q_design"],["rpMc","min_conf"],
                   ["rpMbh","min_box_h"]];

  // 재계산 전 원본 구역지표 — 전/후 비교용 (zone_id -> {idr, delay, ratio})
  let idrBase = null;

  // ------------------------------------------------------------ 세션 목록
  async function loadList() {
    $("rpConn").textContent = "불러오는 중…";
    if (mode === "drill") {
      try { drills = await API.getDrills(); } catch (e) { drills = []; }
      renderDrillList();
      $("rpConn").textContent = `${drills.length}건`;
      return;
    }
    try {
      sessions = await API.getSessions();
      // 훈련(건물)에 속한 층별 세션은 '개별 층' 목록에서 제외 — 같은 이벤트 중복 노출 방지.
      // (진짜 단독 단일-층 세션만 남긴다.)
      try {
        const dids = new Set((await API.getDrills()).map((d) => d.session_id));
        sessions = sessions.filter((s) => !dids.has(s.session_id));
      } catch (e) { /* 훈련 조회 실패 시 원본 유지 */ }
    } catch (e) { sessions = []; }
    renderList();
    $("rpConn").textContent = `${sessions.length}건`;
  }

  function fmtVal(v, d) { return (v == null) ? "—" : (+v).toFixed(d); }

  function renderList() {
    const box = $("rpSessList");
    if (!sessions.length) {
      box.innerHTML = `<div class="grow">저장된 세션 없음 — ③ 운영 뷰에서 경보 세션을 실행하면 이력이 쌓입니다.</div>`;
      return;
    }
    box.innerHTML = sessions.map((s) => {
      const t = s.alarm_ts ? new Date(s.alarm_ts * 1000).toLocaleString("ko-KR", {hour12:false}) : s.session_id;
      const rec = s.has_record
        ? `<span class="badge ok">재생가능</span>`
        : `<span class="badge" title="녹화 이전 세션 — 지표 요약만 조회 가능">요약만</span>`;
      return `<div class="camrow rpsess${s.session_id === selId ? " sel" : ""}" data-id="${s.session_id}">
        <div class="r1"><span class="nm">${t}</span>${rec}</div>
        <div class="r2"><span>SEI ${fmtVal(s.sei,0)}</span>
          <span>EPFI ${fmtVal(s.epfi_avg,0)}</span>
          <span>CBS ${fmtVal(s.cbs_total,1)}</span></div>
        <button class="rpdel" title="이 세션 이력 삭제" aria-label="삭제">🗑</button></div>`;
    }).join("");
    wireDelete(box, async (id) => {
      if (!confirm(`세션 ${id}\n\n이 세션 이력을 삭제할까요?\n`
        + `녹화(.db)와 결과가 함께 지워지며 되돌릴 수 없습니다.`)) return;
      await API.deleteSession(id, curFloor());
      if (selId === id) { selId = null; clearMetrics(); setControlsEnabled(false); }
      await loadList();
    });
    box.querySelectorAll(".rpsess").forEach((el) => {
      el.onclick = () => selectSession(el.dataset.id);
    });
  }

  /** 목록 행의 🗑 버튼 배선 — 행 선택과 섞이지 않게 전파를 끊는다. */
  function wireDelete(box, onDelete) {
    box.querySelectorAll(".rpdel").forEach((btn) => {
      btn.onclick = async (ev) => {
        ev.stopPropagation();
        const id = btn.closest(".rpsess").dataset.id;
        btn.disabled = true;
        try { await onDelete(id); }
        catch (e) { alert("삭제 실패: " + (e.message || e)); btn.disabled = false; }
      };
    });
  }

  // ------------------------------------------------------------ 건물 드릴 모드
  function renderDrillList() {
    const box = $("rpSessList");
    if (!drills.length) {
      box.innerHTML = `<div class="grow">저장된 건물 훈련 없음 — ③ 운영 뷰에서 [🔔 건물 전체 경보]를 실행하면 이력이 쌓입니다.</div>`;
      return;
    }
    box.innerHTML = drills.map((d) => {
      const dt = d.alarm_ts ? new Date(d.alarm_ts * 1000) : null;
      const p2 = (n) => String(n).padStart(2, "0");
      // 좁은 사이드바(280px) — 시각은 "08/28 04:05" 로, 전체 시각은 title 로
      const t = dt ? `${p2(dt.getMonth() + 1)}/${p2(dt.getDate())} ${p2(dt.getHours())}:${p2(dt.getMinutes())}` : d.session_id;
      const tFull = dt ? dt.toLocaleString("ko-KR", {hour12:false}) : d.session_id;
      const rec = d.has_record
        ? `<span class="badge ok">재계산가능</span>`
        : `<span class="badge" title="일부 층 녹화 없음 — 재계산·재생 불가">요약만</span>`;
      const gidb = d.global_id
        ? `<span class="badge" title="글로벌 ID(카메라 간 동일인 연결)로 측정된 훈련 — 결과에 개인 이동 기록 포함">🌐</span>` : "";
      // 라벨(🎬 패키지 — 시나리오)은 행 제목으로 — 배지에 넣으면 길어서 행이 두세 줄로 깨진다.
      // 패키지명은 title 로 내리고 시나리오 부분만 보인다("🎬 전체 (01~14 연속)").
      let title = tFull;
      if (d.label) {
        const parts = String(d.label).split(" — ");
        title = parts.length > 1 ? `${parts[0].split(" ")[0]} ${parts.slice(1).join(" — ")}` : d.label;
      }
      const floors = (d.floors || []).map((f) => floorName(f)).join("·");
      return `<div class="camrow rpsess${d.session_id === selId ? " sel" : ""}" data-id="${d.session_id}">
        <div class="r1"><span class="nm" title="${(d.label || "") + " · " + d.session_id}">${title}</span>${gidb}${rec}</div>
        <div class="r2"><span class="cid" title="${tFull}">${t}</span><span>${floors}</span>
          <span class="mtr">EPFI ${fmtVal(d.epfi_avg,0)} · CBS ${fmtVal(d.cbs_total,1)} · 통과 ${d.total_passed || 0}</span></div>
        <button class="rpdel" title="이 훈련 이력 삭제" aria-label="삭제">🗑</button></div>`;
    }).join("");
    wireDelete(box, async (id) => {
      const d = drills.find((x) => x.session_id === id);
      const what = (d && d.label) ? `"${d.label}"` : id;
      if (!confirm(`${what}\n\n이 건물 훈련 이력을 삭제할까요?\n`
        + `녹화(.db)와 결과가 함께 지워지며 되돌릴 수 없습니다.`)) return;
      await API.deleteDrill(id);
      if (selId === id) { selId = null; clearMetrics(); setControlsEnabled(false); }
      await loadList();
    });
    box.querySelectorAll(".rpsess").forEach((el) => {
      el.onclick = () => {
        const d = drills.find((x) => x.session_id === el.dataset.id);
        if (d && !d.has_record) {
          selId = el.dataset.id; renderDrillList(); clearMetrics();
          setControlsEnabled(false); $("rpReset").disabled = true; $("rpApply").disabled = true;
          $("rpReport").disabled = false;
          $("rpHint").textContent = "이 훈련은 일부 층 녹화가 없어 재계산·재생이 불가합니다 (요약만).";
          drill = null;
          return;
        }
        selectDrill(el.dataset.id);
      };
    });
  }

  const floorName = (f) => (typeof App !== "undefined" ? App.floorName(f) : f);
  const floorResultOf = (f) => {
    const pf = (drill && drill.per_floor || []).find((p) => p.floor_id === f);
    return pf ? pf.result : null;
  };

  async function selectDrill(id) {
    if (playing) pause();
    selId = id;
    baseRow = drills.find((d) => d.session_id === id) || null;
    renderDrillList();
    $("rpHint").textContent = "건물 훈련 재계산·재생 데이터를 불러오는 중…";
    $("rpMsg").textContent = "";
    let resp;
    try { resp = await API.drillReplay(id, { fps: 5 }); }
    catch (e) { $("rpHint").textContent = "훈련 로드 실패: " + e.message; return; }
    drill = resp.drill;
    drillFrames = resp.frames_by_floor || {};
    drillSites = resp.site_by_floor || {};
    drillTimelines = resp.timeline_by_floor || {};
    const floors = drill.floors || [];
    $("rpFloorSel").innerHTML = floors.map((f) =>
      `<option value="${f}">${floorName(f)}</option>`).join("");
    $("rpFloorSel").disabled = false;
    showBuildingMetrics(drill, "원본값");
    const st0 = drillSites[floors[0]];
    fillThresholds(st0 && st0.thresholds);
    setControlsEnabled(true);
    $("rpReset").disabled = false; $("rpApply").disabled = false; $("rpReport").disabled = false;
    // 관측이 **있는** 층을 먼저 연다. floors[0] 은 사이트 층 순서라 카메라가 없는
    // 층(예: 리허설 빙의 때의 default)이 걸리면 빈 화면부터 보게 된다.
    const first = floors.slice().sort(
      (a, b) => (drillFrames[b] || []).length - (drillFrames[a] || []).length)[0];
    if (floors.length) loadDrillFloor(first || floors[0]);
    $("rpHint").textContent = `건물 훈련 · 참여 ${floors.length}개 층 — 층을 골라 2D 재생, 임계값을 바꿔 [재계산]하면 건물 지표가 갱신됩니다.`;
  }

  // 선택 층의 프레임을 기존 재생 파이프라인(data/site)에 실어 그대로 재생.
  // seekAbsTs(선택): 이 절대 시각(초)으로 맞춘다 — 층 전환 시 같은 순간 유지용.
  // (드릴은 전 층 t_alarm 공유라 절대 ts로 맞추면 다른 층의 '같은 시점'이 보인다.)
  function loadDrillFloor(floor, seekAbsTs) {
    curDrillFloor = floor;
    $("rpFloorSel").value = floor;
    if (drill) {
      const pf0 = (drill.per_floor || []).find((x) => x.floor_id === floor);
      renderObjTbl((pf0 && pf0.result && pf0.result.person_metrics) || []);
    }
    const st = drillSites[floor] || null;
    site = st;
    data = { frames: drillFrames[floor] || [], site: st, result: floorResultOf(floor),
             timeline: drillTimelines[floor] || [],
             meta: { alarm_origins: (st && st.alarm_origins) || [] } };
    prepPlayback();
    setDrillCanvasImage(floor, st);
    const f = data.frames;
    if (seekAbsTs != null && f.length) {
      cursor = Math.max(0, Math.min(duration, seekAbsTs - f[0].ts));
      $("rpSeek").value = String(frameIndexAt(cursor));
      updateTimeLabel();
      showMetricsAtCursor();
    } else {
      goTo(0);
    }
    renderRpBn();                       // 층 전환·재계산 후 그 층 병목 기준으로 갱신
    if (mc) mc.render();
  }

  /** 재생 바의 층 셀렉터 채우기.
   *  drill: 그 훈련에 참여한 층만 (selectDrill 에서 따로 채운다)
   *  sess : 사이트 층 전체 — 세션 목록이 층별이라 여기서 고른다.
   *  상단바 전역 셀렉터는 이 탭에서 숨긴다(중복 + 바꾸면 뷰가 재진입돼 보던 게 날아간다). */
  function fillFloorSel() {
    if (mode === "drill") {
      // 훈련을 고르기 전에는 참여 층을 알 수 없다 — 빈 셀렉터 대신 안내를 둔다.
      if (!drill) {
        $("rpFloorSel").innerHTML = `<option value="">(훈련을 선택하세요)</option>`;
        $("rpFloorSel").disabled = true;
      }
      return;                                      // 고른 뒤는 selectDrill 이 채운다
    }
    $("rpFloorSel").disabled = false;
    const fs = (App.floors && App.floors.length) ? App.floors : ((App.site && App.site.floors) || []);
    const cur = (typeof API !== "undefined") ? API._floor() : "";
    $("rpFloorSel").innerHTML = App.floorOptions ? App.floorOptions(fs, cur)
      : fs.map((f) => `<option value="${f.id}"${f.id === cur ? " selected" : ""}>${f.name || f.id}</option>`).join("");
  }

  function setDrillCanvasImage(floor, st) {
    if (!mc || !st || !st.map) { if (mc) mc.render(); return; }
    const img = new Image();
    img.onload = () => { mc.setImage(img, st.map.w, st.map.h); mc.render(); };
    img.onerror = () => { mc.setImage(null, st.map.w, st.map.h); mc.render(); };
    img.src = API.mapImageUrl(floor);
  }

  function showBuildingMetrics(dr, tag) {
    const b = dr.building || {};
    $("rpTag").textContent = tag || "건물값";
    $("rpSei").textContent = fmtVal(b.sei, 1);
    $("rpEpfi").textContent = fmtVal(b.epfi_avg, 1);
    $("rpCbs").textContent = fmtVal(b.cbs_total, 1);
    let zSt = 0, zTot = 0;
    const allZ = [];
    Object.entries(b.idr_by_floor || {}).forEach(([fid, zs]) =>
      (zs || []).forEach((z) => {
        zTot++; if (z.status === "started") zSt++;
        allZ.push({ ...z, floor_id: fid });
      }));
    // 카드는 **평균 IDR(m/s)** — 지금까지 개시 구역 수만 보여 값 변화를 못 봤다
    const vs = allZ.map((z) => z.idr).filter((v) => v != null);
    $("rpIdr").textContent = vs.length
      ? (vs.reduce((s, v) => s + v, 0) / vs.length).toFixed(2) : "—";
    $("rpIdrProg").textContent = `${zSt}/${zTot}`;
    renderIdrTbl(allZ, dr.alarm_ts, tag);
    // 객체별 지표는 층 단위 — 드릴이면 지금 보고 있는 층 것. 층이 정해지기 전에
    // 한 번 도므로 loadDrillFloor 에서 그 층 것으로 다시 그린다.
    const pf = (dr.per_floor || []).find((x) => x.floor_id === curDrillFloor)
            || (dr.per_floor || [])[0];
    renderObjTbl((pf && pf.result && pf.result.person_metrics) || []);
    $("rpBase").innerHTML = (dr.per_floor || []).map((pf) => {
      const r = pf.result || {};
      return `<div class="rpbase-row"><b>${floorName(pf.floor_id)}</b> · SEI ${fmtVal(r.sei,0)} · EPFI ${fmtVal(r.epfi_avg,0)} · CBS ${fmtVal(r.cbs_total,1)}</div>`;
    }).join("");
  }

  /** 객체별 지표 표 — 재계산된 person_metrics(EPFI·이탈거리·배정경로·지속).
   *  트랙렛(카메라별 조각) 단위다. 사람 단위로 묶은 값·썸네일은 리포트의
   *  [ID 재구성] 탭에 있다 — 재생 화면 옆에 늘어놓으면 조잡해서 옮겼다. */
  function renderObjTbl(pms) {
    const wrap = $("rpObjTbl");
    if (!wrap) return;
    objRows = pms || [];
    $("rpObjCnt").textContent = objRows.length;
    if (!objRows.length) {
      wrap.innerHTML = `<div class="mnote">객체 지표 없음</div>`;
      return;
    }
    const key = { epfi: (o) => o.epfi == null ? -1 : o.epfi,
                  dev:  (o) => o.mean_deviation_m == null ? -1 : o.mean_deviation_m,
                  dur:  (o) => o.duration_sec == null ? -1 : o.duration_sec }[objSortKey];
    // EPFI 는 낮을수록 나쁨 → 오름차순(문제 객체 먼저), 나머지는 큰 값 먼저
    const rows = [...objRows].sort((a, b) =>
      objSortKey === "epfi" ? key(a) - key(b) : key(b) - key(a));
    const f = (v, d) => v == null ? "—" : v.toFixed(d);
    wrap.innerHTML = rows.map((o) => {
      const id = o.global_track_id || "—";
      const bad = o.epfi != null && o.epfi < 60;
      return `<div class="rpobj-row${id === objSel ? " sel" : ""}" data-oid="${id}"
           title="${id} · 경로 ${o.assigned_route_id || "—"} · 최대이탈 ${f(o.max_deviation_m, 2)}m">
        <span class="oid">${id.replace("rh_", "")}</span>
        <span class="t-num"${bad ? ' style="color:#e5484d"' : ""}>${f(o.epfi, 0)}</span>
        <span class="t-num">${f(o.mean_deviation_m, 1)}</span>
        <span class="t-num">${f(o.duration_sec, 1)}</span>
        <span class="ort">${(o.assigned_route_id || "—").replace("auto-evac-", "ae")}</span>
      </div>`;
    }).join("");
    wrap.querySelectorAll(".rpobj-row").forEach((el) => {
      el.onclick = () => {
        objSel = objSel === el.dataset.oid ? null : el.dataset.oid;
        renderObjTbl(objRows);
        if (mc) mc.render();
      };
    });
  }

  /** IDR 구역별 표 — 값(m/s)·개시지연·참여비율. 재계산이면 원본 대비 변화를 함께 보여준다.
   *  임계값 조정(v_th·a_th·r_th·dt_hold)은 전부 IDR 판정용인데, 지금까지 이 화면엔
   *  개시 구역 수만 있어 "값이 어떻게 바뀌었나"를 볼 수 없었다. */
  function renderIdrTbl(zs, alarmTs, tag) {
    const wrap = $("rpIdrTbl");
    if (!wrap) return;
    const isRecalc = tag === "재계산값";
    if (!isRecalc) idrBase = Object.fromEntries(zs.map((z) => [z.zone_id, z]));
    $("rpIdrTag").textContent = isRecalc ? "원본 대비 변화" : "원본 저장값";
    if (!zs.length) { wrap.innerHTML = `<div class="mnote">구역 없음 — ① 맵 설정에서 추가</div>`; return; }
    const f2 = (v) => v == null ? "—" : v.toFixed(2);
    const f1 = (v) => v == null ? "—" : v.toFixed(1);
    const delta = (now, was, d) => {
      if (!isRecalc || was == null && now == null) return "";
      if (was == null && now != null) return `<span class="rpd up">신규</span>`;
      if (was != null && now == null) return `<span class="rpd dn">소실</span>`;
      const diff = now - was;
      if (Math.abs(diff) < 1e-9) return "";
      return `<span class="rpd ${diff > 0 ? "up" : "dn"}">${diff > 0 ? "▲" : "▼"}${Math.abs(diff).toFixed(d)}</span>`;
    };
    wrap.innerHTML = `<div class="rpidr-hd"><span>구역</span><span>IDR m/s</span><span>개시</span><span>참여</span></div>`
      + zs.map((z) => {
      const b = (idrBase && idrBase[z.zone_id]) || {};
      const det = z.status === "started";
      const rel = (z.evacuation_start_at != null && alarmTs != null)
        ? z.evacuation_start_at - alarmTs : z.response_delay_sec;
      const relB = (b.evacuation_start_at != null && alarmTs != null)
        ? b.evacuation_start_at - alarmTs : b.response_delay_sec;
      return `<div class="rpidr-row ${det ? "det" : ""}" title="${z.floor_id} · ${z.zone_id}${
          z.graph_distance != null ? ` · 경보원까지 ${z.graph_distance.toFixed(1)}m` : ""}">
        <span class="rpz">${z.zone_id}</span>
        <span class="t-num">${f2(z.idr)}${delta(z.idr, b.idr, 2)}</span>
        <span class="t-num">${rel == null ? "—" : f1(rel) + "s"}${delta(rel, relB, 1)}</span>
        <span class="t-num">${z.participant_ratio != null ? Math.round(z.participant_ratio * 100) + "%" : "—"}</span>
      </div>`;
    }).join("");
  }

  function clearMetrics() {
    ["rpSei","rpEpfi","rpCbs","rpIdr"].forEach((id) => { $(id).textContent = "—"; });
    $("rpBase").innerHTML = "";
    if ($("rpObjTbl")) $("rpObjTbl").innerHTML = "";
    if ($("rpObjCnt")) $("rpObjCnt").textContent = "0";
    objRows = []; objSel = null;
    if ($("rpIdrTbl")) $("rpIdrTbl").innerHTML = "";
    if ($("rpIdrProg")) $("rpIdrProg").textContent = "—";
    idrBase = null;
    $("rpTag").textContent = mode === "drill" ? "건물값" : "현재값";
    $("rpBnTag").textContent = "";
    if (bnPanel) bnPanel.clear("이력을 선택하면 병목별 CBS가 표시됩니다");
  }

  // CBS 병목 선택 집계 (v1.12 — ③ 운영뷰와 동일 패널). 결과의 병목별 CBS·초과초를
  // 집계로, 재생 프레임(fps 격자)의 병목 밀도를 스파크라인으로 쓴다.
  // 드릴 모드에선 현재 재생 중인 층의 병목 기준 — 층을 바꾸면 따라간다.
  function renderRpBn() {
    if (!bnPanel) return;
    const res = (mode === "drill") ? floorResultOf(curDrillFloor) : (data && data.result);
    const bns = (site && site.bottlenecks) || [];
    $("rpBnTag").textContent = (mode === "drill" && curDrillFloor) ? floorName(curDrillFloor) : "";
    if (!res || !bns.length) {
      bnPanel.clear(bns.length ? "재계산 결과 없음" : "이 층에 병목 없음 — 맵 설정에서 추가");
      return;
    }
    const per = {}, resBn = {};
    (res.bottleneck_metrics || []).forEach((m) => { per[m.bottleneck_id] = m.cbs; resBn[m.bottleneck_id] = m; });
    const frames = (data && data.frames) || [];
    const dOf = (f, bid) => {
      const b = (f.bottlenecks || []).find((x) => x.id === bid);
      return (b && b.density != null) ? b.density : null;
    };
    bnPanel.render({ bns, per, resBn,
                     series: (bid) => frames.map((f) => dOf(f, bid)) });
  }

  function setMode(m) {
    if (mode === m) return;
    mode = m;
    $("rpModeSess").classList.toggle("on", m === "sess");
    $("rpModeDrill").classList.toggle("on", m === "drill");
    $("rpFloorWrap").classList.remove("hidden");
    $("rpFloorWrap").firstChild.textContent = mode === "drill" ? "재생 층 " : "층 ";
    fillFloorSel();
    $("rpReport").classList.toggle("hidden", m !== "drill");
    pause();
    selId = null; data = null; site = null; drill = null;
    setControlsEnabled(false);
    $("rpReset").disabled = true; $("rpApply").disabled = true; $("rpReport").disabled = true;
    clearMetrics();
    $("rpHint").textContent = m === "drill"
      ? "건물 훈련 이력을 선택하면 전 층 결과를 건물 롤업으로 보여주고, 층을 골라 2D 재생·재계산할 수 있습니다."
      : "좌측에서 경보 세션을 선택하면 그 세션의 이동 기록을 도면 위에 그대로 재생합니다.";
    loadList();
    if (mc) mc.render();
  }

  async function recomputeDrill() {
    if (!selId) return;
    $("rpMsg").textContent = "건물 재계산 중…"; $("rpApply").disabled = true;
    const keepFloor = curDrillFloor, keepIdx = frameIndexAt(cursor);
    try {
      const resp = await API.drillReplay(selId, collectOverrides());
      drill = resp.drill;
      drillFrames = resp.frames_by_floor || {};
      drillSites = resp.site_by_floor || {};
      drillTimelines = resp.timeline_by_floor || {};
      showBuildingMetrics(drill, "재계산값");
      const floors = drill.floors || [];
      const fl = floors.includes(keepFloor) ? keepFloor : floors[0];
      if (fl) { loadDrillFloor(fl); goTo(Math.min(keepIdx, (data.frames || []).length - 1)); }
      $("rpMsg").textContent = "재계산 완료 — 원본 저장값은 그대로 보존됩니다.";
      if (mc) mc.render();
    } catch (e) {
      $("rpMsg").textContent = "재계산 실패: " + e.message;
    } finally { $("rpApply").disabled = false; }
  }

  // ------------------------------------------------------------ 세션 선택·로드
  async function selectSession(id) {
    if (playing) pause();
    selId = id;
    baseRow = sessions.find((s) => s.session_id === id) || null;
    renderList();
    const s = baseRow;
    if (s && !s.has_record) {
      $("rpHint").textContent = "이 세션은 녹화 이전이라 2D 재생이 불가합니다 (지표 요약만 존재).";
      setControlsEnabled(false);
      data = null; site = null; if (mc) mc.render();
      showMetricsFromRow(s);
      return;
    }
    $("rpHint").textContent = "재생 데이터를 불러오는 중…";
    $("rpMsg").textContent = "";
    try {
      data = await API.replaySession(id, { fps: 5 });
    } catch (e) {
      $("rpHint").textContent = "재생 로드 실패: " + e.message;
      return;
    }
    site = data.site || null;
    prepPlayback();
    fillThresholds(site && site.thresholds);
    showMetrics(data.result, "현재값");
    setControlsEnabled(true);
    $("rpReset").disabled = false; $("rpApply").disabled = false;
    $("rpHint").textContent = `${(data.frames||[]).length} 프레임 · ${fmtDur(duration)} · 트랙 ${data.meta && data.meta.track_row_count || 0}행`;
    if (mc) { setCanvasImage(); goTo(0); }
  }

  function prepPlayback() {
    const f = (data && data.frames) || [];
    duration = f.length ? (f[f.length - 1].ts - f[0].ts) : 0;
    cursor = 0;
    $("rpSeek").max = String(Math.max(0, f.length - 1));
    $("rpSeek").value = "0";
  }

  /** 개별 층 모드의 도면. **선택된 세션의 층** 것을 쓴다 — App.mapImg 는 상단바
   *  전역 층의 도면이라, 재생 바에서 층을 바꿔도 그대로 남아 17F 도면이 박혔다.
   *  (건물 훈련은 setDrillCanvasImage 가 층별로 따로 받는다.) */
  /** 지금 보고 있는 층 — 건물 훈련이면 재생 중인 층, 개별 층이면 재생 바 선택값.
   *  (같은 이름의 함수가 session.js 에도 있었는데 리포트 탭으로 옮겨가며 사라져
   *  여기서 ReferenceError 가 났다.) */
  function curFloor() {
    return curDrillFloor || (typeof API !== "undefined" ? API._floor() : "");
  }

  function setCanvasImage() {
    const wh = (site && site.map) || (App.site && App.site.map) || { w: 1000, h: 600 };
    if (!mc) return;
    const fid = curFloor();
    if (!fid) { mc.setImage(null, wh.w, wh.h); return; }
    const img = new Image();
    img.onload = () => { mc.setImage(img, wh.w, wh.h); mc.render(); };
    img.onerror = () => { mc.setImage(null, wh.w, wh.h); mc.render(); };
    img.src = API.mapImageUrl(fid);
  }

  // ------------------------------------------------------------ 재생 컨트롤
  function setControlsEnabled(on) {
    ["rpPlay","rpToStart","rpSeek"].forEach((id) => { $(id).disabled = !on; });
  }

  function play() {
    if (!data || !data.frames || !data.frames.length) return;
    if (cursor >= duration - 1e-3) cursor = 0;   // 끝이면 처음부터
    playing = true; $("rpPlay").textContent = "⏸ 일시정지";
    lastRaf = performance.now();
  }
  function pause() { playing = false; $("rpPlay").textContent = "▶ 재생"; }
  function togglePlay() { playing ? pause() : play(); }

  function goTo(idx) {                             // 슬라이더(프레임 인덱스) → cursor
    const f = data && data.frames; if (!f || !f.length) return;
    idx = Math.max(0, Math.min(f.length - 1, idx | 0));
    cursor = f[idx].ts - f[0].ts;
    $("rpSeek").value = String(idx);
    updateTimeLabel();
    showMetricsAtCursor();
  }

  function frameIndexAt(cur) {                     // cursor(초) → 프레임 인덱스(≤)
    const f = data.frames, t0 = f[0].ts, target = t0 + cur;
    let lo = 0, hi = f.length - 1, ans = 0;
    while (lo <= hi) { const m = (lo + hi) >> 1;
      if (f[m].ts <= target) { ans = m; lo = m + 1; } else hi = m - 1; }
    return ans;
  }

  function fmtDur(s) {
    s = Math.max(0, Math.round(s));
    return `${String(Math.floor(s/60)).padStart(2,"0")}:${String(s%60).padStart(2,"0")}`;
  }
  function updateTimeLabel() { $("rpTime").textContent = `${fmtDur(cursor)} / ${fmtDur(duration)}`; }

  // ------------------------------------------------------------ 렌더
  function currentInterp() {
    // cursor 위치의 두 프레임 보간 → {objs:[{gid,cam_id,x,y,vx,vy}], state}
    const f = data.frames, t0 = f[0].ts;
    const i = frameIndexAt(cursor);
    const a = f[i], b = f[Math.min(f.length - 1, i + 1)];
    const span = (b.ts - a.ts) || 1;
    const alpha = Math.max(0, Math.min(1, (t0 + cursor - a.ts) / span));
    const bpos = {}; (b.objects || []).forEach((o) => { bpos[o.gid] = o; });
    const objs = (a.objects || []).map((o) => {
      const nb = bpos[o.gid];
      return { gid:o.gid, cam_id:o.cam_id,
               x: nb ? o.x + (nb.x - o.x) * alpha : o.x,
               y: nb ? o.y + (nb.y - o.y) * alpha : o.y,
               vx:o.vx, vy:o.vy };
    });
    return { objs, state: a };
  }

  function overlay(g) {
    if (site) drawSiteElements(g, site, { state: dataState() });
    drawAlarmOrigins(g);
    if (!data || !data.frames || !data.frames.length) return;
    const { ctx, TX, TY } = g;
    const { objs } = currentInterp();
    objs.forEach((o) => {
      const x = TX(o.x), y = TY(o.y), col = camColor(o.cam_id, App.cameras);
      if (o.vx || o.vy) {
        const L = 16, ex = x + o.vx * L, ey = y + o.vy * L;
        ctx.strokeStyle = col; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(ex, ey); ctx.stroke();
        mcArrowHead(ctx, ex, ey, Math.atan2(o.vy, o.vx), 6, col);
      }
      ctx.fillStyle = col;
      ctx.beginPath(); ctx.arc(x, y, 4.5, 0, 7); ctx.fill();
      ctx.strokeStyle = "rgba(0,0,0,.55)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(x, y, 4.5, 0, 7); ctx.stroke();
      if (g.s > 0.7) {
        ctx.font = "10px Pretendard, sans-serif"; ctx.fillStyle = "rgba(0,0,0,.7)";
        ctx.fillText(o.gid, x + 7, y + 4);
      }
    });
  }

  function dataState() {                           // drawSiteElements용 상태(구역/병목/출구 카운트)
    if (!data || !data.frames || !data.frames.length) return null;
    return data.frames[frameIndexAt(cursor)];
  }

  function drawAlarmOrigins(g) {
    const os = data && data.meta && data.meta.alarm_origins;
    if (!os || !os.length) return;
    const { ctx, TX, TY } = g;
    os.forEach((o, i) => {
      const x = TX(o[0]), y = TY(o[1]);
      ctx.strokeStyle = "#ff5b5b"; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(x, y, 10, 0, 7); ctx.stroke();
      ctx.beginPath(); ctx.arc(x, y, 3, 0, 7); ctx.fillStyle = "#ff5b5b"; ctx.fill();
      ctx.font = "13px Pretendard, sans-serif";
      ctx.fillText(os.length > 1 ? `🔔${i + 1}` : "🔔", x + 12, y - 8);
    });
  }

  // ------------------------------------------------------------ 지표 패널

  /* 재생 커서 시점의 지표.
   *
   * 왜: 리플레이는 지금까지 **세션 최종 결과값**만 찍어 재생 내내 같은 숫자가
   * 박혀 있었다. ③ 운영 뷰는 SSE 로 매 순간 갱신되는데 ④ 리플레이만 정지값이라
   * "그 시점에 무슨 일이 있었나"를 볼 수 없었다. 1초 타임라인으로 맞춘다.
   *
   * IDR 만 타임라인에 값이 없다(개시 판정 시각만 안다) — 구역의 개시 시각이
   * 커서를 지난 구역들의 IDR 평균을 쓴다. 즉 개시 전에는 '—' 이고 개시하는
   * 순간 값이 뜬다. 실제 판정 흐름 그대로다. */
  function timelineAt(cur) {
    const tl = data && data.timeline;
    if (!tl || !tl.length) return null;
    const target = tl[0].ts + cur;
    let lo = 0, hi = tl.length - 1, ans = -1;
    while (lo <= hi) { const m = (lo + hi) >> 1;
      if (tl[m].ts <= target) { ans = m; lo = m + 1; } else hi = m - 1; }
    return ans < 0 ? null : tl[ans];
  }

  /* KPI 카드 시간곡선 — 세션 전체 추이 + 현재 커서 위치.
   * 리플레이는 '지금 몇인가'보다 '언제 어떻게 올라갔나'가 중요하다.
   * 데이터는 이미 받아온 1초 타임라인 그대로 쓴다(추가 요청 없음). */
  const SPARKS = [
    ["rpSparkSei",  (p) => p.sei,       "#30DCFB", 0, 100],
    ["rpSparkEpfi", (p) => p.epfi_avg,  "#7ad17a", 0, 100],
    ["rpSparkCbs",  (p) => p.cbs_total, "#e08a2e", 0, null],
    ["rpSparkIdr",  (p) => p.zones_started, "#c48ce0", 0, null],
  ];

  function drawSparks() {
    const tl = (data && data.timeline) || [];
    SPARKS.forEach(([id, pick, col, lo, hiFix]) => {
      const cv = $(id);
      if (!cv || !cv.clientWidth) return;               // 숨김 모드면 폭 0
      const dpr = window.devicePixelRatio || 1;
      const W = cv.clientWidth, H = cv.clientHeight;
      cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
      const c = cv.getContext("2d");
      c.setTransform(dpr, 0, 0, dpr, 0, 0);
      c.clearRect(0, 0, W, H);
      if (tl.length < 2) return;
      const vals = tl.map((p) => { const v = pick(p); return v == null ? null : +v; });
      const seen = vals.filter((v) => v != null);
      const hi = hiFix != null ? hiFix : Math.max(1, ...seen);
      const t0 = tl[0].ts, span = Math.max(1e-6, tl[tl.length - 1].ts - t0);
      const X = (t) => (t - t0) / span * (W - 2) + 1;
      const Y = (v) => H - 2 - (v - lo) / Math.max(1e-6, hi - lo) * (H - 4);
      // 값이 나오기 시작한 구간만 그린다 — 앞쪽 null 까지 채우면 없던
      // 상승 삼각형이 생겨 "처음부터 올라간 것"처럼 보인다.
      let x0 = null, x1 = null;
      c.beginPath();
      vals.forEach((v, i) => {
        if (v == null) return;
        const x = X(tl[i].ts), y = Y(v);
        if (x0 === null) { x0 = x; c.moveTo(x, y); } else c.lineTo(x, y);
        x1 = x;
      });
      if (x0 === null) return;                       // 전 구간 값 없음
      c.strokeStyle = col; c.lineWidth = 1.6; c.stroke();
      c.lineTo(x1, H); c.lineTo(x0, H); c.closePath();
      c.fillStyle = col + "22"; c.fill();
      // 현재 커서
      const cx = X(t0 + cursor);
      c.beginPath(); c.moveTo(cx, 0); c.lineTo(cx, H);
      c.strokeStyle = "#ffffff88"; c.lineWidth = 1; c.stroke();
    });
  }

  const DENS_NOTE = {
    num:  "<b>수치</b> — 4대 지표 숫자만 크게. 표·그래프를 모두 숨깁니다.",
    full: "<b>표</b> — 숫자에 더해 구역·객체·병목 <b>표</b>를 모두 봅니다. (기본)",
    viz:  "<b>시각화</b> — 지표마다 <b>시간 곡선</b>과 병목 그래프를 봅니다. ③ 운영 뷰에 가깝습니다.",
  };
  function setDensNote(d) {
    const el = $("rpDensNote");
    if (el) el.innerHTML = DENS_NOTE[d] || DENS_NOTE.full;
  }

  function showMetricsAtCursor() {
    if (!liveAtCursor) return;
    const p = timelineAt(cursor);
    if (!p) return;                       // 타임라인 없는 세션 — 최종값 그대로 둔다
    $("rpSei").textContent = p.sei == null ? "—" : Math.round(p.sei);
    $("rpEpfi").textContent = p.epfi_avg == null ? "—" : Math.round(p.epfi_avg);
    $("rpCbs").textContent = (p.cbs_total || 0).toFixed(1);

    const res = (data && data.result) || {};
    const zm = res.zone_metrics || [];
    const t = p.ts;
    const done = zm.filter((z) => z.evacuation_start_at != null
                                  && z.evacuation_start_at <= t);
    const vs = done.map((z) => z.idr).filter((v) => v != null);
    $("rpIdr").textContent = vs.length
      ? (vs.reduce((a, b) => a + b, 0) / vs.length).toFixed(2) : "—";
    $("rpIdrProg").textContent = `${done.length}/${zm.length}`;

    $("rpTag").textContent = `t=${fmtDur(cursor)} 시점값`;
    drawSparks();
    if ($("rpExitNow")) {
      const ec = p.exit_counts || {};
      const ks = Object.keys(ec).sort();
      $("rpExitNow").textContent = ks.length
        ? ks.map((k) => `${exitName(k)} ${ec[k]}`).join(" · ") : "";
    }
  }

  /** 출구 id → 표기명 (세션 스냅샷 기준, 없으면 id). */
  function exitName(id) {
    const ex = ((site && site.exits) || []).find((e) => e.id === id);
    return (ex && ex.name) || id;
  }

  function showMetrics(res, tag) {
    $("rpTag").textContent = tag || "현재값";
    $("rpSei").textContent = res.sei == null ? "—" : Math.round(res.sei);
    $("rpEpfi").textContent = res.epfi_avg == null ? "—" : Math.round(res.epfi_avg);
    $("rpCbs").textContent = (res.cbs_total || 0).toFixed(1);
    renderObjTbl(res.person_metrics || []);        // 개별 층 모드 경로
    const zm = res.zone_metrics || [];
    const started = zm.filter((z) => z.status === "started").length;
    const vs = zm.map((z) => z.idr).filter((v) => v != null);
    $("rpIdr").textContent = vs.length
      ? (vs.reduce((s, v) => s + v, 0) / vs.length).toFixed(2) : "—";
    $("rpIdrProg").textContent = `${started}/${zm.length}`;
    renderIdrTbl(zm.map((z) => ({ ...z, floor_id: res.floor_id || "" })), res.alarm_ts, tag);
    if (baseRow) {
      $("rpBase").innerHTML = `<span class="rpbase">원본 저장값 — SEI ${fmtVal(baseRow.sei,0)} · `
        + `EPFI ${fmtVal(baseRow.epfi_avg,0)} · CBS ${fmtVal(baseRow.cbs_total,1)}</span>`;
    } else $("rpBase").innerHTML = "";
    renderRpBn();
  }

  function showMetricsFromRow(s) {                 // 녹화 없는 세션 — 요약만
    $("rpTag").textContent = "원본 저장값";
    $("rpSei").textContent = fmtVal(s.sei,0) === "—" ? "—" : Math.round(s.sei);
    $("rpEpfi").textContent = s.epfi_avg == null ? "—" : Math.round(s.epfi_avg);
    $("rpCbs").textContent = (s.cbs_total || 0).toFixed(1);
    $("rpIdr").textContent = "—";
    if ($("rpIdrProg")) $("rpIdrProg").textContent = "—";
    if ($("rpIdrTbl")) $("rpIdrTbl").innerHTML = `<div class="mnote">녹화 이전 세션 — 구역별 IDR 없음</div>`;
    $("rpBase").innerHTML = "";
    $("rpBnTag").textContent = "";
    if (bnPanel) bnPanel.clear("녹화 이전 세션 — 병목별 CBS 없음");
  }

  // ------------------------------------------------------------ 임계값
  function fillThresholds(th) {
    th = th || {};
    TH_KEYS.forEach(([id, key]) => { if (th[key] != null) $(id).value = th[key]; });
    $("rpMeanA").checked = !!th.idr_mean_align;   // 불리언이라 value 로 못 넣는다
    // ρcrit — 세션 병목들의 대표값(첫 병목) 또는 2.0
    const bns = (site && site.bottlenecks) || [];
    $("rpRho").value = bns.length ? bns[0].rho_crit : 2.0;
  }

  function collectOverrides() {
    const thresholds = {};
    TH_KEYS.forEach(([id, key]) => {
      const v = parseFloat($(id).value);
      if (!isNaN(v)) thresholds[key] = v;
    });
    thresholds.idr_mean_align = $("rpMeanA").checked;
    const ov = { thresholds, fps: 5 };
    const rho = parseFloat($("rpRho").value);
    if (!isNaN(rho)) ov.rho_crit = rho;
    return ov;
  }

  async function recompute() {
    if (mode === "drill") return recomputeDrill();
    if (!selId || !data) return;
    $("rpMsg").textContent = "재계산 중…"; $("rpApply").disabled = true;
    const keepIdx = frameIndexAt(cursor);
    try {
      data = await API.replaySession(selId, collectOverrides());
      site = data.site || site;
      prepPlayback();
      showMetrics(data.result, "재계산값");
      goTo(Math.min(keepIdx, (data.frames||[]).length - 1));  // 위치 유지(+시점값 갱신)
      $("rpMsg").textContent = "재계산 완료 — 원본 저장값은 그대로 보존됩니다.";
      if (mc) mc.render();
    } catch (e) {
      $("rpMsg").textContent = "재계산 실패: " + e.message;
    } finally { $("rpApply").disabled = false; }
  }

  // ------------------------------------------------------------ RAF 루프
  function loop(ts) {
    if (!active) { rafId = null; return; }
    rafId = requestAnimationFrame(loop);
    if (playing && data && data.frames && data.frames.length) {
      const dt = (ts - lastRaf) / 1000;
      lastRaf = ts;
      cursor += dt * speed;
      if (cursor >= duration) { cursor = duration; pause(); }
      $("rpSeek").value = String(frameIndexAt(cursor));
      updateTimeLabel();
      showMetricsAtCursor();
    } else { lastRaf = ts; }
    if (mc) mc.render();
  }

  // ------------------------------------------------------------ lifecycle
  function init() {
    if (inited) return;
    inited = true;
    mc = new MapCanvas($("rpCv"), { draw: overlay });
    window.addEventListener("resize", () => { if (active) drawSparks(); });
    if (window.CbsBnPanel) bnPanel = CbsBnPanel($("rpBn"));
    $("rpPlay").onclick = togglePlay;
    $("rpToStart").onclick = () => { pause(); goTo(0); if (mc) mc.render(); };
    $("rpSeek").oninput = (e) => { pause(); goTo(parseInt(e.target.value)); if (mc) mc.render(); };
    $("rpSpeed").onchange = (e) => { speed = parseFloat(e.target.value) || 1; };
    $("rpApply").onclick = recompute;
    $("rpReset").onclick = () => { fillThresholds(site && site.thresholds); $("rpMsg").textContent = "원래값으로 되돌림 — [재계산]을 눌러 반영"; };
    $("rpObjSort").onclick = () => {                    // EPFI↑ → 이탈↓ → 지속↓ 순환
      const nxt = { epfi: "dev", dev: "dur", dur: "epfi" };
      objSortKey = nxt[objSortKey];
      $("rpObjSort").textContent = { epfi: "EPFI↑", dev: "이탈↓", dur: "지속↓" }[objSortKey];
      renderObjTbl(objRows);
    };
    $("rpModeSess").onclick = () => setMode("sess");
    $("rpModeDrill").onclick = () => setMode("drill");
    $("rpFloorSel").onchange = (e) => {
      if (mode !== "drill") {
        // 개별 층: 세션 목록이 층별이라 목록만 다시 받는다. App.setFloor 를 쓰면
        // 뷰가 재진입(leave/enter)되어 보던 것이 날아가므로 값만 바꾼다.
        App.currentFloor = e.target.value;
        if (App.updateChip) App.updateChip();
        if (App.updateExportLinks) App.updateExportLinks();
        selId = null; data = null; site = null;
        clearMetrics(); setControlsEnabled(false);
        setCanvasImage();                 // 층이 바뀌면 도면도 그 층 것으로
        loadList();
        return;
      }
      // 재생 중 층 전환: 현재 절대 시각을 유지해 다른 층의 같은 순간으로 자연스럽게 전환.
      // (재생 중이면 그대로 계속 재생, 정지 중이면 그 시점에 멈춰 있음.)
      const absNow = (data && data.frames && data.frames.length)
        ? data.frames[0].ts + cursor : null;
      loadDrillFloor(e.target.value, absNow);
    };
    $("rpReport").onclick = () => {
      if (!(drill && window.Session && Session.openDrillReport)) return;
      // ID 재구성은 리포트 안의 [ID 재구성] 탭에서 처리한다 — 재생 화면 옆에
      // 객체를 하나씩 늘어놓으면 조잡하고, 필요한 건 분석 결과를 따로 보는 것.
      Session.openDrillReport({ ...drill, jy_floor: curDrillFloor });
    };
  }

  let _rpPanelWired = false;
  /** 우측 지표 패널 — 밀도(요약·카드·표)·패널 접기 배선(1회). */
  function wireRpPanel() {
    if (_rpPanelWired || typeof PanelView === "undefined") return;
    _rpPanelWired = true;
    PanelView.wire("rpSide", "rpDens", "rpFold", [
      { el: "#rpGrpKpi", key: "rp.kpi" },
      // 기본은 전부 펼침(기존 화면). 길면 보는 사람이 접는다.
      { el: "#rpGrpIdr", key: "rp.idr" },
      { el: "#rpGrpObj", key: "rp.obj" },
      { el: "#rpGrpBn",  key: "rp.bn" },
    ]);
    // 모드가 무엇을 바꾸는지 한 줄로 알린다 (PanelView 에는 변경 콜백이 없다)
    const seg = $("rpDens");
    if (seg) {
      seg.querySelectorAll("[data-dens]").forEach((b) =>
        b.addEventListener("click", () => {
          setDensNote(b.dataset.dens);
          setTimeout(drawSparks, 30);     // 숨김→표시로 캔버스 폭이 생긴 뒤
        }));
      const on = seg.querySelector("[data-dens].on");
      setDensNote(on ? on.dataset.dens : "full");
    }
    const at = $("rpAtCursor");
    if (at) {
      at.onchange = () => {
        liveAtCursor = at.checked;
        if (liveAtCursor) showMetricsAtCursor();
        else if (data && data.result) showMetrics(data.result, "세션 최종값");
      };
    }
  }

  function enter() {
    init();
    wireRpPanel();
    active = true;
    // 상단바 전역 층 셀렉터 숨김은 App.renderFloorSelector 가 책임진다
    // (App.view === "replay" 조건). 여기서만 숨기면 그 함수가 다시 불릴 때 되살아난다.
    if (App.renderFloorSelector) App.renderFloorSelector();
    // 다층 사이트는 '건물 훈련'이 기본(실사용 단위). 최초 1회만 자동 설정 —
    // 이후 사용자가 '개별 층'을 고르면 그대로 존중.
    if (!modeAuto) {
      modeAuto = true;
      const multi = App.site && (App.site.floors || []).length >= 2;
      mode = "drill";                      // 리허설·훈련 모두 건물 세션 — 개별 층은 디버그용
      $("rpModeDrill").classList.toggle("on", mode === "drill");
      $("rpModeSess").classList.toggle("on", mode === "sess");
      $("rpFloorWrap").classList.remove("hidden");     // 두 모드 다 쓴다
      $("rpReport").classList.toggle("hidden", mode !== "drill");
    }
    fillFloorSel();
    setCanvasImage();
    loadList();
    if (selId && mode === "sess") {                // 재진입 시 선택 유지(세션 모드)
      const still = sessions.find((s) => s.session_id === selId);
      if (!still) { selId = null; data = null; site = null; }
    }
    lastRaf = performance.now();
    rafId = requestAnimationFrame(loop);
  }

  function leave() {
    active = false; pause();
    // leave() 는 App.view 가 바뀌기 **전에** 불린다 — 지금 그리면 여전히 replay 로
    // 판정돼 숨은 채로 남는다. 전환이 끝난 뒤에 다시 그린다.
    setTimeout(() => { if (App.renderFloorSelector) App.renderFloorSelector(); }, 0);
    if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
  }

  return { enter, leave };
})();
