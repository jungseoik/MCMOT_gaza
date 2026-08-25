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
          return `<div class="vsrow${s.publishing ? " on" : ""}${cid ? " clk" : ""}"` +
            `${cid ? ` data-cam="${cid}" title="클릭하면 ${cid} 매핑 화면으로 이동"` : ""}>
             <span class="dot"></span>
             <span class="nm" title="${s.file}">${s.path}</span>
             ${camLabel(sc, s.path)}
             <span class="pos">${s.pos_sec != null ? s.pos_sec.toFixed(0) + "s" : "종료"}</span>
           </div>`;
        }).join("");
        box.querySelectorAll(".vsrow.clk").forEach((row) => {
          row.onclick = () => {
            const id = row.dataset.cam;
            if (typeof Views !== "undefined" && Views.cams && Views.cams.selectCamera) {
              Views.cams.selectCamera(id);
              // 가운데(카메라 프레임 | 맵)가 매핑 화면이다 — 거기로 시선을 옮긴다
              const duo = document.querySelector("#viewCams .duo");
              if (duo) duo.scrollIntoView({ behavior: "smooth", block: "center" });
            }
          };
        });
      }
    }
    if (msgEl && st && st.running && !busy && Date.now() >= stickyUntil) {
      const nx = st.next_cycle_in != null
        ? ` · 다음 사이클 ${Math.max(0, Math.round(st.next_cycle_in))}s 후` : "";
      msgEl.innerHTML = `<span class="ok">송출 중</span> — 위치 ${fmtSec(st.cycle_pos_sec)}${nx}`
        + `<br><span class="vshint">채널을 클릭하면 그 카메라 매핑 화면으로 갑니다</span>`;
    }
    // ③ 운영뷰 칩 — 경보를 언제 누르면 t=0에 맞는지 알려준다
    if (chip) {
      const on = !!(st && st.running);
      chip.classList.toggle("hidden", !on);
      if (on) {
        chip.textContent = st.next_cycle_in != null
          ? `▶ 리허설 송출 중 · 다음 사이클 ${Math.max(0, Math.round(st.next_cycle_in))}s 후`
          : `▶ 리허설 송출 중 · ${fmtSec(st.cycle_pos_sec)}`;
      }
    }
  }

  async function refresh() {
    try { renderStatus(await jget("/api/vsource/status")); }
    catch (e) { /* 일시 오류 무시 */ }
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
    $("vsStart").disabled = true;
    try {
      const st = await jpost("/api/vsource/start",
        { scenario_id: s.id, loop: $("vsLoop").checked });
      const n = (st.pm2_stopped || []).length;
      msg(`<span class="ok">시작됨</span> — ${st.streams.length}채널 동시 송출`
        + (n ? ` · pm2 ${n}개 정지` : "")
        + `<br>카메라가 다시 붙는 데 10초쯤 걸립니다.`, 8);
    } catch (e) {
      msg(`<span class="warn">시작 실패: ${e.message}</span>`, 15);
    } finally {
      busy = false;
      $("vsStart").disabled = false;
      refresh();
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
    $("vsStart").onclick = start;
    $("vsStop").onclick = stop;
    $("vsScenario").onchange = renderMeta;
    loadScenarios().then(refresh);
    // 상태 폴링 — 사이클 카운트다운이 있어야 경보 시점을 맞출 수 있다
    if (!poll) poll = setInterval(refresh, 2000);
  }

  return { init, refresh };
})();
