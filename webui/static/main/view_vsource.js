/* 훈련영상 동기 송출 (ADR 08) — ② 카메라 화면의 제어 패널 + ③ 운영뷰 상태 칩.
 *
 * 본체와 분리된 리허설 도구다. 여기서 아무것도 안 하면 시스템은 지금과 동일하게
 * 동작한다. [송출 시작]을 누르면 전 채널이 같은 시각으로 t=0부터 흐른다.
 */
"use strict";

var VSource = (() => {
  const $ = (id) => document.getElementById(id);
  let scenarios = [];
  let poll = null;
  let inited = false;
  let busy = false;
  // 성공/실패 메시지가 2초 폴링에 덮여 사라지는 걸 막는다 — 눌렀는데 아무 반응이
  // 없어 보이면 또 누르게 된다(추론 모델 패널에서 같은 문제를 겪었다).
  let stickyUntil = 0;

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
    } catch (e) { scenarios = []; }
    if (!scenarios.length) {
      sel.innerHTML = `<option value="">(시나리오 없음 — data/scenarios/*.json)</option>`;
      $("vsMeta").textContent = "";
      return;
    }
    const cur = sel.value;
    sel.innerHTML = scenarios.map((s) =>
      `<option value="${s.id}"${s.id === cur ? " selected" : ""}>` +
      `${s.name}${s.ok ? "" : " ⚠"}</option>`).join("");
    renderMeta();
  }

  function current() {
    const id = $("vsScenario") && $("vsScenario").value;
    return scenarios.find((s) => s.id === id) || null;
  }

  function renderMeta() {
    const s = current(), el = $("vsMeta");
    if (!el) return;
    if (!s) { el.textContent = ""; return; }
    // 영상 길이가 제각각이라 채널별 루프는 못 쓴다 — 사이클을 명시해 준다(ADR 08 §4).
    const durs = s.streams.map((x) => x.duration_sec).filter((v) => v != null);
    const range = durs.length
      ? (Math.min(...durs).toFixed(0) === Math.max(...durs).toFixed(0)
          ? `${Math.min(...durs).toFixed(0)}s`
          : `${Math.min(...durs).toFixed(0)}~${Math.max(...durs).toFixed(0)}s`)
      : "—";
    // 송출만 되고 운영뷰가 비는 상태를 미리 알린다 — 원인 찾기가 제일 어려운 구간이다.
    const mapped = s.streams.filter((x) => x.cam_mapped && x.cam_enabled).length;
    el.innerHTML = `${s.streams.length}채널 · 영상 ${range} · 사이클 <b>${fmtSec(s.cycle_sec)}</b>`
      + `<br>카메라 매핑 <b class="${mapped === s.streams.length ? "ok" : "warn"}">`
      + `${mapped}/${s.streams.length}</b>`
      + (mapped === s.streams.length ? " — 운영 뷰에 바로 표출됩니다"
                                      : " — 매핑 안 된 채널은 운영 뷰에 안 찍힙니다")
      + (s.ok ? "" : `<br><span class="warn">⚠ ${s.problems.join(" / ")}</span>`)
      + ((s.warns || []).length ? `<br><span class="warn">⚠ ${s.warns.join("<br>⚠ ")}</span>` : "");
  }

  /** 경로별 카메라 상태 한 줄 — 왜 안 뜨는지 바로 보이게. */
  function camLabel(sc, path) {
    const st = sc && sc.streams.find((x) => x.path === path);
    if (!st) return "";
    if (!st.cam_id) return `<span class="vscam warn">카메라 없음</span>`;
    const bad = !st.cam_enabled ? "비활성" : (!st.cam_mapped ? "매핑 없음" : null);
    return `<span class="vscam${bad ? " warn" : ""}">${st.cam_id}${bad ? " · " + bad : " ✓"}</span>`;
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
        box.innerHTML = (st.streams || []).map((s) => {
          const cs = sc && sc.streams.find((x) => x.path === s.path);
          const cid = cs && cs.cam_id;
          // 송출(dot)과 카메라 수신(rx)은 다르다 — 송출은 떴는데 카메라가 아직
          // 안 붙은 구간이 20초쯤 있어서, 둘을 갈라 보여야 오해가 없다.
          const rx = s.receiving
            ? `<span class="vsrx on">수신</span>`
            : `<span class="vsrx">붙는 중…</span>`;
          return `<div class="vsrow${s.publishing ? " on" : ""}${cid ? " clk" : ""}"` +
            `${cid ? ` data-cam="${cid}" title="클릭하면 ${cid} 매핑 화면으로 이동"` : ""}>
             <span class="dot"></span>
             <span class="nm" title="${s.file}">${s.path}</span>
             ${rx}
             ${camLabel(sc, s.path)}
             <span class="pos">${s.pos_sec != null ? s.pos_sec.toFixed(0) + "s" : "종료"}</span>
           </div>`;
        }).join("");
        box.querySelectorAll(".vsrow.clk").forEach((row) => {
          row.onclick = () => gotoMapping(row.dataset.cam);
        });
      }
    }
    if (msgEl && st && st.running && !busy && Date.now() >= stickyUntil) {
      const rxN = st.cams_receiving, rxT = st.cams_total;
      const rxTxt = (rxT && rxN < rxT)
        ? `<br><span class="warn">카메라 붙는 중 ${rxN}/${rxT} — 20초쯤 걸립니다</span>`
        : "";
      if (st.mode === "standby") {
        msgEl.innerHTML = `<span class="ok">대기 중</span> — 정지화면 (영상 멈춤)` + rxTxt
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
        { scenario_id: s.id, loop: $("vsLoop").checked });
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
      msg("대기 송출 준비 중" + ".".repeat(dots)
        + `<br><span class="vshint">첫 프레임 추출 · 상시송출 정지 · 리허설 밖 카메라 정리</span>`);
    }, 500);
    $("vsStandby").disabled = true;
    try {
      const st = await jpost("/api/vsource/standby", { scenario_id: s.id });
      const n = (st.pm2_stopped || []).length;
      msg(`<span class="ok">대기 송출 시작</span> — ${st.streams.length}채널 정지화면`
        + (n ? ` · pm2 ${n}개 정지` : "")
        + `<br><b>카메라가 붙는 데 20초쯤 걸립니다</b> — 아래 채널이 전부 '수신'이 되면 매핑하세요.`, 25);
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
      step = "pick"; txt = "리허설이 꺼져 있습니다 — 시나리오를 고르고 [⏸ 대기 송출 시작].";
    } else if (st.mode === "standby" && !allRx) {
      step = "standby"; txt = `대기 송출 준비 중 — 카메라 붙는 중 ${rxN}/${rxT} (20초쯤 걸립니다)`;
    } else if (st.mode === "standby") {
      step = "map"; txt = "대기 중(정지화면) — 매핑할 채널을 클릭하거나, ③ 운영 뷰에서 [🎬 리허설 훈련 시작]";
    } else if (!allRx) {
      step = "run"; txt = `훈련 재생 중 · 위치 ${fmtSec(st.cycle_pos_sec)} — 카메라 붙는 중 ${rxN}/${rxT}`;
    } else {
      step = "run"; txt = `훈련 재생 중 · 위치 ${fmtSec(st.cycle_pos_sec)} · 전 채널 수신`;
    }
    const order = ["pick", "standby", "map", "run"];
    const cur = order.indexOf(step);
    box.querySelectorAll("li").forEach((li) => {
      const i = order.indexOf(li.dataset.step);
      li.classList.toggle("done", i < cur);
      li.classList.toggle("cur", i === cur);
    });
    lab.textContent = txt;
    lab.classList.toggle("warn", busy || (running && !allRx));
    if ($("rhChanN")) $("rhChanN").textContent = (st && st.streams || []).length;
  }

  return {
    init, refresh,
    // ⑤ 탭 진입점 — App.switchView 가 부른다
    enter() { init(); refresh(); },
    leave() {},
  };
})();

// 화면 5 = 리허설 (view_map/view_cams 와 같은 규약)
var Views = window.Views || (window.Views = {});
Views.rehearsal = VSource;
