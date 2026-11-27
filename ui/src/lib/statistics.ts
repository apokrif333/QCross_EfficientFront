/** Linear interpolation between adjacent observations sorted in ascending order. */
export function linearQuantile(
  sorted: number[],
  quantile: number,
): number | null {
  if (!sorted.length) return null;
  const position = (sorted.length - 1) * quantile;
  const lower = Math.floor(position);
  return (
    sorted[lower] +
    (sorted[Math.ceil(position)] - sorted[lower]) * (position - lower)
  );
}

/** Color each value relative to its column, with yellow at the median. */
export function trendHeatmap(
  values: (number | null | undefined)[],
  lowerIsBetter = false,
) {
  const direction = lowerIsBetter ? -1 : 1;
  const scores = values.map((value) =>
    value != null && Number.isFinite(value) ? value * direction : null,
  );
  const sorted = scores.filter((value) => value != null).sort((a, b) => a - b);
  const worst = sorted[0];
  const best = sorted[sorted.length - 1];
  const median = linearQuantile(sorted, 0.5);
  // A tied median at an endpoint must leave room for both endpoint colors.
  const middle =
    median != null && worst < median && median < best
      ? median
      : (worst + best) / 2;
  return scores.map((score) => {
    if (score == null) return undefined;
    const hue =
      worst === best
        ? 60
        : score <= middle
          ? (60 * (score - worst)) / (middle - worst)
          : 60 + (60 * (score - middle)) / (best - middle);
    return "hsl(" + hue + ", 75%, 85%)";
  });
}
