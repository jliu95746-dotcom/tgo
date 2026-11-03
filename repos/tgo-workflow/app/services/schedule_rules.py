"""Bounded, timezone-explicit clock rules for persistent workflow schedules.

The five numeric fields are minute, hour, day, month, weekday (Sunday=0/7).
Lists, ascending ranges and positive steps are supported. Limited day and
weekday fields are alternatives. A skipped wall minute never runs; an
ambiguous wall minute runs once, at its earliest UTC instant.
"""

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ScheduleRuleError(ValueError):
    """A fixed, safe message suitable for configuration validation."""


def _field_values(token: str, minimum: int, maximum: int) -> frozenset[int]:
    values: set[int] = set()
    for item in token.split(","):
        pieces = item.split("/")
        if len(pieces) > 2 or not pieces[0]:
            raise ScheduleRuleError("定时规则的列表、范围或步长格式无效。")
        step = 1
        if len(pieces) == 2:
            if not pieces[1].isdigit():
                raise ScheduleRuleError("定时规则的步长必须是正整数。")
            step = int(pieces[1])
            if not 1 <= step <= maximum - minimum + 1:
                raise ScheduleRuleError("定时规则的步长超出允许范围。")
        base = pieces[0]
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            limits = base.split("-")
            if len(limits) != 2 or not all(
                value.isdigit() for value in limits
            ):
                raise ScheduleRuleError("定时规则必须使用递增的数字范围。")
            start, end = (int(value) for value in limits)
        elif base.isdigit():
            start = int(base)
            end = maximum if len(pieces) == 2 else start
        else:
            raise ScheduleRuleError("定时规则仅支持数字、星号、列表、范围和步长。")
        if not minimum <= start <= end <= maximum:
            raise ScheduleRuleError("定时规则的数字或范围超出该时间字段的限制。")
        values.update(range(start, end + 1, step))
    return frozenset(values)


def _time_zone(name: str) -> ZoneInfo:
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= 100
        or not re.fullmatch(r"[A-Za-z0-9_+./-]+", name)
        or name.startswith("/")
        or any(part in {"", ".", ".."} for part in name.split("/"))
    ):
        raise ScheduleRuleError("请选择有效的 IANA 时区名称。")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ScheduleRuleError("所选时区不可用，请核对时区数据库。") from None


@dataclass(frozen=True)
class ScheduleRule:
    expression: str
    timezone_name: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    zone: ZoneInfo = field(repr=False)

    def _matches_day(self, day: date) -> bool:
        if day.month not in self.months:
            return False
        day_matches = day.day in self.days
        weekday_matches = (day.weekday() + 1) % 7 in self.weekdays
        if len(self.days) == 31:
            return weekday_matches
        if len(self.weekdays) == 7:
            return day_matches
        return day_matches or weekday_matches

    def _day_instants(self, day: date) -> list[datetime]:
        values: list[datetime] = []
        for hour in sorted(self.hours):
            for minute in sorted(self.minutes):
                wall = datetime(day.year, day.month, day.day, hour, minute)
                candidates: set[datetime] = set()
                for fold in (0, 1):
                    utc = wall.replace(tzinfo=self.zone, fold=fold).astimezone(
                        timezone.utc,
                    )
                    # Round-trip excludes imaginary times during clock jumps.
                    if utc.astimezone(self.zone).replace(tzinfo=None) == wall:
                        candidates.add(utc)
                if candidates:
                    values.append(min(candidates))
        return sorted(values)

    def next_occurrences(
        self,
        after: datetime,
        *,
        count: int = 1,
    ) -> tuple[datetime, ...]:
        """Return strictly later UTC instants, never the current due minute.

        Search is bounded to eight years per requested occurrence. This
        accommodates leap days without accepting an impossible calendar.
        Registration/recovery must persist its own due instant; this helper
        does not silently replay missed ticks or use the server's timezone.
        """
        if type(count) is not int or not 1 <= count <= 10:
            raise ScheduleRuleError("预览次数必须为 1 到 10 的整数。")
        if not isinstance(after, datetime) or after.utcoffset() is None:
            raise ScheduleRuleError("计算定时任务必须提供带时区的参考时间。")
        try:
            boundary = after.astimezone(timezone.utc)
            first_day = boundary.astimezone(self.zone).date()
            found: list[datetime] = []
            for offset in range(366 * 8 * count):
                day = first_day + timedelta(days=offset)
                if not self._matches_day(day):
                    continue
                for value in self._day_instants(day):
                    if value > boundary:
                        found.append(value)
                        if len(found) == count:
                            return tuple(found)
        except (OverflowError, ValueError):
            raise ScheduleRuleError("定时规则超出可计算的日期范围。") from None
        raise ScheduleRuleError("定时规则在允许的日期范围内没有足够的有效时间。")


def parse_schedule(
    expression: str, timezone_name: str = "UTC"
) -> ScheduleRule:
    """Parse a bounded numeric five-field rule without evaluating text."""
    if (
        not isinstance(expression, str)
        or not 1 <= len(expression) <= 512
        or not re.fullmatch(r"[0-9*,/ \-]+", expression)
    ):
        raise ScheduleRuleError("定时规则必须是五段数字时间表达式。")
    tokens = expression.split()
    if len(tokens) != 5:
        raise ScheduleRuleError("定时规则必须包含分钟、小时、日期、月份和星期五段。")
    ranges = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
    fields = [
        _field_values(token, minimum, maximum)
        for token, (minimum, maximum) in zip(tokens, ranges)
    ]
    weekdays = frozenset(value % 7 for value in fields[4])
    # With a restricted weekday, that alternative always occurs in a month.
    # Otherwise a leap year provides every valid month/day combination.
    if len(weekdays) == 7 and not any(
        day <= monthrange(2000, month)[1]
        for month in fields[3]
        for day in fields[2]
    ):
        raise ScheduleRuleError("定时规则没有有效的日期组合。")
    return ScheduleRule(
        expression=" ".join(tokens),
        timezone_name=timezone_name,
        minutes=fields[0],
        hours=fields[1],
        days=fields[2],
        months=fields[3],
        weekdays=weekdays,
        zone=_time_zone(timezone_name),
    )
