"""Read-only monthly adjClose reconciliation against stored LazyPortfolioETF USD returns.

Run from the repository root: python -m scripts.audit_tiingo_returns
No downloads, ingestion updates, currency conversion, or source-file writes.
"""

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.db.models import Instrument, MonthlyReturn, ReturnSeries
from app.db.session import make_engine, make_session_factory

US_EXCHANGES = {"NYSE ARCA", "NYSE", "NASDAQ", "BATS", "NYSE AMERICAN", "AMEX", "CBOE BZX"}
DEFAULT_ROOT = Path("H:/") / "Биржа" / "Stocks. BigData" / "Цены" / "Дейли" / "tiingo/USA/ETF"


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def load_prices(path: Path) -> tuple[pd.DataFrame, dict]:
    before = digest(path)
    frame = pd.read_csv(path, usecols=["date", "adjClose", "close", "divCash", "splitFactor"])
    frame["date"] = pd.to_datetime(frame["date"].astype(str).str[:10], errors="raise")
    frame["adjClose"] = pd.to_numeric(frame["adjClose"], errors="raise")
    if frame.empty or frame["date"].isna().any():
        raise ValueError("Empty price history or missing dates.")
    if frame["date"].duplicated().any():
        raise ValueError("Duplicate daily dates; no arbitrary choice of closing price is allowed.")
    for column in ["adjClose", "close", "splitFactor", "divCash"]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        if not np.isfinite(frame[column]).all():
            raise ValueError(f"{column} must be finite.")
        if column != "divCash" and (frame[column] <= 0).any():
            raise ValueError(f"{column} must be positive.")
    frame = frame.sort_values("date")
    frame["month"] = frame["date"].dt.to_period("M")
    monthly = frame.groupby("month", sort=True).tail(1).set_index("month")
    monthly["divCash"] = frame.groupby("month")["divCash"].sum()
    monthly["splitFactor"] = frame.groupby("month")["splitFactor"].prod()
    if digest(path) != before:
        raise ValueError("Price file changed while being read.")
    return monthly, {
        "price_file": str(path),
        "price_file_sha256": before,
        "price_first_date": str(frame["date"].iloc[0].date()),
        "price_last_date": str(frame["date"].iloc[-1].date()),
        "daily_observations": len(frame),
    }


def statistics(frame: pd.DataFrame, threshold: float) -> dict | None:
    if frame.empty:
        return None
    difference = frame["difference_pp"].abs()
    contiguous = frame.index.equals(pd.period_range(frame.index[0], frame.index[-1], freq="M"))
    result = {
        "start": str(frame.index[0]),
        "end": str(frame.index[-1]),
        "months": len(frame),
        "contiguous": contiguous,
        "mean_absolute_difference_pp": float(difference.mean()),
        "median_absolute_difference_pp": float(difference.median()),
        "p95_absolute_difference_pp": float(difference.quantile(0.95)),
        "maximum_absolute_difference_pp": float(difference.max()),
        "worst_month": str(difference.idxmax()),
        "months_over_threshold": int((difference >= threshold).sum()),
        "lazy_cagr_pct": None,
        "tiingo_cagr_pct": None,
        "cagr_difference_pp": None,
        "relative_terminal_wealth_difference_pct": None,
    }
    for source in ["lazy", "tiingo"]:
        result[f"{source}_volatility_pct"] = float(
            frame[f"{source}_return"].std(ddof=0) * np.sqrt(12) * 100
        )
    if contiguous:
        logs = {source: np.log1p(frame[f"{source}_return"]).sum() for source in ["lazy", "tiingo"]}
        for source, total in logs.items():
            result[f"{source}_cagr_pct"] = float(np.expm1(total * 12 / len(frame)) * 100)
        result["cagr_difference_pp"] = result["lazy_cagr_pct"] - result["tiingo_cagr_pct"]
        result["relative_terminal_wealth_difference_pct"] = float(
            np.expm1(logs["lazy"] - logs["tiingo"]) * 100
        )
    return result


def save_csv(path: Path, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig", float_format="%.10f")


def write_report(output: Path, result: dict, monthly_rows: list[dict]) -> None:
    monthly = pd.DataFrame(monthly_rows)
    lines = [
        "# Сверка LazyPortfolioETF и локальных Tiingo ETF",
        "",
        f"Дата проверки (UTC): {result['created_at_utc']}.",
        f"Сопоставлены **{result['compared_series']} из {result['validated_usd_series']}** "
        f"валидированных USD-рядов: **{result['compared_months']} пар месячных доходностей**.",
        "Для ^BTC соответствующего US ETF CSV нет. Дубликаты VTI, EMB и TIP из ASX исключены.",
        "",
        "## Методика",
        "",
        "Доходность Tiingo = adjClose последней торговой сессии месяца / adjClose последней "
        "сессии предыдущего месяца − 1. Сравниваются одинаковые календарные месяцы. "
        "Тикеры сопоставлены точно, без догадок и конвертации валют. "
        "Никакого заполнения пропусков.",
        "Окончания месяцев проверяются по максимальной наблюдаемой дате среди сопоставленных "
        "US-файлов. Это локальная проверка дат, а не внешний биржевой календарь. Последний "
        "месяц исключается, если не достигнут его последний будний день. Частичные окончания "
        "отдельных файлов (например, 21 ноября 2024) тоже исключаются.",
        "Каждый актив проверен за собственный полный доступный период: разные окончания "
        "файлов не обрезают все активы до самого старого файла. Истории до появления ETF "
        "по этой базе проверить невозможно.",
        f"Порог заметного месячного отклонения: **{result['monthly_threshold_pp']:g} п.п.** "
        "по абсолютной разнице доходностей, а не относительные проценты. "
        "Знак разницы: LazyPortfolioETF минус Tiingo.",
        "CAGR рассчитан компаундированием за одинаковое число месяцев; волатильность — "
        "стандартное отклонение месячных доходностей (ddof=0) × √12. "
        "CAGR не публикуется для периода с внутренними пропусками.",
        "",
        f"Всего превышений порога: **{result['flagged_months']}**. "
        f"Из них **{result['flagged_after_proxy_cutoff']}** после опубликованной границы "
        "исторической реконструкции. Граница из метаданных приблизительная: она не "
        "доказывает точную дату смены источника. При отсутствии границы показан весь "
        "доступный ETF-период с отдельной пометкой.",
        "",
        "## Заметные отклонения после границы реконструкции",
        "",
        "В таблице CAGR относится ко всему указанному периоду, а месячная разница — "
        "к самому сильному выбросу. Малый разрыв CAGR не исключает крупных месячных различий.",
        "",
        "| ETF | Проверенный период | Месяцев ≥ порога | Худший месяц | "
        "Lazy, % | Tiingo, % | Разница, п.п. | Разница CAGR, п.п./год |",
        "|---|---|---:|---|---:|---:|---:|---:|",
    ]
    ranked = []
    for record in result["summaries"]:
        stats = record.get("after_published_proxy_cutoff") or record.get("all_overlap")
        if stats and stats["months_over_threshold"]:
            ranked.append((record, stats))
    ranked.sort(key=lambda item: item[1]["maximum_absolute_difference_pp"], reverse=True)
    for record, stats in ranked:
        row = monthly.loc[
            (monthly.ticker == record["ticker"]) & (monthly.month == stats["worst_month"])
        ].iloc[0]
        annotation = " *" if not record["proxy_through"] else ""
        gap = stats["cagr_difference_pp"]
        gap_display = f"{gap:+.4f}" if gap is not None else "непрерывного периода нет"
        lines.append(
            f"| {record['ticker']}{annotation} | {stats['start']}–{stats['end']} | "
            f"{stats['months_over_threshold']} | {stats['worst_month']} | "
            f"{row.lazy_return_pct:.4f} | {row.tiingo_return_pct:.4f} | "
            f"{row.difference_pp:+.4f} | {gap_display} |"
        )
    lines += [
        "",
        "\\* В метаданных нет границы реконструкции; показан весь доступный ETF-период.",
        "",
        "## IUSV: вероятная проблема корректировки дивидендов вокруг сплита",
        "",
        "В локальном файле 25 июля 2016 указан splitFactor=3. До сплита дивидендная "
        "прибавка к месячной доходности Tiingo примерно втрое больше прибавки Lazy. "
        "Например, март 2009: изменение необработанной цены (с учётом сплитов) "
        "+6,8861%; Lazy +7,9224%; Tiingo adjClose +9,9933%. Прибавки: "
        "+1,0363 и +3,1072 п.п. В декабре 2008 и июле 2015 наблюдается та же пропорция.",
        "Это сильный признак проблемы масштабирования дивидендов в локальном Tiingo "
        "до сплита. Без третьего независимого источника размер дивидендов нельзя "
        "считать окончательно проверенным. Нельзя называть эту разницу доказанной "
        "ошибкой LazyPortfolioETF.",
        "",
    ]
    iusv = monthly.loc[monthly.ticker == "IUSV"].copy()
    iusv.index = pd.PeriodIndex(iusv.month, freq="M")
    iusv["lazy_return"] = iusv.lazy_return_pct / 100
    iusv["tiingo_return"] = iusv.tiingo_return_pct / 100
    for title, start, end in [
        ("До сплита, после границы реконструкции", "2001-01", "2016-06"),
        ("После сплита", "2016-08", "2024-10"),
    ]:
        stats = statistics(iusv.loc[start:end], result["monthly_threshold_pp"])
        if stats is None:
            continue
        gap = stats["cagr_difference_pp"]
        gap_display = f"{gap:+.4f}" if gap is not None else "не определена"
        lines.append(
            f"- {title}: {stats['start']}–{stats['end']}; "
            f"средняя абсолютная месячная разница {stats['mean_absolute_difference_pp']:.4f} п.п.; "
            f"разница CAGR {gap_display} п.п./год."
        )
    lines += [
        "",
        "## URTH: разное размещение дивидендной доходности по месяцам",
        "",
        "Июнь 2012: Lazy +0,7526%, Tiingo +2,2178%; июль 2012: Lazy +4,7991%, "
        "Tiingo +3,3222%. В локальном файле выплата divCash=0,739753 стоит "
        "21 июня. Аналогичные пары видны в июне–июле 2019 и декабре 2012–январе 2013. "
        "Различия почти компенсируются при компаундировании соседних месяцев: "
        "разница CAGR за март 2012–октябрь 2024 около −0,0040 п.п./год. "
        "Наблюдения согласуются с разным месяцем учёта дивиденда. "
        "Даже при совпадающем долгосрочном CAGR месячные ковариации могут различаться.",
        "",
        "## Другие важные случаи",
        "",
        "- IMTM: декабрь 2021 +4,4912% против +1,5133%. Один крупный выброс "
        "даёт разницу CAGR около +0,29 п.п./год. Причина не установлена.",
        "- DBC: октябрь 2008 −25,0812% против −21,4031%; ноябрь "
        "−12,3671% против −15,8472%. Различия соседних месяцев почти компенсируются; "
        "в этих месяцах локальный divCash равен нулю. Причина не установлена.",
        "- UST: июль–август 2014 — противоположные отклонения около 2,8 п.п.; "
        "почти без долгосрочного разрыва CAGR. Причина не установлена.",
        "- LQD и IEF: декабрь 2003 выделяется; CAGR на доступном периоде "
        "выше у Lazy примерно на 0,14 п.п./год. Требуют отдельной проверки источника.",
        "- IJS: после декабря 2000 средняя абсолютная месячная разница около "
        "0,0010 п.п.; максимальная 0,1982 п.п. Существенных превышений порога нет.",
        "",
        "Ранние расхождения, попавшие в опубликованную реконструкцию, также сохранены "
        "в CSV. Например, у IJR, IJK, VT, GSG и GLTR там встречаются крупные "
        "отклонения. Их нельзя автоматически трактовать как ошибку реального ETF-ряда.",
        "",
        "## Файлы и воспроизводимость",
        "",
        "- [instrument-summary.csv](instrument-summary.csv): все 73 актива, полные и "
        "после-реконструкционные периоды, CAGR, волатильность, накопленная разница.",
        "- [flagged-months.csv](flagged-months.csv): все превышения порога.",
        "- [monthly-comparison.csv](monthly-comparison.csv): все сравнения, цены, "
        "дивиденды и сплит-факторы для проверки вручную.",
        "- [excluded-months.csv](excluded-months.csv): исключённые неполные месяцы с причинами.",
        "- [reference-month-ends.csv](reference-month-ends.csv): "
        "использованные даты окончания месяцев.",
        "- [audit.json](audit.json): параметры, покрытие, метаданные и SHA-256 исходных файлов.",
        "",
        "Повторить из корня репозитория:",
        "```powershell",
        ".venv\\Scripts\\python.exe -m scripts.audit_tiingo_returns --monthly-threshold-pp 0.5",
        "```",
        "",
        "База и исходные CSV не изменялись: SHA-256 проверены до и после аудита. "
        "del.py не изменён. Сеть, загрузка истории и пересчёт наблюдений не использовались.",
    ]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, default=Path("docs/tiingo-audit"))
    parser.add_argument("--monthly-threshold-pp", type=float, default=0.5)
    arguments = parser.parse_args()
    if not np.isfinite(arguments.monthly_threshold_pp) or arguments.monthly_threshold_pp <= 0:
        raise ValueError("Threshold must be finite and positive.")
    output = arguments.output
    output.mkdir(parents=True, exist_ok=True)
    files = defaultdict(list)
    all_paths = list(arguments.prices_root.rglob("*.csv"))
    for path in all_paths:
        files[path.stem.upper()].append(path)
    settings = get_settings()
    database = Path(make_url(settings.database_url).database)
    before = digest(database)
    engine = make_engine(settings.database_url)
    series_data, coverage = [], []
    try:
        with make_session_factory(engine)() as session:
            pairs = session.execute(
                select(Instrument, ReturnSeries)
                .join(ReturnSeries)
                .where(
                    Instrument.source == "lazyportfolioetf",
                    ReturnSeries.currency == "USD",
                    ReturnSeries.validation_status == "validated",
                    ReturnSeries.frequency == "monthly",
                    ReturnSeries.return_type == "nominal_total_return",
                )
                .order_by(Instrument.id)
            ).all()
            for instrument, series in pairs:
                paths = files.get(instrument.ticker.upper(), [])
                selected = [p for p in paths if p.parent.name.upper() in US_EXCHANGES]
                record = {
                    "instrument_id": instrument.id,
                    "series_id": series.id,
                    "ticker": instrument.ticker,
                    "name": instrument.name,
                    "source_symbol": instrument.source_symbol,
                    "currency": series.currency,
                    "currency_kind": series.currency_kind,
                    "proxy_through": series.source_metadata.get("historical_proxy_through"),
                    "candidate_files": [str(p) for p in paths],
                    "excluded_non_us_files": [str(p) for p in paths if p not in selected],
                }
                if len(selected) != 1 or series.currency_kind != "native":
                    record["status"] = "no_us_file" if not selected else "ambiguous_or_non_native"
                    coverage.append(record)
                    continue
                rows = session.execute(
                    select(
                        MonthlyReturn.date,
                        MonthlyReturn.return_value,
                        MonthlyReturn.quality_flag,
                        MonthlyReturn.source_metadata,
                    )
                    .where(MonthlyReturn.series_id == series.id)
                    .order_by(MonthlyReturn.date)
                ).all()
                lazy = pd.DataFrame(
                    {
                        "lazy_return": [float(r.return_value) for r in rows],
                        "quality_flag": [r.quality_flag for r in rows],
                    },
                    index=pd.PeriodIndex([r.date for r in rows], freq="M"),
                )
                if lazy.index.has_duplicates or not np.isfinite(lazy["lazy_return"]).all():
                    raise ValueError(f"Invalid stored series for {instrument.ticker}.")
                record["status"] = "matched"
                record["price_file"] = str(selected[0])
                coverage.append(record)
                series_data.append((record, lazy, selected[0]))
    finally:
        engine.dispose()
    # Unequal publication endpoints must not truncate newer files to SPY's endpoint.
    price_cache = {}
    for record, _, path in series_data:
        try:
            price_cache[path] = load_prices(path)
        except (ValueError, KeyError, pd.errors.ParserError) as exc:
            record.update(status="price_data_error", error=str(exc))
    calendar = pd.concat([entry[0][["date"]] for entry in price_cache.values()])
    calendar = calendar.groupby(level=0).max().sort_index()
    calendar_identity = {"method": "Latest observed monthly US date across matched price files"}
    final_month = calendar.index[-1]
    final_observed = calendar["date"].iloc[-1]
    expected_weekday_end = final_month.to_timestamp(how="end").normalize()
    while expected_weekday_end.weekday() > 4:
        expected_weekday_end -= pd.Timedelta(days=1)
    excluded_calendar_months = []
    if final_observed < expected_weekday_end:
        excluded_calendar_months.append(str(final_month))
        calendar = calendar.iloc[:-1]
    summaries, monthly_rows, excluded_rows = [], [], []
    for number, (record, lazy, path) in enumerate(series_data, 1):
        try:
            if path not in price_cache:
                summaries.append(record)
                continue
            prices, identity = price_cache[path]
            record.update(identity)
            prices["previous_date"] = prices["date"].shift(1)
            prices["previous_adjClose"] = prices["adjClose"].shift(1)
            prices["previous_month"] = pd.Series(prices.index, index=prices.index).shift(1)
            prices["tiingo_return"] = prices["adjClose"] / prices["previous_adjClose"] - 1
            prices["price_only_return"] = (
                prices["close"] / prices["close"].shift(1) * prices["splitFactor"] - 1
            )
            joined = prices.join(lazy, how="inner")
            valid = []
            for month, row in joined.iterrows():
                reason = None
                if pd.isna(row["previous_adjClose"]):
                    reason = "no_previous_month_end_price"
                elif row["previous_month"] != month - 1:
                    reason = "missing_previous_price_month"
                elif month not in calendar.index or month - 1 not in calendar.index:
                    reason = "incomplete_or_unverified_calendar_month"
                elif row["date"] != calendar.at[month, "date"]:
                    reason = "current_price_not_at_reference_month_end"
                elif row["previous_date"] != calendar.at[month - 1, "date"]:
                    reason = "previous_price_not_at_reference_month_end"
                if reason:
                    excluded_rows.append(
                        {"ticker": record["ticker"], "month": str(month), "reason": reason}
                    )
                else:
                    valid.append(month)
            joined = joined.loc[valid].copy()
            if (joined["lazy_return"] <= -1).any():
                raise ValueError("Stored monthly returns must exceed -100% for compounding.")
            if not joined.empty and joined.index.equals(
                pd.period_range(joined.index[0], joined.index[-1], freq="M")
            ):
                # Independent endpoint identity catches month-order/return-chain mistakes.
                chain = np.exp(np.log1p(joined["tiingo_return"]).sum())
                endpoints = joined["adjClose"].iloc[-1] / joined["previous_adjClose"].iloc[0]
                if not np.isclose(chain, endpoints, rtol=1e-10, atol=1e-12):
                    raise ValueError("Compounded Tiingo returns disagree with endpoint prices.")
            joined["difference_pp"] = (joined["lazy_return"] - joined["tiingo_return"]) * 100
            cutoff = (
                pd.Period(record["proxy_through"], freq="M") if record["proxy_through"] else None
            )
            native = joined.loc[joined.index > cutoff] if cutoff else joined.iloc[0:0]
            all_stats = statistics(joined, arguments.monthly_threshold_pp)
            native_stats = statistics(native, arguments.monthly_threshold_pp)
            record.update(
                status="compared" if all_stats else "no_complete_common_months",
                all_overlap=all_stats,
                after_published_proxy_cutoff=native_stats,
            )
            summaries.append(record)
            for month, row in joined.iterrows():
                monthly_rows.append(
                    {
                        "ticker": record["ticker"],
                        "instrument_id": record["instrument_id"],
                        "series_id": record["series_id"],
                        "month": str(month),
                        "lazy_return_pct": float(row["lazy_return"] * 100),
                        "tiingo_return_pct": float(row["tiingo_return"] * 100),
                        "difference_pp": float(row["difference_pp"]),
                        "absolute_difference_pp": float(abs(row["difference_pp"])),
                        "after_published_proxy_cutoff": bool(cutoff and month > cutoff),
                        "quality_flag": row["quality_flag"],
                        "previous_price_date": str(row["previous_date"].date()),
                        "price_date": str(row["date"].date()),
                        "previous_adjClose": float(row["previous_adjClose"]),
                        "adjClose": float(row["adjClose"]),
                        "price_only_return_pct": float(row["price_only_return"] * 100),
                        "divCash": float(row["divCash"]),
                        "splitFactor": float(row["splitFactor"]),
                    }
                )
            maximum = all_stats["maximum_absolute_difference_pp"] if all_stats else 0
            print(
                f"{number}/{len(series_data)} {record['ticker']}: "
                f"{all_stats['months'] if all_stats else 0} months, "
                f"max difference {maximum:.4f} pp",
                flush=True,
            )
        except (ValueError, KeyError, pd.errors.ParserError) as exc:
            record.update(status="price_data_error", error=str(exc))
            summaries.append(record)
    changed = [
        r["ticker"]
        for r in summaries
        if r.get("price_file_sha256") and digest(Path(r["price_file"])) != r["price_file_sha256"]
    ]
    after = digest(database)
    if before != after or changed:
        raise RuntimeError(
            f"Sources changed during comparison: database={before != after}, tickers={changed}."
        )
    flagged = sorted(
        [r for r in monthly_rows if r["absolute_difference_pp"] >= arguments.monthly_threshold_pp],
        key=lambda r: r["absolute_difference_pp"],
        reverse=True,
    )
    flat = []
    for record in summaries:
        row = {
            k: record.get(k)
            for k in [
                "ticker",
                "instrument_id",
                "series_id",
                "name",
                "status",
                "price_file",
                "price_first_date",
                "price_last_date",
                "proxy_through",
                "price_file_sha256",
            ]
        }
        for prefix, key in [("all", "all_overlap"), ("post_proxy", "after_published_proxy_cutoff")]:
            row.update({f"{prefix}_{k}": v for k, v in (record.get(key) or {}).items()})
        flat.append(row)
    save_csv(output / "instrument-summary.csv", flat)
    save_csv(output / "monthly-comparison.csv", monthly_rows)
    save_csv(output / "flagged-months.csv", flagged)
    save_csv(output / "excluded-months.csv", excluded_rows)
    calendar.to_csv(output / "reference-month-ends.csv", encoding="utf-8-sig")
    result = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "prices_root": str(arguments.prices_root),
        "csv_files_in_inventory": len(all_paths),
        "validated_usd_series": len(coverage),
        "compared_series": sum(r["status"] == "compared" for r in summaries),
        "compared_months": len(monthly_rows),
        "monthly_threshold_pp": arguments.monthly_threshold_pp,
        "flagged_months": len(flagged),
        "flagged_after_proxy_cutoff": sum(r["after_published_proxy_cutoff"] for r in flagged),
        "session_reference": calendar_identity,
        "excluded_reference_end_months": excluded_calendar_months,
        "database_sha256_before": before,
        "database_sha256_after": after,
        "database_unchanged": before == after,
        "price_files_unchanged": not changed,
        "coverage": coverage,
        "summaries": summaries,
        "methodology": {
            "return": "Last observed US month-end adjClose / previous month-end adjClose - 1.",
            "difference": "Lazy return minus Tiingo return, in percentage points.",
            "alignment": (
                "Exact month matching and both price endpoints equal local observed US endpoints; "
                "no fill or month shifting."
            ),
            "calendar": (
                "Maximum observed month-end across matched US files; final month excluded if "
                "ending before the final weekday. Conservative if that weekday was a holiday."
            ),
            "identity": (
                "Exact ticker/file-stem match within explicit US exchange folders. "
                "No ASX price files or FX conversion."
            ),
            "cagr": (
                "Compounded monthly simple returns, annualized using 12 / number of months; "
                "only contiguous periods."
            ),
            "proxy": (
                "Full overlap and months after published broad proxy cutoff are reported. "
                "This cutoff is not proof of a precise reconstruction transition."
            ),
        },
    }
    (output / "audit.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )
    write_report(output, result, monthly_rows)
    print(
        json.dumps(
            {
                k: result[k]
                for k in [
                    "validated_usd_series",
                    "compared_series",
                    "compared_months",
                    "flagged_months",
                    "flagged_after_proxy_cutoff",
                    "database_unchanged",
                ]
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
