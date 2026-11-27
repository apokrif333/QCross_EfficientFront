export function rollingReturns(
  months: string[],
  wealth: (number | null)[],
  years: number,
) {
  const window = years * 12;
  if (
    !Number.isInteger(years) ||
    years < 1 ||
    window > months.length ||
    wealth.length !== months.length + 1
  ) {
    return [];
  }

  return months.slice(window - 1).flatMap((end, i) => {
    const initial = wealth[i];
    const final = wealth[i + window];
    if (
      initial == null ||
      final == null ||
      initial <= 0 ||
      final < 0 ||
      !Number.isFinite(initial) ||
      !Number.isFinite(final)
    ) {
      return [];
    }
    const value = Math.expm1((Math.log(final) - Math.log(initial)) / years);
    return Number.isFinite(value) ? [{ start: months[i], end, value }] : [];
  });
}

export function rollingReturnDifferences(
  months: string[],
  passiveWealth: (number | null)[],
  managedWealth: (number | null)[],
  years: number,
) {
  const managedByEnd = new Map(
    rollingReturns(months, managedWealth, years).map(({ end, value }) => [
      end,
      value,
    ]),
  );
  return rollingReturns(months, passiveWealth, years).flatMap(
    ({ start, end, value: passive }) => {
      const managed = managedByEnd.get(end);
      if (managed === undefined) return [];
      const difference = passive - managed;
      // Equivalent returns can have small floating-point differences between NAV paths.
      return [
        {
          start,
          end,
          value: Math.abs(difference) < 1e-12 ? 0 : difference,
          passive,
          managed,
        },
      ];
    },
  );
}

export function threeYearReturnCounts(
  months: string[] | undefined,
  wealth: (number | null)[] | undefined,
) {
  if (!months || !wealth) return undefined;
  const points = rollingReturns(months, wealth, 3);
  return {
    positive: points.filter(({ value }) => value > 0).length,
    total: points.length,
  };
}
