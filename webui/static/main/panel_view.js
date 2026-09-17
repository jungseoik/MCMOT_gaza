/* 우측 지표 패널 — 표출 밀도(요약·카드·표) · 섹션 접기 · 패널 접기.
 *
 * 왜: 4대 지표 카드가 1열로 쌓이고 그 아래 목록 4개(구역·병목·출입구·객체)가
 * 붙어 스크롤이 생겼다. 맵이 주인공인 시연에선 한 줄이면 충분하고, 값을 정확히
 * 읽을 땐 표가 낫다. 지표 계산에는 영향이 없다 — 표출만 바뀐다.
 *
 * 선택은 보는 사람 취향이지 서버 설정이 아니라 localStorage 에 기억한다
 * (③ [표시] 레이어 토글과 같은 규칙).
 */
"use strict";

const PanelView = {
  _KEY: "macs.panel",

  _load() {
    try { return JSON.parse(localStorage.getItem(PanelView._KEY) || "{}"); }
    catch (e) { return {}; }
  },
  _save(patch) {
    try {
      localStorage.setItem(PanelView._KEY,
        JSON.stringify({ ...PanelView._load(), ...patch }));
    } catch (e) { /* 사생활 모드 등 — 기억만 못 할 뿐 동작은 그대로 */ }
  },

  /** 밀도 적용 — side 에 dens-* 클래스 하나만 남긴다. */
  setDensity(sideId, segId, dens) {
    const side = document.getElementById(sideId);
    const seg = document.getElementById(segId);
    if (!side) return;
    // "full" = 기존 화면 그대로 — 클래스를 하나도 안 붙인다(원래 CSS 가 그대로 산다).
    ["sum", "card"].forEach((d) => side.classList.toggle("dens-" + d, d === dens));
    if (seg) {
      seg.querySelectorAll("[data-dens]").forEach((b) =>
        b.classList.toggle("on", b.dataset.dens === dens));
    }
    PanelView._save({ ["dens_" + sideId]: dens });
    // 캔버스(스파크·막대)는 CSS 박스로 크기가 정해진다 — 폭·높이가 바뀌었으니 다시 그리게 한다.
    setTimeout(() => window.dispatchEvent(new Event("resize")), 0);
  },

  setFolded(sideId, on) {
    const side = document.getElementById(sideId);
    if (!side) return;
    side.classList.toggle("folded", !!on);
    PanelView._save({ ["fold_" + sideId]: !!on });
    // 캔버스가 폭을 다시 재야 한다 — 리사이즈 한 번 태운다.
    setTimeout(() => window.dispatchEvent(new Event("resize")), 0);
  },

  /** `.group` 을 접을 수 있게 만든다. 제목 옆에 항목 수를 띄운다. */
  makeFoldable(groupEl, key, opts) {
    if (!groupEl || groupEl._foldWired) return;
    const t = groupEl.querySelector(".gtitle");
    if (!t) return;
    groupEl._foldWired = true;
    t.classList.add("foldable");
    if (!t.querySelector(".fcount")) {
      const c = document.createElement("span");
      c.className = "fcount";
      t.appendChild(c);
    }
    const st = PanelView._load();
    const saved = st["grp_" + key];
    const folded = saved === undefined ? !!(opts && opts.foldedByDefault) : saved;
    groupEl.classList.toggle("folded", folded);
    t.addEventListener("click", () => {
      const now = !groupEl.classList.contains("folded");
      groupEl.classList.toggle("folded", now);
      PanelView._save({ ["grp_" + key]: now });
    });
  },

  /** 접힌 상태에서도 개수는 보이게 — 렌더 후 호출한다. */
  setCount(groupEl, n) {
    const c = groupEl && groupEl.querySelector(".gtitle .fcount");
    if (c) c.textContent = (n === null || n === undefined) ? "" : String(n);
  },

  /** 한 패널(side)의 밀도 세그·접기 버튼을 배선하고 저장값을 복원한다. */
  wire(sideId, segId, foldBtnId, groups) {
    const st = PanelView._load();
    const seg = document.getElementById(segId);
    if (seg) {
      seg.querySelectorAll("[data-dens]").forEach((b) => {
        b.addEventListener("click", () =>
          PanelView.setDensity(sideId, segId, b.dataset.dens));
      });
    }
    PanelView.setDensity(sideId, segId, (["sum","card"].includes(st["dens_" + sideId]) ? st["dens_" + sideId] : "full"));

    const fb = document.getElementById(foldBtnId);
    if (fb) {
      fb.addEventListener("click", () =>
        PanelView.setFolded(sideId,
          !document.getElementById(sideId).classList.contains("folded")));
    }
    if (st["fold_" + sideId]) PanelView.setFolded(sideId, true);

    (groups || []).forEach((g) => {
      const el = typeof g.el === "string" ? document.querySelector(g.el) : g.el;
      PanelView.makeFoldable(el, g.key, { foldedByDefault: g.foldedByDefault });
    });
  },
};
