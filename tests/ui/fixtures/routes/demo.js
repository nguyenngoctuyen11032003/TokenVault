// Test-only route mounted at js/routes/demo.js by tests/ui/conftest.py.
// Exercises the route extension contract and gives the layout/screenshot tests real panels.
// Demo copy is deliberately hardcoded here: this file never ships with the app.

const TEAMS = [['Tất cả', 14], ['Dev', 8], ['QA', 3], ['BA / PM', 3]];
const PEOPLE = [
  ['tuyen.nguyen@ics.vn', '1.21M', 100, true],
  ['an.tran@ics.vn', '0.94M', 78, true],
  ['minh.le@ics.vn', '0.61M', 50, false],
  ['hoa.pham@ics.vn', '0.40M', 33, true],
];

export default function render(container, ctx) {
  container.innerHTML = `
    <div class="tv-grid" style="grid-template-columns: minmax(220px, 280px) minmax(0, 1fr)"
         data-demo-args='${JSON.stringify(ctx.args)}'>
      <tv-panel heading="Nhóm">
        <ul class="tv-list">
          ${TEAMS.map(([n, c], i) => `
            <li><button class="tv-list__item" ${i === 0 ? 'aria-pressed="true"' : ''}>
              <span class="tv-list__ring"></span>${n}<span class="tv-list__count">${c}</span>
            </button></li>`).join('')}
        </ul>
      </tv-panel>
      <tv-panel heading="Hôm nay">
        <div class="tv-gauge-row">
          <tv-gauge value="4820000" max="8000000" label="Tổng token" unit="TOKEN"></tv-gauge>
          <tv-gauge value="61.4" max="100" display="$61.4" label="Ước tính" unit="CHI PHÍ"></tv-gauge>
          <tv-gauge value="37" max="60" label="Đang/đã chạy" unit="PHIÊN"></tv-gauge>
        </div>
      </tv-panel>
    </div>
    <div class="tv-grid" style="grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr)">
      <tv-panel heading="Nhân viên">
        <table class="tv-table">
          <thead><tr><th>Tài khoản Claude</th><th class="tv-num">Token</th><th></th></tr></thead>
          <tbody>
            ${PEOPLE.map(([e, t, p, on]) => `
              <tr><td><span class="tv-dot ${on ? 'tv-dot--on' : ''}"></span>${e}</td>
              <td class="tv-num">${t}</td>
              <td style="width:84px"><span class="tv-bar"><i style="width:${p}%"></i></span></td></tr>`
            ).join('')}
          </tbody>
        </table>
      </tv-panel>
      <tv-panel heading="Monitoring">
        <div class="tv-meta"><span>Token / giờ – realtime</span><span>Đỉnh: 812K lúc 10:00</span></div>
        <div class="tv-split">
          <div class="tv-chart-area" style="height:150px">
            <svg viewBox="0 0 300 120" preserveAspectRatio="none" width="100%" height="100%">
              <polyline fill="none" stroke="var(--tv-red-hot)" stroke-width="2"
                vector-effect="non-scaling-stroke"
                points="0,110 25,104 50,90 75,60 100,72 125,30 150,48 175,40 200,70 225,55 250,80 275,62 300,58"/>
            </svg>
          </div>
          <div class="tv-kpi">
            <div class="tv-kpi__value tv-num--hot">812K</div>
            <div class="tv-kpi__sub"><span class="tv-num--orange">$9.8</span> /giờ</div>
          </div>
        </div>
        <div class="tv-meta" style="margin-top:12px"><span>Dự án tốn nhất hôm nay</span></div>
        <div class="tv-chips">
          <span>TokenVault <b class="tv-num--orange">1.4M</b></span>
          <span>erp-api <b class="tv-num--orange">0.9M</b></span>
          <span>mobile-app <b class="tv-num--orange">0.7M</b></span>
        </div>
      </tv-panel>
    </div>
    <p class="tv-muted" data-demo-hello>${ctx.t('demo.hello')}</p>`;
  ctx.onLive(() => {});
}

export function destroy() {
  window.__demoDestroyed = true;
}
