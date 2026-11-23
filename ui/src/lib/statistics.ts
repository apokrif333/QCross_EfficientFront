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
