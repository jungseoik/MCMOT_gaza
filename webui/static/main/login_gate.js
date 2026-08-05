/* 간이 로그인 게이트 (프론트 전용) — 앱 진입 전 브랜딩(PIA × 삼성화재) + 비밀번호.
 *
 * ⚠️ 실제 보안 아님: 비밀번호가 클라이언트에 있으므로 접근 통제/인증 용도로는
 *    쓸 수 없다. "브랜딩 있는 간이 진입 화면" 목적. 진짜 인증이 필요하면 서버측
 *    세션/OAuth로 대체할 것.
 *
 * 끄기(되돌리기): index.html에서 이 스크립트 <script> 한 줄만 지우면 됨.
 * 비밀번호 변경: 아래 PASSCODE. 없애고 '입장'만 원하면 PASSCODE = "".
 */
"use strict";
(() => {
  const ACCEPT_ANY = true;                 // ← mock: 아무 입력(빈 값 포함)이나 그냥 통과
  const REMEMBER = false;                  // ← mock: false면 접속할 때마다 로그인 화면 표시
                                           //   (true면 한 번 통과 후 그 브라우저에서 기억)
  const PASSCODE = "macs";                 // ACCEPT_ANY=false 일 때만 검사하는 접속 코드
  const KEY = "macs_gate_ok";
  if (REMEMBER && localStorage.getItem(KEY) === "1") return;   // 기억된 경우만 건너뜀

  const css = `
  #loginGate{position:fixed;inset:0;z-index:99999;display:flex;align-items:center;
    justify-content:flex-end;padding-right:clamp(24px,9vw,150px);
    background:
      linear-gradient(100deg, rgba(7,9,14,.10) 0%, rgba(7,9,14,.06) 42%, rgba(7,9,14,.50) 74%, rgba(7,9,14,.72) 100%),
      url('/static/login-bg.jpg') left center / cover no-repeat,
      #07090e;
    font-family:Pretendard,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}
  #loginGate .lg-card{width:min(92vw,440px);background:#fff;border-radius:18px;
    padding:34px 34px 28px;box-shadow:0 24px 80px rgba(0,0,0,.55);text-align:center;}
  #loginGate .lg-collab{display:flex;align-items:flex-end;justify-content:center;gap:26px;margin-bottom:26px;}
  #loginGate .lg-collab img.pia{height:42px}
  #loginGate .lg-collab img.skr{height:33px}
  #loginGate .lg-divider{width:1px;height:30px;background:#dfe3ea}
  #loginGate .lg-title{font-size:21px;font-weight:800;color:#15181f;letter-spacing:-.02em}
  #loginGate .lg-sub{font-size:12.5px;color:#8890a0;margin-top:8px;margin-bottom:24px;letter-spacing:.01em}
  #loginGate .lg-form{display:flex;flex-direction:column;gap:10px}
  #loginGate input{height:44px;border:1px solid #d6dae1;border-radius:10px;padding:0 14px;
    font-size:14px;color:#15181f;outline:none;transition:border-color .15s}
  #loginGate input:focus{border-color:#1a37d6}
  #loginGate button{height:44px;border:0;border-radius:10px;background:#1a37d6;color:#fff;
    font-size:15px;font-weight:700;cursor:pointer;transition:background .15s}
  #loginGate button:hover{background:#122ba8}
  #loginGate .lg-err{min-height:16px;font-size:12px;color:#e5484d;margin-top:2px}
  #loginGate .lg-foot{margin-top:16px;font-size:11px;color:#9aa1ac}
  @media (max-width:820px){
    #loginGate{justify-content:center;padding-right:0;
      background:
        linear-gradient(180deg, rgba(7,9,14,.55), rgba(7,9,14,.78)),
        url('/static/login-bg.jpg') center center / cover no-repeat, #07090e;}
  }
  `;
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  const wrap = document.createElement("div");
  wrap.id = "loginGate";
  wrap.innerHTML = `
    <div class="lg-card">
      <div class="lg-collab">
        <img class="pia" src="/static/pia-logo.png" alt="PIA" />
        <span class="lg-divider"></span>
        <img class="skr" src="/static/samsung-fire-kr.png" alt="삼성화재" />
      </div>
      <div class="lg-title">피난대피 지표보드</div>
      <div class="lg-sub">공동 개발 소프트웨어</div>
      <form class="lg-form" id="lgForm">
        ${PASSCODE ? '<input id="lgPw" type="password" placeholder="접속 코드" autocomplete="off" autofocus />' : ""}
        <button type="submit">${PASSCODE ? "로그인" : "입장"}</button>
        <div class="lg-err" id="lgErr"></div>
      </form>
      <div class="lg-foot">CCTV 영상분석 기반 피난 분석 엔진</div>
    </div>`;
  document.documentElement.appendChild(wrap);

  const done = () => { if (REMEMBER) localStorage.setItem(KEY, "1"); wrap.remove(); };
  document.getElementById("lgForm").addEventListener("submit", (e) => {
    e.preventDefault();
    if (ACCEPT_ANY || !PASSCODE) return done();   // mock: 무엇을 넣든 통과
    const v = (document.getElementById("lgPw").value || "").trim();
    if (v === PASSCODE) done();
    else {
      document.getElementById("lgErr").textContent = "접속 코드가 올바르지 않습니다.";
      const pw = document.getElementById("lgPw"); pw.value = ""; pw.focus();
    }
  });
})();
