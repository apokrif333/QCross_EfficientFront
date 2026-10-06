import type { ApiRequest } from "../types";
export interface AssetRow {
  key: string;
  instrumentId: number | null;
  allocation: string;
  minimum: string;
  maximum: string;
  group: number;
}
export const emptyRow = (): AssetRow => ({
  key: crypto.randomUUID(),
  instrumentId: null,
  allocation: "",
  minimum: "0",
  maximum: "100",
  group: 1,
});
export function rowsFromRequest(request: ApiRequest): AssetRow[] {
  const rows: AssetRow[] = request.instrument_ids.map((id) => ({
    ...emptyRow(),
    instrumentId: id,
    allocation:
      request.user_weights?.[String(id)] == null
        ? ""
        : String(request.user_weights[String(id)] * 100),
    minimum: String((request.asset_constraints[String(id)]?.min ?? 0) * 100),
    maximum: String((request.asset_constraints[String(id)]?.max ?? 1) * 100),
    group: request.asset_groups?.[String(id)] ?? 1,
  }));
  while (rows.length < 5) rows.push(emptyRow());
  return rows;
}
export function allocationError(rows: AssetRow[]): string {
  const selected = rows.filter((r) => r.instrumentId !== null);
  if (new Set(selected.map((r) => r.instrumentId)).size !== selected.length)
    return "Один инструмент нельзя выбирать дважды.";
  for (const r of rows) {
    const w = Number(r.allocation || 0),
      min = Number(r.minimum || 0),
      max = Number(r.maximum || 100);
    if (!Number.isFinite(w) || w < 0 || w > 100)
      return "Аллокация должна быть от 0% до 100%.";
    if (r.instrumentId === null && w !== 0)
      return "Выберите инструмент для указанной аллокации.";
    if (![min, max].every(Number.isFinite) || min < 0 || max > 100 || min > max)
      return "Ограничения веса: 0 ≤ минимум ≤ максимум ≤ 100%.";
  }
  const total = selected.reduce((sum, r) => sum + Number(r.allocation || 0), 0);
  return total === 0 || Math.abs(total - 100) < 1e-6
    ? ""
    : `Сумма аллокаций должна быть 100% или 0%. Сейчас: ${total.toFixed(2)}%.`;
}
export function requestFromRows(
  rows: AssetRow[],
  previous: ApiRequest,
): ApiRequest {
  const selected = rows.filter((r) => r.instrumentId !== null),
    groups = [...new Set(selected.map((r) => r.group))];
  const weights = Object.fromEntries(
    selected.map((r) => [
      String(r.instrumentId),
      Number(r.allocation || 0) / 100,
    ]),
  );
  return {
    ...previous,
    instrument_ids: selected.map((r) => r.instrumentId!),
    asset_constraints: Object.fromEntries(
      selected.map((r) => [
        String(r.instrumentId),
        {
          min: Number(r.minimum || 0) / 100,
          max: Number(r.maximum || 100) / 100,
        },
      ]),
    ),
    asset_groups: Object.fromEntries(
      selected.map((r) => [String(r.instrumentId), r.group]),
    ),
    group_constraints: Object.fromEntries(
      groups.map((g) => [
        `Group ${g}`,
        previous.group_constraints[`Group ${g}`] ?? { min: 0, max: 1 },
      ]),
    ),
    user_weights: Object.values(weights).some((w) => w !== 0) ? weights : null,
  };
}
