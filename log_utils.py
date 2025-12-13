"""Logging helpers for bet records."""

from dataclasses import dataclass
from typing import Dict, Iterable, List
import csv
import os
import logging


@dataclass
class BetLogEntry:
    """Structured representation of a single bet log entry."""

    timestamp: str
    event_id: str
    match: str
    league: str
    ahc_odds: float
    home_win_odds: float
    stake: float
    loss_streak: int
    result: str
    balance: float
    score: float
    notes: str
    pnl: str = ""

    def to_row(self) -> List[str]:
        return [
            self.timestamp,
            self.event_id,
            self.match,
            self.league,
            str(self.ahc_odds),
            str(self.home_win_odds),
            str(self.stake),
            str(self.loss_streak),
            self.result,
            self.pnl,
            str(self.balance),
            str(self.score),
            self.notes,
        ]


class BetLogManager:
    """Manage reading and writing bet logs."""

    headers = [
        "Timestamp",
        "EventID",
        "Match",
        "League",
        "AHC_Odds",
        "HomeWin_Odds",
        "Stake",
        "LossStreak",
        "Result",
        "PnL",
        "Balance",
        "Score",
        "Notes",
    ]

    def __init__(self, log_file: str) -> None:
        self.log_file = log_file

    def init_file(self) -> None:
        if not os.path.exists(self.log_file):
            with open(self.log_file, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self.headers)
            logging.info("创建日志文件: %s", self.log_file)

    def load(self) -> List[Dict[str, str]]:
        if not os.path.exists(self.log_file):
            return []

        with open(self.log_file, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return list(reader)

    def append(self, entry: BetLogEntry) -> None:
        with open(self.log_file, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(entry.to_row())

    @staticmethod
    def has_bet_on_event(event_id: str, logs: Iterable[Dict[str, str]]) -> bool:
        return any(str(log.get("EventID", "")) == str(event_id) for log in logs)

