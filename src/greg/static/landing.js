const money = new Intl.NumberFormat('en-IE', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
const byId = (id) => document.getElementById(id);
const controls = byId('comparison-controls');
const views = {
  baseline: {
    title: 'Baseline forecast',
    copy: 'Restocking and payroll fall due before card settlements resume. The baseline shows how low cash could go without intervention.',
  },
  stabilized: {
    title: 'With simulated actions',
    copy: 'The simulated plan bridges the gap and keeps the forecast above zero. It is a comparison, not a promise or a bank transaction.',
  },
};

function plot(line, marker, points, key, width, height, min, max) {
  const x = (i) => 12 + i * (width - 24) / (points.length - 1);
  const y = (value) => height - 15 - (value - min) * (height - 30) / (max - min);
  const values = points.map((point) => point[key]);
  const low = values.indexOf(Math.min(...values));
  byId(line).setAttribute('points', values.map((value, i) => `${x(i).toFixed(1)},${y(value).toFixed(1)}`).join(' '));
  byId(marker).setAttribute('cx', x(low));
  byId(marker).setAttribute('cy', y(values[low]));
  byId(marker).removeAttribute('visibility');
  return y(0);
}

async function loadDemo() {
  const response = await fetch('/api/demo/pizzeria');
  if (!response.ok) throw new Error(`Demo HTTP ${response.status}`);
  const { series, summary } = await response.json();
  const values = series.flatMap(({ baseline, stabilized }) => [baseline, stabilized]);
  if (series.length !== 13 || !values.every(Number.isFinite)) throw new Error('Unexpected forecast series');
  const min = Math.min(0, ...values);
  const max = Math.max(min + 1, ...values);
  document.querySelector('[data-cash="baseline"]').textContent = money.format(summary.min_baseline_eur);
  byId('gap-week').textContent = `Week ${summary.min_baseline_week} · baseline`;
  plot('mini-line', 'mini-low', series, 'baseline', 320, 110, min, max);

  function show(view) {
    const amount = money.format(summary[`min_${view}_eur`]);
    const week = summary[`min_${view}_week`];
    byId('chart-heading').textContent = views[view].title;
    byId('compare-value').textContent = amount;
    byId('compare-week').textContent = `Week ${week} of 13`;
    byId('compare-copy').textContent = views[view].copy;
    byId('chart-alt').textContent = `${views[view].title}: lowest projected cash ${amount} in week ${week}, across 13 weeks.`;
    const zero = plot('comparison-line', 'comparison-low', series, view, 440, 150, min, max);
    const line = byId('zero-line');
    line.setAttribute('y1', zero);
    line.setAttribute('y2', zero);
    line.removeAttribute('visibility');
    const label = byId('zero-label');
    label.setAttribute('y', zero - 5);
    label.removeAttribute('visibility');
    for (const button of controls.querySelectorAll('button')) {
      button.setAttribute('aria-pressed', String(button.dataset.view === view));
    }
  }

  for (const button of controls.querySelectorAll('button')) {
    button.addEventListener('click', () => show(button.dataset.view));
  }
  show('baseline');
  controls.hidden = false;
}

loadDemo().catch(() => {
  byId('compare-week').textContent = 'Sample forecast unavailable. Open the workspace to explore it.';
  byId('gap-week').textContent = 'Sample forecast unavailable';
});
