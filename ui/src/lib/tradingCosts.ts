export const costLabels = {
  total_commissions: "Накопленные комиссии на $1 начального капитала",
  total_capital_gains_tax: "Уплаченный налог на $1 начального капитала",
  total_costs: "Совокупные издержки на $1 начального капитала",
  annualized_commissions: "Годовые комиссии",
  annualized_capital_gains_tax: "Годовой налог на прирост капитала",
  annualized_total_costs: "Совокупные годовые издержки",
  unpaid_capital_gains_tax: "Непогашенный налог на $1 начального капитала",
} as const;

export const annualCostKeys = [
  "annualized_commissions",
  "annualized_capital_gains_tax",
  "annualized_total_costs",
] as const;

export const annualCostHint =
  "Годовые издержки Net: сумма списаний в долях капитала перед каждой сделкой, приведённая к 12 месяцам.";

export const eventLabels: Record<string, string> = {
  initial_purchase: "Начальная покупка",
  rebalancing_policy: "Плановая ребалансировка",
  calendar_rebalance: "Плановая ребалансировка",
  threshold_rebalance: "Пороговая ребалансировка",
  control_change: "Изменение управляющего сигнала",
  fold_target_change: "Смена весов Fold",
  tax_settlement: "Закрытие налогового года",
  combined_rebalance_and_tax_settlement:
    "Ребалансировка и закрытие налогового года",
  terminal_tax_settlement: "Закрытие налога в конце периода",
};
