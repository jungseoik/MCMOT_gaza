/* 훈련영상 동기 송출 (ADR 08) — ② 카메라 화면의 제어 패널 + ③ 운영뷰 상태 칩.
 *
 * 본체와 분리된 리허설 도구다. 여기서 아무것도 안 하면 시스템은 지금과 동일하게
 * 동작한다. [송출 시작]을 누르면 전 채널이 같은 시각으로 t=0부터 흐른다.
 */
"use strict";

var VSource = (() => {
  const $ = (id) => document.getElementById(id);
  let scenarios = [];
  let packages = [];      // 리허설 패키지 요약 (ADR 09) — media/vsource/*/*/rehearsal.json
  let poll = null;
  let inited = false;
  let busy = false;
  // 대기 송출에서 카메라가 다 붙는 데 걸린 실측 시간 — 훈련 시작 때 앞머리
  // 길이를 이 값으로 정한다(고정 상수를 쓰지 않는다).
  let standbyAt = null, attachSec = null;
  // 성공/실패 메시지가 2초 폴링에 덮여 사라지는 걸 막는다 — 눌렀는데 아무 반응이
  // 없어 보이면 또 누르게 된다(추론 모델 패널에서 같은 문제를 겪었다).
  let stickyUntil = 0;
  let lastOwnMapped = -1;   // 리허설 매핑 수 — 바뀌면 시나리오 목록을 다시 읽는다

  function msg(html, holdSec) {
    const el = $("vsMsg");
    if (!el) return;
    el.innerHTML = html;
    stickyUntil = holdSec ? Date.now() + holdSec * 1000 : 0;
  }

  const fmtSec = (v) => (v == null ? "—" : `${Math.round(v)}s`);

  async function jget(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`${r.status}`);
    return r.json();
  }
  async function jpost(url, body) {
    const r = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `${r.status}`);
    return d;
  }

  // ------------------------------------------------------------ 시나리오
  async function loadScenarios() {
    const sel = $("vsScenario");
    if (!sel) return;
    try {
      const d = await jget("/api/vsource/scenarios");
      scenarios = d.scenarios || [];
      packages = d.packages || [];
    } catch (e) { scenarios = []; packages = []; }
    if (!scenarios.length) {
      sel.innerHTML = `<option value="">(시나리오 없음 — media/vsource/&lt;site&gt;/&lt;set&gt;/ 패키지)</option>`;
      $("vsMeta").textContent = "";
      return;
    }
    // 패키지별 optgroup — 리허설 패키지(신규 기준)가 위, legacy mock 이 아래 (ADR 09)
    const cur = sel.value;
    const opt = (s) => `<option value="${s.id}"${s.id === cur ? " selected" : ""}>` +
      `${s.package_id ? s.name.replace(`${s.package_name} — `, "") : s.name}${s.ok ? "" : " ⚠"}</option>`;
    let html = "";
    for (const p of packages) {
      const mine = scenarios.filter((s) => s.package_id === p.id);
      if (mine.length) html += `<optgroup label="📦 ${p.name}">${mine.map(opt).join("")}</optgroup>`;
    }
    const legacy = scenarios.filter((s) => !s.package_id);
    if (legacy.length) html += `<optgroup label="수동 시나리오 (legacy mock)">${legacy.map(opt).join("")}</optgroup>`;
    sel.innerHTML = html;
    renderMeta();
  }

  function current() {
    const id = $("vsScenario") && $("vsScenario").value;
    return scenarios.find((s) => s.id === id) || null;
  }

  /** 패키지 시나리오의 "붙일 층" — 사이트 층 중 하나 (ADR 09 §7 빙의 모드).
   *  기본값은 manifest 가 정한 층. 여기서 바꾸면 standby/start 때 서버가 manifest 에
   *  되써서 다음부터는 그 층이다. 사이트에 없는 층을 가리키면 경고. */
  function renderFloorPick(s) {
    const lab = $("vsFloorLab"), sel = $("vsFloor");
    if (!lab || !sel) return;
    const isPkg = !!(s && s.package_id);
    lab.classList.toggle("hidden", !isPkg);
    if (!isPkg) return;
    const floors = (App.site && App.site.floors) || [];
    const want = (s.streams.find((x) => x.cam_floor) || {}).cam_floor || "";
    const known = floors.some((f) => f.id === want);
    sel.innerHTML = floors.map((f) =>
      `<option value="${f.id}"${f.id === want ? " selected" : ""}>${f.name || f.id}</option>`).join("")
      + (known || !want ? "" : `<option value="" selected>⚠ ${want} — 사이트에 없음 (① 맵설정에서 만들기)</option>`);
  }

  function pickedFloor() {
    const s = current(), sel = $("vsFloor");
    if (!s || !s.package_id || !sel) return undefined;
    const cur = (s.streams.find((x) => x.cam_floor) || {}).cam_floor || "";
    return sel.value && sel.value !== cur ? sel.value : undefined;   // 바뀐 경우만 보낸다
  }

  const isFile = (s) => !!(s && s.source === "file");

  function renderMeta() {
    const s = current(), el = $("vsMeta"), wn = $("vsWarn");
    renderFloorPick(s);
    const sb = $("vsStandby");
    if (sb) sb.textContent = isFile(s) ? "▶ 준비 (파일)" : "⏸ 대기 송출";
    if (!el) return;
    if (!s) { el.innerHTML = ""; if (wn) wn.innerHTML = ""; return; }
    const durs = s.streams.map((x) => x.duration_sec).filter((v) => v != null);
    const range = !durs.length ? "—"
      : (Math.min(...durs).toFixed(0) === Math.max(...durs).toFixed(0)
          ? `${Math.min(...durs).toFixed(0)}s` : `${Math.min(...durs).toFixed(0)}~${Math.max(...durs).toFixed(0)}s`);
    const n = s.streams.length;
    const mapped = s.streams.filter((x) => x.cam_mapped && x.cam_enabled).length;
    const noCam = s.streams.filter((x) => !x.cam_id).length;
    const p = s.package_id && packages.find((x) => x.id === s.package_id);
    const floorTxt = (s.floors || []).map((f) => App.floorName ? App.floorName(f) : f).join(", ") || "—";
    const fact = (k, v, cls) => `<div class="fact"><span>${k}</span><b class="${cls || ""}">${v}</b></div>`;
    el.innerHTML =
      fact("채널", n) + fact("영상", range) + fact("사이클", fmtSec(s.cycle_sec)) + fact("층", floorTxt)
      + fact("매핑", `${mapped}/${n}`, mapped === n ? "ok" : "warn")
      + (p ? fact("사전검증", p.prep_ok === true ? "통과" : p.prep_ok === false ? "실패" : "미실행",
                  p.prep_ok === true ? "ok" : "warn") : "")
      + (p ? `<div class="fact wide"><span>패키지</span><b title="${p.root}">📦 ${p.name} · 카메라 ${p.cameras_mapped}/${p.cameras_total} 매핑</b></div>` : "");
    // 경고는 **종류별 한 줄**로 — 채널마다 같은 문장을 5번 반복하면 읽을 수 없다.
    const lines = [];
    if (!s.ok) lines.push(`<span class="err">✕ ${s.problems.join(" / ")}</span>`);
    if (p && (p.prep_fails || []).length) lines.push(`<span class="err">✕ 사전검증 실패 ${p.prep_fails.length}건 — ${p.prep_fails[0]}</span>`);
    if (p) for (const f of p.floors) {
      if (f.mode === "site" && !((App.site && App.site.floors) || []).some((x) => x.id === f.id))
        lines.push(`<span class="warn">⚠ 층 ${f.id} 가 사이트에 없음 — ① 맵 설정에서 먼저 만드세요</span>`);
    }
    if (noCam) lines.push(`<span class="warn">⚠ 카메라 없는 채널 ${noCam} — ② 카메라 등록 필요</span>`);
    const unmapped = n - mapped - noCam;
    if (unmapped > 0) lines.push(`<span class="warn">⚠ 매핑 없는 채널 ${unmapped} — 대기 송출을 켜고 채널을 클릭해 매핑하세요</span>`);
    if (!lines.length && n) lines.push(`<span class="ok">✓ 바로 송출·표출 가능</span>`);
    if (wn) wn.innerHTML = lines.join("<br>");
  }

  // ------------------------------------------------------------ 상태
  function renderStatus(st) {
    const box = $("vsStreams"), msgEl = $("vsMsg");
    const chip = $("vsChip");
    if (box) {
      if (!st || !st.running) {
        box.innerHTML = "";
      } else {
        const sc = scenarios.find((x) => x.id === st.scenario_id);
        const standby = st.mode === "standby";
        box.innerHTML = (st.streams || []).map((s) => {
          const cs = sc && sc.streams.find((x) => x.path === s.path);
          const cid = cs && cs.cam_id;
          // 송출(dot)과 카메라 수신(rx)은 다르다 — 송출은 떴는데 카메라가 아직
          // 안 붙은 구간이 20초쯤 있어서, 둘을 갈라 보여야 오해가 없다.
          const rx = st.source === "file"
            ? (s.receiving ? `<span class="vsrx on">파일</span>` : `<span class="vsrx">끝</span>`)
            : (s.receiving ? `<span class="vsrx on">수신</span>` : `<span class="vsrx">붙는 중</span>`);
          // 매핑 배지는 하나만: 없음 / 있음(리허설용 or 원래 것 상속)
          let mp;
          if (!cid) mp = `<span class="vsmp warn" title="이 경로를 보는 카메라가 없다 — ②에서 등록">카메라 없음</span>`;
          else if (s.mapping_stale) mp = `<span class="vsmp warn" title="매핑 당시와 도면이 달라졌다 — 다시 찍어야 한다">매핑 낡음</span>`;
          else if (s.own_mapping) mp = `<span class="vsmp on" title="이 리허설용으로 잡은 매핑">매핑 ✓</span>`;
          else if (cs && cs.cam_mapped) mp = `<span class="vsmp on" title="원래 카메라 매핑을 그대로 쓴다 — 시점이 다르면 클릭해 다시">매핑 ✓ 상속</span>`;
          else mp = `<span class="vsmp warn">매핑 필요</span>`;
          const sub = cid ? `${cid}${cs.cam_floor ? " · " + (App.floorName ? App.floorName(cs.cam_floor) : cs.cam_floor) : ""}` : "";
          // 위치는 재생 중에만 — 대기(정지화면)는 시간 개념이 없다 (예전엔 '종료'로 잘못 떴다)
          const pos = standby ? "" : `<span class="pos">${s.pos_sec != null ? s.pos_sec.toFixed(0) + "s" : "끝"}</span>`;
          return `<div class="vsrow${s.publishing ? " on" : ""}${cid ? " clk" : ""}"` +
            `${cid ? ` data-cam="${cid}" title="클릭하면 ${cid} 매핑 화면으로 이동"` : ""}>
             <span class="dot"></span>
             <span class="nm" title="${s.file}">${s.path}</span>
             <span class="sub">${sub}</span>
             ${rx}${mp}${pos}
           </div>`;
        }).join("");
        box.querySelectorAll(".vsrow.clk").forEach((row) => {
          row.onclick = () => gotoMapping(row.dataset.cam);
        });
      }
    }
    const sh = $("vsStreamsHint");
    if (sh) sh.textContent = (st && st.running) ? "채널을 클릭하면 그 카메라 매핑 화면으로 갑니다." : "";
    // 매핑을 찍고 돌아오면 목록의 매핑 수가 낡아 있다 — 바뀐 걸 감지해 다시 읽는다
    const om = st && st.running ? (st.own_mapped || 0) : -1;
    if (om !== lastOwnMapped) { lastOwnMapped = om; loadScenarios(); }
    if (msgEl && st && st.running && !busy && Date.now() >= stickyUntil) {
      const rxN = st.cams_receiving, rxT = st.cams_total;
    if (st.mode === "standby" && rxT && rxN === rxT && standbyAt && attachSec == null) {
      attachSec = (Date.now() - standbyAt) / 1000;
    }
      const rxTxt = (rxT && rxN < rxT)
        ? `<br><span class="warn">카메라 붙는 중 ${rxN}/${rxT} — 20초쯤 걸립니다</span>`
        : "";
      const parked = st.site_parked ? `<br><span class="vshint">기존 RTSP 카메라는 리허설 동안 일시 정지(GPU 해제) — 종료 시 복원</span>` : "";
      if (st.mode === "standby") {
        msgEl.innerHTML = `<span class="ok">${st.source === "file" ? "준비됨 (파일 모드)" : "대기 중"}</span> — 정지화면 (영상 멈춤)` + rxTxt + parked
          + `<br><span class="vshint">채널을 클릭해 매핑 → 끝나면 ③ 운영 뷰에서 [🎬 리허설 훈련 시작]</span>`;
      } else {
        msgEl.innerHTML = `<span class="ok">훈련 재생 중</span> — 위치 ${fmtSec(st.cycle_pos_sec)}` + rxTxt
          + `<br><span class="vshint">채널을 클릭하면 <b>대기로 돌리고</b> 매핑 화면으로 갑니다</span>`;
      }
    }
    // ③ 운영뷰 — 상태 칩 + 리허설 전용 시작 버튼.
    // 평상시 [🔔 경보 시작]은 건드리지 않는다(리허설과 무관하게 기존 동작 유지).
    // 대기 중일 때만 [🎬 리허설 훈련 시작]이 나타나 영상 재생과 경보를 함께 건다.
    if (chip) {
      const on = !!(st && st.running);
      chip.classList.toggle("hidden", !on);
      if (on) {
        chip.textContent = st.mode === "standby"
          ? `⏸ 리허설 대기 중 — [🎬 리허설 훈련 시작]으로 t=0부터`
          : `▶ 리허설 재생 중 · ${fmtSec(st.cycle_pos_sec)}`;
      }
    }
    renderSteps(st);
    const sd = $("vsStartDrill");
    if (sd) sd.disabled = !(st && st.running && st.mode === "standby");
    // ② 카메라 목록이 "어느 게 리허설 채널인지" 를 알아야 한다 — 9대가 나란히
    // 있으면 구분이 안 간다. 상태가 바뀐 순간에만 다시 그린다.
    const wasOn = !!(window.VSourceState && window.VSourceState.running);
    const nowOn = !!(st && st.running);
    const key = nowOn ? (st.streams || []).map((x) => x.cam_id).join(",") : "";
    const prevKey = window.VSourceState && window.VSourceState._key;
    window.VSourceState = st ? { ...st, _key: key } : null;
    document.body.classList.toggle("rehearsing", nowOn);
    if (wasOn !== nowOn || prevKey !== key) {
      // 리허설 패키지는 가상 카메라(rh_*)·가상 층(rh_*)을 **서버에서** 얹었다
      // 뗐다 한다(ADR 09) — 다시 그리기 전에 반드시 API에서 재조회해야 한다.
      // (기존엔 renderList만 불러서 stale 목록이 그려졌다 — rh 카메라 안 보임)
      Promise.all([
        App.reloadCameras(),
        API.getFloors().then((f) => { App.floors = f; }).catch(() => {}),
      ]).then(() => {
        if (typeof Views !== "undefined" && Views.cams && Views.cams.renderList) {
          Views.cams.renderList();
        }
        if (App.renderFloorSelector) App.renderFloorSelector();
      });
    }
    const who = $("rhBannerWho");
    if (who && nowOn) {
      const n = (st.streams || []).length;
      who.textContent = `${st.scenario_name || st.scenario_id} · ${n}채널`
        + (st.own_mapped ? ` · 리허설 매핑 ${st.own_mapped}` : "");
    }
    const rb = $("sessRehearsalBtn"), sb = $("sessBtn");
    if (rb && sb) {
      const standby = !!(st && st.running && st.mode === "standby");
      // 세션 진행 중이면 둘 다 숨긴다(기존 sessBtn 토글은 session.js 소관이므로
      // 그쪽이 숨긴 상태를 덮지 않도록 hidden 여부를 보고 따라간다).
      const sessionOn = sb.classList.contains("hidden");
      rb.classList.toggle("hidden", !standby || sessionOn);
      sb.classList.toggle("vs-dim", standby && !sessionOn);
    }
  }

  async function refresh() {
    try { renderStatus(await jget("/api/vsource/status")); }
    catch (e) { renderSteps(null); }
  }

  /** 매핑하러 간다 — 재생 중이면 먼저 대기(정지화면)로 돌린다.
   *  매핑하려는데 화면이 계속 흐르면 대응점을 찍기 어렵다. "매핑하러 간다"는
   *  행동 자체가 "멈춰라"는 뜻이므로 여기서 알아서 바꾼다. */
  async function gotoMapping(camId) {
    const st = await jget("/api/vsource/status").catch(() => null);
    if (st && st.running && st.mode === "play") {
      busy = true;
      msg("매핑을 위해 대기(정지화면)로 전환 중…");
      try {
        standbyAt = Date.now(); attachSec = null;
        await jpost("/api/vsource/standby", { scenario_id: st.scenario_id });
        msg(`<span class="ok">대기로 전환</span> — 영상이 멈췄습니다. 매핑하세요.`
          + `<br>끝나면 ③ 운영 뷰 [경보 시작]이 처음부터 다시 재생합니다.`, 10);
      } catch (e) {
        msg(`<span class="warn">전환 실패: ${e.message}</span>`, 12);
      } finally { busy = false; refresh(); }
    }
    // 매핑 화면은 ② 다 — 리허설 탭에서 눌렀으면 거기로 데려간다.
    if (typeof App !== "undefined" && App.view !== "cams") App.switchView("cams");
    setTimeout(() => {
      if (typeof Views !== "undefined" && Views.cams && Views.cams.selectCamera) {
        Views.cams.selectCamera(camId);
        const duo = document.querySelector("#viewCams .duo");
        if (duo) duo.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }, 300);
  }

  // ------------------------------------------------------------ 제어
  async function start() {
    const s = current();
    if (!s) return;
    if (!s.ok && !confirm(`이 시나리오에 문제가 있습니다:\n\n${s.problems.join("\n")}\n\n그래도 시도할까요?`)) return;
    // 매핑이 빠진 채널이 있으면 송출은 되지만 운영 뷰가 빈다 — 미리 알린다.
    if ((s.warns || []).length && !confirm(
        `송출은 되지만 운영 뷰에 안 찍히는 채널이 있습니다:\n\n${s.warns.join("\n")}\n\n`
        + `송출을 켜면 그 화면에서 바로 매핑할 수 있습니다.\n계속할까요?`)) return;
    busy = true;
    msg("송출 시작 중…");
    try {
      const st = await jpost("/api/vsource/start",
        { scenario_id: s.id, loop: $("vsLoop").checked, floor_id: pickedFloor() });
      const n = (st.pm2_stopped || []).length;
      msg(`<span class="ok">훈련 시작</span> — ${st.streams.length}채널 t=0 동시 재생`
        + (n ? ` · pm2 ${n}개 정지` : "")
        + `<br>카메라 전체가 다시 붙는 데 <b>20~25초</b> 걸립니다`
        + `(영상 앞 여유 구간이 그만큼 필요합니다).`, 12);
    } catch (e) {
      msg(`<span class="warn">시작 실패: ${e.message}</span>`, 15);
    } finally {
      busy = false;
      refresh();
    }
  }

  async function standby() {
    const s = current();
    if (!s) return;
    busy = true;
    // 첫 프레임 추출 + pm2 정지 + 리허설 밖 카메라 파킹(워커 재시작 1회)이 걸려
    // 응답까지 몇 초 걸린다. 진행 표시가 없으면 "안 눌린다"고 오해한다.
    let dots = 0;
    const tick = setInterval(() => {
      dots = (dots + 1) % 4;
      msg((isFile(s) ? "준비 중" : "대기 송출 준비 중") + ".".repeat(dots)
        + `<br><span class="vshint">${isFile(s) ? "영상 열기 · 첫 프레임 · 분석 모델 로드(첫 1회 수 초)" : "첫 프레임 추출 · 상시송출 정지 · 리허설 밖 카메라 정리"}</span>`);
    }, 500);
    $("vsStandby").disabled = true;
    try {
      standbyAt = Date.now(); attachSec = null;   // 부착시간 측정 시작
      const st = await jpost("/api/vsource/standby",
        { scenario_id: s.id, floor_id: pickedFloor() });
      await loadScenarios();                          // 층을 바꿨으면 manifest 가 갱신됐다
      const n = (st.pm2_stopped || []).length;
      if (st.source === "file") {
        msg(`<span class="ok">준비 완료</span> — ${st.streams.length}채널 첫 프레임 (파일 모드)`
          + `<br>바로 매핑하거나 [🎬 훈련 시작]을 누르세요 — 카메라 대기 없음.`, 12);
      } else {
        msg(`<span class="ok">대기 송출 시작</span> — ${st.streams.length}채널 정지화면`
          + (n ? ` · pm2 ${n}개 정지` : "")
          + `<br><b>카메라가 붙는 데 20초쯤 걸립니다</b> — 아래 채널이 전부 '수신'이 되면 매핑하세요.`, 25);
      }
    } catch (e) {
      msg(`<span class="warn">대기 실패: ${e.message}</span>`, 15);
    } finally {
      clearInterval(tick);
      busy = false; $("vsStandby").disabled = false; refresh();
    }
  }

  async function stop() {
    busy = true;
    msg("정지 중…");
    try {
      const d = await jpost("/api/vsource/stop", {});
      const r = (d.pm2_restored || []).length;
      msg(`<span class="ok">정지됨</span> — ${d.stopped}채널`
        + (r ? ` · pm2 ${r}개 복구` : ""), 8);
    } catch (e) {
      msg(`<span class="warn">정지 실패: ${e.message}</span>`, 15);
    } finally {
      busy = false;
      refresh();
    }
  }

  // ------------------------------------------------------------ lifecycle
  function init() {
    if (inited || !$("vsScenario")) return;
    inited = true;
    $("vsStandby").onclick = standby;
    $("vsStop").onclick = stop;
    if ($("vsStartDrill")) $("vsStartDrill").onclick = startDrill;
    $("vsScenario").onchange = renderMeta;
    loadScenarios().then(refresh);
    // 상태 폴링 — 사이클 카운트다운이 있어야 경보 시점을 맞출 수 있다
    if (!poll) poll = setInterval(refresh, 2000);
  }

  /** 단계 표시 — 지금 어디인지 한 줄로. 상태가 여럿이라 헷갈린다는 지적 반영. */
  function renderSteps(st) {
    const box = $("rhSteps"), lab = $("rhState");
    if (!box || !lab) return;
    const running = !!(st && st.running);
    const rxN = st && st.cams_receiving, rxT = st && st.cams_total;
    const allRx = running && rxT && rxN === rxT;
    let step, txt;
    if (busy) {                       // 요청 처리 중 — 직전 상태를 보여 오해를 만들지 않는다
      step = "standby"; txt = "전환 중… (첫 프레임 추출 · 상시송출 정지)";
    } else if (!running) {
      step = "pick"; txt = "리허설이 꺼져 있습니다 — 시나리오를 고르고 [⏸ 대기 송출].";
    } else if (st.mode === "standby" && !allRx) {
      step = "standby"; txt = `대기 송출 준비 중 — 카메라 붙는 중 ${rxN}/${rxT} (20초쯤 걸립니다)`;
    } else if (st.mode === "standby") {
      step = "map"; txt = (st.source === "file" ? "준비됨 · 파일 모드(RTSP 없음) — " : "대기 중(정지화면) — ")
        + "매핑할 채널을 클릭하거나, [🎬 훈련 시작]";
    } else if (st.in_lead) {
      // 앞머리 = 정지화면으로 카메라 복귀를 덮는 구간. 본영상은 아직 안 흐른다.
      step = "run";
      txt = `🎬 훈련 시작까지 ${Math.ceil(st.lead_left_sec)}초 — 정지화면으로 `
          + `카메라 복귀를 덮는 중 (${rxN}/${rxT})`;
    } else if (!allRx) {
      step = "run"; txt = `훈련 재생 중 · 위치 ${fmtSec(st.cycle_pos_sec)} — 카메라 붙는 중 ${rxN}/${rxT}`;
    } else {
      step = "run"; txt = `훈련 재생 중 · 위치 ${fmtSec(st.cycle_pos_sec)}` + (st.source === "file" ? " · 파일 모드(프레임 동기)" : " · 전 채널 수신");
    }
    const order = ["pick", "standby", "map", "run"];
    const cur = order.indexOf(step);
    box.querySelectorAll("li").forEach((li) => {
      const i = order.indexOf(li.dataset.step);
      li.classList.toggle("done", i < cur);
      li.classList.toggle("cur", i === cur);
    });
    lab.textContent = txt;
    lab.classList.toggle("warn", busy || (running && (!allRx || st.in_lead)));
    if ($("rhChanN")) $("rhChanN").textContent = (st && st.streams || []).length;
  }

  /** ⑤에서 바로 훈련 시작 → ③ 운영 뷰로 데려간다.
   *  전에는 ⑤에서 준비하고 ③으로 옮겨 다시 눌러야 해 왔다갔다했다. */
  async function startDrill() {
    if (busy) return;
    const st = await jget("/api/vsource/status").catch(() => null);
    if (!st || !st.running || st.mode !== "standby") {
      msg("대기 송출을 먼저 켜세요.", 6); return;
    }
    if (typeof App !== "undefined") App.switchView("live");
    setTimeout(() => {
      const b = document.getElementById("sessRehearsalBtn");
      if (b && !b.disabled) b.click();
      else msg("③ 운영 뷰에서 경보 원점을 찍고 [🎬 리허설 훈련 시작]을 누르세요.", 8);
    }, 600);
  }

  return {
    init, refresh, startDrill,
    // 대기 송출 실측 부착시간 — session.js 가 앞머리 길이를 정하는 데 쓴다
    get attachSec() { return attachSec; },
    // ⑤ 탭 진입점 — App.switchView 가 부른다
    enter() { init(); loadScenarios().then(refresh); },
    leave() {},
  };
})();

// 화면 5 = 리허설 (view_map/view_cams 와 같은 규약)
var Views = window.Views || (window.Views = {});
Views.rehearsal = VSource;
