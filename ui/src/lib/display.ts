import type { Instrument, Metrics, Portfolio } from "../types";
export const percent = (n: number | null | undefined, digits = 2) =>
  n == null ? "—" : `${(n * 100).toFixed(digits)}%`;
export const number = (n: number | null | undefined, digits = 3) =>
  n == null ? "—" : n.toFixed(digits);
export const ticker = (asset: string, catalog: Instrument[]) =>
  catalog.find((i) => String(i.id) === asset)?.ticker || asset;
export const escapeHtml = (text: string) =>
  text.replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ]!,
  );
export function portfolioHover(
  portfolio: Portfolio,
  catalog: Instrument[],
): string {
  const m: Metrics | null = portfolio.metrics;
  return m
    ? `Доходность: ${percent(m.expected_return)}<br>Волатильность: ${percent(m.volatility)}<br>Sharpe: ${number(m.sharpe_ratio)}<br><br>${Object.entries(
        portfolio.weights || {},
      )
        .map(([a, w]) => `${escapeHtml(ticker(a, catalog))}: ${percent(w)}`)
        .join("<br>")}`
    : "Решение недоступно";
}
export function commonHistory(
  selected: Instrument[],
): { start: string; end: string } | null {
  if (!selected.length) return null;
  const start = selected
    .map((i) => i.start_date.slice(0, 7))
    .sort()
    .at(-1)!;
  const end = selected.map((i) => i.end_date.slice(0, 7)).sort()[0];
  return start <= end ? { start, end } : null;
}
