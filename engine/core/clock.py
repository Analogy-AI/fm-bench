"""Game calendar: 360-day year, 12 months x 30 days. Year and day are 1-based."""

from __future__ import annotations

DAYS_PER_YEAR = 360
DAYS_PER_MONTH = 30


class GameDate:
    __slots__ = ("year", "day")

    def __init__(self, year: int = 1, day: int = 1):
        self.year = year
        self.day = day

    @property
    def month(self) -> int:
        return (self.day - 1) // DAYS_PER_MONTH + 1

    @property
    def day_of_month(self) -> int:
        return (self.day - 1) % DAYS_PER_MONTH + 1

    @property
    def abs_day(self) -> int:
        return (self.year - 1) * DAYS_PER_YEAR + self.day

    def advance(self) -> bool:
        """Move one day forward. Returns True if a new year started."""
        self.day += 1
        if self.day > DAYS_PER_YEAR:
            self.day = 1
            self.year += 1
            return True
        return False

    def is_monday(self) -> bool:
        return self.day % 7 == 1

    def copy(self) -> "GameDate":
        return GameDate(self.year, self.day)

    def to_dict(self) -> dict:
        return {"day": self.day, "year": self.year}

    def __str__(self) -> str:
        return f"Y{self.year:02d}-D{self.day:03d}"

    def __repr__(self) -> str:
        return f"GameDate({self.year}, {self.day})"
