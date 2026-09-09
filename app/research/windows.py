from __future__ import annotations

from datetime import date, timedelta

import pandas as pd


def walk_forward_windows(
    start: date,
    end: date,
    training_months: int,
    testing_months: int,
    roll_months: int,
) -> list[dict]:
    if min(training_months, testing_months, roll_months) <= 0:
        raise ValueError("walk-forward month counts must be positive")
    if end < start:
        raise ValueError("end must be on or after start")
    cursor = pd.Timestamp(start)
    final = pd.Timestamp(end)
    output = []
    number = 1
    while True:
        train_start = cursor
        test_start = train_start + pd.DateOffset(months=training_months)
        train_end = test_start - pd.Timedelta(days=1)
        test_end = test_start + pd.DateOffset(months=testing_months) - pd.Timedelta(days=1)
        if test_end > final:
            break
        output.append(
            {
                "window": number,
                "training": {"start": train_start.date(), "end": train_end.date()},
                "testing": {"start": test_start.date(), "end": test_end.date()},
            }
        )
        number += 1
        cursor += pd.DateOffset(months=roll_months)
    return output


def quick_period(end: date, preset: str, available_start: date | None = None) -> tuple[date, date]:
    finish = pd.Timestamp(end)
    offsets = {
        "1w": pd.DateOffset(weeks=1), "1m": pd.DateOffset(months=1),
        "3m": pd.DateOffset(months=3), "6m": pd.DateOffset(months=6),
        "1y": pd.DateOffset(years=1),
    }
    if preset == "ytd":
        start = date(end.year, 1, 1)
    elif preset == "all":
        if available_start is None:
            raise ValueError("available_start is required for all-data period")
        start = available_start
    elif preset in offsets:
        start = (finish - offsets[preset] + pd.Timedelta(days=1)).date()
    else:
        raise ValueError(f"unknown quick period: {preset}")
    return start, end
